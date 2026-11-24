"""Arabic alert card formatters for Admin Bot live feed (HTML — no business logic)."""
from __future__ import annotations

from html import escape
from typing import Any, Dict, Optional


def _e(val: Any) -> str:
    if val is None:
        return "—"
    return escape(str(val), quote=False)


def format_treasury_card(summary: Dict[str, Any]) -> str:
    today = summary.get("profit_today") or {}
    d7 = summary.get("profit_7d") or {}
    d30 = summary.get("profit_30d") or {}
    return (
        "📊 <b>الخزينة والأرباح — نظرة فورية</b>\n\n"
        f"💎 <b>الأصول (Assets):</b> <code>${summary.get('total_assets', 0):.2f}</code>\n"
        f"   ├ المحفظة الساخنة: <code>${summary.get('hot_wallet_usdt', 0):.2f}</code>\n"
        f"   ├ أرصدة موردين معلّقة: <code>${summary.get('supplier_credits', 0):.2f}</code>\n"
        f"   └ شام كاش مؤكد: <code>${summary.get('sham_cash_collected', 0):.2f}</code>\n\n"
        f"👥 <b>التزامات العملاء:</b> <code>${summary.get('total_liabilities', 0):.2f}</code>\n"
        f"💵 <b>صافي قابل للسحب:</b> <code>${summary.get('net_withdrawable', 0):.2f}</code>\n\n"
        f"📈 <b>أرباح اليوم:</b> <code>${today.get('net_profit', 0):.2f}</code> "
        f"({today.get('order_count', 0)} طلب)\n"
        f"📆 <b>7 أيام:</b> <code>${d7.get('net_profit', 0):.2f}</code>\n"
        f"🗓 <b>30 يوماً:</b> <code>${d30.get('net_profit', 0):.2f}</code>\n"
        f"💰 <b>إجمالي الإيرادات:</b> <code>${summary.get('total_revenue', 0):.2f}</code>\n"
        f"↩️ <b>إجمالي الاستردادات:</b> <code>${summary.get('total_refunds', 0):.2f}</code>"
    )


def format_pending_sham_list(items: list) -> str:
    if not items:
        return "✅ لا توجد إيصالات شام كاش بانتظار المراجعة حالياً."
    lines = ["⏳ <b>إيصالات شام كاش المعلقة:</b>\n"]
    for i, inv in enumerate(items[:20], 1):
        fraud = inv.get("fraud_warning")
        fraud_line = f"\n   {_e(fraud)}" if fraud else ""
        lines.append(
            f"{i}. <code>{_e(inv.get('order_number'))}</code> — "
            f"<code>${inv.get('amount_expected', 0):.2f}</code>\n"
            f"   👤 {_e(inv.get('customer_name') or inv.get('customer_telegram_id'))} · "
            f"{_e(inv.get('product_title') or 'منتج')}\n"
            f"   🏭 المورد: {_e(inv.get('supplier_name') or '—')} · "
            f"📎 {_e(inv.get('receipt_type') or 'photo')}"
            f"{fraud_line}"
        )
    return "\n".join(lines)


def format_recent_orders(items: list) -> str:
    if not items:
        return "لا توجد طلبات بعد."
    lines = ["📋 <b>آخر الطلبات:</b>\n"]
    for o in items[:15]:
        lines.append(
            f"• <code>{_e(o.get('order_number'))}</code> — <code>{_e(o.get('state'))}</code> — "
            f"${o.get('our_price', 0):.2f}\n"
            f"  {_e(o.get('product_title') or '—')} · مورد: {_e(o.get('supplier_name') or '—')}"
        )
    return "\n".join(lines)


def format_order_state_alert(payload: Dict[str, Any]) -> Optional[str]:
    """Build Arabic card from orders:live Redis event."""
    new_state = payload.get("new_state") or ""
    details = payload.get("details") or {}
    order_number = details.get("order_number") or payload.get("order_id", "")[:8]
    trigger = details.get("trigger") or ""

    state_ar = {
        "awaiting_payment": "بانتظار الدفع",
        "sham_cash_review": "⏳ إيصال شام كاش بانتظار المراجعة",
        "payment_confirmed": "⚡ تم تأكيد الدفع — بدء التوريد",
        "completed": "✅ اكتمل التسليم",
        "failed_refunded": "❌ فشل مع استرداد",
        "failed_manual_review": "🚨 فشل — مراجعة يدوية عاجلة",
        "rejected": "❌ مرفوض",
        "cancelled": "تم الإلغاء",
    }.get(new_state, new_state)

    text = (
        f"🔔 <b>تحديث طلب</b>\n\n"
        f"🔖 الطلب: <code>{_e(order_number)}</code>\n"
        f"📌 الحالة: <b>{_e(state_ar)}</b>\n"
        f"⚙️ المصدر: <code>{_e(trigger)}</code>"
    )
    return text


def format_admin_alert(payload: Dict[str, Any]) -> str:
    meta = payload.get("meta") or {}
    if meta.get("alert_type") == "fulfillment_report" or payload.get("type") == "fulfillment_report":
        return payload.get("message") or format_fulfillment_report(meta)
    level = (payload.get("level") or "warning").upper()
    icon = {"DANGER": "🚨", "WARNING": "⚠️", "INFO": "ℹ️"}.get(level, "📣")
    title = payload.get("title") or "تنبيه إداري"
    message = payload.get("message") or ""
    return f"{icon} <b>{_e(title)}</b>\n\n{_e(message)}"


def format_fulfillment_report(data: Dict[str, Any]) -> str:
    from src.core.services.fulfillment_report import fulfillment_report_service
    return fulfillment_report_service.format_card(data)


def format_ticket_alert(payload: Dict[str, Any]) -> str:
    return (
        "🎫 <b>تذكرة دعم جديدة</b>\n\n"
        f"الموضوع: {_e(payload.get('subject', '—'))}\n"
        f"العميل: {_e(payload.get('customer_name') or payload.get('telegram_id', '—'))}\n"
        f"المعرف: <code>{_e(payload.get('ticket_id', ''))}</code>"
    )


def format_low_balance_alert(payload: Dict[str, Any]) -> str:
    kind = payload.get("kind", "wallet")
    if kind == "supplier":
        return (
            "⚠️ <b>رصيد مورد منخفض</b>\n\n"
            f"المورد: {_e(payload.get('supplier_name', '—'))}\n"
            f"الرصيد: <code>${float(payload.get('balance', 0)):.2f}</code>"
        )
    return (
        "⚠️ <b>المحفظة الساخنة منخفضة</b>\n\n"
        f"USDT: <code>${float(payload.get('usdt_balance', 0)):.2f}</code>\n"
        f"BNB: <code>{float(payload.get('bnb_balance', 0)):.4f}</code>"
    )
