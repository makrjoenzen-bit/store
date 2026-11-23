"""Admin Bot — suppliers list / add wizard / catalog cherry-pick / toggle."""
from __future__ import annotations

from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from src.core.database import AsyncSessionLocal
from src.core.models.enums import PricingRuleType, PaymentNetwork
from src.core.services.supplier_admin_service import supplier_admin_service
from src.admin_bot.keyboards.main_menu import coming_soon_keyboard

router = Router(name="admin_suppliers")


class AddSupplierFSM(StatesGroup):
    name = State()
    driver = State()
    base_url = State()
    api_key = State()


class CherryPickFSM(StatesGroup):
    title_ar = State()
    pricing = State()


def _suppliers_kb(rows):
    buttons = []
    for s in rows[:20]:
        bal = f"${s['cached_balance']:.2f}" if s.get("cached_balance") is not None else "—"
        buttons.append([InlineKeyboardButton(
            text=f"{s['status_emoji']} {s['name']} · {s['product_count']}SKU · {bal}",
            callback_data=f"adm:sup:view:{s['id']}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ إضافة مورد API", callback_data="adm:sup:add")])
    buttons.append([InlineKeyboardButton(text="🏠 القائمة", callback_data="adm:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _supplier_detail_kb(sid: str, active: bool):
    toggle = "🔴 تعطيل" if active else "🟢 تفعيل"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 تصفح منتجات المورد", callback_data=f"adm:sup:cat:{sid}:0")],
        [InlineKeyboardButton(text="🔌 اختبار الاتصال", callback_data=f"adm:sup:test:{sid}")],
        [InlineKeyboardButton(text=toggle, callback_data=f"adm:sup:toggle:{sid}")],
        [InlineKeyboardButton(text="🔙 الموردين", callback_data="adm:suppliers")],
    ])


@router.message(F.text == "🔌 الموردين والـ APIs")
@router.callback_query(F.data == "adm:suppliers")
async def list_suppliers(event: Message | CallbackQuery):
    async with AsyncSessionLocal() as session:
        rows = await supplier_admin_service.list_suppliers(session)
    text = "🔌 **الموردين والـ APIs**\n\n" + (
        f"عدد الموردين: `{len(rows)}`" if rows else "لا يوجد موردون بعد."
    )
    kb = _suppliers_kb(rows)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data.startswith("adm:sup:view:"))
async def view_supplier(callback: CallbackQuery):
    sid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        rows = await supplier_admin_service.list_suppliers(session)
    s = next((r for r in rows if r["id"] == sid), None)
    if not s:
        await callback.answer("غير موجود", show_alert=True)
        return
    bal = f"${s['cached_balance']:.2f}" if s.get("cached_balance") is not None else "—"
    text = (
        f"{s['status_emoji']} **{s['name']}**\n\n"
        f"النوع: `{s['driver_type']}`\n"
        f"الحالة: `{s['status']}`\n"
        f"الرصيد: {bal}\n"
        f"المنتجات المرتبطة: `{s['product_count']}`"
    )
    await callback.message.edit_text(
        text, parse_mode="Markdown",
        reply_markup=_supplier_detail_kb(sid, s["status"] == "active"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:sup:test:"))
async def test_supplier(callback: CallbackQuery):
    sid = callback.data.split(":")[-1]
    try:
        async with AsyncSessionLocal() as session:
            result = await supplier_admin_service.test_connection(session, sid)
        emoji = result["status_emoji"]
        bal = result.get("balance_usd")
        msg = (
            f"{emoji} **Connected** · {result.get('latency_ms')}ms"
            if result["is_alive"] else
            f"🔴 فشل الاتصال: {result.get('error_message') or '—'}"
        )
        if bal is not None:
            msg += f"\nالرصيد: `${bal:.2f}`"
        await callback.answer(msg[:180], show_alert=True)
    except Exception as e:
        await callback.answer(str(e)[:180], show_alert=True)


@router.callback_query(F.data.startswith("adm:sup:toggle:"))
async def toggle_supplier(callback: CallbackQuery):
    sid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        rows = await supplier_admin_service.list_suppliers(session)
        s = next((r for r in rows if r["id"] == sid), None)
        if not s:
            await callback.answer("غير موجود", show_alert=True)
            return
        active = s["status"] != "active"
        result = await supplier_admin_service.set_status(
            session, sid, active=active, reviewer=f"tg:{callback.from_user.id}"
        )
    await callback.answer(f"الحالة → {result['status']}", show_alert=False)
    callback.data = f"adm:sup:view:{sid}"
    await view_supplier(callback)


@router.callback_query(F.data.startswith("adm:sup:cat:"))
async def catalog_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    sid, page = parts[3], int(parts[4])
    try:
        async with AsyncSessionLocal() as session:
            catalog = await supplier_admin_service.get_catalog(session, sid)
    except Exception as e:
        await callback.answer(str(e)[:160], show_alert=True)
        return

    page_size = 8
    chunk = catalog[page * page_size:(page + 1) * page_size]
    buttons = []
    for p in chunk:
        mapped = "✅" if p["is_already_mapped"] else "➕"
        stock = p.get("available_quantity")
        stock_s = f"×{stock}" if stock is not None else ("✅" if p["is_in_stock"] else "❌")
        buttons.append([InlineKeyboardButton(
            text=f"{mapped} ${p['cost']:.2f} {stock_s} · {(p['name'] or '')[:28]}",
            callback_data=f"adm:sup:pick:{sid}:{p['supplier_product_id'][:40]}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm:sup:cat:{sid}:{page-1}"))
    if (page + 1) * page_size < len(catalog):
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm:sup:cat:{sid}:{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="🔙", callback_data=f"adm:sup:view:{sid}")])
    await callback.message.edit_text(
        f"📦 **كتالوج المورد** — صفحة {page + 1}\nاختر منتجاً للاستيراد أو الربط:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:sup:pick:"))
async def pick_product(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    sid, spid = parts[3], parts[4]
    await state.set_state(CherryPickFSM.title_ar)
    await state.update_data(supplier_id=sid, supplier_product_id=spid)
    await callback.message.edit_text(
        "✏️ أرسل **العنوان العربي** للمنتج الجديد (أو اكتب `MAP:<uuid>` لربطه بمنتج موجود):",
        parse_mode="Markdown",
        reply_markup=coming_soon_keyboard(),
    )
    await callback.answer()


@router.message(CherryPickFSM.title_ar)
async def cherry_title(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    data = await state.get_data()
    if text.upper().startswith("MAP:"):
        mid = text.split(":", 1)[1].strip()
        try:
            async with AsyncSessionLocal() as session:
                result = await supplier_admin_service.cherry_pick_map(
                    session,
                    supplier_id=data["supplier_id"],
                    supplier_product_id=data["supplier_product_id"],
                    master_product_id=mid,
                    reviewer=f"tg:{message.from_user.id}",
                )
            await state.clear()
            await message.answer(
                f"✅ تم الربط بالمنتج `{result['product_id']}` · تكلفة `${result['wholesale_cost']:.2f}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            await message.answer(f"فشل الربط: {e}")
        return

    await state.update_data(title_ar=text)
    await state.set_state(CherryPickFSM.pricing)
    await message.answer(
        "💰 أرسل قاعدة التسعير:\n"
        "`PERCENT:20` أو `FIXED:3` أو `FLOOR:15`",
        parse_mode="Markdown",
    )


@router.message(CherryPickFSM.pricing)
async def cherry_pricing(message: Message, state: FSMContext):
    raw = (message.text or "").strip().upper()
    data = await state.get_data()
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
        await message.answer("صيغة غير صالحة. مثال: `PERCENT:20`", parse_mode="Markdown")
        return

    try:
        async with AsyncSessionLocal() as session:
            result = await supplier_admin_service.cherry_pick_create(
                session,
                supplier_id=data["supplier_id"],
                supplier_product_id=data["supplier_product_id"],
                title_ar=data["title_ar"],
                pricing_rule_type=rule_type,
                pricing_value=value,
                reviewer=f"tg:{message.from_user.id}",
            )
        await state.clear()
        await message.answer(
            f"✅ تم إنشاء المنتج `{result['title_ar']}`\n"
            f"SKU: `{result['internal_sku']}` · تكلفة `${result['wholesale_cost']:.2f}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await message.answer(f"فشل الاستيراد: {e}")
        await state.clear()


@router.callback_query(F.data == "adm:sup:add")
async def add_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddSupplierFSM.name)
    await callback.message.edit_text("➕ أرسل **اسم المورد**:", parse_mode="Markdown")
    await callback.answer()


@router.message(AddSupplierFSM.name)
async def add_name(message: Message, state: FSMContext):
    from src.core.adapters.registry import SUPPLIER_PRESETS
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(AddSupplierFSM.driver)
    rows = [
        [InlineKeyboardButton(
            text=f"{meta.get('label', key)} (`{key}`)",
            callback_data=f"adm:sup:drv:{key}",
        )]
        for key, meta in SUPPLIER_PRESETS.items()
    ]
    await message.answer("اختر نوع الـ Adapter:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("adm:sup:drv:"))
async def add_driver(callback: CallbackQuery, state: FSMContext):
    from src.core.adapters.registry import SUPPLIER_PRESETS
    driver = callback.data.split(":")[-1]
    preset = SUPPLIER_PRESETS.get(driver) or {}
    base = (preset.get("base_url") or "").rstrip("/")
    await state.update_data(driver_type=driver, base_url=base)
    await state.set_state(AddSupplierFSM.base_url)
    if base:
        await callback.message.edit_text(
            f"🌐 Base URL الافتراضي:\n`{base}`\n\n"
            f"أرسل رابطاً جديداً أو اكتب `-` للاعتماد على الافتراضي:",
            parse_mode="Markdown",
        )
    else:
        await callback.message.edit_text(
            "🌐 أرسل **Base URL** أو الصق كود `conn_...` لاحقاً مع المفتاح (Lara):",
            parse_mode="Markdown",
        )
    await callback.answer()


@router.message(AddSupplierFSM.base_url)
async def add_url(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    data = await state.get_data()
    if raw and raw != "-":
        await state.update_data(base_url=raw.rstrip("/"))
    await state.set_state(AddSupplierFSM.api_key)
    hint = "🔑 أرسل **API Key** أو الصق كود `conn_...` (Lara):" if data.get("driver_type") == "larastock_v1" else "🔑 أرسل **API Key** (سيُشفَّر بـ Fernet):"
    await message.answer(hint, parse_mode="Markdown")


@router.message(AddSupplierFSM.api_key)
async def add_key(message: Message, state: FSMContext):
    from src.core.adapters.registry import AdapterRegistry
    data = await state.get_data()
    api_key = (message.text or "").strip()
    config = AdapterRegistry.normalize_config(
        data["driver_type"],
        {"base_url": data.get("base_url") or "", "api_key": api_key},
    )
    try:
        async with AsyncSessionLocal() as session:
            created = await supplier_admin_service.create_supplier(
                session,
                name=data["name"],
                driver_type=data["driver_type"],
                config=config,
                payment_network=PaymentNetwork.USDT_BEP20,
                reviewer=f"tg:{message.from_user.id}",
            )
            test = await supplier_admin_service.test_connection(session, created["id"])
        await state.clear()
        emoji = test["status_emoji"]
        bal = test.get("balance_usd")
        bal_s = f"${bal:.2f}" if bal is not None else "—"
        await message.answer(
            f"✅ تم إنشاء المورد **{created['name']}**\n"
            f"{emoji} اختبار الاتصال · الرصيد: `{bal_s}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await state.clear()
        await message.answer(f"فشل الإنشاء: {e}")
