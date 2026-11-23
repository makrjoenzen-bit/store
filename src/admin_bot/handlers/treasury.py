"""Treasury menu — calls shared treasury_service (same as Dashboard GET /api/treasury/summary)."""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

from src.core.database import AsyncSessionLocal
from src.core.services.treasury_service import treasury_service
from src.admin_bot.formatters.alerts import format_treasury_card
from src.admin_bot.keyboards.main_menu import coming_soon_keyboard

router = Router(name="admin_treasury")


async def _send_treasury(target: Message):
    async with AsyncSessionLocal() as session:
        summary = await treasury_service.get_summary(session)
    await target.answer(
        format_treasury_card(summary),
        parse_mode="Markdown",
        reply_markup=coming_soon_keyboard(),
    )


@router.message(F.text == "📊 الخزينة والأرباح")
async def msg_treasury(message: Message):
    await _send_treasury(message)


@router.callback_query(F.data == "adm:treasury")
async def cb_treasury(callback: CallbackQuery):
    async with AsyncSessionLocal() as session:
        summary = await treasury_service.get_summary(session)
    text = format_treasury_card(summary)
    await callback.message.edit_text(
        text, parse_mode="Markdown", reply_markup=coming_soon_keyboard()
    )
    await callback.answer()
