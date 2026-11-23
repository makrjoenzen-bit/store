"""
Push Sham Cash / Binance Pay manual-review cards to ALL ADMIN_TELEGRAM_IDS via Admin Bot.
Never exposes supplier names on customer channels.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from src.core.redis import event_bus


def format_manual_review_caption(data: Dict[str, Any]) -> str:
    """Admin/staff-only caption including winning supplier."""
    kind = data.get("review_kind") or "sham_cash"
    header = (
        "🟡 **مراجعة Binance Pay**"
        if kind == "binance_pay"
        else "🏦 **مراجعة شام كاش**"
    )
    supplier = data.get("supplier_name") or "—"
    username = data.get("customer_username")
    user_line = f"@{username}" if username else (data.get("customer_name") or "—")
    qty = data.get("quantity") or 1
    lines = [
        header,
        "",
        f"🔖 الطلب: `{data.get('order_number')}`",
        f"👤 العميل: {user_line} · TG `{data.get('customer_telegram_id')}`",
        f"📦 المنتج: {data.get('product_title') or '—'} × `{qty}`",
        f"💰 المبلغ: `${float(data.get('amount') or 0):.2f}`",
        f"🏭 المورد / التاجر: **{supplier}**",
    ]
    if data.get("tx_ref"):
        lines.append(f"🔢 Tx Ref: `{data['tx_ref']}`")
    if data.get("binance_pay_order_id"):
        lines.append(f"🟡 Pay Order ID: `{data['binance_pay_order_id']}`")
    if data.get("receipt_type"):
        lines.append(f"📎 المرفق: `{data['receipt_type']}`")
    if data.get("fraud_warning"):
        lines.append("")
        lines.append(str(data["fraud_warning"]))
    forensics = data.get("forensics_summary") or data.get("pdf_forensics_summary")
    if forensics:
        lines.append("")
        lines.append(str(forensics))
    lines.append("")
    lines.append("اضغط للتأكيد والتسليم أو الرفض:")
    lines.append("_التشريح الجنائي مساعد فقط — لا يُعتمد الدفع تلقائياً._")
    return "\n".join(lines)


async def publish_manual_review_alert(payload: Dict[str, Any]) -> None:
    """
    payload keys:
      order_id, order_number, review_kind (sham_cash|binance_pay),
      supplier_name, customer_*, product_title, quantity, amount,
      tx_ref?, binance_pay_order_id?,
      media_file_id?, media_type? (photo|document),
      source_bot_token_role? (customer)
    """
    caption = format_manual_review_caption(payload)
    try:
        await event_bus.publish("admin:manual_review", {
            **payload,
            "caption": caption,
        })
    except Exception as e:
        logger.error(f"Failed to publish admin:manual_review: {e}")
