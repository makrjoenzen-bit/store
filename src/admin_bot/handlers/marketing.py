"""Admin Bot — coupons + safe throttled broadcast."""
from __future__ import annotations

from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from src.core.database import AsyncSessionLocal
from src.core.models.enums import DiscountType
from src.core.services.marketing_admin_service import marketing_admin_service

router = Router(name="admin_marketing")


class CouponFSM(StatesGroup):
    code = State()
    type_value = State()
    max_uses = State()


class BroadcastFSM(StatesGroup):
    audience = State()
    message = State()
    confirm = State()


def _hub_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 الكوبونات", callback_data="adm:mkt:coupons")],
        [InlineKeyboardButton(text="➕ كوبون جديد", callback_data="adm:mkt:coupon:new")],
        [InlineKeyboardButton(text="🎁 حملات الدعوة المجانية", callback_data="adm:mkt:ref")],
        [InlineKeyboardButton(text="📣 إنشاء إعلان", callback_data="adm:mkt:promo")],
        [InlineKeyboardButton(text="📢 بث آمن", callback_data="adm:mkt:bcast")],
        [InlineKeyboardButton(text="🏠 القائمة", callback_data="adm:home")],
    ])


class RefCampaignFSM(StatesGroup):
    stock_upload = State()
    set_invites = State()


class PromoAdFSM(StatesGroup):
    photo = State()
    caption = State()
    product = State()
    destinations = State()
    confirm = State()


@router.message(F.text == "📢 التسويق والبث")
@router.callback_query(F.data == "adm:marketing")
async def marketing_hub(event: Message | CallbackQuery, state: FSMContext):
    await state.clear()
    text = "📢 **التسويق والكوبونات والبث**"
    kb = _hub_kb()
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data == "adm:mkt:coupons")
async def list_coupons(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        coupons = await marketing_admin_service.list_coupons(session)
    if not coupons:
        await callback.message.edit_text("لا توجد كوبونات.", reply_markup=_hub_kb())
        await callback.answer()
        return
    buttons = []
    for c in coupons[:20]:
        flag = "🟢" if c["is_active"] else "⚪️"
        buttons.append([InlineKeyboardButton(
            text=f"{flag} {c['code']} · {c['discount_type']}={c['value']} · {c['used_count']}/{c['max_uses'] or '∞'}",
            callback_data=f"adm:mkt:coupon:tog:{c['id']}",
        )])
    buttons.append([InlineKeyboardButton(text="🔙", callback_data="adm:marketing")])
    await callback.message.edit_text(
        "🎟 **الكوبونات** (اضغط للتبديل تفعيل/إيقاف):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:mkt:coupon:tog:"))
async def toggle_coupon(callback: CallbackQuery):
    cid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        result = await marketing_admin_service.toggle_coupon(
            session, cid, reviewer=f"tg:{callback.from_user.id}"
        )
    await callback.answer(
        f"{result['code']} → {'مفعّل' if result['is_active'] else 'موقوف'}",
        show_alert=False,
    )
    await list_coupons(callback)


@router.callback_query(F.data == "adm:mkt:coupon:new")
async def coupon_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CouponFSM.code)
    await callback.message.edit_text("🎟 أرسل **كود الكوبون** (مثال: VIP20):", parse_mode="Markdown")
    await callback.answer()


@router.message(CouponFSM.code)
async def coupon_code(message: Message, state: FSMContext):
    await state.update_data(code=(message.text or "").strip())
    await state.set_state(CouponFSM.type_value)
    await message.answer(
        "أرسل النوع والقيمة:\n`PERCENT:15` أو `FIXED:5`",
        parse_mode="Markdown",
    )


@router.message(CouponFSM.type_value)
async def coupon_type(message: Message, state: FSMContext):
    raw = (message.text or "").strip().upper()
    try:
        kind, val = raw.split(":", 1)
        dtype = DiscountType.PERCENT if kind.startswith("PERCENT") else DiscountType.FIXED
        value = Decimal(val)
    except Exception:
        await message.answer("صيغة غير صالحة.")
        return
    await state.update_data(discount_type=dtype.value, value=str(value))
    await state.set_state(CouponFSM.max_uses)
    await message.answer("أقصى عدد استخدامات؟ (رقم أو `0` لغير محدود)")


@router.message(CouponFSM.max_uses)
async def coupon_save(message: Message, state: FSMContext):
    data = await state.get_data()
    raw = (message.text or "0").strip()
    max_uses = None if raw in ("0", "", "∞") else int(raw)
    try:
        async with AsyncSessionLocal() as session:
            result = await marketing_admin_service.create_coupon(
                session,
                code=data["code"],
                discount_type=DiscountType(data["discount_type"]),
                value=Decimal(data["value"]),
                max_uses=max_uses,
                reviewer=f"tg:{message.from_user.id}",
            )
        await state.clear()
        await message.answer(f"✅ كوبون `{result['code']}` جاهز.", parse_mode="Markdown")
    except ValueError as e:
        await message.answer(str(e))
        await state.clear()


@router.callback_query(F.data == "adm:mkt:bcast")
async def bcast_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastFSM.audience)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="الجميع", callback_data="adm:mkt:aud:all")],
        [InlineKeyboardButton(text="المشترين", callback_data="adm:mkt:aud:buyers_only")],
        [InlineKeyboardButton(text="ذوي الرصيد", callback_data="adm:mkt:aud:balance_holders")],
        [InlineKeyboardButton(text="🔙", callback_data="adm:marketing")],
    ])
    await callback.message.edit_text("📢 اختر الجمهور المستهدف:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:mkt:aud:"))
async def bcast_audience(callback: CallbackQuery, state: FSMContext):
    aud = callback.data.split(":")[-1]
    await state.update_data(audience=aud)
    await state.set_state(BroadcastFSM.message)
    await callback.message.edit_text("✍️ أرسل نص البث أو صورة مع تعليق:")
    await callback.answer()


@router.message(BroadcastFSM.message)
async def bcast_preview(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id if message.photo else None
    caption = (message.caption or message.text or "").strip()
    await state.update_data(message_text=caption, photo_file_id=photo_id)
    data = await state.get_data()
    await state.set_state(BroadcastFSM.confirm)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 تأكيد الإرسال (~25 ر/ث)", callback_data="adm:mkt:bcast:go")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="adm:marketing")],
    ])
    labels = {
        "all": "الجميع",
        "buyers_only": "المشترين",
        "balance_holders": "ذوي الرصيد",
    }
    preview = (
        f"📋 <b>معاينة البث</b>\n\n"
        f"الجمهور: <b>{labels.get(data['audience'], data['audience'])}</b>\n"
        f"{'🖼 مع صورة' if photo_id else 'نص فقط'}\n\n"
        f"{caption or '—'}\n\n"
        f"هل تريد الإرسال؟"
    )
    if photo_id:
        await message.answer_photo(photo_id, caption=preview, parse_mode="HTML", reply_markup=kb)
    else:
        await message.answer(preview, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "adm:mkt:bcast:go")
async def bcast_go(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    try:
        async with AsyncSessionLocal() as session:
            result = await marketing_admin_service.create_broadcast(
                session,
                title=f"AdminBot {callback.from_user.id}",
                message_text=data.get("message_text") or "",
                target_audience=data.get("audience") or "all",
                reviewer=f"tg:{callback.from_user.id}",
                launch=True,
                photo_file_id=data.get("photo_file_id"),
            )
        await state.clear()
        done = (
            f"✅ تم إطلاق البث `{result['campaign_id'][:8]}` بمعدل ~25 رسالة/ثانية."
        )
        try:
            await callback.message.edit_text(done, parse_mode="Markdown", reply_markup=_hub_kb())
        except Exception:
            await callback.message.answer(done, parse_mode="Markdown", reply_markup=_hub_kb())
        await callback.answer()
    except ValueError as e:
        await callback.answer(str(e)[:160], show_alert=True)
        await state.clear()


# ---------------- Visual Promo Ad Creator ----------------
@router.callback_query(F.data == "adm:mkt:promo")
async def promo_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PromoAdFSM.photo)
    await state.update_data(
        image_file_id=None,
        publish_proof=True,
        publish_main=True,
        broadcast_customers=False,
        pin=False,
        product_id=None,
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 توليد صورة إعلان تلقائياً", callback_data="adm:mkt:promo:autogen")],
        [InlineKeyboardButton(text="⏭ تخطي الصورة", callback_data="adm:mkt:promo:skip_photo")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="adm:marketing")],
    ])
    await callback.message.edit_text(
        "📣 **إنشاء إعلان مرئي**\n\nأرسل صورة الإعلان الآن، أو ولّدها تلقائياً، أو تخطَّ:",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data == "adm:mkt:promo:autogen")
async def promo_autogen_image(callback: CallbackQuery, state: FSMContext):
    """One-click Pillow template → send PNG preview into the promo FSM."""
    import tempfile
    from pathlib import Path
    from src.core.services.ad_image_generator import ad_image_generator
    from src.core.models.entities import MasterProduct
    from src.core.services.pricing_service import pricing_service
    from src.core.database import AsyncSessionLocal

    data = await state.get_data()
    product_id = data.get("product_id")
    title = "عرض خاص"
    price = 9.99
    warranty = 30
    async with AsyncSessionLocal() as session:
        if product_id:
            from sqlalchemy import select
            prod = (await session.execute(
                select(MasterProduct).where(MasterProduct.id == product_id)
            )).scalar_one_or_none()
            if prod:
                title = prod.title_ar
                warranty = int(prod.warranty_days or 30)
                pricing = await pricing_service.get_best_supplier_and_price(session, str(prod.id))
                if pricing.get("final_price") is not None:
                    price = float(pricing["final_price"])
        png = ad_image_generator.generate(
            template="promo_flash",
            title=title,
            price_usd=price,
            warranty_days=warranty,
            features=["تفعيل فوري", f"ضمان {warranty} يوم", "دعم سريع"],
        )
    tmp = Path(tempfile.gettempdir()) / "promo_autogen.png"
    tmp.write_bytes(png)
    from aiogram.types import FSInputFile
    msg = await callback.message.answer_photo(
        photo=FSInputFile(str(tmp)),
        caption=f"🎨 تم توليد صورة `{title}` — يمكنك المتابعة بكتابة النص.",
        parse_mode="Markdown",
    )
    file_id = msg.photo[-1].file_id if msg.photo else None
    await state.update_data(image_file_id=file_id)
    await state.set_state(PromoAdFSM.caption)
    await callback.message.answer("✍️ أرسل نص الإعلان (Caption) مع الإيموجي والهاشتاقات:")
    await callback.answer()


@router.callback_query(F.data == "adm:mkt:promo:skip_photo")
async def promo_skip_photo(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PromoAdFSM.caption)
    await callback.message.edit_text("✍️ أرسل نص الإعلان (Caption) مع الإيموجي والهاشتاقات:")
    await callback.answer()


@router.message(PromoAdFSM.photo, F.photo)
async def promo_photo(message: Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    await state.update_data(image_file_id=file_id)
    await state.set_state(PromoAdFSM.caption)
    await message.answer("✍️ أرسل نص الإعلان (Caption) مع الإيموجي والهاشتاقات:")


@router.message(PromoAdFSM.caption)
async def promo_caption(message: Message, state: FSMContext):
    await state.update_data(caption=(message.text or message.caption or "").strip())
    await state.set_state(PromoAdFSM.product)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ بدون منتج محدد", callback_data="adm:mkt:promo:noprod")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="adm:marketing")],
    ])
    await message.answer(
        "🛒 أرسل **UUID المنتج** للربط بـ `buy_{id}`، أو تخطَّ للواجهة العامة:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


@router.callback_query(F.data == "adm:mkt:promo:noprod")
async def promo_no_product(callback: CallbackQuery, state: FSMContext):
    await state.update_data(product_id=None)
    await _promo_destinations(callback.message, state)
    await callback.answer()


@router.message(PromoAdFSM.product)
async def promo_product(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    await state.update_data(product_id=raw or None)
    await _promo_destinations(message, state)


async def _promo_destinations(target, state: FSMContext):
    await state.set_state(PromoAdFSM.destinations)
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'✅' if data.get('publish_proof', True) else '⬜️'} قناة المصداقية",
            callback_data="adm:mkt:promo:tog:proof",
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if data.get('publish_main', True) else '⬜️'} القناة الرئيسية",
            callback_data="adm:mkt:promo:tog:main",
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if data.get('broadcast_customers') else '⬜️'} بث لمستخدمي البوت",
            callback_data="adm:mkt:promo:tog:bcast",
        )],
        [InlineKeyboardButton(
            text=f"{'📌' if data.get('pin') else '⬜️'} تثبيت المنشور",
            callback_data="adm:mkt:promo:tog:pin",
        )],
        [InlineKeyboardButton(text="👀 معاينة", callback_data="adm:mkt:promo:preview")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="adm:marketing")],
    ])
    text = "📡 **اختر وجهات النشر** ثم اضغط معاينة:"
    if hasattr(target, "edit_text"):
        try:
            await target.edit_text(text, parse_mode="Markdown", reply_markup=kb)
            return
        except Exception:
            pass
    await target.answer(text, parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data.startswith("adm:mkt:promo:tog:"))
async def promo_toggle_dest(callback: CallbackQuery, state: FSMContext):
    key = callback.data.split(":")[-1]
    data = await state.get_data()
    mapping = {
        "proof": "publish_proof",
        "main": "publish_main",
        "bcast": "broadcast_customers",
        "pin": "pin",
    }
    field = mapping.get(key)
    if field:
        await state.update_data(**{field: not bool(data.get(field))})
    await _promo_destinations(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "adm:mkt:promo:preview")
async def promo_preview(callback: CallbackQuery, state: FSMContext):
    from src.core.services.promo_ad_service import build_promo_keyboard
    from src.config import settings

    data = await state.get_data()
    caption = data.get("caption") or ""
    if not caption:
        await callback.answer("النص مطلوب", show_alert=True)
        return
    await state.set_state(PromoAdFSM.confirm)
    me = await callback.bot.get_me()
    kb_preview = build_promo_keyboard(
        bot_username=me.username or "shop",
        product_id=data.get("product_id"),
        miniapp_url=settings.MINIAPP_URL,
    )
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 نشر الآن", callback_data="adm:mkt:promo:go")],
        [InlineKeyboardButton(text="❌ إلغاء", callback_data="adm:marketing")],
    ])
    file_id = data.get("image_file_id")
    preview_note = (
        f"\n\n———\n📡 Proof={data.get('publish_proof')} · Main={data.get('publish_main')} · "
        f"Broadcast={data.get('broadcast_customers')} · Pin={data.get('pin')}"
    )
    if file_id:
        await callback.message.answer_photo(
            photo=file_id,
            caption=(caption + preview_note)[:1024],
            parse_mode="Markdown",
            reply_markup=kb_preview,
        )
    else:
        await callback.message.answer(caption + preview_note, parse_mode="Markdown", reply_markup=kb_preview)
    await callback.message.answer("تأكيد النشر؟", reply_markup=confirm_kb)
    await callback.answer()


@router.callback_query(F.data == "adm:mkt:promo:go")
async def promo_go(callback: CallbackQuery, state: FSMContext):
    from src.core.services.promo_ad_service import promo_ad_service

    data = await state.get_data()
    try:
        async with AsyncSessionLocal() as session:
            result = await promo_ad_service.publish_promo_ad(
                session,
                caption=data.get("caption") or "",
                reviewer=f"tg:{callback.from_user.id}",
                product_id=data.get("product_id"),
                image_file_id=data.get("image_file_id"),
                publish_proof_channel=bool(data.get("publish_proof", True)),
                publish_main_channel=bool(data.get("publish_main", True)),
                broadcast_customers=bool(data.get("broadcast_customers")),
                pin_in_channel=bool(data.get("pin")),
            )
        await state.clear()
        d = result.get("delivery") or {}
        await callback.message.edit_text(
            f"✅ تم نشر الإعلان\n"
            f"Proof: {d.get('proof_channel', 0)} · Main: {d.get('main_channel', 0)}\n"
            f"Broadcast: `{d.get('broadcast_campaign_id') or '—'}`",
            parse_mode="Markdown",
            reply_markup=_hub_kb(),
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(str(e)[:160], show_alert=True)
        await state.clear()


# ---------------- Referral Milestone Giveaway ----------------
@router.callback_query(F.data == "adm:mkt:ref")
async def ref_hub(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    from src.core.services.referral_campaign_service import referral_campaign_service

    async with AsyncSessionLocal() as session:
        campaigns = await referral_campaign_service.list_campaigns(session)
    if not campaigns:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ إنشاء حملة", callback_data="adm:mkt:ref:new")],
            [InlineKeyboardButton(text="🔙", callback_data="adm:marketing")],
        ])
        await callback.message.edit_text(
            "🎁 لا توجد حملات دعوة بعد.",
            reply_markup=kb,
        )
        await callback.answer()
        return

    active = next((c for c in campaigns if c["is_active"]), campaigns[0])
    stock = active.get("stock") or {}
    text = (
        f"🎁 **{active['title']}**\n"
        f"🏆 {active['reward_name']}\n"
        f"👥 المطلوب: **{active['required_invites']}** دعوات\n"
        f"📦 المخزون: متاح `{stock.get('available', 0)}` / مُستلم `{stock.get('claimed', 0)}`\n"
        f"状态: {'🟢 نشطة' if active['is_active'] else '⏸ متوقفة'}\n"
        f"ID: `{active['id']}`"
    )
    # Fix accidental Chinese character
    text = text.replace("状态:", "الحالة:")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 رفع حسابات (سطر لكل حساب)", callback_data=f"adm:mkt:ref:stock:{active['id']}")],
        [InlineKeyboardButton(text="🔢 تعديل عدد الدعوات", callback_data=f"adm:mkt:ref:invites:{active['id']}")],
        [InlineKeyboardButton(
            text="⏸ إيقاف" if active["is_active"] else "▶️ تفعيل",
            callback_data=f"adm:mkt:ref:toggle:{active['id']}",
        )],
        [InlineKeyboardButton(text="➕ حملة جديدة", callback_data="adm:mkt:ref:new")],
        [InlineKeyboardButton(text="🔙", callback_data="adm:marketing")],
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "adm:mkt:ref:new")
async def ref_new(callback: CallbackQuery, state: FSMContext):
    from src.core.services.referral_campaign_service import referral_campaign_service
    async with AsyncSessionLocal() as session:
        c = await referral_campaign_service.create_campaign(
            session,
            title="حملة حسابات مجانية",
            reward_name="حساب مجاني",
            required_invites=3,
            reviewer=f"tg:{callback.from_user.id}",
        )
    await callback.answer("تم إنشاء حملة افتراضية", show_alert=True)
    await ref_hub(callback, state)


@router.callback_query(F.data.startswith("adm:mkt:ref:toggle:"))
async def ref_toggle(callback: CallbackQuery, state: FSMContext):
    from src.core.services.referral_campaign_service import referral_campaign_service
    cid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        c = await referral_campaign_service.get_campaign(session, cid)
        if not c:
            await callback.answer("غير موجود", show_alert=True)
            return
        await referral_campaign_service.update_campaign(
            session, cid,
            is_active=not c.is_active,
            reviewer=f"tg:{callback.from_user.id}",
        )
    await ref_hub(callback, state)


@router.callback_query(F.data.startswith("adm:mkt:ref:stock:"))
async def ref_stock_prompt(callback: CallbackQuery, state: FSMContext):
    cid = callback.data.split(":")[-1]
    await state.set_state(RefCampaignFSM.stock_upload)
    await state.update_data(campaign_id=cid)
    await callback.message.edit_text(
        "📥 أرسل الحسابات/المفاتيح — **سطر واحد لكل حساب**:",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(RefCampaignFSM.stock_upload)
async def ref_stock_upload(message: Message, state: FSMContext):
    from src.core.services.referral_campaign_service import referral_campaign_service
    data = await state.get_data()
    cid = data.get("campaign_id")
    lines = [ln.strip() for ln in (message.text or "").splitlines() if ln.strip()]
    try:
        async with AsyncSessionLocal() as session:
            result = await referral_campaign_service.add_stock(
                session, cid, lines, reviewer=f"tg:{message.from_user.id}"
            )
        await state.clear()
        stock = result.get("stock") or {}
        await message.answer(
            f"✅ تمت إضافة **{result.get('added', 0)}** حساباً\n"
            f"المتبقي: `{stock.get('available', 0)}`",
            parse_mode="Markdown",
            reply_markup=_hub_kb(),
        )
    except Exception as e:
        await message.answer(f"❌ {e}")
        await state.clear()


@router.callback_query(F.data.startswith("adm:mkt:ref:invites:"))
async def ref_invites_prompt(callback: CallbackQuery, state: FSMContext):
    cid = callback.data.split(":")[-1]
    await state.set_state(RefCampaignFSM.set_invites)
    await state.update_data(campaign_id=cid)
    await callback.message.edit_text("🔢 أرسل العدد المطلوب من الدعوات (مثال: `3`):", parse_mode="Markdown")
    await callback.answer()


@router.message(RefCampaignFSM.set_invites)
async def ref_set_invites(message: Message, state: FSMContext):
    from src.core.services.referral_campaign_service import referral_campaign_service
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("أدخل رقماً صحيحاً >= 1")
        return
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        await referral_campaign_service.update_campaign(
            session,
            data.get("campaign_id"),
            required_invites=int(raw),
            reviewer=f"tg:{message.from_user.id}",
        )
    await state.clear()
    await message.answer(f"✅ تم ضبط المطلوب على **{raw}** دعوات", parse_mode="Markdown", reply_markup=_hub_kb())
