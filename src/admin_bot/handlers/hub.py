"""Main hub — start/menu + home. Section handlers live in dedicated routers."""
from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery

from src.admin_bot.keyboards.main_menu import (
    admin_main_menu_reply,
    admin_main_menu_inline,
)
from src.admin_bot.middlewares.admin_acl import is_staff_only, is_owner
from src.config import settings

router = Router(name="admin_hub")


def _staff_flag(user_id: int) -> bool:
    return is_staff_only(user_id) and not is_owner(user_id)


@router.message(CommandStart())
@router.message(Command("menu"))
@router.message(Command("start"))
async def cmd_start(message: Message):
    staff = _staff_flag(message.from_user.id)
    role = "موظف (Staff)" if staff else "مالك (Owner)"
    await message.answer(
        f"🛡 **بوت إدارة المتجر — لوحة تحكم الجوال**\n\n"
        f"دورك: **{role}**\n"
        f"اختر قسماً من القائمة أدناه.",
        parse_mode="Markdown",
        reply_markup=admin_main_menu_reply(staff_only=staff),
    )
    await message.answer("اختصارات سريعة:", reply_markup=admin_main_menu_inline(staff_only=staff))


@router.callback_query(F.data == "adm:home")
async def cb_home(callback: CallbackQuery):
    staff = _staff_flag(callback.from_user.id)
    await callback.message.edit_text(
        "🛡 **القائمة الرئيسية**",
        parse_mode="Markdown",
        reply_markup=admin_main_menu_inline(staff_only=staff),
    )
    await callback.answer()


# Alias staff menu label → orders hub
@router.message(F.text == "🧾 طلبات المراجعة (شام كاش / باينانس)")
async def msg_staff_orders(message: Message):
    from src.admin_bot.handlers.orders import msg_orders_hub
    await msg_orders_hub(message)


@router.message(F.text == "🛟 تذاكر الدعم الفني")
async def msg_staff_support(message: Message):
    # Same entry as CRM hub (tickets live there)
    message_text_backup = message.text
    try:
        # Simulate CRM menu open via answering with CRM prompt
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        await message.answer(
            "🛟 **تذاكر الدعم الفني**\nاختر من قائمة العملاء والدعم:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👥 العملاء والدعم", callback_data="adm:crm")],
            ]),
        )
    finally:
        pass
