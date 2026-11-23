"""Arabic alert card formatters for Admin Bot live feed (presentation only — no business logic)."""
from __future__ import annotations

from typing import Any, Dict, Optional


def format_treasury_card(summary: Dict[str, Any]) -> str:
    today = summary.get("profit_today") or {}
    d7 = summary.get("profit_7d") or {}
    d30 = summary.get("profit_30d") or {}
    return (
        "📊 **الخزينة والأرباح — نظرة فورية**\n\n"
        f"💎 **الأصول (Assets):** `${summary.get('total_assets', 0):.2f}`\n"
        f"   ├ المحفظة الساخنة: `${summary.get('hot_wallet_usdt', 0):.2f}`\n"
        f"   ├ أرصدة موردين معلّقة: `${summary.get('supplier_credits', 0):.2f}`\n"
        f"   └ شام كاش مؤكد: `${summary.get('sham_cash_collected', 0):.2f}`\n\n"
        f"👥 **التزامات العملاء:** `${summary.get('total_liabilities', 0):.2f}`\n"
        f"💵 **صافي قابل للسحب:** `${summary.get('net_withdrawable', 0):.2f}`\n\n"
        f"📈 **أرباح اليوم:** `${today.get('net_profit', 0):.2f}` "
        f"({today.get('order_count', 0)} طلب)\n"
        f"📆 **7 أيام:** `${d7.get('net_profit', 0):.2f}`\n"
        f"🗓 **30 يوماً:** `${d30.get('net_profit', 0):.2f}`\n"
        f"💰 **إجمالي الإيرادات:** `${summary.get('total_revenue', 0):.2f}`\n"
        f"↩️ **إجمالي الاستردادات:** `${summary.get('total_refunds', 0):.2f}`"
    )


def format_pending_sham_list(items: list) -> str:
    if not items:
        return "✅ لا توجد إيصالات شام كاش بانتظار المراجعة حالياً."
    lines = ["⏳ **إيصالات شام كاش المعلقة:**\n"]
    for i, inv in enumerate(items[:20], 1):
        fraud = inv.get("fraud_warning")
        fraud_line = f"\n   {fraud}" if fraud else ""
        lines.append(
            f"{i}. `{inv.get('order_number')}` — `${inv.get('amount_expected', 0):.2f}`\n"
            f"   👤 {inv.get('customer_name') or inv.get('customer_telegram_id')} · "
            f"{inv.get('product_title') or 'منتج'}\n"
            f"   🏭 المورد: {inv.get('supplier_name') or '—'} · "
            f"📎 {inv.get('receipt_type') or 'photo'}"
            f"{fraud_line}"
        )
    return "\n".join(lines)


def format_recent_orders(items: list) -> str:
    if not items:
        return "لا توجد طلبات بعد."
    lines = ["📋 **آخر الطلبات:**\n"]
    for o in items[:15]:
        lines.append(
            f"• `{o.get('order_number')}` — `{o.get('state')}` — "
            f"${o.get('our_price', 0):.2f}\n"
            f"  {o.get('product_title') or '—'} · مورد: {o.get('supplier_name') or '—'}"
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
        f"🔔 **تحديث طلب**\n\n"
        f"🔖 الطلب: `{order_number}`\n"
        f"📌 الحالة: **{state_ar}**\n"
        f"⚙️ المصدر: `{trigger}`"
    )
    return text


def format_admin_alert(payload: Dict[str, Any]) -> str:
    meta = payload.get("meta") or {}
    if meta.get("alert_type") == "fulfillment_report" or payload.get("type") == "fulfillment_report":
        # message already contains the Arabic card from fulfillment_report_service
        return payload.get("message") or format_fulfillment_report(meta)
    level = (payload.get("level") or "warning").upper()
    icon = {"DANGER": "🚨", "WARNING": "⚠️", "INFO": "ℹ️"}.get(level, "📣")
    title = payload.get("title") or "تنبيه إداري"
    message = payload.get("message") or ""
    return f"{icon} **{title}**\n\n{message}"


def format_fulfillment_report(data: Dict[str, Any]) -> str:
    from src.core.services.fulfillment_report import fulfillment_report_service
    return fulfillment_report_service.format_card(data)


def format_ticket_alert(payload: Dict[str, Any]) -> str:
    return (
        "🎫 **تذكرة دعم جديدة**\n\n"
        f"الموضوع: {payload.get('subject', '—')}\n"
        f"العميل: {payload.get('customer_name') or payload.get('telegram_id', '—')}\n"
        f"المعرف: `{payload.get('ticket_id', '')}`"
    )


def format_low_balance_alert(payload: Dict[str, Any]) -> str:
    kind = payload.get("kind", "wallet")
    if kind == "supplier":
        return (
            "⚠️ **رصيد مورد منخفض**\n\n"
            f"المورد: {payload.get('supplier_name', '—')}\n"
            f"الرصيد: `${float(payload.get('balance', 0)):.2f}`"
        )
    return (
        "⚠️ **المحفظة الساخنة منخفضة**\n\n"
        f"USDT: `${float(payload.get('usdt_balance', 0)):.2f}`\n"
        f"BNB: `{float(payload.get('bnb_balance', 0)):.4f}`"
    )
