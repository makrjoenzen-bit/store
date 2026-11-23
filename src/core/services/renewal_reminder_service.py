"""3-day subscription renewal reminders (sent once per order)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, List

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.models.entities import Order, MasterProduct, Customer
from src.core.models.enums import OrderState
from src.core.redis import event_bus


class RenewalReminderService:
    async def send_due_reminders(self, session: AsyncSession) -> int:
        """
        Find COMPLETED orders with subscription_duration_days where
        delivered_at + duration - 3d <= now < delivered_at + duration
        and renewal_reminder_sent is False. Send one Arabic DM each.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(Order)
            .join(MasterProduct, Order.master_product_id == MasterProduct.id)
            .where(
                and_(
                    Order.state == OrderState.COMPLETED,
                    Order.delivered_at.isnot(None),
                    Order.renewal_reminder_sent == False,  # noqa: E712
                    MasterProduct.subscription_duration_days.isnot(None),
                    MasterProduct.subscription_duration_days > 0,
                )
            )
            .options(
                selectinload(Order.customer),
                selectinload(Order.master_product),
            )
            .limit(100)
        )
        orders = (await session.execute(stmt)).scalars().all()
        sent = 0
        for order in orders:
            prod = order.master_product
            if not prod or not order.delivered_at or not order.customer:
                continue
            duration = int(prod.subscription_duration_days or 0)
            delivered = order.delivered_at
            if delivered.tzinfo is None:
                delivered = delivered.replace(tzinfo=timezone.utc)
            expires = delivered + timedelta(days=duration)
            remind_at = expires - timedelta(days=3)
            if now < remind_at or now > expires:
                continue
            tg = order.customer.telegram_id
            title = prod.title_ar
            text = (
                f"⏰ **تذكير تجديد الاشتراك**\n\n"
                f"اشتراكك في **{title}** ينتهي خلال ~3 أيام.\n"
                f"جدّد الآن لتفادي الانقطاع 👇"
            )
            await event_bus.publish("bot:send_message", {
                "telegram_id": tg,
                "text": text,
                "reply_markup": {
                    "inline_keyboard": [[
                        {
                            "text": "🔄 تجديد الاشتراك الآن",
                            "url": f"https://t.me/share?text=buy_{prod.id}",
                            # Bot deep-link handled by customer bot listener via callback fallback:
                            "callback_data": f"prod:{prod.id}",
                        }
                    ]]
                },
                # Prefer start deep-link payload for listener
                "renewal_product_id": str(prod.id),
            })
            # Also publish dedicated channel for bot_main to attach proper keyboard
            await event_bus.publish("bot:renewal_reminder", {
                "telegram_id": tg,
                "product_id": str(prod.id),
                "product_title": title,
                "order_number": order.order_number,
                "text": text,
            })
            order.renewal_reminder_sent = True
            sent += 1
        if sent:
            await session.commit()
        return sent


renewal_reminder_service = RenewalReminderService()
