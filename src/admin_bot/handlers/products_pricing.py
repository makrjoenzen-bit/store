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


class ProductCreateFSM(StatesGroup):
    category = State()
    name_ar = State()
    name_en = State()
    price = State()
    cost = State()
    description = State()


class CategoryCreateFSM(StatesGroup):
    name_ar = State()
    name_en = State()


def _list_kb(rows, page: int):
    buttons = [
        [
            InlineKeyboardButton(text="➕ منتج", callback_data="adm:prod:new"),
            InlineKeyboardButton(text="📁 فئة", callback_data="adm:cat:new"),
        ],
    ]
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
        [InlineKeyboardButton(text="🗑 حذف المنتج", callback_data=f"adm:prod:del:{pid}")],
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


@router.callback_query(F.data.startswith("adm:prod:del:"))
async def delete_product(callback: CallbackQuery):
    pid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        await product_admin_service.delete_product(
            session, pid, reviewer=f"tg:{callback.from_user.id}",
        )
    await callback.answer("تم الحذف")
    callback.data = "adm:products"
    await list_products(callback)


@router.callback_query(F.data == "adm:cat:new")
async def cat_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CategoryCreateFSM.name_ar)
    await callback.message.edit_text("📁 أرسل اسم الفئة بالعربية:")
    await callback.answer()


@router.message(CategoryCreateFSM.name_ar)
async def cat_name_ar(message: Message, state: FSMContext):
    await state.update_data(name_ar=(message.text or "").strip())
    await state.set_state(CategoryCreateFSM.name_en)
    await message.answer("أرسل اسم الفئة بالإنجليزية (أو أعد نفس الاسم):")


@router.message(CategoryCreateFSM.name_en)
async def cat_save(message: Message, state: FSMContext):
    data = await state.get_data()
    async with AsyncSessionLocal() as session:
        cat = await product_admin_service.create_category(
            session,
            name_ar=data.get("name_ar") or message.text,
            name_en=(message.text or "").strip(),
            reviewer=f"tg:{message.from_user.id}",
        )
    await state.clear()
    await message.answer(f"✅ فئة جديدة: <b>{cat['name_ar']}</b>", parse_mode="HTML")


@router.callback_query(F.data == "adm:prod:new")
async def prod_new(callback: CallbackQuery, state: FSMContext):
    async with AsyncSessionLocal() as session:
        cats = await product_admin_service.list_categories(session)
    if not cats:
        await callback.answer("أنشئ فئة أولاً", show_alert=True)
        await state.set_state(CategoryCreateFSM.name_ar)
        await callback.message.edit_text("لا توجد فئات. أرسل اسم الفئة بالعربية:")
        return
    await state.set_state(ProductCreateFSM.category)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{c.get('icon_emoji') or '📦'} {c['name_ar']}",
            callback_data=f"adm:prod:cat:{c['id']}",
        )]
        for c in cats[:20]
    ] + [[InlineKeyboardButton(text="❌ إلغاء", callback_data="adm:products")]])
    await callback.message.edit_text("اختر الفئة للمنتج الجديد:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:prod:cat:"), ProductCreateFSM.category)
async def prod_pick_cat(callback: CallbackQuery, state: FSMContext):
    await state.update_data(category_id=callback.data.split(":")[-1])
    await state.set_state(ProductCreateFSM.name_ar)
    await callback.message.edit_text("📝 أرسل اسم المنتج بالعربية:")
    await callback.answer()


@router.message(ProductCreateFSM.name_ar)
async def prod_name_ar(message: Message, state: FSMContext):
    await state.update_data(name_ar=(message.text or "").strip())
    await state.set_state(ProductCreateFSM.name_en)
    await message.answer("أرسل الاسم بالإنجليزية (أو نفس الاسم):")


@router.message(ProductCreateFSM.name_en)
async def prod_name_en(message: Message, state: FSMContext):
    await state.update_data(name_en=(message.text or "").strip())
    await state.set_state(ProductCreateFSM.price)
    await message.answer("💰 أرسل سعر البيع بالدولار (مثال: 9.99):")


@router.message(ProductCreateFSM.price)
async def prod_price(message: Message, state: FSMContext):
    try:
        price = Decimal((message.text or "").strip())
        if price <= 0:
            raise ValueError()
    except Exception:
        await message.answer("أدخل سعراً صالحاً مثل 9.99")
        return
    await state.update_data(price=str(price))
    await state.set_state(ProductCreateFSM.cost)
    await message.answer("📉 أرسل التكلفة (wholesale) بالدولار — أو 0 إن لم تُعرف:")


@router.message(ProductCreateFSM.cost)
async def prod_cost(message: Message, state: FSMContext):
    try:
        cost = Decimal((message.text or "0").strip() or "0")
    except Exception:
        await message.answer("أدخل رقماً صالحاً.")
        return
    await state.update_data(cost=str(cost))
    await state.set_state(ProductCreateFSM.description)
    await message.answer("📝 أرسل وصف المنتج (أو - للتخطي):")


@router.message(ProductCreateFSM.description)
async def prod_save(message: Message, state: FSMContext):
    data = await state.get_data()
    desc = (message.text or "").strip()
    if desc == "-":
        desc = None
    try:
        async with AsyncSessionLocal() as session:
            p = await product_admin_service.create_product(
                session,
                title_ar=data["name_ar"],
                title_en=data.get("name_en"),
                category_id=data.get("category_id"),
                retail_price=Decimal(data["price"]),
                wholesale_cost=Decimal(data.get("cost") or "0"),
                description_ar=desc,
                reviewer=f"tg:{message.from_user.id}",
            )
        await state.clear()
        await message.answer(
            f"✅ تم إنشاء المنتج\n"
            f"<b>{p.get('title_ar')}</b>\n"
            f"السعر: <code>${float(p.get('final_price') or data['price']):.2f}</code>\n"
            f"يظهر فوراً في البوت والـ Mini App.",
            parse_mode="HTML",
        )
    except Exception as e:
        await state.clear()
        await message.answer(f"فشل الإنشاء: {e}")


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
