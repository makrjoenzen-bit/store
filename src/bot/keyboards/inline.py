from typing import List, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from src.config import settings

try:
    from aiogram.types import CopyTextButton
    _HAS_COPY_TEXT = True
except Exception:  # pragma: no cover
    CopyTextButton = None  # type: ignore
    _HAS_COPY_TEXT = False


def copy_btn(label: str, text: str, fallback_cb: str = "noop") -> InlineKeyboardButton:
    """Native 1-tap CopyTextButton when supported; otherwise callback noop."""
    if _HAS_COPY_TEXT and text:
        return InlineKeyboardButton(text=label, copy_text=CopyTextButton(text=str(text)))
    return InlineKeyboardButton(text=label, callback_data=fallback_cb)


def crypto_payment_keyboard(
    *,
    order_id: str,
    address: str,
    amount: str,
    network: str = "bep20",
) -> InlineKeyboardMarkup:
    help_cb = f"payhelp:txid:{order_id}:{network}"
    back_cb = f"pay_options:{order_id}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [copy_btn("📋 نسخ عنوان المحفظة", address)],
        [copy_btn("📋 نسخ المبلغ الدقيق", str(amount))],
        [InlineKeyboardButton(text="❓ أين أجد TxID؟", callback_data=help_cb)],
        [InlineKeyboardButton(text="↩️ رجوع لوسائل الدفع", callback_data=back_cb)],
    ])


def binance_payment_keyboard(*, order_id: str, pay_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [copy_btn(f"📋 نسخ Pay ID: {pay_id}", pay_id)],
        [InlineKeyboardButton(text="📝 لصق Order ID هنا", callback_data=f"binance_oid:{order_id}")],
        [InlineKeyboardButton(text="❓ أين أجد Order ID؟", callback_data=f"payhelp:binance:{order_id}")],
        [InlineKeyboardButton(text="↩️ رجوع لوسائل الدفع", callback_data=f"pay_options:{order_id}")],
    ])


def sham_cash_payment_keyboard(*, order_id: str, account_code: str = "") -> InlineKeyboardMarkup:
    rows = []
    if account_code:
        rows.append([copy_btn("📋 نسخ رقم/كود شام كاش", account_code)])
    rows.append([InlineKeyboardButton(text="❌ إلغاء", callback_data=f"cancel_order:{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_fsm_keyboard(callback_data: str = "nav:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ إلغاء", callback_data=callback_data)],
    ])


def product_detail_keyboard(
    product_id: str,
    is_in_stock: bool,
    buy_button_text: str = "⚡ شراء الآن (دفع فوري)",
    *,
    show_notify: bool = False,
) -> InlineKeyboardMarkup:
    buttons = []
    if is_in_stock:
        buttons.append([
            InlineKeyboardButton(text=buy_button_text, callback_data=f"select_qty:{product_id}")
        ])
    elif show_notify:
        buttons.append([
            InlineKeyboardButton(
                text="🔔 نبّهني عند التوفر",
                callback_data=f"stock_alert:{product_id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="📜 الشروط والضمان", callback_data="nav:faq"),
    ])
    buttons.append([InlineKeyboardButton(text="🔙 العودة للقائمة", callback_data="nav:categories")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_menu_keyboard(
    main_channel_url: Optional[str] = None,
    proof_channel_url: Optional[str] = None,
    language: str = "ar"
) -> InlineKeyboardMarkup:
    """Rich Enterprise Welcome Hub Keyboard."""
    en = language == "en"
    labels = {
        "miniapp": "🚀 Open Mini App 🛍️" if en else "🚀 فتح المتجر التفاعلي (Mini App) 🛍️",
        "catalog": "🛍️ Browse & Buy Now" if en else "🛍️ تصفح المتجر والشراء الفوري",
        "search": "🔍 Quick Search" if en else "🔍 بحث سريع عن منتج أو مفتاح",
        "wallet": "👛 My Wallet & Top-Up" if en else "👛 محفظتي وشحن الرصيد",
        "orders": "📋 My Orders" if en else "📋 سجل مشترياتي",
        "profile": "👤 My Account & Referrals" if en else "👤 حسابي والأصدقاء (الإحالات)",
        "support": "🛟 Support & Disputes" if en else "🛟 الدعم الفني والنزاعات",
        "giveaway": "🎁 Free Account Giveaway" if en else "🎁 اربح حساب مجاني",
        "faq": "📜 Terms & Warranty (FAQ)" if en else "📜 الشروط والضمان (FAQ)",
        "lang": "🌐 Language: EN" if en else "🌐 اللغة: العربية",
    }
    buttons = [
        [InlineKeyboardButton(text=labels["miniapp"], web_app=WebAppInfo(url=settings.MINIAPP_URL))],
        [InlineKeyboardButton(text=labels["catalog"], callback_data="nav:categories")],
        [InlineKeyboardButton(text=labels["giveaway"], callback_data="user:giveaway")],
        [InlineKeyboardButton(text=labels["search"], callback_data="nav:search")],
        [
            InlineKeyboardButton(text=labels["wallet"], callback_data="user:wallet"),
            InlineKeyboardButton(text=labels["orders"], callback_data="user:orders"),
        ],
        [
            InlineKeyboardButton(text=labels["profile"], callback_data="user:profile"),
            InlineKeyboardButton(text=labels["support"], callback_data="user:support"),
        ],
        [
            InlineKeyboardButton(text=labels["faq"], callback_data="nav:faq"),
            InlineKeyboardButton(text=labels["lang"], callback_data="nav:toggle_lang"),
        ],
    ]

    # Row 6: Channel URLs (Dual-Channel Architecture)
    ch_row = []
    if main_channel_url:
        ch_row.append(InlineKeyboardButton(text="📢 القناة الرسمية", url=main_channel_url))
    if proof_channel_url:
        ch_row.append(InlineKeyboardButton(text="⚡ قناة الإثباتات الحية", url=proof_channel_url))
    if ch_row:
        buttons.append(ch_row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def categories_keyboard(categories: List[dict]) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for cat in categories:
        cid = str(cat["id"])
        if cid.startswith("fam:"):
            cb = f"cat:fam:{cid.split(':', 1)[1]}:1"
        else:
            cb = f"cat:{cid}:1"
        row.append(
            InlineKeyboardButton(
                text=f"{cat['icon_emoji']} {cat['name_ar']}",
                callback_data=cb,
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="🔙 العودة للقائمة الرئيسية", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def paginated_products_keyboard(
    products: List[dict],
    category_id: str,
    page: int = 1,
    total_pages: int = 1
) -> InlineKeyboardMarkup:
    """Clean edit-in-place paginated products listing with live numeric stock."""
    buttons = []
    for p in products:
        emoji = p.get("custom_emoji") or "✨"
        price_str = f"${p.get('cached_final_price', '0.0')}"
        in_stock = p.get("cached_in_stock", True)
        stock_qty = p.get("available_quantity")
        if in_stock:
            stock_s = "∞" if stock_qty is None else str(stock_qty)
            label = f"{emoji} {p['title_ar']} | {price_str} | 📦 {stock_s}"
        else:
            label = f"{p['title_ar']} | {price_str} | ❌ نفد"
        # Telegram button text hard limit ~64 chars
        if len(label) > 64:
            label = label[:61] + "…"
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"prod:{p['id']}"
            )
        ])

    # Pagination navigation row
    if total_pages > 1:
        nav_row = []
        # Support brand family ids like "fam:ChatGPT"
        if str(category_id).startswith("fam:"):
            fam = str(category_id).split(":", 1)[1]
            prev_cb = f"cat:fam:{fam}:{page - 1}"
            next_cb = f"cat:fam:{fam}:{page + 1}"
        else:
            prev_cb = f"cat:{category_id}:{page - 1}"
            next_cb = f"cat:{category_id}:{page + 1}"
        if page > 1:
            nav_row.append(InlineKeyboardButton(text="⬅️ السابق", callback_data=prev_cb))
        nav_row.append(InlineKeyboardButton(text=f"📄 {page} / {total_pages}", callback_data="noop"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton(text="التالي ➡️", callback_data=next_cb))
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="🔙 رجوع للأقسام", callback_data="nav:categories")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def products_keyboard(products: List[dict], category_id: str) -> InlineKeyboardMarkup:
    return paginated_products_keyboard(products, category_id, page=1, total_pages=1)


def quantity_selector_keyboard(product_id: str, max_qty: int = 99) -> InlineKeyboardMarkup:
    """Fast inline quantity selector: 1, 2, 3, 5, 10, or custom."""
    row1 = []
    row2 = []
    for q in [1, 2, 3]:
        if q <= max_qty:
            row1.append(InlineKeyboardButton(text=f"{q}", callback_data=f"buy_qty:{product_id}:{q}"))
    for q in [5, 10]:
        if q <= max_qty:
            row2.append(InlineKeyboardButton(text=f"{q}", callback_data=f"buy_qty:{product_id}:{q}"))

    buttons = []
    if row1:
        buttons.append(row1)
    if row2:
        buttons.append(row2)
    buttons.append([InlineKeyboardButton(text="✏️ كمية مخصصة", callback_data=f"custom_qty:{product_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 إلغاء", callback_data=f"prod:{product_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def payment_methods_keyboard(
    order_id: str,
    total_price: float = 0.0,
    has_wallet_balance: bool = False,
    wallet_balance: float = 0.0,
    has_coupon_applied: bool = False
) -> InlineKeyboardMarkup:
    buttons = []

    # Promo Coupon button if not applied yet
    if not has_coupon_applied:
        buttons.append([
            InlineKeyboardButton(text="🎟️ لدي كود خصم (كوبون)", callback_data=f"apply_coupon:{order_id}")
        ])

    # If full balance payment is available
    if has_wallet_balance and wallet_balance >= total_price and total_price > 0:
        buttons.append([
            InlineKeyboardButton(
                text=f"💰 الدفع بالكامل من رصيدك (${wallet_balance:.2f})",
                callback_data=f"pay:balance:{order_id}"
            )
        ])
    # If hybrid / split payment is available (0 < wallet_balance < total_price)
    elif 0 < wallet_balance < total_price:
        remainder = total_price - wallet_balance
        buttons.append([
            InlineKeyboardButton(
                text=f"💰 خصم ${wallet_balance:.2f} من رصيدك ودفع المتبقي ${remainder:.2f}",
                callback_data=f"pay:split_prompt:{order_id}"
            )
        ])

    buttons.extend([
        [InlineKeyboardButton(text="💎 USDT (BEP-20 / شبكة BSC)", callback_data=f"pay:usdt_bep20:{order_id}")],
        [InlineKeyboardButton(text="⚡ USDT (TRC-20 / شبكة TRON)", callback_data=f"pay:usdt_trc20:{order_id}")],
        [InlineKeyboardButton(text="🟡 Binance Pay", callback_data=f"pay:binance_pay:{order_id}")],
        [InlineKeyboardButton(text="🏦 تحويل محلي (شام كاش - Sham Cash)", callback_data=f"pay:sham_cash:{order_id}")],
        [InlineKeyboardButton(text="❌ إلغاء الطلب", callback_data=f"cancel_order:{order_id}")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def split_payment_methods_keyboard(order_id: str, remainder_amount: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💎 دفع المتبقي (${remainder_amount:.2f}) عبر USDT BEP-20",
                    callback_data=f"pay:split_usdt:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⚡ دفع المتبقي (${remainder_amount:.2f}) عبر USDT TRC-20",
                    callback_data=f"pay:split_trc20:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🟡 دفع المتبقي (${remainder_amount:.2f}) عبر Binance Pay",
                    callback_data=f"pay:split_binance:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🏦 دفع المتبقي (${remainder_amount:.2f}) عبر شام كاش",
                    callback_data=f"pay:split_sham_cash:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 العودة لوسائل الدفع الكاملة",
                    callback_data=f"pay_options:{order_id}"
                )
            ],
        ]
    )


def topup_presets_keyboard() -> InlineKeyboardMarkup:
    """Quick presets for wallet top-up."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💵 $10", callback_data="topup_amt:10"),
                InlineKeyboardButton(text="💵 $25", callback_data="topup_amt:25"),
            ],
            [
                InlineKeyboardButton(text="💵 $50 (+بونص)", callback_data="topup_amt:50"),
                InlineKeyboardButton(text="💵 $100 (+بونص)", callback_data="topup_amt:100"),
            ],
            [
                InlineKeyboardButton(text="🔢 إدخال مبلغ مخصص", callback_data="topup_custom"),
            ],
            [
                InlineKeyboardButton(text="🔙 العودة للرئيسية", callback_data="nav:home"),
            ]
        ]
    )


def topup_methods_keyboard(amount: float) -> InlineKeyboardMarkup:
    """Multi-method selector for wallet top-up."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💎 USDT (BEP-20 / شبكة BSC) — ${amount}", callback_data=f"topup_pay:bep20:{amount}")],
            [InlineKeyboardButton(text=f"⚡ USDT (TRC-20 / شبكة TRON) — ${amount}", callback_data=f"topup_pay:trc20:{amount}")],
            [InlineKeyboardButton(text=f"🟡 Binance Pay — ${amount}", callback_data=f"topup_pay:binance:{amount}")],
            [InlineKeyboardButton(text=f"🏦 تحويل محلي شام كاش — ${amount}", callback_data=f"topup_pay:sham_cash:{amount}")],
            [InlineKeyboardButton(text="🔙 العودة لاختيار المبلغ", callback_data="user:wallet")],
        ]
    )


def order_rating_keyboard(order_id: str) -> InlineKeyboardMarkup:
    """1-Click 5-Star verified review rating keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ 1", callback_data=f"rate:{order_id}:1"),
                InlineKeyboardButton(text="⭐⭐ 2", callback_data=f"rate:{order_id}:2"),
                InlineKeyboardButton(text="⭐⭐⭐ 3", callback_data=f"rate:{order_id}:3"),
                InlineKeyboardButton(text="⭐⭐⭐⭐ 4", callback_data=f"rate:{order_id}:4"),
                InlineKeyboardButton(text="⭐⭐⭐⭐⭐ 5", callback_data=f"rate:{order_id}:5"),
            ],
            [
                InlineKeyboardButton(text="⚠️ مشكلة في الطلب / فتح تذكرة", callback_data=f"open_dispute:{order_id}"),
            ]
        ]
    )


def admin_sham_cash_review_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأكيد الدفع وتسليم المنتج",
                    callback_data=f"adm_appr:{order_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ رفض الطلب",
                    callback_data=f"adm_rej:{order_id}",
                ),
            ],
        ]
    )
