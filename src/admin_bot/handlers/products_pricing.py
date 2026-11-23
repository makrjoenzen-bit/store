"""Admin Bot — Master Products list + title/pricing/active edits."""
from __future__ import annotations

from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton  # noqa: F401

from src.core.database import AsyncSessionLocal
from src.core.models.enums import PricingRuleType
from src.core.services.product_admin_service import product_admin_service

router = Router(name="admin_products")


class ProductEditFSM(StatesGroup):
    title = State()
    description = State()
    warranty = State()
    pricing = State()
    photo = State()


def _list_kb(rows, page: int):
    buttons = []
    for p in rows:
        price = f"${p['final_price']:.2f}" if p.get("final_price") is not None else "—"
        active = "🟢" if p["is_active"] else "⚪️"
        buttons.append([InlineKeyboardButton(
            text=f"{active} {price} · {(p['title_ar'] or '')[:32]}",
            callback_data=f"adm:prod:view:{p['id']}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:products:p:{page-1}"))
    nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:products:p:{page+1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🏠 القائمة", callback_data="adm:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _detail_kb(pid: str, active: bool):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ العنوان", callback_data=f"adm:prod:edit:title:{pid}")],
        [InlineKeyboardButton(text="📝 الوصف", callback_data=f"adm:prod:edit:desc:{pid}")],
        [InlineKeyboardButton(text="🛡 الضمان (أيام)", callback_data=f"adm:prod:edit:warr:{pid}")],
        [InlineKeyboardButton(text="💰 قاعدة التسعير", callback_data=f"adm:prod:edit:price:{pid}")],
        [InlineKeyboardButton(text="🖼️ تحديث صورة المنتج", callback_data=f"adm:prod:edit:photo:{pid}")],
        [InlineKeyboardButton(
            text="⚪️ إخفاء" if active else "🟢 تفعيل",
            callback_data=f"adm:prod:toggle:{pid}",
        )],
        [InlineKeyboardButton(text="🔙 المنتجات", callback_data="adm:products")],
    ])


@router.message(F.text == "📦 المنتجات والأسعار")
@router.callback_query(F.data == "adm:products")
@router.callback_query(F.data.startswith("adm:products:p:"))
async def list_products(event: Message | CallbackQuery):
    page = 0
    if isinstance(event, CallbackQuery) and event.data.startswith("adm:products:p:"):
        page = int(event.data.split(":")[-1])
    async with AsyncSessionLocal() as session:
        rows = await product_admin_service.list_products(session, offset=page * 12, limit=12)
    text = f"📦 **المنتجات والأسعار** — صفحة {page + 1}\nاضغط منتجاً للتعديل:"
    kb = _list_kb(rows, page)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data.startswith("adm:prod:view:"))
async def view_product(callback: CallbackQuery):
    pid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        p = await product_admin_service.get_product(session, pid)
    rule = p.get("pricing_rule") or {}
    text = (
        f"{'🟢' if p['is_active'] else '⚪️'} **{p['title_ar']}**\n\n"
        f"SKU: `{p['internal_sku']}`\n"
        f"السعر الحالي: `${p.get('final_price') or 0:.2f}`\n"
        f"المورد الفائز: {p.get('winning_supplier') or '—'}\n"
        f"التكلفة: `${p.get('wholesale_cost') or 0:.2f}`\n"
        f"الضمان: `{p['warranty_days']}` يوم\n"
        f"التسعير: `{rule.get('rule_type') or '—'} = {rule.get('value')}`"
    )
    await callback.message.edit_text(
        text, parse_mode="Markdown", reply_markup=_detail_kb(pid, p["is_active"])
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:prod:toggle:"))
async def toggle_product(callback: CallbackQuery):
    pid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        p = await product_admin_service.get_product(session, pid)
        await product_admin_service.update_product(
            session, pid, is_active=not p["is_active"],
            reviewer=f"tg:{callback.from_user.id}",
        )
    callback.data = f"adm:prod:view:{pid}"
    await view_product(callback)


@router.callback_query(F.data.startswith("adm:prod:edit:"))
async def edit_start(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    field, pid = parts[3], parts[4]
    await state.update_data(product_id=pid, field=field)
    prompts = {
        "title": ("أرسل العنوان العربي الجديد:", ProductEditFSM.title),
        "desc": ("أرسل الوصف العربي الجديد:", ProductEditFSM.description),
        "warr": ("أرسل عدد أيام الضمان (رقم):", ProductEditFSM.warranty),
        "price": ("أرسل قاعدة التسعير مثل `PERCENT:20` أو `FIXED:3` أو `FLOOR:15`:", ProductEditFSM.pricing),
        "photo": ("🖼️ أرسل **صورة المنتج** الآن (ستظهر في البوت وMini App):", ProductEditFSM.photo),
    }
    prompt, st = prompts[field]
    await state.set_state(st)
    await callback.message.edit_text(prompt, parse_mode="Markdown")
    await callback.answer()


@router.message(ProductEditFSM.title)
async def save_title(message: Message, state: FSMContext):
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        await product_admin_service.update_product(
            session, data["product_id"], title_ar=message.text,
            reviewer=f"tg:{message.from_user.id}",
        )
    await state.clear()
    await message.answer("✅ تم تحديث العنوان — يسري فوراً على البوت والـ Mini App والقناة.")


@router.message(ProductEditFSM.description)
async def save_desc(message: Message, state: FSMContext):
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        await product_admin_service.update_product(
            session, data["product_id"], description_ar=message.text,
            reviewer=f"tg:{message.from_user.id}",
        )
    await state.clear()
    await message.answer("✅ تم تحديث الوصف.")


@router.message(ProductEditFSM.warranty)
async def save_warr(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        days = int((message.text or "").strip())
    except ValueError:
        await message.answer("أدخل رقماً صحيحاً.")
        return
    async with AsyncSessionLocal() as session:
        await product_admin_service.update_product(
            session, data["product_id"], warranty_days=days,
            reviewer=f"tg:{message.from_user.id}",
        )
    await state.clear()
    await message.answer("✅ تم تحديث الضمان.")


@router.message(ProductEditFSM.pricing)
async def save_pricing(message: Message, state: FSMContext):
    data = await state.get_data()
    raw = (message.text or "").strip().upper()
    try:
        kind, val = raw.split(":", 1)
        mapping = {
            "PERCENT": PricingRuleType.PERCENTAGE,
            "PERCENTAGE": PricingRuleType.PERCENTAGE,
            "FIXED": PricingRuleType.FIXED,
            "FLOOR": PricingRuleType.FLOOR,
        }
        rule_type = mapping[kind]
        value = Decimal(val)
    except Exception:
        await message.answer("صيغة غير صالحة. مثال: `PERCENT:25`", parse_mode="Markdown")
        return
    async with AsyncSessionLocal() as session:
        p = await product_admin_service.upsert_product_pricing_rule(
            session, data["product_id"],
            rule_type=rule_type, value=value,
            reviewer=f"tg:{message.from_user.id}",
        )
    await state.clear()
    await message.answer(
        f"✅ التسعير محدّث. السعر الحالي: `${p.get('final_price') or 0:.2f}`\n"
        f"(يسري فوراً على البوت والـ Mini App والقناة والداشبورد)",
        parse_mode="Markdown",
    )


@router.message(ProductEditFSM.photo, F.photo)
async def save_photo(message: Message, state: FSMContext):
    """Re-upload via Customer Bot so file_id works in shop + Mini App syncs photo_file_id."""
    import httpx
    from aiogram.types import BufferedInputFile
    from src.config import settings
    from src.core.services.telegram_file_proxy import download_telegram_file

    data = await state.get_data()
    admin_file_id = message.photo[-1].file_id
    customer_file_id = admin_file_id
    try:
        # Download with Admin Bot token, re-upload with Customer Bot token
        content, _path = await download_telegram_file(
            admin_file_id, bot_token=settings.ADMIN_BOT_TOKEN or settings.TELEGRAM_BOT_TOKEN
        )
        cust_token = settings.TELEGRAM_BOT_TOKEN
        if cust_token and settings.admin_telegram_id_set:
            dump_chat = next(iter(settings.admin_telegram_id_set))
            async with httpx.AsyncClient(timeout=60.0) as client:
                files = {"photo": ("product.jpg", content, "image/jpeg")}
                form = {"chat_id": str(dump_chat), "caption": "🖼 sync product image"}
                res = await client.post(
                    f"https://api.telegram.org/bot{cust_token}/sendPhoto",
                    data=form,
                    files=files,
                )
                payload = res.json()
                if payload.get("ok"):
                    photos = payload["result"].get("photo") or []
                    if photos:
                        customer_file_id = photos[-1]["file_id"]
    except Exception as e:
        # Fall back to admin file_id (still useful if same bot token)
        from loguru import logger
        logger.warning(f"Product photo re-upload via customer bot failed: {e}")

    async with AsyncSessionLocal() as session:
        await product_admin_service.update_product(
            session,
            data["product_id"],
            photo_file_id=customer_file_id,
            reviewer=f"tg:{message.from_user.id}",
        )
    await state.clear()
    await message.answer("✅ تم تحديث صورة المنتج — تظهر فوراً في بوت العملاء وMini App.")
