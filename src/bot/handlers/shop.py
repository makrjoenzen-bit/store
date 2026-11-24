import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Tuple, List, Any
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ContentType, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload
from loguru import logger

from src.core.database import AsyncSessionLocal
from src.core.models.entities import (
    Customer, Category, MasterProduct, Order, PaymentInvoice, BotConfig, Supplier, FormalInvoice,
    Coupon, CustomerReview, TopUpBonusTier
)
from src.core.models.enums import PaymentMethod, OrderState, InvoiceStatus, WalletTxType, DiscountType
from src.core.services.pricing_service import pricing_service
from src.core.services.order_service import order_service
from src.core.services.jit_queue import enqueue_jit_order
from src.core.adapters.registry import AdapterRegistry
from src.core.redis import event_bus
from src.bot.keyboards.inline import (
    main_menu_keyboard, categories_keyboard, products_keyboard,
    product_detail_keyboard, payment_methods_keyboard,
    quantity_selector_keyboard, split_payment_methods_keyboard,
    paginated_products_keyboard, topup_presets_keyboard, topup_methods_keyboard,
    order_rating_keyboard, cancel_fsm_keyboard,
    crypto_payment_keyboard, binance_payment_keyboard, sham_cash_payment_keyboard,
)
from src.core.services.admin_review_notify import publish_manual_review_alert
from src.core.services.stock_alert_service import stock_alert_service
from src.config import settings

router = Router()


def _as_uuid(value: Any):
    """Coerce path/callback string IDs to uuid.UUID for SQLAlchemy UUID(as_uuid=True)."""
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _is_telegram_admin(user_id: int) -> bool:
    """Any ID in ADMIN_TELEGRAM_IDS (incl. legacy ADMIN_TELEGRAM_ID) may review."""
    return int(user_id) in {int(x) for x in settings.admin_telegram_id_set}


class CustomQuantityState(StatesGroup):
    waiting_for_qty = State()


class SearchProductState(StatesGroup):
    waiting_for_query = State()


class TopUpCustomAmountState(StatesGroup):
    waiting_for_amount = State()


class ApplyCouponState(StatesGroup):
    waiting_for_code = State()


class CheckoutEmailState(StatesGroup):
    waiting_email = State()


class CheckoutCustomerInputState(StatesGroup):
    waiting_input = State()


class WarrantyClaimState(StatesGroup):
    waiting_description = State()
    waiting_screenshot = State()


class ShamCashUploadState(StatesGroup):
    waiting_for_tx_ref = State()  # optional legacy / manual ref
    waiting_for_receipt = State()  # photo or PDF after sham_paid


class BinancePayOrderState(StatesGroup):
    waiting_order_id = State()


async def get_or_create_customer(session, tg_user) -> Customer:
    customer, _ = await get_or_create_customer_ex(session, tg_user)
    return customer


async def get_or_create_customer_ex(session, tg_user) -> tuple:
    """Returns (customer, is_new). Existing users never count for referral milestones."""
    stmt = select(Customer).where(Customer.telegram_id == tg_user.id)
    res = await session.execute(stmt)
    customer = res.scalar_one_or_none()
    if not customer:
        customer = Customer(
            telegram_id=tg_user.id,
            telegram_username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            language_code=tg_user.language_code or "ar",
        )
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
        return customer, True
    return customer, False


async def get_dynamic_config(session, key: str, default_val: str) -> str:
    stmt = select(BotConfig).where(BotConfig.key == key)
    res = await session.execute(stmt)
    cfg = res.scalar_one_or_none()
    if cfg and cfg.value is not None and cfg.value != "":
        if isinstance(cfg.value, str):
            return cfg.value
        if isinstance(cfg.value, bool):
            return "true" if cfg.value else "false"
        return str(cfg.value)
    return default_val


def _cfg_truthy(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val or "").lower() in ("true", "1", "yes", "on")


async def payment_rail_flags(session) -> dict:
    return {
        "enable_usdt": _cfg_truthy(await get_dynamic_config(session, "pay_usdt_enabled", "true")),
        "enable_binance": _cfg_truthy(await get_dynamic_config(session, "pay_binance_enabled", "true")),
        "enable_sham_cash": _cfg_truthy(await get_dynamic_config(session, "pay_sham_cash_enabled", "true")),
    }


# ---------------- Rich User Welcome Hub ----------------
async def render_welcome_hub(customer: Customer, session) -> Tuple[str, InlineKeyboardMarkup]:
    from html import escape as html_escape
    from src.bot.i18n import t, normalize_lang

    lang = normalize_lang(customer.language_code)
    welcome_key = "welcome_message_en" if lang == "en" else "welcome_message_ar"
    welcome_default = t(lang, "welcome_fallback")
    welcome_text = await get_dynamic_config(session, welcome_key, welcome_default)
    if isinstance(welcome_text, str) is False:
        welcome_text = welcome_default
    main_url = await get_dynamic_config(session, "main_channel_url", settings.TELEGRAM_MAIN_CHANNEL_URL)
    proof_url = await get_dynamic_config(session, "proof_channel_url", settings.TELEGRAM_PROOF_CHANNEL_URL)

    from sqlalchemy import func
    total_users = int((await session.execute(select(func.count(Customer.id)))).scalar_one() or 0)
    raw_name = customer.first_name or ("Customer" if lang == "en" else "عزيزي العميل")
    user_tag = f"@{customer.telegram_username}" if customer.telegram_username else raw_name
    text = (
        f"👋 <b>{html_escape(str(user_tag))}</b>\n"
        f"<blockquote>"
        f"🆔 ID: <code>{customer.telegram_id}</code>\n"
        f"💰 <b>${float(customer.wallet_balance or 0):.2f}</b>\n"
        f"📦 {int(customer.total_orders or 0)} · ${float(customer.total_spent or 0):.2f}\n"
        f"👥 {total_users}"
        f"</blockquote>\n"
        f"{html_escape(str(welcome_text))}\n\n"
        f"{t(lang, 'home')}"
    )
    kb = main_menu_keyboard(
        main_channel_url=main_url,
        proof_channel_url=proof_url,
        language=lang,
    )
    return text, kb


# ---------------- /start & Navigation ----------------
@router.message(CommandStart())
async def handle_start(message: Message, state: FSMContext):
    await state.clear()
    async with AsyncSessionLocal() as session:
        if _cfg_truthy(await get_dynamic_config(session, "maintenance_mode", "false")):
            await message.answer(
                "🛠️ المتجر في وضع الصيانة مؤقتاً.\nجرّب لاحقاً — شكراً لتفهمك."
            )
            return
        customer, is_new = await get_or_create_customer_ex(session, message.from_user)

        # Check for deep-link: /start prod_<id> or /start buy_<id> or /start ref_<id>
        args = message.text.split()[1:] if message.text else []
        if args:
            param = args[0]
            if param == "giveaway":
                await show_giveaway_screen(message, customer, session)
                return
            if param.startswith("prod_"):
                prod_id = param.replace("prod_", "")
                await show_direct_buy_quantity(message, prod_id, session)
                return
            elif param.startswith("buy_"):
                prod_id = param.replace("buy_", "")
                # Remember intent in case Force-Sub interrupts before this handler runs next time
                redis = event_bus._redis
                if redis:
                    await redis.set(f"pending_buy:{message.from_user.id}", prod_id, ex=3600)
                await show_direct_buy_quantity(message, prod_id, session)
                return
            elif param.startswith("ref_"):
                ref_id_str = param.replace("ref_", "")
                if ref_id_str.isdigit() and int(ref_id_str) != customer.telegram_id:
                    ref_stmt = select(Customer).where(Customer.telegram_id == int(ref_id_str))
                    referrer = (await session.execute(ref_stmt)).scalar_one_or_none()
                    if referrer and referrer.id != customer.id:
                        if not customer.referred_by:
                            customer.referred_by = referrer.id
                            referrer.referral_count = (referrer.referral_count or 0) + 1
                            await session.commit()
                            logger.info(
                                f"Customer {customer.telegram_id} linked to referrer {referrer.telegram_id}"
                            )
                        # Milestone campaign invite (anti-fraud: new users only)
                        try:
                            from src.core.services.referral_campaign_service import referral_campaign_service
                            await referral_campaign_service.record_pending_invite(
                                session,
                                referrer=referrer,
                                invitee=customer,
                                is_new_customer=is_new,
                            )
                        except Exception as e:
                            logger.warning(f"Referral campaign invite record failed: {e}")

        text, kb = await render_welcome_hub(customer, session)
        from src.bot.effects import EFFECT_FIRE, send_with_effect
        await send_with_effect(
            message,
            text=text,
            effect_id=EFFECT_FIRE if is_new else None,
            reply_markup=kb,
            parse_mode="HTML",
        )


@router.callback_query(F.data == "nav:home")
async def nav_home(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        text, kb = await render_welcome_hub(customer, session)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "nav:support_lang")
async def nav_support_lang(callback: CallbackQuery):
    await callback.answer()
    from src.bot.keyboards.inline import support_lang_keyboard
    from src.bot.i18n import t, normalize_lang
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        lang = normalize_lang(customer.language_code)
        main_url = await get_dynamic_config(session, "main_channel_url", settings.TELEGRAM_MAIN_CHANNEL_URL)
        text = f"<b>{t(lang, 'menu_support_lang')}</b>\n\n{t(lang, 'support_prompt')}"
        kb = support_lang_keyboard(language=lang, main_channel_url=main_url)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


# ---------------- Force-Subscribe Verification Callback ----------------
@router.callback_query(F.data == "verify_sub")
async def cb_verify_subscription(callback: CallbackQuery, state: FSMContext):
    async with AsyncSessionLocal() as session:
        main_channel_id = await get_dynamic_config(session, "main_channel_id", settings.TELEGRAM_PUBLIC_CHANNEL_ID)
        proof_channel_id = await get_dynamic_config(
            session, "proof_channel_id", getattr(settings, "TELEGRAM_PROOF_CHANNEL_ID", None) or ""
        )
        force_proof_raw = await get_dynamic_config(session, "force_sub_proof_channel_enabled", "")
        force_proof = (
            str(force_proof_raw).lower() in ("true", "1", "yes", "on")
            if force_proof_raw not in ("", None)
            else bool(proof_channel_id)
        )

        user_id = callback.from_user.id
        redis = event_bus._redis
        valid = {"creator", "administrator", "member", "restricted"}

        async def _check(ch_id) -> bool:
            if not ch_id:
                return True
            try:
                target = int(ch_id) if str(ch_id).lstrip("-").isdigit() else ch_id
                member = await callback.bot.get_chat_member(chat_id=target, user_id=user_id)
                ok = member.status in valid
                if ok and redis:
                    await redis.set(f"cache:forcesub:{user_id}:{ch_id}", "1", ex=120)
                return ok
            except Exception as e:
                logger.warning(f"verify_sub check failed for {ch_id}: {e}")
                return True  # fail-open

        main_ok = await _check(main_channel_id) if main_channel_id else True
        proof_ok = await _check(proof_channel_id) if (force_proof and proof_channel_id) else True
        if not (main_ok and proof_ok):
            missing = []
            if not main_ok:
                missing.append("القناة الرسمية")
            if not proof_ok:
                missing.append("قناة الإثباتات")
            await callback.answer(
                "⚠️ لم يتم العثور على اشتراكك في: " + " و ".join(missing) + "\nانضم ثم أعد المحاولة.",
                show_alert=True,
            )
            return

        await callback.answer("✅ تم التحقق من اشتراكك بنجاح! أهلاً بك في المتجر.", show_alert=True)
        customer = await get_or_create_customer(session, callback.from_user)
        try:
            from src.core.services.referral_campaign_service import referral_campaign_service
            await referral_campaign_service.verify_invitee(session, invitee_id=customer.id)
        except Exception as e:
            logger.warning(f"Referral verify_invitee failed: {e}")

        pending_id = None
        if redis:
            pending_id = await redis.get(f"pending_buy:{user_id}")
            if pending_id:
                await redis.delete(f"pending_buy:{user_id}")
                if isinstance(pending_id, bytes):
                    pending_id = pending_id.decode()
        if pending_id:
            await show_direct_buy_quantity(callback, str(pending_id), session)
            return
        text, kb = await render_welcome_hub(customer, session)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


# ---------------- User Hub: Wallet, Orders, Profile, Support ----------------
@router.callback_query(F.data == "user:wallet")
async def cb_user_wallet(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        
        # Check active top-up bonus tiers
        tiers_stmt = select(TopUpBonusTier).order_by(TopUpBonusTier.min_deposit_usd.asc())
        tiers = (await session.execute(tiers_stmt)).scalars().all()
        bonus_text = ""
        if tiers:
            bonus_text = "\n🎁 **مكافآت الإيداع الحصرية:**\n"
            for t in tiers:
                bonus_text += f"• اشحن `${t.min_deposit_usd:.0f}` وأكثر ⬅️ **احصل على +${t.bonus_usd:.2f} هدية فورية!**\n"

        text = (
            f"👛 **محفظتي ورصيد الحساب**\n\n"
            f"💰 **الرصيد المتاح حالياً:** `${customer.wallet_balance:.2f} USD`\n"
            f"📊 **إجمالي ما تم إنفاقه:** `${customer.total_spent:.2f} USD`\n"
            f"{bonus_text}\n"
            f"⚡ اشحن رصيدك مسبقاً للشراء الفوري السريع بنقرة واحدة دون انتظار تأكيدات الشبكة لكل طلب.\n\n"
            f"👇 اختر مبلغ الشحن السريع أدناه:"
        )
        kb = topup_presets_keyboard()
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "user:orders")
async def cb_user_orders(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        stmt = (
            select(Order)
            .where(Order.customer_id == customer.id)
            .options(selectinload(Order.master_product))
            .order_by(desc(Order.created_at))
            .limit(5)
        )
        orders = (await session.execute(stmt)).scalars().all()

        if not orders:
            text = (
                f"📋 **سجل مشترياتك:**\n\n"
                f"لا توجد لديك طلبات سابقة حتى الآن.\n"
                f"تصفح المتجر واختر منتجك المفضل للبدء! 🚀"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛍️ تصفح المتجر الآن", callback_data="nav:categories")],
                    [InlineKeyboardButton(text="🔙 العودة للقائمة الرئيسية", callback_data="nav:home")],
                ]
            )
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            return

        text = f"📋 **آخر مشترياتك ({len(orders)} طلبات):**\n\n"
        buttons = []
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        for o in orders:
            st_badge = "✅ مكتمل" if o.state == OrderState.COMPLETED else str(o.state.value)

            warranty_str = ""
            if o.warranty_expires_at:
                if now_utc > o.warranty_expires_at:
                    warranty_str = " • ⚠️ انتهى الضمان"
                else:
                    warranty_str = f" • 🛡️ ضمان حتى {o.warranty_expires_at.strftime('%Y-%m-%d')}"

            # Check formal invoice number
            fi_stmt = select(FormalInvoice.invoice_number).where(FormalInvoice.order_id == o.id)
            fi_res = (await session.execute(fi_stmt)).scalar_one_or_none()
            fi_line = f"🧾 **فاتورة رقم:** `{fi_res}`\n" if fi_res else ""

            text += (
                f"🔖 **طلب #{o.order_number}**\n"
                f"{fi_line}"
                f"📦 {o.master_product.title_ar} (الكمية: {o.quantity or 1})\n"
                f"💰 `${o.our_price}` • {st_badge}{warranty_str}\n"
            )
            if o.delivered_product:
                text += f"🔑 `{o.delivered_product[:25]}...`\n"
            text += "─────────────────\n"

            if o.state == OrderState.COMPLETED:
                row = [
                    InlineKeyboardButton(
                        text=f"⚠️ تذكرة #{o.order_number}",
                        callback_data=f"open_dispute:{o.id}"
                    )
                ]
                # Warranty claim if still within window
                if o.warranty_expires_at and now_utc <= o.warranty_expires_at:
                    row.append(InlineKeyboardButton(
                        text="🛡️ طلب استبدال (ضمن الضمان)",
                        callback_data=f"warranty:{o.id}",
                    ))
                buttons.append(row)

        buttons.append([InlineKeyboardButton(text="🔙 العودة للقائمة الرئيسية", callback_data="nav:home")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("warranty:"))
async def cb_warranty_start(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        from src.core.services.warranty_service import warranty_service
        customer = await get_or_create_customer(session, callback.from_user)
        stmt = select(Order).where(Order.id == _as_uuid(order_id))
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order or order.customer_id != customer.id:
            await callback.answer("طلب غير صالح", show_alert=True)
            return
        if not warranty_service.is_eligible(order):
            await callback.answer("انتهت فترة الضمان لهذا الطلب.", show_alert=True)
            return
        await state.set_state(WarrantyClaimState.waiting_description)
        await state.update_data(order_id=str(order.id))
        await callback.message.answer(
            "🛡️ **طلب استبدال ضمن الضمان**\n\nاشرح المشكلة باختصار:",
            parse_mode="HTML",
            reply_markup=cancel_fsm_keyboard(),
        )
        await callback.answer()


@router.message(WarrantyClaimState.waiting_description, F.text)
async def warranty_desc(message: Message, state: FSMContext):
    await state.update_data(issue=message.text.strip())
    await state.set_state(WarrantyClaimState.waiting_screenshot)
    await message.answer(
        "📸 أرسل لقطة شاشة اختيارية، أو أرسل `-` للمتابعة بدون صورة:",
        reply_markup=cancel_fsm_keyboard(),
    )


async def _warranty_finish(message: Message, state: FSMContext, screenshot_file_id=None):
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        from src.core.services.warranty_service import warranty_service
        customer = await get_or_create_customer(session, message.from_user)
        try:
            result = await warranty_service.open_claim(
                session,
                order_id=data["order_id"],
                customer_id=customer.id,
                issue_description=data.get("issue") or "",
                screenshot_file_id=screenshot_file_id,
            )
            await state.clear()
            await message.answer(
                f"✅ تم فتح طلب الضمان.\nمتبقي من الضمان: `{result['days_remaining']}` يوم.\n"
                f"سيراجع فريقنا طلبك قريباً.",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
        except ValueError as e:
            await state.clear()
            await message.answer(str(e))


@router.message(WarrantyClaimState.waiting_screenshot, F.photo)
async def warranty_submit_photo(message: Message, state: FSMContext):
    await _warranty_finish(message, state, message.photo[-1].file_id)


@router.message(WarrantyClaimState.waiting_screenshot, F.text)
async def warranty_submit_text(message: Message, state: FSMContext):
    await _warranty_finish(message, state, None)


@router.callback_query(F.data == "user:profile")
async def cb_user_profile(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        bot_info = await callback.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{customer.telegram_id}"

        ref_cfg = (await session.execute(select(BotConfig).where(BotConfig.key == "referral_commission_percent"))).scalar_one_or_none()
        ref_percent = float(ref_cfg.value) if ref_cfg and ref_cfg.value is not None else 5.0

        cb_cfg = (await session.execute(select(BotConfig).where(BotConfig.key == "cashback_percent"))).scalar_one_or_none()
        cb_percent = float(cb_cfg.value) if cb_cfg and cb_cfg.value is not None else 2.0

        text = (
            f"👤 **الملف الشخصي وبرنامج الأرباح:**\n\n"
            f"🆔 **معرفك (ID):** `{customer.telegram_id}`\n"
            f"👤 **المستخدم:** @{customer.telegram_username or 'غير محدد'}\n"
            f"📅 **تاريخ الانضمام:** {customer.first_seen_at.strftime('%Y-%m-%d')}\n"
            f"📦 **إجمالي الطلبات:** `{customer.total_orders}` طلب\n"
            f"💰 **إجمالي الإنفاق:** `${customer.total_spent:.2f} USD`\n"
            f"🎁 **نسبة الكاش باك الخاص بك:** `{cb_percent}%` على كل طلب تلقائياً\n"
            f"─────────────────\n"
            f"🤝 **برنامج الإحالة المباشر ({ref_percent}% عمولة دائمة):**\n"
            f"👥 **عدد الذين سجلوا عبرك:** `{customer.referral_count or 0}` عميل\n"
            f"💵 **إجمالي أرباح الإحالة:** `${customer.referral_earnings or Decimal('0.0'):.2f} USD`\n\n"
            f"🔗 **رابط دعوتك الخاص (شاركه واكسب فوراً):**\n`{ref_link}`"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔙 العودة للقائمة الرئيسية", callback_data="nav:home")]
            ]
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


async def show_giveaway_screen(message_or_cb, customer: Customer, session):
    """🎁 اربح حساب مجاني — live campaign progress + share link."""
    from src.core.services.referral_campaign_service import referral_campaign_service

    progress = await referral_campaign_service.get_user_progress(session, customer)
    bot_username = "shop"
    try:
        if isinstance(message_or_cb, CallbackQuery):
            me = await message_or_cb.bot.get_me()
        else:
            me = await message_or_cb.bot.get_me()
        bot_username = me.username or bot_username
    except Exception:
        pass

    if not progress:
        text = (
            "🎁 **حملة الحسابات المجانية**\n\n"
            "لا توجد حملة نشطة حالياً. تابع القناة — سنعلن عن الجوائز قريباً!"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 العودة", callback_data="nav:home")],
        ])
    else:
        c = progress["campaign"]
        stock = c.get("stock") or {}
        verified = progress["verified_invites"]
        required = progress["required_invites"]
        filled = "█" * min(verified, required)
        empty = "░" * max(0, required - verified)
        ref_link = f"https://t.me/{bot_username}?start=ref_{customer.telegram_id}"
        text = (
            f"🎁 **{c['title']}**\n\n"
            f"🏆 الجائزة: **{c['reward_name']}**\n"
            f"🔥 متبقي **{stock.get('available', 0)}** حساباً فقط\n"
            f"📋 المطلوب: ادعُ **{required}** أصدقاء ينضمون للقناة عبر رابطك\n\n"
            f"تقدمك: **{verified} / {required}** أصدقاء\n"
            f"`[{filled}{empty}]`\n\n"
            f"🔗 **رابط دعوتك (اضغط للنسخ):**\n`{ref_link}`"
        )
        share_url = f"https://t.me/share/url?url={ref_link}&text=جرب هذا المتجر معي وخذ مكافأة!"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 مشاركة الرابط الآن", url=share_url)],
            [InlineKeyboardButton(text="🔄 تحديث التقدّم", callback_data="user:giveaway")],
            [InlineKeyboardButton(text="🔙 العودة", callback_data="nav:home")],
        ])

    if isinstance(message_or_cb, CallbackQuery):
        try:
            await message_or_cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await message_or_cb.message.answer(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message_or_cb.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "user:giveaway")
async def cb_user_giveaway(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        await show_giveaway_screen(callback, customer, session)
    await callback.answer()


@router.callback_query(F.data == "user:support")
async def cb_user_support(callback: CallbackQuery):
    admin_id = settings.ADMIN_TELEGRAM_ID
    admin_tag = f"@{admin_id}" if admin_id else "فريق الدعم"

    text = (
        f"🛟 **الدعم الفني والنزاعات:**\n\n"
        f"إذا واجهتك أي مشكلة في استلام منتجك أو تفعيل المفتاح:\n\n"
        f"1️⃣ يمكنك الضغط على **[⚠️ فتح تذكرة]** من رسالة تسليم المنتج أو من سجل مشترياتك.\n"
        f"2️⃣ سيتم توجيه تذكرتك فوراً للوحة الإدارة وسيصلك الرد هنا مباشرة.\n"
        f"3️⃣ لتقديم استفسار عام تواصل مع المشرف: {admin_tag}."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 فتح تذكرة من سجل المشتريات", callback_data="user:orders")],
            [InlineKeyboardButton(text="🔙 العودة للقائمة الرئيسية", callback_data="nav:home")],
        ]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "nav:categories")
async def nav_categories(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        stmt = select(Category).where(Category.is_active == True).order_by(Category.sort_order.asc())
        res = await session.execute(stmt)
        categories = [
            {"id": str(c.id), "name_ar": c.name_ar, "icon_emoji": c.icon_emoji}
            for c in res.scalars().all()
        ]
        # Brand families as virtual browse groups
        fam_rows = (await session.execute(
            select(MasterProduct.brand_family)
            .where(
                and_(
                    MasterProduct.is_active == True,
                    MasterProduct.brand_family.isnot(None),
                    MasterProduct.brand_family != "",
                )
            )
            .distinct()
        )).scalars().all()
        for fam in fam_rows:
            if fam and not any(c["id"] == f"fam:{fam}" for c in categories):
                categories.insert(0, {
                    "id": f"fam:{fam}",
                    "name_ar": fam,
                    "icon_emoji": "🤖",
                })

        kb = categories_keyboard(categories)
        # Prepend featured button
        rows = list(kb.inline_keyboard)
        rows.insert(0, [InlineKeyboardButton(
            text="🔥 الأكثر طلباً الآن",
            callback_data="nav:featured",
        )])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        await callback.message.edit_text(
            "اختر القسم أو العائلة المطلوبة لتصفح المنتجات:",
            reply_markup=kb,
        )


@router.callback_query(F.data == "nav:featured")
async def nav_featured(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        pinned = (await session.execute(
            select(MasterProduct).where(
                and_(MasterProduct.is_active == True, MasterProduct.is_featured == True)
            ).order_by(MasterProduct.sort_order.asc()).limit(10)
        )).scalars().all()
        if not pinned:
            # Auto top-5 by completed order count
            from sqlalchemy import func
            top = (await session.execute(
                select(MasterProduct, func.count(Order.id).label("cnt"))
                .join(Order, Order.master_product_id == MasterProduct.id)
                .where(
                    and_(
                        MasterProduct.is_active == True,
                        Order.state == OrderState.COMPLETED,
                    )
                )
                .group_by(MasterProduct.id)
                .order_by(desc("cnt"))
                .limit(5)
            )).all()
            pinned = [row[0] for row in top]
        products_data = []
        for p in pinned:
            pricing_info = await pricing_service.get_best_supplier_and_price(session, str(p.id))
            products_data.append({
                "id": str(p.id),
                "title_ar": p.title_ar,
                "custom_emoji": getattr(p, "custom_emoji", None) or "🔥",
                "cached_final_price": str(pricing_info.get("final_price", 0)),
                "cached_in_stock": pricing_info.get("is_available", False),
                "available_quantity": None,
            })
        await callback.message.edit_text(
            "🔥 **الأكثر طلباً الآن**\nاختر منتجاً:",
            parse_mode="HTML",
            reply_markup=paginated_products_keyboard(products_data, "featured", page=1, total_pages=1),
        )


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("cat:"))
async def nav_category_products(callback: CallbackQuery):
    parts = callback.data.split(":")
    category_id = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    page_size = 6

    async with AsyncSessionLocal() as session:
        # Brand family browse: cat:fam:ChatGPT:1
        if category_id.startswith("fam:") or (len(parts) > 2 and parts[1] == "fam"):
            # callback forms: cat:fam:ChatGPT:1  OR legacy id "fam:ChatGPT"
            if parts[1] == "fam":
                fam_name = parts[2]
                page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
                category_id = f"fam:{fam_name}"
            else:
                fam_name = category_id.split(":", 1)[1]
            count_stmt = select(MasterProduct).where(
                and_(
                    MasterProduct.brand_family == fam_name,
                    MasterProduct.is_active == True,
                )
            )
        else:
            count_stmt = select(MasterProduct).where(
                and_(
                    MasterProduct.category_id == category_id,
                    MasterProduct.is_active == True
                )
            )
        all_prods = (await session.execute(count_stmt)).scalars().all()
        total_items = len(all_prods)
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages
        if page < 1:
            page = 1

        offset_val = (page - 1) * page_size
        paged_prods = all_prods[offset_val:offset_val + page_size]

        products_data = []
        for p in paged_prods:
            pricing_info = await pricing_service.get_best_supplier_and_price(session, str(p.id))
            final_p = pricing_info.get("final_price", Decimal("0.0"))
            is_stock = pricing_info.get("is_available", False)
            winner = pricing_info.get("winner_link")
            qty = None
            if winner and winner.available_quantity is not None:
                qty = int(winner.available_quantity)
            products_data.append({
                "id": str(p.id),
                "title_ar": p.title_ar,
                "custom_emoji": getattr(p, "custom_emoji", None) or "✨",
                "cached_final_price": str(final_p),
                "cached_in_stock": is_stock,
                "available_quantity": qty,
            })

        if str(category_id).startswith("fam:"):
            cat_name = str(category_id).split(":", 1)[1]
            # Keep fam:Name in callback for pagination
            page_cat_id = f"fam:{cat_name}"
        else:
            cat_stmt = select(Category.name_ar).where(Category.id == category_id)
            cat_name = (await session.execute(cat_stmt)).scalar_one_or_none() or "القسم"
            page_cat_id = category_id

        text = (
            f"📂 **منتجات: {cat_name}**\n\n"
            f"اختر المنتج لعرض التفاصيل والسعر الفوري (صفحة {page} من {total_pages}):"
        )
        # For fam pagination encode as cat:fam:Name:page via keyboard — use special id
        kb_cat = page_cat_id if not str(page_cat_id).startswith("fam:") else f"fam:{cat_name}"
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=paginated_products_keyboard(products_data, kb_cat, page=page, total_pages=total_pages)
        )


async def _edit_or_send(target_message, text: str, reply_markup=None, parse_mode: str = "HTML"):
    """edit_text when possible; falls back if the message is a photo/caption card."""
    try:
        await target_message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return
    except Exception:
        pass
    try:
        await target_message.edit_caption(caption=text, parse_mode=parse_mode, reply_markup=reply_markup)
        return
    except Exception:
        pass
    await target_message.answer(text, parse_mode=parse_mode, reply_markup=reply_markup)


# ---------------- Product Details & Buy ----------------
async def show_direct_buy_quantity(message_or_cb, product_id: str, session):
    """1-click deep-link: product card + instant quantity buttons (skips menu hunting)."""
    from html import escape as html_escape

    stmt = (
        select(MasterProduct)
        .where(MasterProduct.id == _as_uuid(product_id))
        .options(selectinload(MasterProduct.category))
    )
    product = (await session.execute(stmt)).scalar_one_or_none()
    if not product:
        text = "عذراً، هذا المنتج غير متوفر حالياً."
        if isinstance(message_or_cb, CallbackQuery):
            await _edit_or_send(message_or_cb.message, text)
        else:
            await message_or_cb.answer(text)
        return

    pricing_info = await pricing_service.get_best_supplier_and_price(session, str(product_id))
    final_price = pricing_info.get("final_price", Decimal("0.0")) or Decimal("0.0")
    is_in_stock = pricing_info.get("is_available", False)
    winner = pricing_info.get("winner_link")
    max_qty = int(winner.available_quantity) if winner and winner.available_quantity else 50
    warranty = int(getattr(product, "warranty_days", 30) or 30)
    emoji = getattr(product, "custom_emoji", "✨") or "✨"
    stock_qty = int(winner.available_quantity) if winner and winner.available_quantity is not None else ("∞" if is_in_stock else 0)
    stock_text = f"متوفر (<code>{stock_qty}</code>)" if is_in_stock else "نفد من المخزون مؤقتاً ❌"
    title = html_escape(product.title_ar or product.title_en or "منتج")
    unit_cents = int((Decimal(str(final_price)) * 100).quantize(Decimal("1")))

    card_text = (
        f"{emoji} <b>{title}</b>\n\n"
        f"💰 سعر القطعة: <code>${float(final_price):.2f}</code>\n"
        f"📦 المخزون: {stock_text}\n"
        f"🛡️ الضمان: <code>{warranty}</code> يوم\n\n"
        f"اختر الكمية — يتم حساب الإجمالي فوراً:"
    )
    if not is_in_stock:
        kb = product_detail_keyboard(str(product.id), False)
    else:
        kb = quantity_selector_keyboard(
            str(product.id), max_qty=max_qty, current_qty=1, unit_cents=unit_cents
        )

    if isinstance(message_or_cb, CallbackQuery):
        await _edit_or_send(message_or_cb.message, card_text, reply_markup=kb)
    else:
        await message_or_cb.answer(card_text, parse_mode="HTML", reply_markup=kb)


async def show_product_detail(message_or_cb, product_id: str, session, *, language: str = "ar"):
    from html import escape as html_escape
    from src.bot.i18n import normalize_lang

    lang = normalize_lang(language)
    stmt = (
        select(MasterProduct)
        .where(MasterProduct.id == _as_uuid(product_id))
        .options(selectinload(MasterProduct.category))
    )
    res = await session.execute(stmt)
    product = res.scalar_one_or_none()
    if not product:
        text = "Product unavailable." if lang == "en" else "عذراً، هذا المنتج غير متوفر حالياً."
        if isinstance(message_or_cb, CallbackQuery):
            await _edit_or_send(message_or_cb.message, text)
        else:
            await message_or_cb.answer(text)
        return

    pricing_info = await pricing_service.get_best_supplier_and_price(session, str(product_id))
    final_price = pricing_info.get("final_price", Decimal("0.0")) or Decimal("0.0")
    is_in_stock = pricing_info.get("is_available", False)
    winner = pricing_info.get("winner_link")
    stock_qty = int(winner.available_quantity) if winner and winner.available_quantity is not None else None
    if is_in_stock:
        stock_label = "In stock" if lang == "en" else "متوفر"
        stock_text = f"{stock_label} (<code>{stock_qty if stock_qty is not None else '∞'}</code>) ✅"
    else:
        stock_text = "Out of stock ❌" if lang == "en" else "نفد من المخزون مؤقتاً ❌"

    emoji = getattr(product, "custom_emoji", "✨") or "✨"
    if lang == "en":
        title = product.title_en or product.title_ar or "Product"
        desc = (
            getattr(product, "custom_description_en", None)
            or product.description_en
            or product.description_ar
            or "Instant digital delivery."
        )
        button_label = "⚡ Buy now"
        price_lbl, warranty_lbl, stock_lbl2, desc_lbl = "Price", "Warranty", "Stock", "Description"
        days_lbl = "days"
    else:
        title = product.title_ar or product.title_en or "منتج"
        desc = getattr(product, "custom_description_ar", None) or product.description_ar or "تسليم آلي وفوري للحساب أو المفتاح."
        button_label = getattr(product, "custom_button_label_ar", None) or "⚡ شراء الآن (دفع فوري)"
        price_lbl, warranty_lbl, stock_lbl2, desc_lbl = "السعر النهائي", "الضمان", "المخزون", "الوصف"
        days_lbl = "يوم"

    warranty = int(getattr(product, "warranty_days", 30) or 30)
    card_text = (
        f"{emoji} <b>{html_escape(title)}</b>\n\n"
        f"📝 <b>{desc_lbl}:</b>\n{html_escape(str(desc))}\n\n"
        f"💰 <b>{price_lbl}:</b> <code>${float(final_price):.2f}</code>\n"
        f"🛡️ <b>{warranty_lbl}:</b> <code>{warranty}</code> {days_lbl}\n"
        f"📦 <b>{stock_lbl2}:</b> {stock_text}"
    )

    kb = product_detail_keyboard(
        str(product.id),
        is_in_stock,
        buy_button_text=button_label,
        show_notify=not is_in_stock,
    )
    photo_id = getattr(product, "photo_file_id", None)
    photo_url = getattr(product, "photo_url", None)

    async def _send_text(msg_target):
        await msg_target.answer(card_text, parse_mode="HTML", reply_markup=kb)

    if isinstance(message_or_cb, CallbackQuery):
        cb = message_or_cb
        if photo_id or photo_url:
            try:
                await cb.message.delete()
            except Exception:
                pass
            try:
                await cb.message.answer_photo(
                    photo=photo_id or photo_url,
                    caption=card_text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception:
                await _send_text(cb.message)
        else:
            await _edit_or_send(cb.message, card_text, reply_markup=kb)
    else:
        if photo_id or photo_url:
            try:
                await message_or_cb.answer_photo(
                    photo=photo_id or photo_url,
                    caption=card_text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception:
                await _send_text(message_or_cb)
        else:
            await _send_text(message_or_cb)


@router.callback_query(F.data.startswith("prod:"))
async def cb_product_detail(callback: CallbackQuery):
    import uuid as _uuid
    product_id = callback.data.split(":")[1]
    try:
        product_id = str(_uuid.UUID(product_id))
    except (ValueError, AttributeError):
        await callback.answer("هذا الزر قديم أو غير صالح. يرجى فتح القائمة من جديد.", show_alert=True)
        return
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        await show_product_detail(
            callback, product_id, session, language=customer.language_code or "ar"
        )
    await callback.answer()


# ---------------- Quantity Selection & Multi-Quantity Purchasing ----------------
@router.callback_query(F.data.startswith("select_qty:"))
async def cb_select_quantity(callback: CallbackQuery):
    product_id = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        pricing_info = await pricing_service.get_best_supplier_and_price(session, product_id)
        if not pricing_info.get("is_available"):
            await callback.answer("عذراً، هذا المنتج غير متوفر في المخزون حالياً.", show_alert=True)
            return

        winner_link = pricing_info.get("winner_link")
        max_qty = int(winner_link.available_quantity) if winner_link and winner_link.available_quantity else 50
        unit = Decimal(str(pricing_info.get("final_price") or 0))
        unit_cents = int((unit * 100).quantize(Decimal("1")))
        await callback.answer()

        text = (
            f"📦 <b>تحديد الكمية</b>\n\n"
            f"سعر القطعة: <code>${float(unit):.2f}</code>\n"
            f"المتاح: <code>{max_qty}</code>\n\n"
            f"استخدم ➖ / ➕ أو اختر كمية سريعة — الإجمالي يتحدث فوراً:"
        )
        await _edit_or_send(
            callback.message,
            text,
            reply_markup=quantity_selector_keyboard(
                product_id, max_qty=max_qty, current_qty=1, unit_cents=unit_cents
            ),
        )


@router.callback_query(F.data.startswith("qty_adj:"))
async def cb_qty_adjust(callback: CallbackQuery):
    """+/- stepper and preset taps — recalculate total without leaving the card."""
    parts = callback.data.split(":")
    # qty_adj:{product_id}:{qty}:{unit_cents}:{max_qty}
    if len(parts) < 5:
        await callback.answer()
        return
    product_id, qty_s, unit_s, max_s = parts[1], parts[2], parts[3], parts[4]
    try:
        qty = max(1, int(qty_s))
        unit_cents = max(0, int(unit_s))
        max_qty = max(1, int(max_s))
    except ValueError:
        await callback.answer()
        return
    qty = min(qty, max_qty)
    unit = unit_cents / 100.0
    total = unit * qty
    await callback.answer(f"${total:.2f}")
    text = (
        f"📦 <b>تحديد الكمية</b>\n\n"
        f"سعر القطعة: <code>${unit:.2f}</code>\n"
        f"الكمية: <b>{qty}</b> · الإجمالي: <b>${total:.2f}</b>\n"
        f"المتاح: <code>{max_qty}</code>"
    )
    await _edit_or_send(
        callback.message,
        text,
        reply_markup=quantity_selector_keyboard(
            product_id, max_qty=max_qty, current_qty=qty, unit_cents=unit_cents
        ),
    )


@router.callback_query(F.data.startswith("custom_qty:"))
async def cb_custom_qty_prompt(callback: CallbackQuery, state: FSMContext):
    product_id = callback.data.split(":")[1]
    await state.set_state(CustomQuantityState.waiting_for_qty)
    await state.update_data(product_id=product_id)
    await callback.message.edit_text(
        "🔢 **يرجى كتابة الكمية المطلوبة كرقم** (مثال: `4`):",
        parse_mode="HTML"
    )


@router.message(CustomQuantityState.waiting_for_qty, F.text)
async def handle_custom_qty_input(message: Message, state: FSMContext):
    raw_text = message.text.strip()
    if not raw_text.isdigit() or int(raw_text) <= 0:
        await message.answer("⚠️ يرجى إدخال رقم صحيح أكبر من الصفر:")
        return

    qty = int(raw_text)
    if qty > 100:
        await message.answer("⚠️ الحد الأقصى للشراء في الطلب الواحد هو 100 قطعة. يرجى إدخال رقم أقل:")
        return

    data = await state.get_data()
    product_id = data.get("product_id")
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass

    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, message.from_user)
        await _execute_order_creation_flow(message, product_id, qty, customer, session, state=state)


@router.callback_query(F.data.startswith("stock_alert:"))
async def cb_stock_alert(callback: CallbackQuery):
    product_id = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        await stock_alert_service.subscribe(
            session, customer_id=customer.id, product_id=product_id
        )
    await callback.answer("🔔 تم تفعيل التنبيه — سنخبرك فور التوفر!", show_alert=True)


@router.message(CheckoutEmailState.waiting_email, F.text)
@router.message(CheckoutCustomerInputState.waiting_input, F.text)
async def handle_buyer_email_for_checkout(message: Message, state: FSMContext):
    """Explicit FSM capture for email / link / username / text — no chat collisions."""
    import re
    data = await state.get_data()
    product_id = data.get("product_id")
    qty = int(data.get("qty") or 1)
    input_type = (data.get("input_type") or "email").lower()
    value = (message.text or "").strip()
    if input_type == "email" and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
        await message.answer(
            "⚠️ بريد غير صالح. أرسل بريداً مثل `name@example.com`:",
            parse_mode="HTML",
            reply_markup=cancel_fsm_keyboard(),
        )
        return
    if input_type == "link" and not value.startswith(("http://", "https://")):
        await message.answer("⚠️ أرسل رابطاً يبدأ بـ https://", reply_markup=cancel_fsm_keyboard())
        return
    await state.clear()
    redis = event_bus._redis
    if redis and product_id:
        await redis.set(f"buyer_email:{message.from_user.id}:{product_id}", value, ex=3600)
        await redis.delete(f"pending_email_checkout:{message.from_user.id}")
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(f"✅ تم الحفظ: `{value}`\nجاري إنشاء الطلب...", parse_mode="HTML")
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, message.from_user)
        await _execute_order_creation_flow(message, product_id, qty, customer, session, state=state)


@router.callback_query(F.data.startswith("buy_qty:"))
async def cb_buy_qty(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    product_id = parts[1]
    qty = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        await _execute_order_creation_flow(callback, product_id, qty, customer, session, state=state)


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy_product(callback: CallbackQuery, state: FSMContext):
    # Backward compatibility: redirect single purchase
    product_id = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        await _execute_order_creation_flow(callback, product_id, 1, customer, session, state=state)


async def _execute_order_creation_flow(
    message_or_cb, product_id: str, qty: int, customer: Customer, session, state: FSMContext = None
):
    # --- Step 1: Get best supplier & cached pricing ---
    pricing_info = await pricing_service.get_best_supplier_and_price(session, product_id)
    if not pricing_info.get("is_available"):
        err = "عذراً، هذا المنتج غير متوفر في المخزون الآن."
        if isinstance(message_or_cb, CallbackQuery):
            await message_or_cb.answer(err, show_alert=True)
        else:
            await message_or_cb.answer(err)
        return

    winner_link = pricing_info["winner_link"]
    supplier = pricing_info["supplier"]

    # Validate against live available quantity
    if winner_link.available_quantity and qty > winner_link.available_quantity:
        err = f"عذراً، الكمية المتوفرة حالياً هي {winner_link.available_quantity} فقط."
        if isinstance(message_or_cb, CallbackQuery):
            await message_or_cb.answer(err, show_alert=True)
        else:
            await message_or_cb.answer(err)
        return

    # --- Step 2: Two-Tier Redis Quote Cache with Mutex Lock ---
    adapter = AdapterRegistry.create_from_supplier(supplier)
    async def _fetch_upstream_quote():
        return await adapter.get_quote(winner_link.supplier_product_id, quantity=qty)

    try:
        cached_quote = await event_bus.get_or_set_cached_quote(
            supplier_id=str(supplier.id),
            product_id=str(winner_link.supplier_product_id),
            fetch_coro=_fetch_upstream_quote,
            ttl_seconds=25
        )
        if not cached_quote.get("is_available", True):
            err = "عذراً، المنتج نفد من المخزون لدى المورد حالياً."
            if isinstance(message_or_cb, CallbackQuery):
                await message_or_cb.answer(err, show_alert=True)
            else:
                await message_or_cb.answer(err)
            return
        live_cost = Decimal(str(cached_quote.get("cost", pricing_info["wholesale_cost"])))
    except Exception as e:
        logger.error(f"Quote resolution failed: {e}. Falling back.")
        live_cost = pricing_info["wholesale_cost"] * qty

    # --- Step 3: Compute retail price for the full quantity ---
    prod_stmt = select(MasterProduct).where(MasterProduct.id == _as_uuid(product_id))
    product = (await session.execute(prod_stmt)).scalar_one_or_none()

    rule = await pricing_service.get_active_pricing_rule(
        session,
        product_id=str(product_id),
        category_id=str(product.category_id) if product and product.category_id else None
    )
    unit_cost = live_cost / Decimal(qty)
    list_unit_price, unit_margin, gas_buf = pricing_service.calculate_price_from_cost(unit_cost, rule)
    incentive_rate = await pricing_service.get_incentive_rate(session)
    unit_final_price = pricing_service.resolve_bulk_unit_price(
        list_unit_price,
        qty,
        getattr(product, "bulk_pricing_tiers", None) if product else None,
        unit_cost=unit_cost,
        incentive_rate=incentive_rate,
    )
    total_final_price = (unit_final_price * Decimal(qty)).quantize(Decimal("0.01"))
    total_margin = (total_final_price - live_cost).quantize(Decimal("0.01"))

    # Flexible customer activation input (email / link / username / text)
    buyer_email = None
    needs_input = product and (
        getattr(product, "requires_customer_email", False)
        or getattr(product, "requires_customer_input", False)
    )
    if needs_input:
        redis = event_bus._redis
        cached = None
        if redis:
            cached = await redis.get(f"buyer_email:{customer.telegram_id}:{product_id}")
        if not cached:
            label = getattr(product, "customer_input_label_ar", None) or (
                "📧 أرسل بريدك الإلكتروني لتفعيل الدعوة"
            )
            input_type = getattr(product, "customer_input_type", None) or "email"
            if redis:
                import json as _json
                await redis.set(
                    f"pending_email_checkout:{customer.telegram_id}",
                    _json.dumps({"product_id": str(product_id), "qty": qty, "input_type": input_type}),
                    ex=1800,
                )
            prompt = f"{label}\n\nأرسل القيمة الآن:"
            if state is not None:
                await state.set_state(CheckoutCustomerInputState.waiting_input)
                await state.update_data(product_id=str(product_id), qty=qty, input_type=input_type)
            if isinstance(message_or_cb, CallbackQuery):
                await message_or_cb.message.answer(prompt, parse_mode="HTML", reply_markup=cancel_fsm_keyboard())
            else:
                await message_or_cb.answer(prompt, parse_mode="HTML", reply_markup=cancel_fsm_keyboard())
            return
        buyer_email = cached if isinstance(cached, str) else cached.decode()

    # Create Order with multi-quantity
    order, invoice = await order_service.create_order(
        session=session,
        customer_id=str(customer.id),
        master_product_id=product_id,
        supplier_id=str(winner_link.supplier_id),
        supplier_product_id=winner_link.supplier_product_id,
        supplier_cost=live_cost,
        our_price=total_final_price,
        profit_margin=total_margin,
        payment_method=PaymentMethod.USDT_BEP20_DIRECT,
        quantity=qty,
        buyer_email=buyer_email,
        buyer_activation_id=buyer_email,
    )

    card_text = (
        f"🛒 **تفاصيل الطلب:** `#{order.order_number}`\n\n"
        f"📦 **المنتج:** {product.title_ar}\n"
        f"🔢 **الكمية المطلوبة:** `{qty}` قطعة\n"
        f"🧮 **الحساب:** `{qty} × ${unit_final_price:.2f} = ${total_final_price:.2f}`\n"
        f"💵 **سعر الوحدة:** `${unit_final_price}`\n"
        f"💰 **إجمالي المبلغ المطلوب:** `${order.our_price}` USD\n"
        f"👛 **رصيدك الحالي بالمحفظة:** `${customer.wallet_balance:.2f}` USD\n\n"
        f"يرجى اختيار وسيلة الدفع المفضلة لديك:"
    )

    rails = await payment_rail_flags(session)
    kb = payment_methods_keyboard(
        order_id=str(order.id),
        total_price=float(order.our_price),
        has_wallet_balance=customer.wallet_balance > 0,
        wallet_balance=float(customer.wallet_balance),
        **rails,
    )

    if isinstance(message_or_cb, CallbackQuery):
        await message_or_cb.message.edit_text(card_text, parse_mode="HTML", reply_markup=kb)
    else:
        await message_or_cb.answer(card_text, parse_mode="HTML", reply_markup=kb)


# ---------------- Hybrid / Split Payment Handlers ----------------
@router.callback_query(F.data.startswith("pay:split_prompt:"))
async def pay_split_prompt(callback: CallbackQuery):
    order_id = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        stmt = select(Order).where(Order.id == _as_uuid(order_id))
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return

        wallet_portion = min(customer.wallet_balance, order.our_price)
        remainder = order.our_price - wallet_portion

        text = (
            f"💰 **الدفع الجزئي الهجين (رصيد المحفظة + وسيلة أخرى):**\n\n"
            f"🔹 **إجمالي قيمة الطلب:** `${order.our_price}` USD\n"
            f"✅ **سيتم خصم من محفظتك:** `${wallet_portion:.2f}` USD\n"
            f"💵 **المبلغ المتبقي المطلوب دفعه:** `${remainder:.2f}` USD\n\n"
            f"اختر وسيلة دفع المبلغ المتبقي أدناه:"
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=split_payment_methods_keyboard(
                order_id, float(remainder), **(await payment_rail_flags(session))
            )
        )


async def _reserve_split_and_set_crypto(
    session,
    customer,
    order,
    *,
    method: PaymentMethod,
    deposit_address: str,
):
    # Idempotent: double-tap pay:split_* must never double-debit
    already = Decimal(str(order.wallet_portion_reserved or 0))
    if already > 0:
        invoice = order.invoice
        remainder = Decimal(str(invoice.amount_expected or (order.our_price - already)))
        if invoice:
            invoice.deposit_address = deposit_address or invoice.deposit_address
            invoice.payment_method = method
            await session.commit()
        return already, remainder, invoice

    wallet_portion = min(customer.wallet_balance, order.our_price)
    remainder = order.our_price - wallet_portion
    if wallet_portion > 0:
        await order_service.debit_customer_wallet(
            session=session,
            customer_id=str(customer.id),
            amount=wallet_portion,
            tx_type=WalletTxType.SPLIT_PAYMENT_RESERVE,
            description=f"حجز دفعة جزئية للطلب #{order.order_number}",
            order_id=str(order.id),
        )
        order.wallet_portion_reserved = wallet_portion
    invoice = order.invoice
    invoice.amount_expected = remainder
    invoice.unique_amount = order_service.generate_unique_invoice_amount(remainder)
    invoice.deposit_address = deposit_address
    invoice.payment_method = method
    await session.commit()
    return wallet_portion, remainder, invoice


@router.callback_query(F.data.startswith("pay:split_usdt:"))
async def pay_split_usdt(callback: CallbackQuery):
    order_id = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        stmt = select(Order).where(Order.id == _as_uuid(order_id)).options(selectinload(Order.invoice)).with_for_update()
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return

        wallet_portion, _remainder, invoice = await _reserve_split_and_set_crypto(
            session, customer, order,
            method=PaymentMethod.USDT_BEP20_DIRECT,
            deposit_address=settings.HOT_WALLET_ADDRESS or "",
        )
        text = (
            f"💎 **دفع المتبقي بواسطة USDT (شبكة BEP-20):**\n\n"
            f"✅ **تم حجز رصيد المحفظة:** `${wallet_portion:.2f}` USD\n"
            f"💵 **المبلغ المتبقي المطلوب إرساله بالضبط:**\n`{invoice.unique_amount}` USDT\n\n"
            f"📬 **عنوان المحفظة:**\n`{invoice.deposit_address}`\n\n"
            f"⏳ سيتم تنفيذ الطلب وتسليمه آلياً فور رصد المتبقي على البلوكشين."
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=crypto_payment_keyboard(
                order_id=str(order.id),
                address=str(invoice.deposit_address or ""),
                amount=str(invoice.unique_amount),
                network="bep20",
            ),
        )


@router.callback_query(F.data.startswith("pay:split_trc20:"))
async def pay_split_trc20(callback: CallbackQuery):
    order_id = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        stmt = select(Order).where(Order.id == _as_uuid(order_id)).options(selectinload(Order.invoice)).with_for_update()
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return
        tron_addr = (settings.TRON_HOT_WALLET_ADDRESS or "").strip()
        if not tron_addr:
            await callback.answer("محفظة TRC-20 غير مهيأة حالياً.", show_alert=True)
            return
        wallet_portion, _remainder, invoice = await _reserve_split_and_set_crypto(
            session, customer, order,
            method=PaymentMethod.USDT_TRC20_DIRECT,
            deposit_address=tron_addr,
        )
        text = (
            f"⚡ **دفع المتبقي بواسطة USDT (شبكة TRC-20):**\n\n"
            f"✅ **تم حجز رصيد المحفظة:** `${wallet_portion:.2f}` USD\n"
            f"💵 **المبلغ المتبقي المطلوب إرساله بالضبط:**\n`{invoice.unique_amount}` USDT\n\n"
            f"📬 **عنوان محفظة TRON:**\n`{invoice.deposit_address}`\n\n"
            f"⚠️ تأكد من اختيار شبكة **TRC-20** حصراً."
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=crypto_payment_keyboard(
                order_id=str(order.id),
                address=str(invoice.deposit_address or ""),
                amount=str(invoice.unique_amount),
                network="trc20",
            ),
        )


@router.callback_query(F.data.startswith("pay:split_binance:"))
async def pay_split_binance(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        stmt = select(Order).where(Order.id == _as_uuid(order_id)).options(selectinload(Order.invoice)).with_for_update()
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return
        pay_id = (settings.BINANCE_PAY_MERCHANT_ID or "").strip()
        if not pay_id:
            await callback.answer("معرف Binance Pay غير مهيأ حالياً.", show_alert=True)
            return
        wallet_portion, _remainder, invoice = await _reserve_split_and_set_crypto(
            session, customer, order,
            method=PaymentMethod.BINANCE_PAY,
            deposit_address=pay_id,
        )
        await state.set_state(BinancePayOrderState.waiting_order_id)
        await state.update_data(order_id=str(order.id))
        text = (
            f"🟡 **دفع المتبقي عبر Binance Pay:**\n\n"
            f"✅ **تم حجز رصيد المحفظة:** `${wallet_portion:.2f}` USD\n"
            f"💵 **المبلغ المطلوب بالضبط:**\n`{invoice.unique_amount}` USDT\n\n"
            f"🆔 **معرف Binance Pay:** `{pay_id}`\n"
            f"📝 في الملاحظة ضع رقم الطلب: `{order.order_number}`\n\n"
            f"⏳ بعد التحويل الصق **Order ID** هنا مباشرة للتحقق."
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=binance_payment_keyboard(order_id=str(order.id), pay_id=pay_id),
        )


async def _send_sham_cash_qr_card(
    *,
    bot,
    chat_id: int,
    session,
    order: Order,
    amount,
    lang: str = "ar",
    extra_preamble: str = "",
):
    """Send QR photo (or HTML fallback) with ✅ تم الدفع / Cancel under the card."""
    from src.core.services.sham_cash_qr import (
        build_sham_cash_caption,
        get_sham_cash_payment_config,
        resolve_qr_photo_input,
    )
    cfg = await get_sham_cash_payment_config(session)
    caption = await build_sham_cash_caption(
        session,
        amount_usd=amount,
        order_number=order.order_number,
        lang=lang,
    )
    if extra_preamble:
        caption = f"{extra_preamble}\n\n{caption}"
    kb = sham_cash_payment_keyboard(
        order_id=str(order.id),
        account_code=str(cfg.get("account_number") or ""),
        lang=lang,
    )
    photo, _src = await resolve_qr_photo_input(session)
    if photo is not None:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
            return
        except Exception as e:
            logger.warning(f"Sham Cash QR send failed, falling back to text: {e}")
    await bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("pay:split_sham_cash:"))
async def pay_split_sham_cash(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":")[2]
    await state.clear()
    await state.update_data(order_id=order_id)

    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        lang = "en" if str(customer.language_code or "").lower().startswith("en") else "ar"
        stmt = select(Order).where(Order.id == _as_uuid(order_id)).options(selectinload(Order.invoice)).with_for_update()
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return
        if order.customer_id != customer.id:
            await callback.answer("هذا الطلب لا يخص حسابك.", show_alert=True)
            return

        wallet_portion = min(customer.wallet_balance, order.our_price)
        remainder = order.our_price - wallet_portion

        if wallet_portion > 0:
            await order_service.debit_customer_wallet(
                session=session,
                customer_id=str(customer.id),
                amount=wallet_portion,
                tx_type=WalletTxType.SPLIT_PAYMENT_RESERVE,
                description=f"حجز دفعة جزئية للطلب #{order.order_number}",
                order_id=str(order.id)
            )
            order.wallet_portion_reserved = wallet_portion

        invoice = order.invoice
        invoice.amount_expected = remainder
        invoice.payment_method = PaymentMethod.SHAM_CASH
        await session.commit()

        preamble = (
            f"✅ تم حجز رصيد المحفظة: <code>${float(wallet_portion):.2f}</code> USD\n"
            f"💵 المتبقي عبر شام كاش: <code>${float(remainder):.2f}</code> USD"
        )
        await callback.answer()
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _send_sham_cash_qr_card(
            bot=callback.bot,
            chat_id=callback.from_user.id,
            session=session,
            order=order,
            amount=remainder,
            lang=lang,
            extra_preamble=preamble,
        )


@router.callback_query(F.data.startswith("pay_options:"))
async def cb_back_to_pay_options(callback: CallbackQuery):
    order_id = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        stmt = select(Order).where(Order.id == _as_uuid(order_id))
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return

        rails = await payment_rail_flags(session)
        await callback.message.edit_text(
            f"🛒 **تفاصيل الطلب:** #{order.order_number}\n\n"
            f"🔹 المبلغ المطلوب: `${order.our_price}` USD\n\n"
            f"يرجى اختيار وسيلة الدفع المفضلة لديك:",
            parse_mode="HTML",
            reply_markup=payment_methods_keyboard(
                order_id=str(order.id),
                total_price=float(order.our_price),
                has_wallet_balance=customer.wallet_balance > 0,
                wallet_balance=float(customer.wallet_balance),
                **rails,
            )
        )


# ---------------- Payment Handlers ----------------
@router.callback_query(F.data.startswith("pay:usdt_bep20:"))
async def pay_usdt_bep20(callback: CallbackQuery):
    order_id = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(selectinload(Order.invoice))
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return

        invoice = order.invoice
        invoice.payment_method = PaymentMethod.USDT_BEP20_DIRECT
        invoice.deposit_address = settings.HOT_WALLET_ADDRESS
        if not invoice.unique_amount:
            invoice.unique_amount = order_service.generate_unique_invoice_amount(order.our_price)
        await session.commit()

        amount_to_send = invoice.unique_amount or order.our_price
        deposit_addr = invoice.deposit_address or settings.HOT_WALLET_ADDRESS

        text = (
            f"💎 **الدفع بواسطة USDT (شبكة BEP-20 / BNB Smart Chain)**\n\n"
            f"⚠️ **هام جداً:** لربط دفعتك آلياً بدون أي تأخير، يرجى إرسال **المبلغ المحدد بالضبط** أدناه:\n\n"
            f"💵 **المبلغ المطلوب إرساله:**\n`{amount_to_send}` USDT\n\n"
            f"📬 **عنوان المحفظة:**\n`{deposit_addr}`\n\n"
            f"⏳ يتم رصد الدفعة آلياً على البلوكشين وتسليم الطلب خلال ثوانٍ بمجرد وصول التحويل."
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=crypto_payment_keyboard(
                order_id=str(order.id),
                address=str(deposit_addr or ""),
                amount=str(amount_to_send),
                network="bep20",
            ),
        )


@router.callback_query(F.data.startswith("pay:usdt_trc20:"))
async def pay_usdt_trc20(callback: CallbackQuery):
    order_id = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(selectinload(Order.invoice))
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return

        tron_addr = (settings.TRON_HOT_WALLET_ADDRESS or "").strip()
        if not tron_addr:
            await callback.answer("محفظة TRC-20 غير مهيأة حالياً.", show_alert=True)
            return

        invoice = order.invoice
        invoice.payment_method = PaymentMethod.USDT_TRC20_DIRECT
        invoice.deposit_address = tron_addr
        if not invoice.unique_amount:
            invoice.unique_amount = order_service.generate_unique_invoice_amount(order.our_price)
        await session.commit()

        text = (
            f"⚡ **الدفع بواسطة USDT (شبكة TRC-20 / TRON)**\n\n"
            f"⚠️ أرسل **المبلغ المحدد بالضبط** لربط الدفعة آلياً:\n\n"
            f"💵 **المبلغ المطلوب إرساله:**\n`{invoice.unique_amount}` USDT\n\n"
            f"📬 **عنوان محفظة TRON:**\n`{tron_addr}`\n\n"
            f"⚠️ تأكد من اختيار شبكة **TRC-20** حصراً.\n"
            f"⏳ يتم الرصد والتسليم آلياً بعد تأكيد الشبكة."
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=crypto_payment_keyboard(
                order_id=str(order.id),
                address=tron_addr,
                amount=str(invoice.unique_amount),
                network="trc20",
            ),
        )


@router.callback_query(F.data.startswith("pay:binance_pay:"))
async def pay_binance_pay(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(selectinload(Order.invoice))
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return

        pay_id = (settings.BINANCE_PAY_MERCHANT_ID or "").strip()
        if not pay_id:
            await callback.answer("معرف Binance Pay غير مهيأ حالياً.", show_alert=True)
            return

        invoice = order.invoice
        invoice.payment_method = PaymentMethod.BINANCE_PAY
        invoice.deposit_address = pay_id
        if not invoice.unique_amount:
            invoice.unique_amount = order_service.generate_unique_invoice_amount(order.our_price)
        await session.commit()

        await state.set_state(BinancePayOrderState.waiting_order_id)
        await state.update_data(order_id=str(order.id))

        text = (
            f"🟡 **الدفع عبر Binance Pay**\n\n"
            f"💵 **المبلغ المطلوب بالضبط:**\n`{invoice.unique_amount}` USDT\n\n"
            f"🆔 **معرف Binance Pay:** `{pay_id}`\n"
            f"📝 ضع في الملاحظة رقم الطلب: `{order.order_number}`\n\n"
            f"⏳ بعد إتمام التحويل الصق **Pay Order ID** هنا مباشرة للتحقق الآلي."
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=binance_payment_keyboard(order_id=str(order.id), pay_id=pay_id),
        )


@router.callback_query(F.data.startswith("pay:balance:"))
async def pay_balance(callback: CallbackQuery):
    from src.bot.effects import EFFECT_CELEBRATION, send_with_effect
    from src.bot.i18n import t, normalize_lang
    order_id = callback.data.split(":")[2]
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        lang = normalize_lang(customer.language_code)
        stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(selectinload(Order.invoice), selectinload(Order.master_product))
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return

        if order.customer_id != customer.id:
            await callback.answer("هذا الطلب لا يخص حسابك.", show_alert=True)
            return

        if order.state not in (OrderState.CREATED, OrderState.AWAITING_PAYMENT):
            await callback.answer("تمت معالجة هذا الطلب مسبقاً.", show_alert=True)
            return

        if customer.wallet_balance < order.our_price:
            await callback.answer()
            need = float(order.our_price - (customer.wallet_balance or 0))
            text = f"{t(lang, 'low_balance')}\n≈ <b>${need:.2f}</b>"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 $10", callback_data="topup_amt:10"),
                 InlineKeyboardButton(text="💳 $25", callback_data="topup_amt:25")],
                [InlineKeyboardButton(text="🏦 شام كاش للطلب", callback_data=f"pay:sham_cash:{order_id}")],
                [InlineKeyboardButton(text="↩️ رجوع", callback_data=f"pay_options:{order_id}")],
            ])
            try:
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
            return

        await callback.answer()
        await order_service.debit_customer_wallet(
            session=session,
            customer_id=str(customer.id),
            amount=order.our_price,
            tx_type=WalletTxType.PURCHASE_DEBIT,
            description=f"Purchase order #{order.order_number}",
            order_id=str(order.id)
        )

        if order.invoice:
            order.invoice.status = InvoiceStatus.CONFIRMED

        await order_service.transition_order_state(
            session, order, OrderState.PAYMENT_CONFIRMED, "customer_balance_payment"
        )
        await session.commit()
        await session.refresh(customer)

        ok_text = (
            f"{t(lang, 'buy_success')}\n\n"
            f"🔖 <code>#{order.order_number}</code>\n"
            f"💰 <b>${order.our_price}</b>\n"
            f"👛 <b>${float(customer.wallet_balance or 0):.2f}</b>"
        )
        try:
            await callback.message.edit_text(ok_text, parse_mode="HTML")
        except Exception:
            pass
        await send_with_effect(
            callback.bot,
            chat_id=callback.from_user.id,
            text=ok_text,
            effect_id=EFFECT_CELEBRATION,
        )
        await enqueue_jit_order(str(order.id))


@router.callback_query(F.data.startswith("pay:sham_cash:"))
async def pay_sham_cash(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":")[2]
    await state.clear()
    await state.update_data(order_id=order_id)

    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        lang = "en" if str(customer.language_code or "").lower().startswith("en") else "ar"
        stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(selectinload(Order.invoice))
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return
        if order.customer_id != customer.id:
            await callback.answer("هذا الطلب لا يخص حسابك.", show_alert=True)
            return
        if order.invoice:
            order.invoice.payment_method = PaymentMethod.SHAM_CASH
            await session.commit()

        await callback.answer()
        try:
            await callback.message.delete()
        except Exception:
            pass
        await _send_sham_cash_qr_card(
            bot=callback.bot,
            chat_id=callback.from_user.id,
            session=session,
            order=order,
            amount=order.our_price,
            lang=lang,
        )


@router.callback_query(F.data.startswith("sham_paid:"))
async def sham_paid_prompt_receipt(callback: CallbackQuery, state: FSMContext):
    """✅ تم الدفع → IDOR check → enter receipt upload FSM."""
    from src.bot.keyboards.inline import sham_cash_receipt_prompt_keyboard

    order_id = callback.data.split(":", 1)[1]
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        lang = "en" if str(customer.language_code or "").lower().startswith("en") else "ar"
        order = (
            await session.execute(
                select(Order)
                .where(Order.id == _as_uuid(order_id))
                .options(selectinload(Order.invoice))
            )
        ).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود" if lang != "en" else "Order not found", show_alert=True)
            return
        if order.customer_id != customer.id:
            await callback.answer(
                "هذا الطلب لا يخص حسابك." if lang != "en" else "This order does not belong to you.",
                show_alert=True,
            )
            return
        awaiting = (
            OrderState.CREATED,
            OrderState.AWAITING_PAYMENT,
            OrderState.SHAM_CASH_REVIEW,
        )
        if order.state not in awaiting:
            await callback.answer(
                "تمت معالجة هذا الطلب مسبقاً." if lang != "en" else "This order was already processed.",
                show_alert=True,
            )
            return

    await state.set_state(ShamCashUploadState.waiting_for_receipt)
    await state.update_data(order_id=order_id)
    prompt = (
        "Please upload the Sham Cash transfer PDF file (or receipt screenshot) to confirm your payment 📎"
        if lang == "en"
        else "الرجاء إرسال ملف الـ PDF (أو صورة إيصال التحويل) من تطبيق شام كاش لتأكيد الدفع 📎"
    )
    await callback.answer()
    await callback.message.answer(
        prompt,
        parse_mode="HTML",
        reply_markup=sham_cash_receipt_prompt_keyboard(order_id=order_id, lang=lang),
    )


@router.message(ShamCashUploadState.waiting_for_tx_ref, F.text)
async def handle_sham_cash_tx_ref(message: Message, state: FSMContext):
    tx_ref = message.text.strip().replace(" ", "")
    if len(tx_ref) < 4:
        await message.answer("⚠️ رقم العملية قصير جداً. يرجى إدخال رقم مرجعي صحيح لعملية التحويل:")
        return

    async with AsyncSessionLocal() as session:
        dup_stmt = select(PaymentInvoice).where(
            and_(
                PaymentInvoice.sham_cash_tx_ref == tx_ref,
                PaymentInvoice.status.in_([InvoiceStatus.PENDING, InvoiceStatus.CONFIRMED, InvoiceStatus.DETECTED])
            )
        )
        existing_inv = (await session.execute(dup_stmt)).scalar_one_or_none()
        if existing_inv:
            await message.answer(
                "🛑 **تحذير أمني:** رقم العملية المرجعي هذا مسجل مسبقاً في النظام!\n"
                "يرجى التأكد من كتابة الرقم الصحيح لعملية التحويل الحالية."
            )
            return

    await state.update_data(tx_ref=tx_ref)
    await state.set_state(ShamCashUploadState.waiting_for_receipt)
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        f"✅ تم تسجيل رقم العملية: `{tx_ref}`\n\n"
        f"📎 **الخطوة 2 من 2:** أرسل الآن **صورة الإيصال أو ملف PDF** لتأكيد الطلب:",
        parse_mode="HTML",
    )


async def _finalize_sham_receipt(
    message: Message,
    state: FSMContext,
    *,
    file_id: str,
    file_unique_id: str,
    receipt_type: str,
    file_bytes: Optional[bytes],
):
    data = await state.get_data()
    order_id = data.get("order_id")
    tx_ref = data.get("tx_ref")
    receipt_sha256 = (
        hashlib.sha256(file_bytes).hexdigest() if file_bytes else file_unique_id
    )

    async with AsyncSessionLocal() as session:
        from src.core.services.sham_cash_service import sham_cash_service as _scs
        dup = await _scs.find_duplicate_receipt(
            session, receipt_sha256=receipt_sha256, tx_ref=tx_ref
        )

        stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(
                selectinload(Order.invoice),
                selectinload(Order.customer),
                selectinload(Order.master_product),
                selectinload(Order.supplier),
            )
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await message.answer("عذراً، حدث خطأ أثناء تحديد الطلب.")
            await state.clear()
            return

        invoice = order.invoice
        invoice.sham_cash_receipt_photo_file_id = file_id
        invoice.receipt_type = receipt_type
        invoice.receipt_sha256 = receipt_sha256
        invoice.receipt_file_sha256 = receipt_sha256
        invoice.receipt_file_unique_id = file_unique_id
        invoice.sham_cash_tx_ref = (tx_ref or "").replace(" ", "")
        invoice.payment_method = PaymentMethod.SHAM_CASH
        fraud_warning = None
        if dup:
            invoice.duplicate_of_order_id = _as_uuid(dup[0])
            invoice.duplicate_of_order_number = dup[1]
            fraud_warning = _scs.fraud_banner(dup[1])

        forensics_summary = None
        forensics_dict = None
        if receipt_type == "pdf" and file_bytes:
            try:
                from src.core.services.pdf_forensics_service import pdf_forensics_service
                from src.core.services.syp_rate_service import get_sham_cash_rate, usd_to_syp
                expected = Decimal(str(invoice.amount_expected or order.our_price or 0))
                rate, accept_syp = await get_sham_cash_rate(session)
                expected_syp = usd_to_syp(expected, rate) if accept_syp else None
                fr = pdf_forensics_service.analyze(
                    file_bytes, expected_usd=expected, expected_syp=expected_syp
                )
                forensics_dict = fr.to_dict()
                forensics_dict["duplicate_sha256"] = bool(dup)
                invoice.pdf_forensics_json = forensics_dict
                forensics_summary = fr.summary_block_ar()
                if dup:
                    forensics_summary += f"\n♻️ SHA-256 مكرر مع طلب سابق"
            except Exception as e:
                logger.warning(f"PDF forensics failed: {e}")

        await order_service.transition_order_state(
            session, order, OrderState.SHAM_CASH_REVIEW, "customer_uploaded_receipt"
        )
        await session.commit()

        cust = order.customer
        await publish_manual_review_alert({
            "order_id": str(order.id),
            "order_number": order.order_number,
            "review_kind": "sham_cash",
            "supplier_name": order.supplier.name if order.supplier else None,
            "customer_telegram_id": cust.telegram_id if cust else None,
            "customer_username": cust.telegram_username if cust else None,
            "customer_name": (cust.first_name if cust else None) or "",
            "product_title": order.master_product.title_ar if order.master_product else None,
            "quantity": int(order.quantity or 1),
            "amount": float(order.our_price or 0),
            "tx_ref": tx_ref,
            "receipt_type": receipt_type,
            "media_file_id": file_id,
            "media_type": "document" if receipt_type == "pdf" else "photo",
            "fraud_warning": fraud_warning,
            "forensics_summary": forensics_summary,
            "pdf_forensics": forensics_dict,
        })

    await state.clear()
    await message.answer(
        "تم استلام إيصال الدفع بنجاح ✅ جاري المراجعة من قبل الإدارة...",
        reply_markup=main_menu_keyboard(),
    )


_SHAM_RECEIPT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@router.message(ShamCashUploadState.waiting_for_receipt, F.photo)
async def handle_receipt_photo(message: Message, state: FSMContext):
    photo = message.photo[-1]
    file_bytes = None
    try:
        file_obj = await message.bot.get_file(photo.file_id)
        file_stream = await message.bot.download_file(file_obj.file_path)
        file_bytes = file_stream.read()
        if file_bytes and len(file_bytes) > _SHAM_RECEIPT_MAX_BYTES:
            await message.answer("⚠️ الملف كبير جداً (الحد 10MB). أرسل صورة أو PDF أصغر.")
            return
    except Exception as e:
        logger.warning(f"Could not compute photo hash: {e}")
    await _finalize_sham_receipt(
        message,
        state,
        file_id=photo.file_id,
        file_unique_id=photo.file_unique_id,
        receipt_type="photo",
        file_bytes=file_bytes,
    )


@router.message(ShamCashUploadState.waiting_for_receipt, F.document)
async def handle_receipt_pdf(message: Message, state: FSMContext):
    doc = message.document
    mime = (doc.mime_type or "").lower()
    name = (doc.file_name or "").lower()
    if mime != "application/pdf" and not name.endswith(".pdf"):
        await message.answer(
            "⚠️ يُقبل فقط ملف PDF أو صورة للإيصال. أعد إرسال PDF أو صورة الإيصال:"
        )
        return
    if doc.file_size and int(doc.file_size) > _SHAM_RECEIPT_MAX_BYTES:
        await message.answer("⚠️ الملف كبير جداً (الحد 10MB). أرسل PDF أصغر.")
        return
    file_bytes = None
    try:
        file_obj = await message.bot.get_file(doc.file_id)
        file_stream = await message.bot.download_file(file_obj.file_path)
        file_bytes = file_stream.read()
        if file_bytes and len(file_bytes) > _SHAM_RECEIPT_MAX_BYTES:
            await message.answer("⚠️ الملف كبير جداً (الحد 10MB). أرسل PDF أصغر.")
            return
    except Exception as e:
        logger.warning(f"Could not compute PDF hash: {e}")
    await _finalize_sham_receipt(
        message,
        state,
        file_id=doc.file_id,
        file_unique_id=doc.file_unique_id,
        receipt_type="pdf",
        file_bytes=file_bytes,
    )


@router.message(ShamCashUploadState.waiting_for_receipt)
async def handle_receipt_non_photo(message: Message, state: FSMContext):
    """Reject plain text/stickers; keep FSM — never fall through to AI concierge."""
    from src.bot.keyboards.inline import sham_cash_receipt_prompt_keyboard

    data = await state.get_data()
    order_id = data.get("order_id") or ""
    lang = "ar"
    async with AsyncSessionLocal() as session:
        cust = await get_or_create_customer(session, message.from_user)
        lang = "en" if str(cust.language_code or "").lower().startswith("en") else "ar"
    text = (
        "Please upload a Sham Cash PDF receipt or a receipt photo only (or tap Cancel). 📎"
        if lang == "en"
        else "يرجى إرسال ملف PDF أو صورة إيصال شام كاش فقط (أو اضغط إلغاء) 📎"
    )
    await message.answer(
        text,
        reply_markup=sham_cash_receipt_prompt_keyboard(order_id=order_id, lang=lang),
    )


# ---------------- Binance Pay Order ID + payment help guides ----------------
@router.callback_query(F.data.startswith("binance_oid:"))
async def cb_binance_oid_prompt(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":", 1)[1]
    await state.set_state(BinancePayOrderState.waiting_order_id)
    await state.update_data(order_id=order_id)
    await callback.message.edit_text(
        "🟡 **الصق Pay Order ID هنا الآن**\n\n"
        "بعد التحويل من تطبيق Binance، انسخ Order ID / Transaction ID وأرسله في رسالة.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ رجوع", callback_data=f"pay:binance_pay:{order_id}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payhelp:"))
async def cb_pay_help(callback: CallbackQuery):
    parts = callback.data.split(":")
    kind = parts[1] if len(parts) > 1 else ""
    order_id = parts[2] if len(parts) > 2 else ""
    if kind == "binance":
        text = (
            "❓ **أين أجد Order ID في Binance Pay؟**\n\n"
            "1) افتح تطبيق Binance → Pay / المحفظة\n"
            "2) سجل المعاملات → اختر التحويل\n"
            "3) انسخ **Order ID** أو **Transaction ID**\n"
            "4) الصقه هنا في البوت للتحقق"
        )
        back = f"pay:binance_pay:{order_id}" if order_id else "nav:home"
    else:
        network = parts[3] if len(parts) > 3 else "bep20"
        text = (
            "❓ **أين أجد TxID؟**\n\n"
            "1) من محفظتك أو المستكشف (BscScan / Tronscan)\n"
            "2) افتح تفاصيل التحويل\n"
            "3) انسخ **Transaction Hash / TxID**\n"
            f"4) تأكد أن الشبكة صحيحة ({network.upper()})"
        )
        back = f"pay_options:{order_id}" if order_id else "nav:home"
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="↩️ رجوع", callback_data=back)],
        ]),
    )
    await callback.answer()


@router.message(BinancePayOrderState.waiting_order_id, F.text)
async def handle_binance_pay_order_id(message: Message, state: FSMContext):
    pay_oid = (message.text or "").strip()
    if len(pay_oid) < 4:
        await message.answer("⚠️ Order ID قصير جداً. أعد الإرسال أو اضغط إلغاء.")
        return
    data = await state.get_data()
    order_id = data.get("order_id")
    try:
        await message.delete()
    except Exception:
        pass

    from src.outgest.binance_pay_reader import binance_pay_reader
    from src.core.services.payment_verification import payment_verification_service

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(
                selectinload(Order.invoice),
                selectinload(Order.customer),
                selectinload(Order.master_product),
                selectinload(Order.supplier),
            )
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order or not order.invoice:
            await state.clear()
            await message.answer("الطلب غير موجود.")
            return

        invoice = order.invoice
        invoice.binance_pay_order_id = pay_oid
        invoice.payment_method = PaymentMethod.BINANCE_PAY

        # Auto-verify via Spot API when configured
        if binance_pay_reader.is_configured:
            try:
                looked = await binance_pay_reader.find_by_pay_order_id(pay_oid)
                if looked:
                    amount = Decimal(str(looked["amount"]))
                    expected = invoice.unique_amount or invoice.amount_expected
                    if expected and abs(amount - Decimal(str(expected))) <= Decimal("0.02"):
                        await payment_verification_service.confirm_binance_by_pay_order_id(
                            session,
                            pay_order_id=pay_oid,
                            amount=amount,
                            order_id=order.id,
                            reviewer="customer_bot_auto",
                        )
                        await state.clear()
                        await message.answer(
                            f"✅ تم التحقق تلقائياً من Binance Pay!\n"
                            f"🔖 الطلب `{order.order_number}` — جاري التسليم الفوري...",
                            parse_mode="HTML",
                            reply_markup=main_menu_keyboard(),
                        )
                        return
            except Exception as e:
                logger.warning(f"Binance auto-verify failed: {e}")

        await session.commit()
        cust = order.customer
        await publish_manual_review_alert({
            "order_id": str(order.id),
            "order_number": order.order_number,
            "review_kind": "binance_pay",
            "supplier_name": order.supplier.name if order.supplier else None,
            "customer_telegram_id": cust.telegram_id if cust else None,
            "customer_username": cust.telegram_username if cust else None,
            "customer_name": (cust.first_name if cust else None) or "",
            "product_title": order.master_product.title_ar if order.master_product else None,
            "quantity": int(order.quantity or 1),
            "amount": float(order.our_price or 0),
            "binance_pay_order_id": pay_oid,
        })

    await state.clear()
    await message.answer(
        "⏳ تم استلام Order ID بنجاح! فريقنا يراجع طلبك الآن "
        "(عادةً خلال 5–15 دقيقة) وسيتم تسليم منتجك فوراً — شكراً لثقتك بنا.",
        reply_markup=main_menu_keyboard(),
    )


# ---------------- Cancel Pending Order Callback (For Rate-Limiter Quota Relief) ----------------
@router.callback_query(F.data.startswith("cancel_order:"))
async def cb_cancel_pending_order(callback: CallbackQuery):
    order_id = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        caller = await get_or_create_customer(session, callback.from_user)
        stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(selectinload(Order.invoice))
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if order and order.customer_id != caller.id:
            await callback.answer("هذا الطلب لا يخص حسابك.", show_alert=True)
            return
        if order and order.state in (OrderState.CREATED, OrderState.AWAITING_PAYMENT, OrderState.SHAM_CASH_REVIEW):
            refund_msg = ""
            if order.wallet_portion_reserved and order.wallet_portion_reserved > Decimal("0.0"):
                await order_service.credit_customer_wallet(
                    session=session,
                    customer_id=str(order.customer_id),
                    amount=order.wallet_portion_reserved,
                    tx_type=WalletTxType.SPLIT_PAYMENT_REFUND,
                    description=f"إرجاع الرصيد المحجوز بعد إلغاء الطلب الجزئي #{order.order_number}",
                    order_id=str(order.id)
                )
                refund_msg = f"\n💰 تم إعادة `${order.wallet_portion_reserved:.2f}` المحجوزة إلى محفظتك بنجاح!"
                order.wallet_portion_reserved = Decimal("0.0")

            if order.invoice:
                order.invoice.status = InvoiceStatus.EXPIRED

            await order_service.transition_order_state(
                session, order, OrderState.CANCELLED, "customer_cancelled_pending_order"
            )
            await session.commit()
            await callback.answer("تم إلغاء الطلب المعلق بنجاح ✅", show_alert=True)
            await callback.message.edit_text(
                f"❌ تم إلغاء الطلب #{order.order_number}.{refund_msg}\nيمكنك الآن إجراء طلبك الجديد بكل سهولة.",
                reply_markup=main_menu_keyboard()
            )
        else:
            await callback.answer("لا يمكن إلغاء هذا الطلب أو أنه تم تنفيذه مسبقاً.", show_alert=True)


# Admin Sham Cash approve/reject lives ONLY on Admin Bot
# (adm:claim: / adm_sc_ok: / adm_sc_no: via sham_cash_review_keyboard).


# =========================================================================
# Phase 8B: UX Enhancements, Instant Search, FAQ, Language, Top-Up, Coupons, Reviews
# =========================================================================

# ---------------- 1. Instant Search ----------------
@router.callback_query(F.data == "nav:search")
async def cb_search_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SearchProductState.waiting_for_query)
    text = (
        "🔍 **البحث السريع عن المنتجات والاشتراكات:**\n\n"
        "أرسل اسم المنتج أو الخدمة التي تبحث عنها (مثال: `ChatGPT`, `Canva`, `VPN`, `Windows`):\n\n"
        "🔙 أو اضغط أدناه للإلغاء والعودة."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 إلغاء والعودة للرئيسية", callback_data="nav:home")]
        ]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.message(SearchProductState.waiting_for_query, F.text)
async def process_search_query(message: Message, state: FSMContext):
    query = message.text.strip()
    if len(query) < 2:
        await message.answer("⚠️ يرجى إدخال كلمة بحث مكونة من حرفين على الأقل:")
        return

    await state.clear()
    async with AsyncSessionLocal() as session:
        from sqlalchemy import or_
        pattern = f"%{query}%"
        stmt = (
            select(MasterProduct)
            .where(
                and_(
                    MasterProduct.is_active == True,
                    or_(
                        MasterProduct.title_ar.ilike(pattern),
                        MasterProduct.title_en.ilike(pattern),
                        MasterProduct.description_ar.ilike(pattern),
                        MasterProduct.custom_description_ar.ilike(pattern),
                        MasterProduct.brand_family.ilike(pattern),
                        MasterProduct.search_keywords.ilike(pattern),
                    )
                )
            )
            .limit(20)
        )
        products = list((await session.execute(stmt)).scalars().all())

        if not products:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔍 بحث مجدداً", callback_data="nav:search")],
                    [InlineKeyboardButton(text="🛍️ تصفح الأقسام", callback_data="nav:categories")],
                    [InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="nav:home")],
                ]
            )
            await message.answer(
                f"❌ لم يتم العثور على أي نتائج مطابقة لـ: `{query}`\n"
                f"جرّب البحث بكلمة أخرى أو تصفح الأقسام.",
                parse_mode="HTML",
                reply_markup=kb
            )
            return

        await _render_search_results(message, session, products, query, in_stock_only=False, cheapest=False)


async def _render_search_results(message_or_cb, session, products, query, *, in_stock_only, cheapest):
    scored = []
    for p in products:
        pricing_info = await pricing_service.get_best_supplier_and_price(session, str(p.id))
        final_p = pricing_info.get("final_price", Decimal("0.0")) or Decimal("0")
        is_stock = pricing_info.get("is_available", False)
        if in_stock_only and not is_stock:
            continue
        scored.append((p, final_p, is_stock))
    if cheapest:
        scored.sort(key=lambda x: x[1])
    buttons = []
    for p, final_p, is_stock in scored[:8]:
        stock_badge = "🟢" if is_stock else "🔴 نفد"
        badge = getattr(p, "delivery_type_label", None) or ""
        buttons.append([
            InlineKeyboardButton(
                text=f"{p.title_ar} - ${final_p} {stock_badge} {badge}".strip()[:64],
                callback_data=f"prod:{p.id}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="✅ المتوفر فقط" + (" ✓" if in_stock_only else ""),
            callback_data=f"searchf:stock:{query[:40]}",
        ),
        InlineKeyboardButton(
            text="💰 الأرخص أولاً" + (" ✓" if cheapest else ""),
            callback_data=f"searchf:cheap:{query[:40]}",
        ),
    ])
    buttons.append([InlineKeyboardButton(text="🔍 بحث آخر", callback_data="nav:search")])
    buttons.append([InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="nav:home")])
    text = (
        f"🔍 **نتائج البحث عن:** `{query}` ({len(scored)} نتيجة):\n\n"
        f"اضغط على المنتج لعرض التفاصيل والشراء الفوري:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    if isinstance(message_or_cb, CallbackQuery):
        await message_or_cb.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await message_or_cb.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("searchf:"))
async def cb_search_filter(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    mode = parts[1] if len(parts) > 1 else "stock"
    query = parts[2] if len(parts) > 2 else ""
    async with AsyncSessionLocal() as session:
        from sqlalchemy import or_
        pattern = f"%{query}%"
        products = list((await session.execute(
            select(MasterProduct).where(
                and_(
                    MasterProduct.is_active == True,
                    or_(
                        MasterProduct.title_ar.ilike(pattern),
                        MasterProduct.title_en.ilike(pattern),
                        MasterProduct.brand_family.ilike(pattern),
                        MasterProduct.search_keywords.ilike(pattern),
                    )
                )
            ).limit(20)
        )).scalars().all())
        await _render_search_results(
            callback, session, products, query,
            in_stock_only=(mode == "stock"),
            cheapest=(mode == "cheap"),
        )
        await callback.answer()


# ---------------- 2. FAQ & Terms ----------------
@router.callback_query(F.data == "nav:faq")
async def cb_faq(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        faq_text = await get_dynamic_config(
            session,
            "terms_and_faq_ar",
            "📜 **الشروط وسياسة الضمان:**\n\n"
            "1️⃣ **التسليم الفوري:** يتم تسليم المفاتيح والحسابات الرقمية آلياً وفور تأكيد الدفع عبر البوت خلال ثوانٍ معدودة.\n\n"
            "2️⃣ **الضمان الشامل:** كافة المنتجات مشمولة بضمان تشغيلي كامل وفق المدة المحددة لكل منتج (30 يوم إلى سنة).\n\n"
            "3️⃣ **الدعم الفني والنزاعات:** في حال مواجهة أي مشكلة في التفعيل، يمكنك فتح تذكرة فورية بالضغط على زر '⚠️ مشكلة في الطلب' وسيتم تعويضك أو استرداد رصيدك.\n\n"
            "4️⃣ **سياسة الاسترجاع:** إذا تعذر توفير المنتج من المورد، يتم استرداد المبلغ فوراً وتلقائياً إلى محفظتك بالبوت دون أي رسوم."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛍️ تصفح المنتجات الآن", callback_data="nav:categories")],
                [InlineKeyboardButton(text="🔙 العودة للقائمة الرئيسية", callback_data="nav:home")],
            ]
        )
        await callback.message.edit_text(faq_text, parse_mode="HTML", reply_markup=kb)


# ---------------- 3. Language Preference Toggle ----------------
@router.callback_query(F.data == "nav:toggle_lang")
async def cb_toggle_lang(callback: CallbackQuery):
    from src.bot.i18n import t, normalize_lang
    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        new_lang = "ar" if normalize_lang(customer.language_code) == "en" else "en"
        customer.language_code = new_lang
        await session.commit()
        await callback.answer(t(new_lang, "lang_switched"), show_alert=False)
        text, kb = await render_welcome_hub(customer, session)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ---------------- 4. Multi-Method Wallet Top-Up ----------------
@router.callback_query(F.data.startswith("topup_amt:"))
async def cb_topup_preset(callback: CallbackQuery):
    amount_str = callback.data.split(":")[1]
    amount = float(amount_str)

    async with AsyncSessionLocal() as session:
        # Check if eligible for any bonus tier
        tier_stmt = (
            select(TopUpBonusTier)
            .where(TopUpBonusTier.min_deposit_usd <= Decimal(str(amount)))
            .order_by(TopUpBonusTier.min_deposit_usd.desc())
            .limit(1)
        )
        tier = (await session.execute(tier_stmt)).scalar_one_or_none()
        bonus_msg = ""
        if tier:
            bonus_msg = f"\n🎁 **بونص إضافي حصري:** ستحصل على `+${tier.bonus_usd:.2f} USD` هدية تضاف لرصيدك تلقائياً عند تأكيد الإيداع!"

        text = (
            f"💳 **شحن رصيد المحفظة بمبلغ: `${amount:.2f} USD`**\n"
            f"{bonus_msg}\n\n"
            f"اختر وسيلة الدفع التي تناسبك لإتمام الشحن:"
        )
        rails = await payment_rail_flags(session)
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=topup_methods_keyboard(amount, **rails),
        )


@router.callback_query(F.data == "topup_custom")
async def cb_topup_custom(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TopUpCustomAmountState.waiting_for_amount)
    text = (
        "🔢 **شحن مبلغ مخصص:**\n\n"
        "أرسل المبلغ الذي ترغب بشحنه بالدولار (مثال: `15` أو `75` أو `150`):\n"
        "*(الحد الأدنى للشحن: $1.00 USD)*\n\n"
        "🔙 أو اضغط أدناه للإلغاء:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 إلغاء", callback_data="user:wallet")]
        ]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.message(TopUpCustomAmountState.waiting_for_amount, F.text)
async def process_topup_custom_amount(message: Message, state: FSMContext):
    val_str = message.text.strip().replace("$", "")
    try:
        amount = float(val_str)
        if amount < 1.0:
            await message.answer("⚠️ الحد الأدنى للشحن هو $1.00 USD. يرجى إدخال مبلغ صالح:")
            return
    except ValueError:
        await message.answer("⚠️ يرجى إدخال رقم صحيح أو عشري صالح (مثال: `20` أو `35.5`):")
        return

    await state.clear()
    async with AsyncSessionLocal() as session:
        tier_stmt = (
            select(TopUpBonusTier)
            .where(TopUpBonusTier.min_deposit_usd <= Decimal(str(amount)))
            .order_by(TopUpBonusTier.min_deposit_usd.desc())
            .limit(1)
        )
        tier = (await session.execute(tier_stmt)).scalar_one_or_none()
        bonus_msg = ""
        if tier:
            bonus_msg = f"\n🎁 **بونص إضافي حصري:** ستحصل على `+${tier.bonus_usd:.2f} USD` هدية مجانية مع هذا الإيداع!"

        text = (
            f"💳 **شحن رصيد المحفظة بمبلغ: `${amount:.2f} USD`**\n"
            f"{bonus_msg}\n\n"
            f"اختر وسيلة الدفع التي تناسبك لإتمام الشحن:"
        )
        rails = await payment_rail_flags(session)
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=topup_methods_keyboard(amount, **rails),
        )


@router.callback_query(F.data.startswith("topup_pay:"))
async def cb_topup_payment_details(callback: CallbackQuery):
    parts = callback.data.split(":")
    method = parts[1]
    amount = float(parts[2])

    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        
        # Check tier bonus
        tier_stmt = (
            select(TopUpBonusTier)
            .where(TopUpBonusTier.min_deposit_usd <= Decimal(str(amount)))
            .order_by(TopUpBonusTier.min_deposit_usd.desc())
            .limit(1)
        )
        tier = (await session.execute(tier_stmt)).scalar_one_or_none()
        bonus_note = f" (بالإضافة إلى بونص +${tier.bonus_usd:.2f})" if tier else ""

        if method == "bep20":
            wallet_addr = settings.HOT_WALLET_ADDRESS or "0x..."
            text = (
                f"💎 **إيداع USDT (شبكة BNB Smart Chain - BEP-20):**\n\n"
                f"💵 **المبلغ المطلوب إرساله:** `${amount:.2f} USDT`{bonus_note}\n\n"
                f"📬 **عنوان المحفظة للإيداع:**\n`{wallet_addr}`\n\n"
                f"⚠️ **ملاحظة هامة:** تأكد من اختيار شبكة **BEP-20 (BSC)** حصراً عند التحويل.\n"
                f"يتم قيد الرصيد آلياً وفورياً بعد تأكيد الشبكة خلال دقائق! ⚡"
            )
        elif method == "trc20":
            tron_wallet = getattr(settings, "TRON_HOT_WALLET_ADDRESS", None) or "T..."
            text = (
                f"💎 **إيداع USDT (شبكة TRON - TRC-20):**\n\n"
                f"💵 **المبلغ المطلوب إرساله:** `${amount:.2f} USDT`{bonus_note}\n\n"
                f"📬 **عنوان محفظة TRON:**\n`{tron_wallet}`\n\n"
                f"⚠️ تأكد من اختيار شبكة **TRC-20** حصراً. يُقيد الرصيد فور تأكيد المعاملة ⚡"
            )
        elif method == "binance":
            binance_id = (settings.BINANCE_PAY_MERCHANT_ID or "").strip()
            if not binance_id:
                text = (
                    "🟡 **Binance Pay غير مهيأ حالياً.**\n"
                    "يرجى اختيار وسيلة إيداع أخرى أو التواصل مع الدعم."
                )
            else:
                text = (
                    f"🟡 **إيداع عبر Binance Pay (بدون رسوم تحويل 0%):**\n\n"
                    f"💵 **المبلغ:** `${amount:.2f} USDT`{bonus_note}\n"
                    f"🆔 **معرف بينانس (Binance Pay ID):** `{binance_id}`\n\n"
                    f"بعد التحويل يُقيَّد الرصيد آلياً. إن تأخر، أرسل Pay Order ID للدعم."
                )
        else:  # sham_cash — QR card (top-up still needs support for credit until dedicated flow)
            from src.core.services.sham_cash_qr import build_sham_cash_caption, resolve_qr_photo_input, get_sham_cash_payment_config
            cfg = await get_sham_cash_payment_config(session)
            caption = await build_sham_cash_caption(session, amount_usd=amount, lang="ar")
            caption = (
                f"{caption}\n\n"
                f"🎁 {bonus_note.strip()}\n"
                f"بعد التحويل تواصل مع الدعم مع صورة الإشعار لتقييد الرصيد."
                if bonus_note else
                f"{caption}\n\nبعد التحويل تواصل مع الدعم مع صورة الإشعار لتقييد الرصيد."
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🛟 التواصل مع الدعم الفني", callback_data="user:support")],
                    [InlineKeyboardButton(text="🔙 العودة للمحفظة", callback_data="user:wallet")],
                ]
            )
            await callback.answer()
            photo, _ = await resolve_qr_photo_input(session)
            try:
                await callback.message.delete()
            except Exception:
                pass
            if photo is not None:
                try:
                    await callback.bot.send_photo(
                        chat_id=callback.from_user.id,
                        photo=photo,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
                    return
                except Exception as e:
                    logger.warning(f"Top-up Sham QR failed: {e}")
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
            return

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🛟 التواصل مع الدعم الفني", callback_data="user:support")],
                [InlineKeyboardButton(text="🔙 العودة للمحفظة", callback_data="user:wallet")],
            ]
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


# ---------------- 5. Coupon System & Checkout Discount ----------------
@router.callback_query(F.data.startswith("apply_coupon:"))
async def cb_apply_coupon_prompt(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":")[1]
    await state.set_state(ApplyCouponState.waiting_for_code)
    await state.update_data(order_id=order_id)

    text = (
        "🎟️ **تطبيق كود الخصم (الكوبون):**\n\n"
        "أرسل كود الخصم في رسالة الآن (مثال: `VIP20`, `WELCOME10`):\n\n"
        "🔙 أو اضغط أدناه للعودة لخيارات الدفع:"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 إلغاء والعودة", callback_data=f"pay_options:{order_id}")]
        ]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.message(ApplyCouponState.waiting_for_code, F.text)
async def process_coupon_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    data = await state.get_data()
    order_id = data.get("order_id")

    if not order_id:
        await state.clear()
        await message.answer("⚠️ انتهت الجلسة. يرجى بدء الطلب مجدداً.")
        return

    async with AsyncSessionLocal() as session:
        # Fetch coupon + order under row locks (prevents max_uses races)
        now_utc = datetime.now(timezone.utc)
        stmt = (
            select(Coupon)
            .where(Coupon.code == code, Coupon.is_active == True)  # noqa: E712
            .with_for_update()
        )
        coupon = (await session.execute(stmt)).scalar_one_or_none()

        if not coupon:
            await message.answer("❌ كود الخصم غير صحيح أو غير مفعّل. يرجى التأكد وإعادة المحاولة:")
            return

        def _as_utc(value):
            if value is None:
                return None
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

        if coupon.valid_from and now_utc < _as_utc(coupon.valid_from):
            await message.answer("⚠️ هذا الكوبون لم يبدأ تفعيله بعد.")
            return

        if coupon.valid_until and now_utc > _as_utc(coupon.valid_until):
            await message.answer("⚠️ انتهت صلاحية هذا الكوبون.")
            return

        if coupon.max_uses and (coupon.used_count or 0) >= coupon.max_uses:
            await message.answer("⚠️ وصل هذا الكوبون إلى الحد الأقصى من مرات الاستخدام.")
            return

        order_stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(selectinload(Order.master_product))
            .with_for_update()
        )
        order = (await session.execute(order_stmt)).scalar_one_or_none()
        if not order:
            await state.clear()
            await message.answer("⚠️ لم يتم العثور على الطلب.")
            return

        if order.coupon_id:
            await state.clear()
            await message.answer("⚠️ تم تطبيق كوبون خصم على هذا الطلب بالفعل مسبقاً.")
            return

        # Calculate discount
        if coupon.discount_type == DiscountType.PERCENT:
            discount = (order.our_price * Decimal(str(coupon.discount_value / 100))).quantize(Decimal("0.01"))
        else:
            discount = Decimal(str(coupon.discount_value)).quantize(Decimal("0.01"))

        # Profit Circuit Breaker: prevent selling below cost + 0.10
        min_allowed_price = (order.supplier_cost or Decimal("0.0")) + Decimal("0.10")
        if order.our_price - discount < min_allowed_price:
            discount = max(Decimal("0.0"), order.our_price - min_allowed_price)

        if discount <= Decimal("0.0"):
            await state.clear()
            await message.answer("⚠️ لا يمكن تطبيق هذا الكوبون على هذا المنتج لحماية هامش التكلفة الأدنى.")
            return

        # Apply discount
        order.discount_amount = discount
        order.our_price = (order.our_price - discount).quantize(Decimal("0.01"))
        order.coupon_id = coupon.id
        coupon.used_count = (coupon.used_count or 0) + 1
        await session.commit()
        await state.clear()

        customer = await get_or_create_customer(session, message.from_user)

        card_text = (
            f"🎉 **تم تطبيق كود الخصم `{code}` بنجاح!**\n"
            f"🏷️ قيمة الخصم: `-${discount:.2f} USD`\n"
            f"─────────────────\n"
            f"🛒 **الطلب المُحدث:** `#{order.order_number}`\n"
            f"📦 **المنتج:** {order.master_product.title_ar}\n"
            f"🔢 **الكمية:** `{order.quantity or 1}`\n"
            f"💰 **المبلغ المطلوب بعد الخصم:** `${order.our_price}` USD\n"
            f"👛 **رصيدك الحالي:** `${customer.wallet_balance:.2f}` USD\n\n"
            f"يرجى اختيار وسيلة الدفع لإتمام الشراء:"
        )
        rails = await payment_rail_flags(session)
        kb = payment_methods_keyboard(
            order_id=str(order.id),
            total_price=float(order.our_price),
            has_wallet_balance=customer.wallet_balance > 0,
            wallet_balance=float(customer.wallet_balance),
            **rails,
        )
        await message.answer(card_text, parse_mode="HTML", reply_markup=kb)


# ---------------- 6. Verified 1-Click Reviews ----------------
@router.callback_query(F.data.startswith("rate:"))
async def cb_rate_order(callback: CallbackQuery):
    parts = callback.data.split(":")
    order_id = parts[1]
    stars = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 5

    async with AsyncSessionLocal() as session:
        customer = await get_or_create_customer(session, callback.from_user)
        
        # Check if already reviewed
        rev_stmt = select(CustomerReview).where(CustomerReview.order_id == order_id)
        existing_rev = (await session.execute(rev_stmt)).scalar_one_or_none()
        if existing_rev:
            await callback.answer("⭐ شكراً لك! لقد قمت بتقييم هذا الطلب مسبقاً.", show_alert=True)
            return

        # Fetch Order details
        order_stmt = (
            select(Order)
            .where(Order.id == _as_uuid(order_id))
            .options(selectinload(Order.master_product))
        )
        order = (await session.execute(order_stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return

        review = CustomerReview(
            order_id=order.id,
            customer_id=customer.id,
            rating=stars,
            comment=f"تقييم سريع {stars} نجوم من العميل",
            is_published_to_channel=stars >= 4,
        )
        session.add(review)
        await session.commit()

        # If 4 or 5 stars, broadcast verified review card to social proof channel
        if stars >= 4:
            try:
                prod_title = order.master_product.title_ar if order.master_product else "اشتراك رقمي"
                prod_emoji = getattr(order.master_product, "custom_emoji", "⭐") or "⭐"
                await event_bus.publish(
                    "channel:broadcast_proof",
                    {
                        "type": "verified_review",
                        "order_number": order.order_number,
                        "customer_username": customer.telegram_username,
                        "customer_first_name": customer.first_name,
                        "product_title": prod_title,
                        "product_emoji": prod_emoji,
                        "rating": stars,
                        "comment": "تسليم فوري وممتاز!",
                    }
                )
            except Exception as b_err:
                logger.warning(f"Failed to publish verified review to proof channel: {b_err}")

        stars_display = "⭐" * stars
        await callback.answer(f"شكراً جزيلاً لتقييمك! ({stars_display})", show_alert=True)
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"✅ تم تسجيل تقييمك ({stars_display})", callback_data="noop")],
                    [InlineKeyboardButton(text="🛍️ تصفح منتجات أخرى", callback_data="nav:categories")],
                    [InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="nav:home")],
                ]
            )
        )
