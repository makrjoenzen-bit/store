"""
Live Proof & Credibility channel cards — privacy-masked, zero wholesale leaks.
Shared formatters + Redis publishers used by JIT, payment verification, and channel_sync.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from loguru import logger

from src.core.redis import event_bus


def mask_string(val: str, prefix_len: int = 4, suffix_len: int = 2) -> str:
    if not val:
        return "***"
    val = str(val).strip()
    if len(val) <= prefix_len + suffix_len:
        return val[0] + "***" + val[-1] if len(val) > 1 else "***"
    return f"{val[:prefix_len]}***{val[-suffix_len:]}"


def mask_username(username: str = None, fallback_name: str = None) -> str:
    if username:
        clean = username.lstrip("@")
        return f"@{mask_string(clean, prefix_len=1, suffix_len=2)}"
    if fallback_name:
        name = str(fallback_name).strip()
        if not name:
            return "C****"
        return f"{name[0]}****"
    return "C****"


def format_utc_proof_time(dt: Optional[datetime] = None) -> str:
    when = dt or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.strftime("%d-%b-%Y %I:%M %p UTC")


def payment_method_label(raw: Any) -> str:
    if raw is None:
        return "USDT"
    s = str(getattr(raw, "value", raw)).replace("_", " ").strip()
    low = s.lower()
    mapping = {
        "wallet": "Wallet",
        "balance": "Wallet",
        "binance pay": "Binance Pay",
        "binance_pay": "Binance Pay",
        "usdt bep20 direct": "USDT BEP-20",
        "usdt_bep20_direct": "USDT BEP-20",
        "usdt trc20 direct": "USDT TRC-20",
        "usdt_trc20_direct": "USDT TRC-20",
        "sham cash": "Sham Cash",
        "sham_cash": "Sham Cash",
    }
    return mapping.get(low, s.title() if s else "USDT")


def format_purchase_proof_card(data: Dict[str, Any]) -> str:
    """Image-2 style completed purchase proof (Arabic + clean fields)."""
    order_num = data.get("order_number") or data.get("id") or "ORD-XXXX"
    masked_id = mask_string(str(order_num), prefix_len=4, suffix_len=3)
    masked_user = mask_username(data.get("customer_username"), data.get("customer_first_name"))
    product_title = data.get("product_title") or "منتج رقمي"
    qty = int(data.get("quantity") or 1)
    when = format_utc_proof_time(data.get("completed_at"))
    pay = payment_method_label(data.get("payment_method"))
    return (
        f"🎉 **عملية شراء جديدة!**\n"
        f"🎉 New Purchase!\n\n"
        f"📌 **ID:** `{masked_id}`\n"
        f"👤 **العميل:** `{masked_user}`\n"
        f"🛒 **المنتج:** {product_title}\n"
        f"⭐ **الكمية:** `{qty}`\n"
        f"🕒 **الوقت:** `{when}`\n"
        f"💳 **طريقة الدفع:** `{pay}`\n"
        f"✅ تم التسليم بنجاح"
    )


def format_deposit_proof_card(data: Dict[str, Any]) -> str:
    """Wallet top-up / deposit confirmed proof card."""
    tx_ref = data.get("tx_ref") or data.get("id") or "TX-XXXX"
    masked_id = mask_string(str(tx_ref), prefix_len=4, suffix_len=3)
    masked_user = mask_username(data.get("customer_username"), data.get("customer_first_name"))
    when = format_utc_proof_time(data.get("confirmed_at"))
    pay = payment_method_label(data.get("payment_method") or data.get("method"))
    amount = float(data.get("amount_usd") or 0)
    amount_line = f"\n💰 **المبلغ:** `+${amount:.2f}`" if amount > 0 else ""
    return (
        f"💎 **شحن محفظة ناجح!**\n"
        f"💎 Wallet Top-Up!\n\n"
        f"📌 **ID:** `{masked_id}`\n"
        f"👤 **العميل:** `{masked_user}`"
        f"{amount_line}\n"
        f"🕒 **الوقت:** `{when}`\n"
        f"💳 **طريقة الدفع:** `{pay}`\n"
        f"✅ تم شحن المحفظة بنجاح"
    )


def format_restock_card(
    *,
    title: str,
    emoji: str = "✨",
    added_qty: int,
    current_stock: int,
    unit_price: Decimal,
    warranty_days: int = 30,
    bulk_caption: str = "",
    is_new: bool = False,
) -> str:
    header = "🆕 **متوفر الآن**" if is_new else "➕ **JUST RESTOCKED**"
    stock_line = (
        f"➕ Added: `{added_qty}`\n"
        f"📊 Current stock: `{current_stock}`\n"
        if current_stock > 0
        else f"➕ Added: `{added_qty}`\n📊 Current stock: متوفر\n"
    )
    bulk_block = f"\n{bulk_caption}\n" if bulk_caption else "\n"
    return (
        f"{header}\n\n"
        f"{emoji} **{title}**\n"
        f"{stock_line}"
        f"💲 Price: `${Decimal(str(unit_price)):.2f}`\n"
        f"🛡️ Warranty: `{warranty_days}` days"
        f"{bulk_block}"
        f"🪄 Use /shop to see all products, or tap the button below to buy now!"
    )


async def publish_purchase_proof(payload: Dict[str, Any]) -> None:
    event = {"type": "order_completed", **payload}
    await event_bus.publish("channel:broadcast_proof", event)


async def publish_deposit_proof(payload: Dict[str, Any]) -> None:
    event = {"type": "deposit_confirmed", **payload}
    await event_bus.publish("channel:broadcast_proof", event)


def format_trust_channel_card(data: Dict[str, Any], *, stage: str = "delivered") -> str:
    """High-trust HTML card for قناة التوثيق — never includes credentials/receipts."""
    from html import escape as _esc
    order_num = _esc(str(data.get("order_number") or "—"))
    product = _esc(str(data.get("product_title") or "منتج رقمي"))
    qty = int(data.get("quantity") or 1)
    pay = _esc(payment_method_label(data.get("payment_method")))
    masked = _esc(mask_username(data.get("customer_username"), data.get("customer_first_name")))
    if stage == "paid":
        header = "✅ تم تأكيد الدفع وتسليم الطلب"
        status = "تم الدفع والتسليم بنجاح ✅"
    else:
        header = "✅ تم توثيق عملية ناجحة"
        status = "تم الدفع والتسليم بنجاح ✅"
    return (
        f"<b>{header}</b>\n\n"
        f"🔖 الطلب: <code>#{order_num}</code>\n"
        f"📦 المنتج: {product}\n"
        f"⭐ الكمية: <code>{qty}</code>\n"
        f"👤 العميل: <code>{masked}</code>\n"
        f"💳 الدفع: <code>{pay}</code>\n"
        f"📌 الحالة: {status}"
    )


def _cfg_truthy(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes", "on")


async def publish_to_proof_channel(
    order_id: Any,
    *,
    stage: str = "delivered",
    force: bool = False,
    session=None,
) -> Dict[str, Any]:
    """
    Idempotent trust-channel publish for an order.
    - Requires proof_channel_id in BotConfig
    - Skips if proof_posted_at already set (unless force=True still respects idempotency)
    - Never includes delivered credentials / receipt files
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from src.core.database import AsyncSessionLocal
    from src.core.models.entities import Order, BotConfig
    import uuid as _uuid

    async def _run(sess) -> Dict[str, Any]:
        oid = order_id if not isinstance(order_id, str) else _uuid.UUID(order_id)
        order = (
            await sess.execute(
                select(Order)
                .where(Order.id == oid)
                .options(
                    selectinload(Order.customer),
                    selectinload(Order.master_product),
                    selectinload(Order.invoice),
                )
            )
        ).scalar_one_or_none()
        if not order:
            return {"status": "error", "reason": "order_not_found"}

        if order.proof_posted_at is not None:
            return {
                "status": "skipped",
                "reason": "already_posted",
                "order_id": str(order.id),
                "proof_posted_at": order.proof_posted_at.isoformat(),
            }

        rows = (
            await sess.execute(
                select(BotConfig).where(
                    BotConfig.key.in_(["proof_channel_id", "proof_channel_auto_post", "auto_post_proofs_enabled"])
                )
            )
        ).scalars().all()
        cfg = {r.key: r.value for r in rows}
        proof_ch = cfg.get("proof_channel_id") or ""
        if isinstance(proof_ch, dict):
            proof_ch = proof_ch.get("value") or ""
        proof_ch = str(proof_ch or "").strip()
        if not proof_ch:
            return {"status": "error", "reason": "proof_channel_not_configured"}

        if not force:
            auto = cfg.get("proof_channel_auto_post")
            if auto is None:
                auto = cfg.get("auto_post_proofs_enabled")
            if not _cfg_truthy(auto, default=False):
                return {"status": "skipped", "reason": "auto_post_disabled", "order_id": str(order.id)}

        cust = order.customer
        pay_method = None
        if order.invoice and order.invoice.payment_method:
            pay_method = order.invoice.payment_method
        payload = {
            "type": "trust_channel",
            "stage": stage,
            "force": True,
            "order_id": str(order.id),
            "order_number": order.order_number,
            "product_title": order.master_product.title_ar if order.master_product else None,
            "product_id": str(order.master_product_id) if order.master_product_id else None,
            "quantity": int(order.quantity or 1),
            "payment_method": getattr(pay_method, "value", pay_method),
            "customer_username": cust.telegram_username if cust else None,
            "customer_first_name": cust.first_name if cust else None,
            "delivered_product": None,
            "receipt_file_id": None,
        }
        text = format_trust_channel_card(payload, stage=stage)
        secrets = (order.delivered_product or "").strip()
        if secrets and secrets in text:
            text = text.replace(secrets, "***")

        order.proof_posted_at = datetime.now(timezone.utc)
        await sess.commit()

        await event_bus.publish("channel:broadcast_proof", {**payload, "formatted_html": text})
        logger.info(f"Proof channel publish queued for {order.order_number} stage={stage}")
        return {
            "status": "ok",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "stage": stage,
            "text": text,
        }

    if session is not None:
        return await _run(session)
    async with AsyncSessionLocal() as sess:
        return await _run(sess)


async def get_proof_banner_config(session) -> Dict[str, Any]:
    """Optional thank-you / verified-delivery banner from BotConfig."""
    from sqlalchemy import select
    from src.core.models.entities import BotConfig

    keys = (
        "proof_thank_you_image_enabled",
        "proof_thank_you_image_url",
        "proof_thank_you_image_file_id",
    )
    out = {"enabled": False, "image_url": None, "file_id": None}
    rows = (await session.execute(
        select(BotConfig).where(BotConfig.key.in_(keys))
    )).scalars().all()
    by_key = {r.key: r.value for r in rows}
    en = by_key.get("proof_thank_you_image_enabled")
    if isinstance(en, str):
        out["enabled"] = en.lower() in ("true", "1", "yes")
    elif isinstance(en, bool):
        out["enabled"] = en
    out["image_url"] = by_key.get("proof_thank_you_image_url") or None
    out["file_id"] = by_key.get("proof_thank_you_image_file_id") or None
    return out


class ProofService:
    format_purchase = staticmethod(format_purchase_proof_card)
    format_deposit = staticmethod(format_deposit_proof_card)
    format_restock = staticmethod(format_restock_card)
    format_trust = staticmethod(format_trust_channel_card)
    publish_purchase = staticmethod(publish_purchase_proof)
    publish_deposit = staticmethod(publish_deposit_proof)
    publish_to_proof_channel = staticmethod(publish_to_proof_channel)
    get_banner_config = staticmethod(get_proof_banner_config)


proof_service = ProofService()
