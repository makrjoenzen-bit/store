from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from loguru import logger

from src.core.database import AsyncSessionLocal
from src.core.models.entities import Customer, Order, SupportTicket, TicketMessage
from src.core.models.enums import TicketStatus, OrderState
from src.core.redis import event_bus
from src.config import settings

router = Router()


class DisputeState(StatesGroup):
    waiting_for_issue_type = State()
    waiting_for_details = State()
    waiting_for_screenshot = State()


def dispute_reasons_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ المفتاح غير صالح / مستخدم مسبقاً",
                    callback_data=f"disp_reason:{order_id}:invalid_key"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏳ الحساب منتهي أو محظور",
                    callback_data=f"disp_reason:{order_id}:expired_account"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔐 بيانات الحساب غير صحيحة",
                    callback_data=f"disp_reason:{order_id}:wrong_credentials"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❓ مشكلة أخرى في التفعيل",
                    callback_data=f"disp_reason:{order_id}:other_issue"
                )
            ],
            [
                InlineKeyboardButton(text="🔙 إلغاء", callback_data="nav:home")
            ]
        ]
    )


@router.callback_query(F.data.startswith("open_dispute:"))
async def cb_start_dispute(callback: CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":")[1]
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.master_product))
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            await callback.answer("الطلب غير موجود", show_alert=True)
            return

        is_warranty_expired = False
        warranty_note = ""
        if order.warranty_expires_at:
            from datetime import datetime, timezone
            if datetime.now(timezone.utc) > order.warranty_expires_at:
                is_warranty_expired = True
                warranty_note = f"\n⚠️ **تنبيه:** انتهت فترة الضمان لهذا الطلب بتاريخ `{order.warranty_expires_at.strftime('%Y-%m-%d')}`. سيتم تحويل تذكرتك للمراجعة الاستثنائية.\n"
            else:
                warranty_note = f"\n🛡 **الضمان سارٍ حتى:** `{order.warranty_expires_at.strftime('%Y-%m-%d')}`\n"

        await state.set_state(DisputeState.waiting_for_issue_type)
        await state.update_data(
            order_id=order_id,
            order_number=order.order_number,
            is_warranty_expired=is_warranty_expired
        )

        text = (
            f"🛠 **فتح تذكرة دعم للطلب #{order.order_number}**\n\n"
            f"المنتج: {order.master_product.title_ar}"
            f"{warranty_note}\n"
            f"يرجى تحديد نوع المشكلة التي تواجهها لمساعدتك فوراً:"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=dispute_reasons_keyboard(order_id))


@router.callback_query(DisputeState.waiting_for_issue_type, F.data.startswith("disp_reason:"))
async def cb_select_dispute_reason(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    order_id = parts[1]
    reason_code = parts[2]

    reason_labels = {
        "invalid_key": "المفتاح غير صالح / مستخدم مسبقاً",
        "expired_account": "الحساب منتهي الصلاحية أو محظور",
        "wrong_credentials": "بيانات الحساب غير صحيحة",
        "other_issue": "مشكلة عامة في التفعيل",
    }
    subject = reason_labels.get(reason_code, "مشكلة في الطلب")
    await state.update_data(subject=subject)
    await state.set_state(DisputeState.waiting_for_details)

    text = (
        f"📝 **نوع المشكلة:** {subject}\n\n"
        f"يرجى كتابة **شرح مفصل للمشكلة** التي واجهتك أثناء التفعيل:"
    )
    await callback.message.edit_text(text, parse_mode="HTML")


@router.message(DisputeState.waiting_for_details, F.text)
async def handle_dispute_details(message: Message, state: FSMContext):
    details_text = message.text.strip()
    await state.update_data(details=details_text)
    await state.set_state(DisputeState.waiting_for_screenshot)

    skip_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭️ تخطي إرفاق الصورة وإرسال التذكرة", callback_data="skip_ticket_photo")]
        ]
    )
    await message.answer(
        "📸 **(اختياري)** يمكنك إرسال **صورة أو لقطة شاشة** توضح رسالة الخطأ لتسريع التحقق، أو اضغط زر التخطي أدناه:",
        reply_markup=skip_kb
    )


@router.callback_query(DisputeState.waiting_for_screenshot, F.data == "skip_ticket_photo")
async def cb_skip_screenshot(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    await _finalize_ticket_creation(
        bot=callback.bot,
        user=callback.from_user,
        order_id=data.get("order_id"),
        order_number=data.get("order_number"),
        subject=data.get("subject"),
        details=data.get("details"),
        photo_file_id=None,
        reply_target=callback.message,
        is_warranty_expired=data.get("is_warranty_expired", False)
    )
    await state.clear()


@router.message(DisputeState.waiting_for_screenshot, F.photo)
async def handle_ticket_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    photo_file_id = message.photo[-1].file_id
    await _finalize_ticket_creation(
        bot=message.bot,
        user=message.from_user,
        order_id=data.get("order_id"),
        order_number=data.get("order_number"),
        subject=data.get("subject"),
        details=data.get("details"),
        photo_file_id=photo_file_id,
        reply_target=message,
        is_warranty_expired=data.get("is_warranty_expired", False)
    )
    await state.clear()


async def _finalize_ticket_creation(bot, user, order_id, order_number, subject, details, photo_file_id, reply_target, is_warranty_expired: bool = False):
    async with AsyncSessionLocal() as session:
        # Find customer and order
        cust_stmt = select(Customer).where(Customer.telegram_id == user.id)
        customer = (await session.execute(cust_stmt)).scalar_one_or_none()
        if not customer:
            await reply_target.answer("حدث خطأ أثناء تحديد حسابك.")
            return

        subj_prefix = "[⚠️ خارج الضمان] " if is_warranty_expired else ""
        ticket = SupportTicket(
            customer_id=customer.id,
            order_id=order_id,
            subject=f"{subj_prefix}[{order_number}] {subject}",
            status=TicketStatus.OPEN,
            priority=1,
        )
        session.add(ticket)
        await session.flush()

        msg = TicketMessage(
            ticket_id=ticket.id,
            sender_type="customer",
            sender_name=user.first_name or user.username or str(user.id),
            message_text=details,
            attachment_file_id=photo_file_id,
        )
        session.add(msg)

        # Mark order state as dispute open
        if order_id:
            ord_stmt = select(Order).where(Order.id == order_id)
            order = (await session.execute(ord_stmt)).scalar_one_or_none()
            if order:
                order.state = OrderState.DISPUTE_OPEN

        await session.commit()
        ticket_short_id = str(ticket.id)[:8]

        # Publish alert to Redis for Dashboard
        await event_bus.publish(
            "support:ticket_created",
            {
                "ticket_id": str(ticket.id),
                "order_number": order_number,
                "customer_name": user.first_name,
                "subject": subject,
                "details": details,
            }
        )
        await event_bus.publish_alert(
            title=f"New Support Ticket #{ticket_short_id}",
            message=f"Order {order_number}: {subject} - {details[:60]}...",
            level="warning",
            meta={"ticket_id": str(ticket.id), "order_id": order_id}
        )

        # Notify Telegram Admin
        if settings.ADMIN_TELEGRAM_ID:
            try:
                alert_text = (
                    f"🚨 **تذكرة دعم جديدة تم فتحها!**\n\n"
                    f"التذكرة: `#T-{ticket_short_id}`\n"
                    f"الطلب: `#{order_number}`\n"
                    f"العميل: @{user.username or user.id} ({user.first_name})\n"
                    f"الموضوع: {subject}\n"
                    f"التفاصيل:\n_{details}_\n"
                )
                if photo_file_id:
                    await bot.send_photo(
                        chat_id=settings.ADMIN_TELEGRAM_ID,
                        photo=photo_file_id,
                        caption=alert_text,
                        parse_mode="HTML"
                    )
                else:
                    await bot.send_message(
                        chat_id=settings.ADMIN_TELEGRAM_ID,
                        text=alert_text,
                        parse_mode="HTML"
                    )
            except Exception as e:
                logger.error(f"Failed to alert admin of new ticket: {e}")

        confirm_text = (
            f"✅ **تم تسجيل تذكرة الدعم بنجاح!**\n\n"
            f"🔖 **رقم التذكرة:** `#T-{ticket_short_id}`\n"
            f"📦 **الطلب:** `#{order_number}`\n\n"
            f"تم إشعار فريق الدعم الفني بمشكلتك، وسيصلك الرد أو التعويض مباشرة في هذه المحادثة."
        )
        if isinstance(reply_target, Message):
            await reply_target.answer(confirm_text, parse_mode="HTML")
        elif isinstance(reply_target, CallbackQuery):
            await reply_target.message.edit_text(confirm_text, parse_mode="HTML")
        else:
            await bot.send_message(chat_id=user.id, text=confirm_text, parse_mode="HTML")
