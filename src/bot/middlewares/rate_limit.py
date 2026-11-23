import time
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, and_
from loguru import logger

from src.core.redis import event_bus
from src.core.database import AsyncSessionLocal
from src.core.models.entities import Customer, Order
from src.core.models.enums import OrderState


class AntiSpamRateLimitMiddleware(BaseMiddleware):
    """
    Enterprise Anti-Fraud & Leaky-Bucket Rate Limiter.
    1. Sliding window rate limit per user (max 6 requests / 3 seconds) using Redis.
    2. Strict Unpaid Order Quota: Blocks 'buy:' action if customer has >= 3 active unpaid orders.
    """

    MAX_UNPAID_ORDERS = 3
    RATE_LIMIT_WINDOW = 3  # seconds
    MAX_REQUESTS_PER_WINDOW = 6

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        user_id = user.id

        # ----------------------------------------------------
        # 0. Ban Enforcement (Redis + Database)
        # ----------------------------------------------------
        redis = event_bus._redis
        is_banned = False
        if redis:
            is_banned = bool(await redis.get(f"ban:user:{user_id}"))
        # DB fallback: a ban applied directly in the CRM must take effect even if the
        # Redis key expired or was never written.
        if not is_banned:
            async with AsyncSessionLocal() as session:
                banned = (await session.execute(
                    select(Customer.id).where(
                        and_(Customer.telegram_id == user_id, Customer.is_blocked == True)
                    )
                )).scalar_one_or_none()
                is_banned = banned is not None
        if is_banned:
            if isinstance(event, CallbackQuery):
                await event.answer("🛑 تم حظر حسابك من استخدام البوت.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("🛑 **تم حظر حسابك من استخدام البوت من قبل إدارة المتجر.**\nإذا كنت تعتقد أن هذا حدث بالخطأ، يرجى مراجعة الإدارة.")
            return

        # ----------------------------------------------------
        # 1. Leaky-Bucket / Sliding Window Rate Limiting (Redis)
        # ----------------------------------------------------
        redis = event_bus._redis
        if redis:
            key = f"rate:user:{user_id}"
            now = time.time()
            pipe = redis.pipeline()
            # Remove timestamps older than window
            pipe.zremrangebyscore(key, 0, now - self.RATE_LIMIT_WINDOW)
            # Add current timestamp
            pipe.zadd(key, {str(now): now})
            # Count elements
            pipe.zcard(key)
            pipe.expire(key, self.RATE_LIMIT_WINDOW + 2)
            results = await pipe.execute()
            req_count = results[2]

            if req_count > self.MAX_REQUESTS_PER_WINDOW:
                if isinstance(event, CallbackQuery):
                    await event.answer("⚠️ يرجى الانتظار قليلاً وعدم الضغط المتكرر السريع.", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer("⚠️ أنت ترسل طلبات بسرعة كبيرة! يرجى التمهل بضع ثوانٍ.")
                logger.warning(f"Rate limit exceeded for user {user_id} ({req_count} reqs in {self.RATE_LIMIT_WINDOW}s)")
                return

        # ----------------------------------------------------
        # 2. Max 3 Unpaid Orders Quota Protection
        # ----------------------------------------------------
        buy_prefixes = ("buy:", "buy_qty:", "custom_qty:")
        if isinstance(event, CallbackQuery) and event.data and event.data.startswith(buy_prefixes):
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(Order)
                    .join(Customer, Order.customer_id == Customer.id)
                    .where(
                        and_(
                            Customer.telegram_id == user_id,
                            Order.state.in_([
                                OrderState.CREATED,
                                OrderState.AWAITING_PAYMENT,
                                OrderState.SHAM_CASH_REVIEW
                            ])
                        )
                    )
                )
                res = await session.execute(stmt)
                pending_orders = res.scalars().all()

                if len(pending_orders) >= self.MAX_UNPAID_ORDERS:
                    buttons = []
                    for o in pending_orders:
                        buttons.append([
                            InlineKeyboardButton(
                                text=f"❌ إلغاء الطلب #{o.order_number} (${o.our_price})",
                                callback_data=f"cancel_order:{o.id}"
                            )
                        ])
                    buttons.append([
                        InlineKeyboardButton(text="🔙 العودة للقائمة الرئيسية", callback_data="nav:home")
                    ])
                    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

                    await event.answer(
                        f"⚠️ لديك {len(pending_orders)} طلبات معلقة قيد المعالجة!",
                        show_alert=True
                    )
                    await event.message.edit_text(
                        f"🛑 **تنبيه حماية النظام (الحد الأقصى للطلبات المعلقة):**\n\n"
                        f"لديك حالياً `{len(pending_orders)}` طلبات قيد الانتظار أو الدفع. "
                        f"لحماية النظام والمخزون، لا يمكنك إنشاء طلب جديد قبل إتمام أو إلغاء الطلبات السابقة.\n\n"
                        f"👇 يمكنك إلغاء أي طلب معلق أدناه لمتابعة الشراء:",
                        parse_mode="Markdown",
                        reply_markup=kb
                    )
                    return

        return await handler(event, data)
