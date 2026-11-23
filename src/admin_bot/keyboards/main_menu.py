from typing import Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton


def admin_main_menu_reply(*, staff_only: bool = False) -> ReplyKeyboardMarkup:
    """Persistent control hub — Staff sees review/warranty/support only."""
    if staff_only:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🧾 طلبات المراجعة (شام كاش / باينانس)")],
                [KeyboardButton(text="🛡️ طلبات الضمان والاستبدال")],
                [KeyboardButton(text="🛟 تذاكر الدعم الفني")],
            ],
            resize_keyboard=True,
            is_persistent=True,
        )
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 الخزينة والأرباح")],
            [KeyboardButton(text="🔌 الموردين والـ APIs")],
            [KeyboardButton(text="📦 المنتجات والأسعار")],
            [KeyboardButton(text="🧾 الطلبات وشام كاش")],
            [KeyboardButton(text="🛡️ طلبات الضمان والاستبدال")],
            [KeyboardButton(text="👥 العملاء والدعم")],
            [KeyboardButton(text="📢 التسويق والبث")],
            [KeyboardButton(text="⚙️ إعدادات شام كاش / سعر الصرف")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_main_menu_inline(*, staff_only: bool = False) -> InlineKeyboardMarkup:
    if staff_only:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🧾 طلبات المراجعة", callback_data="adm:orders")],
                [InlineKeyboardButton(text="🛡️ طلبات الضمان", callback_data="adm:warranty")],
                [InlineKeyboardButton(text="🛟 تذاكر الدعم", callback_data="adm:crm")],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 الخزينة والأرباح", callback_data="adm:treasury")],
            [InlineKeyboardButton(text="🔌 الموردين والـ APIs", callback_data="adm:suppliers")],
            [InlineKeyboardButton(text="📦 المنتجات والأسعار", callback_data="adm:products")],
            [InlineKeyboardButton(text="🧾 الطلبات وشام كاش", callback_data="adm:orders")],
            [InlineKeyboardButton(text="🛡️ طلبات الضمان", callback_data="adm:warranty")],
            [InlineKeyboardButton(text="👥 العملاء والدعم", callback_data="adm:crm")],
            [InlineKeyboardButton(text="📢 التسويق والبث", callback_data="adm:marketing")],
            [InlineKeyboardButton(text="⚙️ سعر شام كاش", callback_data="adm:settings:syp")],
        ]
    )


def sham_cash_claim_keyboard(order_id: str) -> InlineKeyboardMarkup:
    """Initial card — claim lock before actions."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔒 استلام الطلب", callback_data=f"adm:claim:{order_id}")],
        ]
    )


def sham_cash_review_keyboard(
    order_id: str,
    *,
    claimed: bool = False,
    claimed_by: Optional[str] = None,
    for_claimer: bool = False,
    is_owner: bool = False,
) -> InlineKeyboardMarkup:
    if not claimed:
        return sham_cash_claim_keyboard(order_id)
    rows = []
    if for_claimer or is_owner:
        rows.append([InlineKeyboardButton(
            text="⚡ قبول وشراء آلي بالـ API",
            callback_data=f"adm_sc_ok:{order_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="✍️ قبول وتسليم يدوي",
            callback_data=f"adm:manual:{order_id}",
        )])
        rows.append([InlineKeyboardButton(
            text="❌ رفض الطلب",
            callback_data=f"adm_sc_no:{order_id}",
        )])
    if is_owner:
        rows.append([InlineKeyboardButton(
            text="🔓 فك القفل",
            callback_data=f"adm:unclaim:{order_id}",
        )])
    if not rows:
        # Other staff see locked status only
        rows.append([InlineKeyboardButton(
            text=f"🔒 قيد المعالجة بواسطة: @{claimed_by or '—'}",
            callback_data="noop",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def warranty_claim_keyboard(claim_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="✍️ إرسال بديل للزبون",
                callback_data=f"adm:warranty:replace:{claim_id}",
            )],
            [InlineKeyboardButton(
                text="💸 استرداد للمحفظة",
                callback_data=f"adm:warranty:refund:{claim_id}",
            )],
            [InlineKeyboardButton(
                text="❌ رفض مع سبب",
                callback_data=f"adm:warranty:reject:{claim_id}",
            )],
        ]
    )


def orders_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 بحث برقم الطلب", callback_data="adm:orders:search")],
            [InlineKeyboardButton(text="⏳ إيصالات شام كاش المعلقة", callback_data="adm:orders:sham")],
            [InlineKeyboardButton(text="🟡 تأكيد Binance Pay (Pay Order ID)", callback_data="adm:orders:binance")],
            [InlineKeyboardButton(text="📋 آخر الطلبات", callback_data="adm:orders:recent")],
            [InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="adm:home")],
        ]
    )


def order_actions_keyboard(order_id: str, *, can_retry: bool, can_refund: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_retry:
        rows.append([InlineKeyboardButton(
            text="🔄 إعادة تنفيذ JIT", callback_data=f"adm:ord:retry:{order_id}"
        )])
    if can_refund:
        rows.append([InlineKeyboardButton(
            text="💸 استرداد للمحفظة", callback_data=f"adm:ord:refund:{order_id}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 الطلبات", callback_data="adm:orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def binance_pay_fallback_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🟡 إدخال Pay Order ID", callback_data="adm:orders:binance")],
            [InlineKeyboardButton(text="🔙 الطلبات", callback_data="adm:orders")],
        ]
    )


def coming_soon_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="adm:home")],
        ]
    )
