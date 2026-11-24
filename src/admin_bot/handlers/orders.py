"""Orders & Sham Cash — claim lock, dual approve, warranty queue."""
from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from loguru import logger

from src.core.database import AsyncSessionLocal
from src.core.services.sham_cash_service import sham_cash_service
from src.core.services.order_admin_service import order_admin_service
from src.core.services.order_claim_service import order_claim_service
from src.core.services.warranty_service import warranty_service
from src.admin_bot.formatters.alerts import format_pending_sham_list, format_recent_orders
from src.admin_bot.keyboards.main_menu import (
    orders_hub_keyboard,
    sham_cash_review_keyboard,
    sham_cash_claim_keyboard,
    warranty_claim_keyboard,
    coming_soon_keyboard,
    order_actions_keyboard,
)
from src.admin_bot.middlewares.admin_acl import is_owner
from src.config import settings

router = Router(name="admin_orders")


class OrderSearchFSM(StatesGroup):
    waiting_number = State()


class ManualDeliverFSM(StatesGroup):
    waiting_payload = State()


class WarrantyReplaceFSM(StatesGroup):
    waiting_payload = State()


class WarrantyRejectFSM(StatesGroup):
    waiting_reason = State()


class SypRateFSM(StatesGroup):
    waiting_rate = State()


@router.message(F.text == "🧾 الطلبات وشام كاش")
@router.message(F.text == "🧾 طلبات المراجعة (شام كاش / باينانس)")
async def msg_orders_hub(message: Message):
    await message.answer(
        "🧾 **الطلبات وشام كاش**\nاختر عرضاً:",
        parse_mode="Markdown",
        reply_markup=orders_hub_keyboard(),
    )


@router.callback_query(F.data == "adm:orders")
async def cb_orders_hub(callback: CallbackQuery):
    await callback.message.edit_text(
        "🧾 **الطلبات وشام كاش**\nاختر عرضاً:",
        parse_mode="Markdown",
        reply_markup=orders_hub_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:orders:sham")
async def cb_pending_sham(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        pending = await sham_cash_service.list_pending(session)
    text = format_pending_sham_list(pending)
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=orders_hub_keyboard())
    await callback.answer()
    uid = callback.from_user.id
    owner = is_owner(uid)
    for inv in pending[:10]:
        oid = inv["order_id"]
        fraud = inv.get("fraud_warning") or ""
        claim_line = ""
        if inv.get("claimed_by_telegram_id"):
            claim_line = f"\n🔒 قيد المعالجة بواسطة: @{inv.get('claimed_by_username') or inv['claimed_by_telegram_id']}"
        card = (
            (f"{fraud}\n\n" if fraud else "")
            + f"🧾 **مراجعة شام كاش**\n\n"
            f"🔖 `{inv.get('order_number')}`\n"
            f"💰 `${inv.get('amount_expected', 0):.2f}`\n"
            f"👤 {inv.get('customer_name') or inv.get('customer_telegram_id')}\n"
            f"📦 {inv.get('product_title') or '—'} × `{inv.get('quantity') or 1}`\n"
            f"🏭 المورد / التاجر: **{inv.get('supplier_name') or '—'}**\n"
            f"📎 `{inv.get('receipt_type') or 'photo'}` · Tx `{inv.get('tx_ref') or '—'}`"
            f"{claim_line}"
        )
        claimed = bool(inv.get("claimed_by_telegram_id"))
        for_claimer = claimed and int(inv["claimed_by_telegram_id"]) == int(uid)
        kb = sham_cash_review_keyboard(
            oid,
            claimed=claimed,
            claimed_by=inv.get("claimed_by_username"),
            for_claimer=for_claimer,
            is_owner=owner,
        )
        await callback.message.answer(card, parse_mode="Markdown", reply_markup=kb)


@router.callback_query(F.data == "adm:orders:recent")
async def cb_recent_orders(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        rows = await sham_cash_service.list_recent_orders(session, limit=15)
    await callback.message.edit_text(
        format_recent_orders(rows),
        parse_mode="Markdown",
        reply_markup=orders_hub_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "adm:orders:search")
async def cb_order_search(callback: CallbackQuery, state: FSMContext):
    await state.set_state(OrderSearchFSM.waiting_number)
    await callback.message.edit_text(
        "🔍 أرسل **رقم الطلب** (مثال: `ORD-20261121-1234`):",
        parse_mode="Markdown",
        reply_markup=coming_soon_keyboard(),
    )
    await callback.answer()


@router.message(OrderSearchFSM.waiting_number)
async def msg_order_search(message: Message, state: FSMContext):
    try:
        async with AsyncSessionLocal() as session:
            o = await order_admin_service.find_by_number(session, message.text or "")
        await state.clear()
        text = (
            f"🧾 **طلب `{o['order_number']}`**\n\n"
            f"الحالة: `{o['state']}`\n"
            f"👤 {o.get('customer_name') or o.get('customer_telegram_id')}\n"
            f"📦 {o.get('product_title') or '—'} × `{o.get('quantity')}`\n"
            f"💵 `${o['our_price']:.2f}` · تكلفة `${o['supplier_cost']:.2f}`\n"
            f"🔌 {o.get('supplier_name') or '—'}"
        )
        await message.answer(
            text,
            parse_mode="Markdown",
            reply_markup=order_actions_keyboard(
                o["id"], can_retry=o["can_retry_jit"], can_refund=o["can_refund"]
            ),
        )
    except ValueError as e:
        await message.answer(str(e))
        await state.clear()


@router.callback_query(F.data.startswith("adm:ord:retry:"))
async def cb_retry_jit(callback: CallbackQuery):
    oid = callback.data.split(":")[-1]
    try:
        async with AsyncSessionLocal() as session:
            result = await order_admin_service.retry_jit(
                session, oid, reviewer=f"tg:{callback.from_user.id}"
            )
        await callback.message.edit_text(
            f"🔄 **أُعيد طابور JIT**\nالطلب `{result['order_number']}` → `{result['state']}`",
            parse_mode="Markdown",
            reply_markup=orders_hub_keyboard(),
        )
        await callback.answer()
    except ValueError as e:
        await callback.answer(str(e)[:180], show_alert=True)


@router.callback_query(F.data.startswith("adm:ord:refund:"))
async def cb_refund_order(callback: CallbackQuery):
    oid = callback.data.split(":")[-1]
    try:
        async with AsyncSessionLocal() as session:
            result = await order_admin_service.refund_to_wallet(
                session, oid, reviewer=f"tg:{callback.from_user.id}"
            )
        await callback.message.edit_text(
            f"💸 **استرداد `${result['refund_amount']:.2f}`**\n"
            f"الطلب `{result['order_number']}` → `{result['state']}`",
            parse_mode="Markdown",
            reply_markup=orders_hub_keyboard(),
        )
        await callback.answer()
    except ValueError as e:
        await callback.answer(str(e)[:180], show_alert=True)


# ---------------- Claim / Unclaim ----------------
@router.callback_query(F.data.startswith("adm:claim:"))
async def cb_claim(callback: CallbackQuery):
    oid = callback.data.split(":")[-1]
    reviewer = f"tg:{callback.from_user.id}"
    try:
        async with AsyncSessionLocal() as session:
            result = await order_claim_service.claim(
                session,
                oid,
                telegram_id=callback.from_user.id,
                username=callback.from_user.username,
                reviewer=reviewer,
            )
        uname = result.get("claimed_by_username") or callback.from_user.id
        await callback.message.edit_text(
            (callback.message.text or "")
            + f"\n\n🔒 قيد المعالجة بواسطة: @{uname}",
            parse_mode="Markdown",
            reply_markup=sham_cash_review_keyboard(
                oid,
                claimed=True,
                claimed_by=str(uname),
                for_claimer=True,
                is_owner=is_owner(callback.from_user.id),
            ),
        )
        await callback.answer("تم استلام الطلب 🔒")
    except ValueError as e:
        await callback.answer(str(e)[:180], show_alert=True)


@router.callback_query(F.data.startswith("adm:unclaim:"))
async def cb_unclaim(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("Owner only", show_alert=True)
        return
    oid = callback.data.split(":")[-1]
    try:
        async with AsyncSessionLocal() as session:
            await order_claim_service.release(
                session, oid, telegram_id=callback.from_user.id, force=True,
                reviewer=f"tg:{callback.from_user.id}",
            )
        await callback.message.edit_reply_markup(
            reply_markup=sham_cash_claim_keyboard(oid)
        )
        await callback.answer("تم فك القفل")
    except ValueError as e:
        await callback.answer(str(e)[:180], show_alert=True)


@router.callback_query(F.data.startswith("adm_sc_ok:"))
async def cb_sham_approve(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    reviewer = (
        f"tg:{callback.from_user.id}"
        + (f"/@{callback.from_user.username}" if callback.from_user.username else "")
    )
    try:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload
            from src.core.models.entities import Order
            order = (await session.execute(
                select(Order).where(Order.id == order_id)
            )).scalar_one_or_none()
            if order and not order_claim_service.can_act(order, callback.from_user.id):
                await callback.answer("يجب استلام الطلب أولاً", show_alert=True)
                return
            result = await sham_cash_service.approve(
                session,
                order_id=order_id,
                reviewer=reviewer,
                admin_user_id=None,
                trigger_jit=True,
            )
        from src.admin_bot.keyboards.main_menu import post_fulfillment_keyboard
        from src.core.services.proof_service import publish_to_proof_channel
        try:
            await publish_to_proof_channel(order_id, stage="paid", force=False)
        except Exception as pe:
            logger.warning(f"Auto proof publish after sham approve failed: {pe}")
        await callback.message.edit_text(
            f"⚡ **قبول وشراء آلي بالـ API**\n\n"
            f"الطلب `{result['order_number']}` → تأكيد الدفع وتشغيل التوريد الآلي.",
            parse_mode="Markdown",
            reply_markup=post_fulfillment_keyboard(order_id),
        )
        await callback.answer("تم القبول وبدء التوريد", show_alert=False)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
    except Exception as e:
        logger.exception(f"Admin Bot sham approve failed: {e}")
        await callback.answer("فشل القبول — راجع السجلات", show_alert=True)


@router.callback_query(F.data.startswith("adm:manual:"))
async def cb_manual_start(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":")[-1]
    await state.set_state(ManualDeliverFSM.waiting_payload)
    await state.update_data(order_id=order_id)
    await callback.message.answer(
        "✍️ أرسل الآن **الحساب / المفتاح** لتسليمه للعميل مباشرة:",
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(ManualDeliverFSM.waiting_payload)
async def msg_manual_payload(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("order_id")
    payload = (message.text or "").strip()
    reviewer = f"tg:{message.from_user.id}"
    try:
        async with AsyncSessionLocal() as session:
            result = await sham_cash_service.approve_manual_delivery(
                session,
                order_id,
                delivered_payload=payload,
                reviewer=reviewer,
            )
        await state.clear()
        from src.admin_bot.keyboards.main_menu import post_fulfillment_keyboard
        from src.core.services.proof_service import publish_to_proof_channel
        try:
            await publish_to_proof_channel(order_id, stage="delivered", force=False)
        except Exception as pe:
            logger.warning(f"Auto proof publish after manual deliver failed: {pe}")
        await message.answer(
            f"✅ **تم التسليم اليدوي**\nالطلب `{result['order_number']}`",
            parse_mode="Markdown",
            reply_markup=post_fulfillment_keyboard(str(order_id)),
        )
    except Exception as e:
        await state.clear()
        await message.answer(f"فشل التسليم: {e}")


@router.callback_query(F.data.startswith("adm:proof:"))
async def cb_publish_proof(callback: CallbackQuery):
    """1-click publish to قناة التوثيق (idempotent)."""
    order_id = callback.data.split(":", 2)[-1]
    from src.core.services.proof_service import publish_to_proof_channel
    try:
        result = await publish_to_proof_channel(order_id, stage="delivered", force=True)
        if result.get("status") == "ok":
            await callback.answer("تم النشر في قناة التوثيق ✅", show_alert=True)
            try:
                await callback.message.answer(
                    f"📢 نُشر توثيق الطلب `{result.get('order_number')}` في قناة التوثيق.",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        elif result.get("reason") == "already_posted":
            await callback.answer("تم نشر هذا الطلب مسبقاً (لا تكرار).", show_alert=True)
        elif result.get("reason") == "proof_channel_not_configured":
            await callback.answer("قناة التوثيق غير مهيأة — اضبط proof_channel_id", show_alert=True)
        else:
            await callback.answer(f"تعذر النشر: {result.get('reason')}", show_alert=True)
    except Exception as e:
        logger.exception(f"adm:proof failed: {e}")
        await callback.answer("فشل النشر — راجع السجلات", show_alert=True)


@router.callback_query(F.data.startswith("adm_sc_no:"))
async def cb_sham_reject(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    reason = "إيصال غير صالح أو لم يتم استلام الدفع"
    reviewer = (
        f"tg:{callback.from_user.id}"
        + (f"/@{callback.from_user.username}" if callback.from_user.username else "")
    )
    try:
        async with AsyncSessionLocal() as session:
            result = await sham_cash_service.reject(
                session,
                order_id=order_id,
                reviewer=reviewer,
                reason=reason,
                admin_user_id=None,
            )
        await callback.message.edit_text(
            f"❌ **تم رفض الإيصال**\n\n"
            f"الطلب `{result['order_number']}`\n"
            f"السبب: {reason}",
            parse_mode="Markdown",
            reply_markup=coming_soon_keyboard(),
        )
        await callback.answer("تم الرفض وإشعار العميل", show_alert=False)
    except ValueError as e:
        await callback.answer(str(e), show_alert=True)
    except Exception as e:
        logger.exception(f"Admin Bot sham reject failed: {e}")
        await callback.answer("فشل الرفض — راجع السجلات", show_alert=True)


# ---------------- Warranty ----------------
@router.message(F.text == "🛡️ طلبات الضمان والاستبدال")
@router.callback_query(F.data == "adm:warranty")
async def warranty_hub(event: Message | CallbackQuery):
    async with AsyncSessionLocal() as session:
        rows = await warranty_service.list_open(session)
    text = "🛡️ **طلبات الضمان المفتوحة**\n\n"
    if not rows:
        text += "لا توجد طلبات مفتوحة."
    else:
        for r in rows[:15]:
            text += (
                f"• `{r['order_number']}` — {r.get('product_title') or '—'}\n"
                f"  🏭 {r.get('supplier_name') or '—'} · Ext `{r.get('external_order_id') or '—'}`\n"
                f"  ⏳ {r.get('days_remaining')} يوم\n"
            )
    kb = coming_soon_keyboard()
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        await event.answer()
        msg = event.message
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)
        msg = event
    for r in rows[:8]:
        card = (
            f"🛡️ **ضمان** `{r['order_number']}`\n"
            f"{r.get('issue_description')}\n"
            f"🏭 المورد: **{r.get('supplier_name') or '—'}**\n"
            f"🔖 External: `{r.get('external_order_id') or '—'}`"
        )
        await msg.answer(
            card, parse_mode="Markdown",
            reply_markup=warranty_claim_keyboard(r["claim_id"]),
        )


@router.callback_query(F.data.startswith("adm:warranty:replace:"))
async def cb_warranty_replace(callback: CallbackQuery, state: FSMContext):
    claim_id = callback.data.split(":")[-1]
    await state.set_state(WarrantyReplaceFSM.waiting_payload)
    await state.update_data(claim_id=claim_id)
    await callback.message.answer("✍️ أرسل **البديل** (حساب/مفتاح) لإرساله للزبون:")
    await callback.answer()


@router.message(WarrantyReplaceFSM.waiting_payload)
async def msg_warranty_replace(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        async with AsyncSessionLocal() as session:
            await warranty_service.deliver_replacement(
                session,
                data["claim_id"],
                replacement_payload=(message.text or "").strip(),
                reviewer=f"tg:{message.from_user.id}",
            )
        await state.clear()
        await message.answer("✅ تم إرسال البديل للزبون.")
    except Exception as e:
        await state.clear()
        await message.answer(f"فشل: {e}")


@router.callback_query(F.data.startswith("adm:warranty:refund:"))
async def cb_warranty_refund(callback: CallbackQuery):
    claim_id = callback.data.split(":")[-1]
    try:
        async with AsyncSessionLocal() as session:
            result = await warranty_service.refund_to_wallet(
                session, claim_id, reviewer=f"tg:{callback.from_user.id}"
            )
        await callback.message.edit_text(
            f"💸 استرداد `${result['refund_amount']:.2f}` تم.",
            parse_mode="Markdown",
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(str(e)[:160], show_alert=True)


@router.callback_query(F.data.startswith("adm:warranty:reject:"))
async def cb_warranty_reject_start(callback: CallbackQuery, state: FSMContext):
    claim_id = callback.data.split(":")[-1]
    await state.set_state(WarrantyRejectFSM.waiting_reason)
    await state.update_data(claim_id=claim_id)
    await callback.message.answer("أرسل سبب الرفض:")
    await callback.answer()


@router.message(WarrantyRejectFSM.waiting_reason)
async def msg_warranty_reject(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        async with AsyncSessionLocal() as session:
            await warranty_service.reject(
                session,
                data["claim_id"],
                reason=(message.text or "").strip(),
                reviewer=f"tg:{message.from_user.id}",
            )
        await state.clear()
        await message.answer("تم رفض طلب الضمان.")
    except Exception as e:
        await state.clear()
        await message.answer(f"فشل: {e}")


# ---------------- SYP rate (Owner) ----------------
@router.message(F.text == "⚙️ إعدادات شام كاش / سعر الصرف")
@router.callback_query(F.data == "adm:settings:syp")
async def syp_settings(event: Message | CallbackQuery, state: FSMContext):
    if not is_owner(event.from_user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Owner only", show_alert=True)
        else:
            await event.answer("⛔ للمالك فقط")
        return
    from src.core.services.syp_rate_service import get_sham_cash_rate
    async with AsyncSessionLocal() as session:
        rate, accept = await get_sham_cash_rate(session)
    text = (
        f"💱 **سعر شام كاش**\n\n"
        f"حالياً: `1 USD = {rate} SYP`\n"
        f"عرض الليرة: `{'مفعّل' if accept else 'معطّل'}`\n\n"
        f"أرسل السعر الجديد (مثال: `11500`) أو `off` لتعطيل عرض الليرة:"
    )
    await state.set_state(SypRateFSM.waiting_rate)
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown")
        await event.answer()
    else:
        await event.answer(text, parse_mode="Markdown")


@router.message(SypRateFSM.waiting_rate)
async def msg_syp_rate(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        await state.clear()
        return
    from decimal import Decimal
    from sqlalchemy import select
    from src.core.models.entities import BotConfig, AuditLog
    from src.core.services.audit_helpers import merge_audit_new_values
    raw = (message.text or "").strip().lower()
    async with AsyncSessionLocal() as session:
        if raw in ("off", "0", "false"):
            row = (await session.execute(
                select(BotConfig).where(BotConfig.key == "sham_cash_accept_syp")
            )).scalar_one_or_none()
            if not row:
                row = BotConfig(key="sham_cash_accept_syp", value=False)
                session.add(row)
            else:
                row.value = False
            await session.commit()
            await state.clear()
            await message.answer("تم تعطيل عرض الليرة السورية.")
            return
        try:
            rate = Decimal(raw.replace(",", ""))
        except Exception:
            await message.answer("رقم غير صالح. مثال: 11500")
            return
        row = (await session.execute(
            select(BotConfig).where(BotConfig.key == "sham_cash_usd_to_syp_rate")
        )).scalar_one_or_none()
        if not row:
            row = BotConfig(key="sham_cash_usd_to_syp_rate", value=float(rate))
            session.add(row)
        else:
            row.value = float(rate)
        acc = (await session.execute(
            select(BotConfig).where(BotConfig.key == "sham_cash_accept_syp")
        )).scalar_one_or_none()
        if not acc:
            session.add(BotConfig(key="sham_cash_accept_syp", value=True))
        else:
            acc.value = True
        session.add(AuditLog(
            action="sham_cash_rate_update",
            entity_type="bot_config",
            entity_id=None,
            old_values={},
            new_values=merge_audit_new_values(f"tg:{message.from_user.id}", {"rate": float(rate)}),
        ))
        await session.commit()
    await state.clear()
    await message.answer(f"✅ تم تحديث السعر: 1 USD = `{rate}` SYP", parse_mode="Markdown")
