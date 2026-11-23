"""
AI Sales & Support Concierge — tool-calling agent with strict data boundary.
Never exposes supplier names or wholesale costs to the LLM or customer replies.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger
from sqlalchemy import select, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.core.models.entities import MasterProduct, Order, Customer, BotConfig
from src.core.models.enums import OrderState
from src.core.services.pricing_service import pricing_service
from src.core.services.syp_rate_service import get_sham_cash_rate, usd_to_syp, format_syp
from src.core.services.warranty_service import warranty_service
from src.core.redis import event_bus


SAFE_PRODUCT_KEYS = {
    "product_id", "title_ar", "price_usd", "price_syp", "stock",
    "warranty_days", "delivery_type",
}


async def _ai_enabled(session: AsyncSession) -> bool:
    row = (await session.execute(
        select(BotConfig).where(BotConfig.key == "ai_concierge_enabled")
    )).scalar_one_or_none()
    if row is None or row.value is None:
        return bool(settings.AI_CONCIERGE_API_KEY)
    if isinstance(row.value, bool):
        return row.value
    return str(row.value).lower() in ("true", "1", "yes", "on")


class AIConciergeService:
    async def check_rate_limit(self, telegram_id: int) -> bool:
        """Returns True if under limit (allowed)."""
        redis = event_bus._redis
        if not redis:
            return True
        key = f"ai_concierge:rl:{telegram_id}"
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, 3600)
        return int(n) <= int(settings.AI_CONCIERGE_MAX_PER_HOUR or 15)

    async def search_live_catalog(
        self,
        session: AsyncSession,
        query: str,
        *,
        only_in_stock: bool = True,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Customer-safe catalog search — NEVER includes supplier/wholesale."""
        q = (query or "").strip()
        if len(q) < 2:
            return []
        pattern = f"%{q}%"
        stmt = (
            select(MasterProduct)
            .where(
                and_(
                    MasterProduct.is_active == True,  # noqa: E712
                    or_(
                        MasterProduct.title_ar.ilike(pattern),
                        MasterProduct.title_en.ilike(pattern),
                        MasterProduct.brand_family.ilike(pattern),
                        MasterProduct.search_keywords.ilike(pattern),
                        MasterProduct.description_ar.ilike(pattern),
                    ),
                )
            )
            .limit(20)
        )
        products = list((await session.execute(stmt)).scalars().all())
        rate, accept_syp = await get_sham_cash_rate(session)
        out: List[Dict[str, Any]] = []
        for p in products:
            pricing = await pricing_service.get_best_supplier_and_price(session, str(p.id))
            in_stock = bool(pricing.get("is_available"))
            if only_in_stock and not in_stock:
                continue
            price = Decimal(str(pricing.get("final_price") or 0))
            winner = pricing.get("winner_link")
            stock = None
            if winner and winner.available_quantity is not None:
                stock = int(winner.available_quantity)
            elif in_stock:
                stock = "∞"
            else:
                stock = 0
            item = {
                "product_id": str(p.id),
                "title_ar": p.title_ar,
                "price_usd": float(price),
                "price_syp": float(usd_to_syp(price, rate)) if accept_syp else None,
                "price_syp_label": format_syp(usd_to_syp(price, rate)) if accept_syp else None,
                "stock": stock,
                "warranty_days": int(p.warranty_days or 30),
                "delivery_type": getattr(p, "delivery_type_label", None) or "⚡ فوري",
            }
            # Hard strip any accidental leak keys
            out.append({k: v for k, v in item.items() if k in SAFE_PRODUCT_KEYS or k == "price_syp_label"})
            if len(out) >= limit:
                break
        return out

    async def get_customer_recent_orders(
        self, session: AsyncSession, customer_id: Any, limit: int = 5
    ) -> List[Dict[str, Any]]:
        import uuid as _uuid
        cid = customer_id if isinstance(customer_id, _uuid.UUID) else _uuid.UUID(str(customer_id))
        rows = (await session.execute(
            select(Order)
            .where(Order.customer_id == cid)
            .options(selectinload(Order.master_product))
            .order_by(desc(Order.created_at))
            .limit(limit)
        )).scalars().all()
        out = []
        for o in rows:
            out.append({
                "order_number": o.order_number,
                "product_title": o.master_product.title_ar if o.master_product else None,
                "state": o.state.value if o.state else None,
                "price_usd": float(o.our_price or 0),
                "warranty_expires_at": (
                    o.warranty_expires_at.isoformat() if o.warranty_expires_at else None
                ),
                "warranty_eligible": warranty_service.is_eligible(o),
                "order_id": str(o.id),
            })
        return out

    async def draft_warranty_claim(
        self,
        session: AsyncSession,
        *,
        customer_id: Any,
        order_ref: str,
        issue_summary: str,
    ) -> Dict[str, Any]:
        """Pre-opens claim if eligible; returns status for bot CTAs."""
        stmt = select(Order).where(Order.order_number == order_ref.lstrip("#"))
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            # try by id prefix
            return {"status": "not_found", "message": "الطلب غير موجود"}
        if str(order.customer_id) != str(customer_id):
            return {"status": "forbidden", "message": "هذا الطلب لا يخصك"}
        if not warranty_service.is_eligible(order):
            return {"status": "expired", "message": "انتهت فترة الضمان لهذا الطلب"}
        try:
            result = await warranty_service.open_claim(
                session,
                order_id=order.id,
                customer_id=customer_id,
                issue_description=issue_summary or "مشكلة في الحساب/المفتاح",
            )
            return {"status": "opened", **result, "order_id": str(order.id), "order_number": order.order_number}
        except ValueError as e:
            return {"status": "error", "message": str(e), "order_id": str(order.id)}

    def _heuristic_intent(self, text: str) -> Dict[str, Any]:
        t = (text or "").lower()
        warranty_kw = ("وقف", "ما بيشتغل", "استبدال", "ضمان", "خربان", "مشكلة", "warranty")
        if any(k in t for k in warranty_kw):
            return {"intent": "warranty", "query": text}
        return {"intent": "search", "query": text}

    async def handle_message(
        self,
        session: AsyncSession,
        *,
        customer: Customer,
        text: str,
    ) -> Dict[str, Any]:
        """
        Main entry: returns {reply_ar, products[], warranty_cta?, escalate: bool}.
        Uses LLM when configured; otherwise keyword heuristic + catalog search.
        """
        if not await _ai_enabled(session):
            return {
                "reply_ar": "المساعد الذكي غير مفعّل حالياً. استخدم القائمة أو اكتب اسم المنتج للبحث.",
                "products": [],
                "escalate": True,
            }

        intent = self._heuristic_intent(text)
        products: List[Dict[str, Any]] = []
        warranty_cta = None

        # Prefer tool path (deterministic) — LLM optional for phrasing
        if intent["intent"] == "warranty":
            orders = await self.get_customer_recent_orders(session, customer.id, limit=3)
            eligible = [o for o in orders if o.get("warranty_eligible")]
            if eligible:
                o = eligible[0]
                warranty_cta = {
                    "order_id": o["order_id"],
                    "order_number": o["order_number"],
                }
                reply = (
                    f"فهمت — يبدو أن لديك مشكلة ضمن الضمان للطلب `{o['order_number']}`.\n"
                    f"اضغط الزر أدناه لفتح طلب استبدال، أو تواصل مع الدعم."
                )
            else:
                reply = (
                    "لم أجد طلباً ضمن فترة الضمان حالياً. "
                    "يمكنك فتح تذكرة دعم من القائمة أو مراجعة سجل مشترياتك."
                )
                return {"reply_ar": reply, "products": [], "escalate": True, "warranty_cta": None}
        else:
            products = await self.search_live_catalog(session, text, only_in_stock=False, limit=5)
            if not products:
                # broaden: strip fillers
                cleaned = re.sub(
                    r"(عندك|بدي|قلي|قديش|بكم|بالليرة|ليرة|شو|في|هل|يوجد|من فضلك)",
                    " ",
                    text,
                    flags=re.I,
                )
                products = await self.search_live_catalog(
                    session, cleaned.strip() or text, only_in_stock=False, limit=5
                )
            if products:
                lines = []
                for p in products[:3]:
                    syp = f" ≈ {p.get('price_syp_label')}" if p.get("price_syp_label") else ""
                    lines.append(
                        f"• **{p['title_ar']}** — `${p['price_usd']}`{syp} "
                        f"· ضمان {p['warranty_days']}ي · {p.get('delivery_type') or ''}"
                    )
                reply = (
                    "تمام! لقيت لك هالخيارات المتوفرة:\n\n"
                    + "\n".join(lines)
                    + "\n\nاضغط زر الشراء المباشر تحت الرسالة 👇"
                )
            else:
                reply = (
                    "ما لقيت منتج مطابق حالياً. جرّب اسم أوضح "
                    "(مثل ChatGPT أو Netflix) أو اضغط تصفح المتجر."
                )

        # Optional LLM polish (never feeds supplier data)
        polished = await self._maybe_llm_polish(text, reply, products)
        if polished:
            reply = polished

        # Final safety scrub — strip any accidental leak keys from product payloads
        reply = self._scrub_leaks(reply)
        clean_products: List[Dict[str, Any]] = []
        for p in products:
            clean_products.append({
                k: v for k, v in p.items()
                if k in SAFE_PRODUCT_KEYS or k == "price_syp_label"
            })

        return {
            "reply_ar": reply,
            "products": clean_products,
            "warranty_cta": warranty_cta,
            "escalate": len(clean_products) == 0 and warranty_cta is None,
        }

    async def _maybe_llm_polish(
        self, user_text: str, draft_reply: str, products: List[Dict[str, Any]]
    ) -> Optional[str]:
        key = (settings.AI_CONCIERGE_API_KEY or "").strip()
        if not key:
            return None
        safe_catalog = [
            {k: p[k] for k in p if k in SAFE_PRODUCT_KEYS or k == "price_syp_label"}
            for p in products
        ]
        system = (
            "أنت مساعد مبيعات ودعم لمتجر اشتراكات رقمية سوري. "
            "رد باختصار ودّي بالعربية. لا تذكر أسماء موردين أو تكاليف جملة أبداً. "
            "استخدم فقط بيانات الكتالوج المعطاة. لا تخترع أسعاراً."
        )
        payload = {
            "model": settings.AI_CONCIERGE_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        f"سؤال الزبون: {user_text}\n"
                        f"مسودة الرد: {draft_reply}\n"
                        f"كتالوج آمن: {json.dumps(safe_catalog, ensure_ascii=False)}\n"
                        "أعد صياغة الرد النهائي فقط."
                    ),
                },
            ],
            "temperature": 0.4,
            "max_tokens": 400,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(
                    f"{settings.AI_CONCIERGE_BASE_URL.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if res.status_code != 200:
                    logger.warning(f"AI concierge LLM error: {res.status_code} {res.text[:200]}")
                    return None
                data = res.json()
                content = data["choices"][0]["message"]["content"]
                return self._scrub_leaks(content.strip())
        except Exception as e:
            logger.warning(f"AI concierge LLM failed: {e}")
            return None

    @staticmethod
    def _scrub_leaks(text: str) -> str:
        # Best-effort redaction of leaky words if model misbehaves
        banned = [
            r"wholesale\s*cost",
            r"supplier\s*name",
            r"تكلفة\s*الجملة",
            r"اسم\s*المورد",
        ]
        out = text or ""
        for pat in banned:
            out = re.sub(pat, "•••", out, flags=re.I)
        return out


ai_concierge_service = AIConciergeService()
