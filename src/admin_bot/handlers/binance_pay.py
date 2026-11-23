"""
Admin Bot — Binance Pay Order ID manual fallback.
Uses shared payment_verification_service (+ optional Spot API lookup). No duplicated confirm logic.
"""
from __future__ import annotations

from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.database import AsyncSessionLocal
from src.core.models.entities import Order, PaymentInvoice
from src.core.models.enums import InvoiceStatus, PaymentMethod, OrderState
from src.core.services.payment_verification import payment_verification_service
from src.outgest.binance_pay_reader import binance_pay_reader
from src.admin_bot.keyboards.main_menu import (
    orders_hub_keyboard,
    coming_soon_keyboard,
)

router = Router(name="admin_binance_pay")


class BinancePayFallbackState(StatesGroup):
    waiting_pay_order_id = State()
    waiting_order_number = State()


@router.callback_query(F.data == "adm:orders:binance")
async def cb_binance_fallback_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BinancePayFallbackState.waiting_pay_order_id)
    await callback.message.edit_text(
        "🟡 **تأكيد Binance Pay يدوياً**\n\n"
        "أرسل **Pay Order ID / Transaction ID** من تطبيق Binance.\n"
        "سنحاول أولاً التحقق عبر Spot API ثم تأكيد الفاتورة المطابقة.",
        parse_mode="Markdown",
        reply_markup=coming_soon_keyboard(),
    )
    await callback.answer()


@router.message(BinancePayFallbackState.waiting_pay_order_id)
async def msg_pay_order_id(message: Message, state: FSMContext):
    pay_order_id = (message.text or "").strip()
    if not pay_order_id or len(pay_order_id) < 4:
        await message.answer("معرّف غير صالح. أرسل Pay Order ID كاملاً.")
        return

    await state.update_data(pay_order_id=pay_order_id)

    # Try Spot API lookup for amount
    looked_up = None
    if binance_pay_reader.is_configured:
        try:
            looked_up = await binance_pay_reader.find_by_pay_order_id(pay_order_id)
        except Exception as e:
            logger.warning(f"Binance Spot lookup failed: {e}")

    if looked_up:
        amount = Decimal(str(looked_up["amount"]))
        await state.update_data(looked_up_amount=str(amount))
        # Try auto-match by unique_amount
        try:
            async with AsyncSessionLocal() as session:
                result = await payment_verification_service.confirm_binance_by_pay_order_id(
                    session,
                    pay_order_id=pay_order_id,
                    amount=amount,
                    reviewer=f"tg:{message.from_user.id}",
                )
            await state.clear()
            await message.answer(
                f"✅ **تم التأكيد تلقائياً**\n\n"
                f"الطلب `{result.get('order_number')}`\n"
                f"المبلغ `${amount}`\n"
                f"المرجع `{pay_order_id}`",
                parse_mode="Markdown",
                reply_markup=orders_hub_keyboard(),
            )
            return
        except ValueError:
            pass  # fall through to ask order number

    await state.set_state(BinancePayFallbackState.waiting_order_number)
    hint = ""
    if looked_up:
        hint = f"\n(Spot API: وجدنا مبلغ `${looked_up['amount']}` لهذا المرجع)\n"
    await message.answer(
        f"لم يُعثر على فاتورة مطابقة بالمبلغ تلقائياً.{hint}\n"
        "أرسل الآن **رقم الطلب** (order_number) لتأكيد هذه الدفعة يدوياً:",
        parse_mode="Markdown",
    )


@router.message(BinancePayFallbackState.waiting_order_number)
async def msg_order_number(message: Message, state: FSMContext):
    order_number = (message.text or "").strip().lstrip("#")
    data = await state.get_data()
    pay_order_id = data.get("pay_order_id")
    looked_up_amount = data.get("looked_up_amount")

    if not order_number or not pay_order_id:
        await state.clear()
        await message.answer("انتهت الجلسة. ابدأ من جديد من قائمة الطلبات.")
        return

    reviewer = f"tg:{message.from_user.id}"
    try:
        async with AsyncSessionLocal() as session:
            order = (await session.execute(
                select(Order)
                .where(Order.order_number == order_number)
                .options(selectinload(Order.invoice), selectinload(Order.customer))
            )).scalar_one_or_none()
            if not order:
                await message.answer(f"الطلب `{order_number}` غير موجود.", parse_mode="Markdown")
                return

            inv = order.invoice
            if not inv or inv.payment_method != PaymentMethod.BINANCE_PAY:
                # Allow override: set method if pending
                if inv and inv.status == InvoiceStatus.PENDING:
                    inv.payment_method = PaymentMethod.BINANCE_PAY
                else:
                    await message.answer("لا توجد فاتورة Binance Pay معلّقة لهذا الطلب.")
                    return

            if order.state not in (OrderState.CREATED, OrderState.AWAITING_PAYMENT):
                await message.answer(f"حالة الطلب لا تسمح بالتأكيد ({order.state}).")
                return

            amount = None
            if looked_up_amount:
                amount = Decimal(str(looked_up_amount))
            elif inv.unique_amount:
                amount = inv.unique_amount
            else:
                amount = inv.amount_expected or order.our_price

            result = await payment_verification_service.confirm_binance_by_pay_order_id(
                session,
                pay_order_id=pay_order_id,
                amount=amount,
                order_id=order.id,
                reviewer=reviewer,
            )
        await state.clear()
        await message.answer(
            f"✅ **تم تأكيد Binance Pay يدوياً**\n\n"
            f"الطلب `{result.get('order_number')}`\n"
            f"المرجع `{pay_order_id}`\n"
            f"جاري التوريد الآلي (JIT).",
            parse_mode="Markdown",
            reply_markup=orders_hub_keyboard(),
        )
    except ValueError as e:
        await message.answer(str(e))
    except Exception as e:
        logger.exception(f"Admin Binance fallback failed: {e}")
        await message.answer("فشل التأكيد — راجع السجلات.")
        await state.clear()
