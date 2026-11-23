"""Admin Bot — CRM search + wallet adjust/ban + support desk."""
from __future__ import annotations

from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from src.core.database import AsyncSessionLocal
from src.core.services.crm_service import crm_service
from src.admin_bot.keyboards.main_menu import coming_soon_keyboard

router = Router(name="admin_crm")


class CrmFSM(StatesGroup):
    search = State()
    credit_amount = State()
    credit_reason = State()
    debit_amount = State()
    debit_reason = State()
    ticket_reply = State()


def _crm_hub_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 بحث عن عميل", callback_data="adm:crm:search")],
        [InlineKeyboardButton(text="🎫 تذاكر الدعم المفتوحة", callback_data="adm:crm:tickets")],
        [InlineKeyboardButton(text="🏠 القائمة", callback_data="adm:home")],
    ])


def _customer_kb(cid: str, banned: bool):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ إضافة رصيد", callback_data=f"adm:crm:credit:{cid}"),
            InlineKeyboardButton(text="➖ خصم رصيد", callback_data=f"adm:crm:debit:{cid}"),
        ],
        [InlineKeyboardButton(
            text="✅ فك حظر" if banned else "🚫 حظر",
            callback_data=f"adm:crm:ban:{cid}",
        )],
        [InlineKeyboardButton(text="🔙 العملاء", callback_data="adm:crm")],
    ])


@router.message(F.text == "👥 العملاء والدعم")
@router.callback_query(F.data == "adm:crm")
async def crm_hub(event: Message | CallbackQuery, state: FSMContext):
    await state.clear()
    text = "👥 **العملاء والدعم الفني**\nاختر إجراءً:"
    kb = _crm_hub_kb()
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data == "adm:crm:search")
async def search_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(CrmFSM.search)
    await callback.message.edit_text(
        "🔍 أرسل Telegram ID أو `@username` للبحث:",
        parse_mode="Markdown",
        reply_markup=coming_soon_keyboard(),
    )
    await callback.answer()


@router.message(CrmFSM.search)
async def search_run(message: Message, state: FSMContext):
    try:
        async with AsyncSessionLocal() as session:
            c = await crm_service.search_customer(session, message.text or "")
        await state.clear()
        ban = "🚫 محظور" if c["is_blocked"] else "✅ نشط"
        text = (
            f"👤 **{c.get('first_name') or 'عميل'}** (@{c.get('telegram_username') or '—'})\n\n"
            f"ID: `{c['telegram_id']}`\n"
            f"👛 الرصيد: `${c['wallet_balance']:.2f}`\n"
            f"🎖 VIP: `{c['vip_tier']}`\n"
            f"📦 الطلبات: `{c['total_orders']}` · المصروف: `${c['total_spent']:.2f}`\n"
            f"الحالة: {ban}"
        )
        await message.answer(
            text, parse_mode="Markdown",
            reply_markup=_customer_kb(c["id"], c["is_blocked"]),
        )
    except ValueError as e:
        await message.answer(str(e))


@router.callback_query(F.data.startswith("adm:crm:credit:"))
async def credit_start(callback: CallbackQuery, state: FSMContext):
    cid = callback.data.split(":")[-1]
    await state.set_state(CrmFSM.credit_amount)
    await state.update_data(customer_id=cid, direction="credit")
    await callback.message.answer("💵 أرسل مبلغ الإضافة (USD):")
    await callback.answer()


@router.callback_query(F.data.startswith("adm:crm:debit:"))
async def debit_start(callback: CallbackQuery, state: FSMContext):
    cid = callback.data.split(":")[-1]
    await state.set_state(CrmFSM.debit_amount)
    await state.update_data(customer_id=cid, direction="debit")
    await callback.message.answer("💵 أرسل مبلغ الخصم (USD):")
    await callback.answer()


@router.message(CrmFSM.credit_amount)
@router.message(CrmFSM.debit_amount)
async def amount_then_reason(message: Message, state: FSMContext):
    try:
        amt = Decimal((message.text or "").strip())
        if amt <= 0:
            raise ValueError()
    except Exception:
        await message.answer("مبلغ غير صالح.")
        return
    data = await state.get_data()
    await state.update_data(amount=str(amt))
    if data.get("direction") == "credit":
        await state.set_state(CrmFSM.credit_reason)
    else:
        await state.set_state(CrmFSM.debit_reason)
    await message.answer("📝 أرسل **سبب** العملية (إلزامي):", parse_mode="Markdown")


@router.message(CrmFSM.credit_reason)
@router.message(CrmFSM.debit_reason)
async def apply_adjust(message: Message, state: FSMContext):
    data = await state.get_data()
    reason = (message.text or "").strip()
    try:
        async with AsyncSessionLocal() as session:
            result = await crm_service.adjust_balance(
                session,
                data["customer_id"],
                amount=Decimal(data["amount"]),
                direction=data["direction"],
                reason=reason,
                reviewer=f"tg:{message.from_user.id}",
            )
        await state.clear()
        await message.answer(
            f"✅ تم. الرصيد الجديد: `${result['new_balance']:.2f}`",
            parse_mode="Markdown",
        )
    except ValueError as e:
        await message.answer(str(e))
        await state.clear()


@router.callback_query(F.data.startswith("adm:crm:ban:"))
async def ban_toggle(callback: CallbackQuery):
    cid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        # peek current
        from sqlalchemy import select
        from src.core.models.entities import Customer
        import uuid
        c = (await session.execute(
            select(Customer).where(Customer.id == uuid.UUID(cid))
        )).scalar_one_or_none()
        if not c:
            await callback.answer("غير موجود", show_alert=True)
            return
        result = await crm_service.set_ban(
            session, cid, ban=not c.is_blocked,
            reason="Admin Bot toggle",
            reviewer=f"tg:{callback.from_user.id}",
        )
    await callback.answer(
        "تم الحظر" if result["is_blocked"] else "تم فك الحظر", show_alert=True
    )


@router.callback_query(F.data == "adm:crm:tickets")
async def list_tickets(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        tickets = await crm_service.list_open_tickets(session)
    if not tickets:
        await callback.message.edit_text(
            "لا توجد تذاكر مفتوحة.", reply_markup=_crm_hub_kb(), parse_mode="Markdown"
        )
        await callback.answer()
        return
    buttons = []
    for t in tickets[:15]:
        warn = " ⚠️خارج الضمان" if t["is_warranty_expired"] else ""
        buttons.append([InlineKeyboardButton(
            text=f"#{t['short_id']} · {(t['subject'] or '')[:24]}{warn}",
            callback_data=f"adm:crm:tk:{t['id']}",
        )])
    buttons.append([InlineKeyboardButton(text="🔙", callback_data="adm:crm")])
    await callback.message.edit_text(
        "🎫 **التذاكر المفتوحة**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:crm:tk:"))
async def ticket_card(callback: CallbackQuery):
    tid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        tickets = await crm_service.list_open_tickets(session, limit=50)
    t = next((x for x in tickets if x["id"] == tid), None)
    if not t:
        await callback.answer("غير موجودة", show_alert=True)
        return
    warn = "\n⚠️ **خارج الضمان**" if t["is_warranty_expired"] else ""
    text = (
        f"🎫 `#{t['short_id']}`\n"
        f"الموضوع: {t['subject']}\n"
        f"العميل: {t.get('customer_name')} (`{t.get('telegram_id')}`)\n"
        f"الطلب: `{t.get('order_number') or '—'}`{warn}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 رد على العميل", callback_data=f"adm:crm:reply:{tid}")],
        [InlineKeyboardButton(text="💸 حل واسترداد", callback_data=f"adm:crm:refund:{tid}")],
        [InlineKeyboardButton(text="✅ إغلاق التذكرة", callback_data=f"adm:crm:close:{tid}")],
        [InlineKeyboardButton(text="🔙", callback_data="adm:crm:tickets")],
    ])
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("adm:crm:reply:"))
async def reply_start(callback: CallbackQuery, state: FSMContext):
    tid = callback.data.split(":")[-1]
    await state.set_state(CrmFSM.ticket_reply)
    await state.update_data(ticket_id=tid)
    await callback.message.answer("💬 اكتب ردك للعميل (سيُرسل مباشرة على تيليجرام):")
    await callback.answer()


@router.message(CrmFSM.ticket_reply)
async def reply_send(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        async with AsyncSessionLocal() as session:
            await crm_service.reply_ticket(
                session, data["ticket_id"],
                message_text=message.text or "",
                reviewer=f"tg:{message.from_user.id}",
            )
        await state.clear()
        await message.answer("✅ تم إرسال الرد للعميل.")
    except ValueError as e:
        await message.answer(str(e))
        await state.clear()


@router.callback_query(F.data.startswith("adm:crm:close:"))
async def close_ticket(callback: CallbackQuery):
    tid = callback.data.split(":")[-1]
    async with AsyncSessionLocal() as session:
        await crm_service.close_ticket(
            session, tid, reviewer=f"tg:{callback.from_user.id}"
        )
    await callback.answer("تم الإغلاق", show_alert=False)
    await list_tickets(callback)


@router.callback_query(F.data.startswith("adm:crm:refund:"))
async def refund_ticket(callback: CallbackQuery):
    tid = callback.data.split(":")[-1]
    try:
        async with AsyncSessionLocal() as session:
            result = await crm_service.resolve_refund_ticket(
                session, tid, reviewer=f"tg:{callback.from_user.id}"
            )
        await callback.message.edit_text(
            f"✅ تم الاسترداد `${result['refund_amount']:.2f}` وإغلاق التذكرة.",
            parse_mode="Markdown",
            reply_markup=_crm_hub_kb(),
        )
        await callback.answer()
    except ValueError as e:
        await callback.answer(str(e)[:180], show_alert=True)
