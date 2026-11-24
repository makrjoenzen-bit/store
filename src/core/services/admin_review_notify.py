"""
Push Sham Cash / Binance Pay manual-review cards to ALL ADMIN_TELEGRAM_IDS via Admin Bot.
Never exposes supplier names on customer channels.
Captions use HTML + html.escape so Telegram never drops alerts on special chars.
"""
from __future__ import annotations

from html import escape
from typing import Any, Dict, Optional

from loguru import logger

from src.core.redis import event_bus


def _e(val: Any) -> str:
    if val is None:
        return "—"
    return escape(str(val), quote=False)


def format_manual_review_caption(data: Dict[str, Any]) -> str:
    """Admin/staff-only HTML caption including winning supplier."""
    kind = data.get("review_kind") or "sham_cash"
    header = (
        "🟡 <b>مراجعة Binance Pay</b>"
        if kind == "binance_pay"
        else "🏦 <b>مراجعة شام كاش</b>"
    )
    supplier = data.get("supplier_name") or "—"
    username = data.get("customer_username")
    if username:
        user_line = f"@{_e(username)}"
    else:
        user_line = _e(data.get("customer_name") or "—")
    qty = data.get("quantity") or 1
    lines = [
        header,
        "",
        f"🔖 الطلب: <code>{_e(data.get('order_number'))}</code>",
        f"👤 العميل: {user_line} · TG <code>{_e(data.get('customer_telegram_id'))}</code>",
        f"📦 المنتج: {_e(data.get('product_title') or '—')} × <code>{_e(qty)}</code>",
        f"💰 المبلغ: <code>${float(data.get('amount') or 0):.2f}</code>",
        f"🏭 المورد / التاجر: <b>{_e(supplier)}</b>",
    ]
    if data.get("supplier_cost") is not None:
        try:
            lines.append(f"💵 تكلفة المورد: <code>${float(data['supplier_cost']):.2f}</code>")
        except (TypeError, ValueError):
            pass
    if data.get("tx_ref"):
        lines.append(f"🔢 Tx Ref: <code>{_e(data['tx_ref'])}</code>")
    if data.get("binance_pay_order_id"):
        lines.append(f"🟡 Pay Order ID: <code>{_e(data['binance_pay_order_id'])}</code>")
    if data.get("receipt_type"):
        lines.append(f"📎 المرفق: <code>{_e(data['receipt_type'])}</code>")
    if data.get("fraud_warning"):
        lines.append("")
        lines.append(_e(data["fraud_warning"]))
    forensics = data.get("forensics_summary") or data.get("pdf_forensics_summary")
    if forensics:
        lines.append("")
        # Forensics already formatted by our service — escape to be safe
        lines.append(_e(forensics))
    lines.append("")
    lines.append("اضغط للتأكيد والتسليم أو الرفض:")
    lines.append("<i>التشريح الجنائي مساعد فقط — لا يُعتمد الدفع تلقائياً.</i>")
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
