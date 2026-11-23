"""
Customer Bot free-text AI Concierge — only when NOT inside an active FSM state.
"""
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from loguru import logger
from sqlalchemy import select

from src.core.database import AsyncSessionLocal
from src.core.models.entities import Customer
from src.core.services.ai_concierge_service import ai_concierge_service

router = Router(name="ai_concierge")


def _build_cta_keyboard(result: dict) -> InlineKeyboardMarkup:
    rows = []
    for p in (result.get("products") or [])[:5]:
        title = (p.get("title_ar") or "منتج")[:28]
        price = p.get("price_usd")
        label = f"⚡ اشترِ {title} الآن — ${price}"
        rows.append([InlineKeyboardButton(
            text=label[:64],
            callback_data=f"prod:{p['product_id']}",
        )])
    w = result.get("warranty_cta")
    if w:
        rows.append([InlineKeyboardButton(
            text=f"🛡️ فتح طلب استبدال للطلب #{w.get('order_number')}",
            callback_data=f"warranty:{w.get('order_id')}",
        )])
    if result.get("escalate") or True:
        rows.append([InlineKeyboardButton(
            text="🛟 التحدث مع موظف دعم",
            callback_data="ai:escalate",
        )])
    rows.append([InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "ai:escalate")
async def cb_escalate(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "🛟 تم تحويلك لمسار الدعم.\n"
        "اكتب وصف مشكلتك باختصار، أو افتح تذكرة من القائمة ← الدعم.",
    )
    await callback.answer()


@router.message(F.text & ~F.text.startswith("/"))
async def handle_free_text_concierge(message: Message, state: FSMContext):
    """Catch natural language ONLY when FSM is idle."""
    current = await state.get_state()
    if current is not None:
        # Never interfere with Sham Cash, qty, email, coupon, Binance OID, search, etc.
        return

    text = (message.text or "").strip()
    if not text or len(text) < 2:
        return
    # Ignore pure menu button labels / emoji-only short taps that aren't queries
    menu_labels = {
        "🛒 المتجر", "🏠 الرئيسية", "📦 طلباتي", "🎫 الدعم",
        "💰 المحفظة", "🎁 دعوة أصدقاء", "🔍 بحث",
    }
    if text in menu_labels:
        return

    tg = message.from_user
    if not tg:
        return

    allowed = await ai_concierge_service.check_rate_limit(tg.id)
    if not allowed:
        await message.answer(
            "⏳ وصلت للحد الأقصى لرسائل المساعد الذكي لهذه الساعة. "
            "جرّب القائمة أو البحث لاحقاً."
        )
        return

    async with AsyncSessionLocal() as session:
        cust = (await session.execute(
            select(Customer).where(Customer.telegram_id == tg.id)
        )).scalar_one_or_none()
        if not cust:
            # Let /start create the customer; don't invent one mid-flow
            return
        try:
            result = await ai_concierge_service.handle_message(
                session, customer=cust, text=text
            )
        except Exception as e:
            logger.exception(f"AI concierge failed: {e}")
            await message.answer(
                "تعذر الرد الآن. استخدم القائمة أو تواصل مع الدعم.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🛟 دعم", callback_data="ai:escalate")],
                ]),
            )
            return

    await message.answer(
        result.get("reply_ar") or "…",
        parse_mode="HTML",
        reply_markup=_build_cta_keyboard(result),
    )
