"""
Competitor Price Radar — public t.me/s scrape + optional Telethon.
Auto-reprice is STRICTLY clamped by Profit Guard floor.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger
from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.core.models.entities import (
    CompetitorSource, CompetitorPriceSnapshot, MasterProduct, PricingRule, ProductSupplierLink,
)
from src.core.models.enums import PricingScope, PricingRuleType, SupplierStatus
from src.core.services.pricing_service import pricing_service
from src.core.redis import event_bus


PRICE_RE = re.compile(
    r"(?:\$|USD\s*)(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*(?:\$|USD)",
    re.I,
)


class CompetitorRadarService:
    def parse_prices_from_text(
        self,
        text: str,
        keyword_map: List[Dict[str, Any]],
        *,
        competitor_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Extract {product_id, price_usd, label, snippet} from a message body."""
        body = text or ""
        body_l = body.lower()
        hits = []
        for mapping in keyword_map or []:
            kw = str(mapping.get("keyword") or "").strip().lower()
            pid = mapping.get("product_id")
            if not kw or not pid:
                continue
            if kw not in body_l:
                continue
            # Prefer price near the keyword
            idx = body_l.find(kw)
            window = body[max(0, idx - 80): idx + 120]
            m = PRICE_RE.search(window) or PRICE_RE.search(body)
            if not m:
                continue
            raw = m.group(1) or m.group(2)
            try:
                price = Decimal(raw).quantize(Decimal("0.01"))
            except Exception:
                continue
            hits.append({
                "product_id": str(pid),
                "price_usd": price,
                "product_label": kw,
                "raw_snippet": window.strip()[:240],
                "competitor_name": competitor_name,
            })
        return hits

    async def fetch_telegram_web_preview(self, handle_or_url: str) -> List[str]:
        """Scrape public https://t.me/s/{username} messages (no MTProto)."""
        handle = (handle_or_url or "").strip()
        if handle.startswith("https://t.me/s/"):
            url = handle
        elif handle.startswith("https://t.me/"):
            user = handle.rstrip("/").split("/")[-1].lstrip("@")
            url = f"https://t.me/s/{user}"
        else:
            user = handle.lstrip("@")
            url = f"https://t.me/s/{user}"

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            res = await client.get(url, headers={"User-Agent": "Mozilla/5.0 CompetitorRadar/1.0"})
            res.raise_for_status()
            html = res.text

        try:
            from bs4 import BeautifulSoup  # type: ignore
            soup = BeautifulSoup(html, "html.parser")
            nodes = soup.select(".tgme_widget_message_text")
            return [n.get_text("\n", strip=True) for n in nodes if n.get_text(strip=True)]
        except ImportError:
            # Regex fallback
            return re.findall(
                r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                html,
                flags=re.S,
            )

    async def ingest_source(
        self, session: AsyncSession, source: CompetitorSource
    ) -> List[CompetitorPriceSnapshot]:
        texts: List[str] = []
        if source.source_type in ("telegram_web", "telegram", None) or "t.me" in (source.handle_or_url or ""):
            try:
                texts = await self.fetch_telegram_web_preview(source.handle_or_url)
            except Exception as e:
                logger.warning(f"Radar scrape failed for {source.name}: {e}")
                texts = []
        # Optional Telethon path when env present
        if not texts and settings.TELETHON_API_ID and settings.TELETHON_API_HASH:
            texts = await self._telethon_fetch(source.handle_or_url)

        created: List[CompetitorPriceSnapshot] = []
        for text in texts[:40]:
            hits = self.parse_prices_from_text(
                text, source.keyword_map or [], competitor_name=source.name
            )
            for h in hits:
                snap = CompetitorPriceSnapshot(
                    source_id=source.id,
                    master_product_id=uuid.UUID(str(h["product_id"])),
                    competitor_name=h.get("competitor_name") or source.name,
                    product_label=h.get("product_label"),
                    price_usd=h["price_usd"],
                    raw_snippet=h.get("raw_snippet"),
                )
                session.add(snap)
                created.append(snap)
        if created:
            await session.flush()
        return created

    async def _telethon_fetch(self, handle: str) -> List[str]:
        try:
            from telethon import TelegramClient  # type: ignore
        except ImportError:
            return []
        # Best-effort; session file optional — skip if not configured for headless
        logger.info("Telethon available but session not auto-configured; skipping MTProto fetch")
        return []

    async def scan_all(self, session: AsyncSession) -> Dict[str, Any]:
        sources = (await session.execute(
            select(CompetitorSource).where(CompetitorSource.is_active == True)  # noqa: E712
        )).scalars().all()
        total_snaps = 0
        alerts = []
        for src in sources:
            snaps = await self.ingest_source(session, src)
            total_snaps += len(snaps)
            for snap in snaps:
                alert = await self._maybe_undercut_and_reprice(session, snap)
                if alert:
                    alerts.append(alert)
        await session.commit()
        return {"sources": len(sources), "snapshots": total_snaps, "alerts": alerts}

    async def _maybe_undercut_and_reprice(
        self, session: AsyncSession, snap: CompetitorPriceSnapshot
    ) -> Optional[Dict[str, Any]]:
        if not snap.master_product_id:
            return None
        product = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == snap.master_product_id)
        )).scalar_one_or_none()
        if not product:
            return None

        pricing = await pricing_service.get_best_supplier_and_price(session, str(product.id))
        our_price = pricing.get("final_price")
        cost = pricing.get("wholesale_cost") or pricing.get("supplier_cost")
        if our_price is None:
            return None
        our = Decimal(str(our_price))
        theirs = Decimal(str(snap.price_usd))
        if theirs >= our:
            return None

        alert = {
            "product_id": str(product.id),
            "title_ar": product.title_ar,
            "our_price": float(our),
            "competitor_price": float(theirs),
            "competitor": snap.competitor_name,
            "diff": float(our - theirs),
        }
        await event_bus.publish_alert(
            title="📡 رادار المنافسين — سعر أقل",
            message=(
                f"المنافس {snap.competitor_name} يعرض «{product.title_ar}» بـ ${theirs} "
                f"(سعرنا ${our} — فرق ${our - theirs})."
            ),
            level="warning",
            meta={"alert_type": "competitor_undercut", **alert},
        )

        if product.auto_match_competitor and cost is not None:
            new_price = await self.apply_auto_reprice(
                session,
                product=product,
                competitor_price=theirs,
                supplier_cost=Decimal(str(cost)),
                current_price=our,
            )
            alert["auto_reprice_to"] = float(new_price) if new_price is not None else None
        return alert

    async def apply_auto_reprice(
        self,
        session: AsyncSession,
        *,
        product: MasterProduct,
        competitor_price: Decimal,
        supplier_cost: Decimal,
        current_price: Decimal,
        incentive_rate: Optional[Decimal] = None,
    ) -> Optional[Decimal]:
        """
        Target = competitor_price - $0.05, STRICTLY clamped above Profit Guard floor.
        Returns the new retail price (or None if no change).
        """
        if incentive_rate is None:
            incentive_rate = await pricing_service.get_incentive_rate(session)

        target = (competitor_price - Decimal("0.05")).quantize(Decimal("0.01"))
        # Floor uses current list as gross for incentive reserve conservatism
        floor = pricing_service.profit_guard_min_payable(
            gross_total=max(current_price, target, supplier_cost + Decimal("1")),
            supplier_cost=supplier_cost,
            incentive_rate=incentive_rate,
        )
        # Also ensure classic cost + safety
        hard_floor = (supplier_cost + pricing_service.DEFAULT_PROFIT_SAFETY_FLOOR).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        min_price = max(floor, hard_floor)
        new_price = max(target, min_price)

        # Upsert product FLOOR pricing rule
        stmt = select(PricingRule).where(
            and_(
                PricingRule.scope == PricingScope.PRODUCT,
                PricingRule.scope_id == product.id,
                PricingRule.is_active == True,  # noqa: E712
            )
        ).order_by(PricingRule.priority.desc())
        rule = (await session.execute(stmt)).scalars().first()
        if rule:
            rule.rule_type = PricingRuleType.FLOOR
            rule.value = new_price
            rule.priority = max(int(rule.priority or 0), 100)
        else:
            session.add(PricingRule(
                scope=PricingScope.PRODUCT,
                scope_id=product.id,
                rule_type=PricingRuleType.FLOOR,
                value=new_price,
                priority=100,
                is_active=True,
            ))
        product.cached_final_price = new_price
        return new_price

    async def comparison_table(self, session: AsyncSession) -> List[Dict[str, Any]]:
        """Side-by-side: product | our price | lowest competitor | diff | competitor."""
        products = (await session.execute(
            select(MasterProduct).where(MasterProduct.is_active == True)  # noqa: E712
        )).scalars().all()
        rows = []
        for p in products:
            pricing = await pricing_service.get_best_supplier_and_price(session, str(p.id))
            our = pricing.get("final_price")
            if our is None and p.cached_final_price is not None:
                our = p.cached_final_price
            snap = (await session.execute(
                select(CompetitorPriceSnapshot)
                .where(CompetitorPriceSnapshot.master_product_id == p.id)
                .order_by(CompetitorPriceSnapshot.price_usd.asc(), desc(CompetitorPriceSnapshot.observed_at))
                .limit(1)
            )).scalar_one_or_none()
            if not snap and our is None:
                continue
            if not snap:
                continue
            our_f = float(our) if our is not None else None
            their_f = float(snap.price_usd)
            rows.append({
                "product_id": str(p.id),
                "title_ar": p.title_ar,
                "our_price": our_f,
                "competitor_price": their_f,
                "diff": (our_f - their_f) if our_f is not None else None,
                "competitor": snap.competitor_name,
                "auto_match_competitor": bool(p.auto_match_competitor),
                "observed_at": snap.observed_at.isoformat() if snap.observed_at else None,
            })
        return rows


competitor_radar_service = CompetitorRadarService()
