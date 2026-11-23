import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from loguru import logger
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from sqlalchemy import select, and_

from src.config import settings
from src.core.database import AsyncSessionLocal
from src.core.models.entities import BroadcastCampaign, Customer
from src.core.models.enums import CustomerStatus
from src.core.redis import event_bus


async def run_broadcast_task(campaign_id: str):
    """
    Executes safe, rate-limited broadcast campaign at max 25 msgs/second.
    Prevents FloodWait 429 bans and streams progress to Redis event bus & DB.
    """
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not configured. Broadcast aborted.")
        return

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    import uuid as _uuid
    try:
        camp_uuid = _uuid.UUID(str(campaign_id))
    except (ValueError, AttributeError):
        logger.error(f"Invalid campaign id: {campaign_id}")
        return

    try:
        async with AsyncSessionLocal() as session:
            stmt = select(BroadcastCampaign).where(BroadcastCampaign.id == camp_uuid)
            campaign = (await session.execute(stmt)).scalar_one_or_none()
            if not campaign:
                logger.error(f"BroadcastCampaign {campaign_id} not found.")
                return

            # Select target audience
            cust_stmt = select(Customer.telegram_id).where(
                and_(
                    Customer.account_status == CustomerStatus.ACTIVE,
                    Customer.is_blocked == False
                )
            )
            if campaign.target_audience == "buyers_only":
                cust_stmt = cust_stmt.where(Customer.total_orders > 0)
            elif campaign.target_audience == "balance_holders":
                cust_stmt = cust_stmt.where(Customer.wallet_balance > Decimal("0.0"))

            targets = (await session.execute(cust_stmt)).scalars().all()
            campaign.total_targets = len(targets)
            campaign.status = "running"
            await session.commit()

        logger.info(f"Starting broadcast '{campaign.title}' to {len(targets)} customers at 25 msgs/sec.")

        # Prepare keyboard if CTA button is set
        kb = None
        if campaign.cta_button_text and campaign.cta_button_url:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=campaign.cta_button_text, url=campaign.cta_button_url)]
                ]
            )

        sent_count = 0
        failed_count = 0

        # Max 25 requests per second => 1 msg every 0.04s
        for idx, tg_id in enumerate(targets):
            try:
                photo_ref = campaign.image_url
                if photo_ref and str(photo_ref).startswith("tgfile:"):
                    photo_ref = str(photo_ref).split("tgfile:", 1)[1]
                if photo_ref:
                    await bot.send_photo(
                        chat_id=tg_id,
                        photo=photo_ref,
                        caption=campaign.message_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=kb,
                    )
                else:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=campaign.message_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=kb,
                    )
                sent_count += 1
            except TelegramRetryAfter as e:
                logger.warning(f"Telegram FloodWait hit during broadcast: sleeping {e.retry_after}s")
                await asyncio.sleep(e.retry_after + 1)
                # Retry once
                try:
                    await bot.send_message(chat_id=tg_id, text=campaign.message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
                    sent_count += 1
                except Exception:
                    failed_count += 1
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                # User blocked the bot / invalid chat. Persist it so future campaigns
                # stop targeting the same unreachable user forever.
                failed_count += 1
                try:
                    async with AsyncSessionLocal() as session:
                        blocked = (await session.execute(
                            select(Customer).where(Customer.telegram_id == tg_id)
                        )).scalar_one_or_none()
                        if blocked:
                            blocked.is_blocked = True
                            blocked.blocked_reason = f"broadcast_unreachable: {type(e).__name__}"
                            blocked.account_status = CustomerStatus.BANNED
                            await session.commit()
                    if event_bus and event_bus._redis:
                        await event_bus._redis.set(f"ban:user:{tg_id}", "1", ex=86400 * 365)
                except Exception as persist_err:
                    logger.warning(f"Could not persist blocked broadcast target {tg_id}: {persist_err}")
            except Exception as e:
                logger.error(f"Broadcast error for {tg_id}: {e}")
                failed_count += 1

            # Rate limit throttle: sleep 40ms to stay strictly under 25 req/sec
            await asyncio.sleep(0.04)

            # Update DB and publish progress every 25 messages or on finish
            if (idx + 1) % 25 == 0 or idx == len(targets) - 1:
                async with AsyncSessionLocal() as session:
                    stmt = select(BroadcastCampaign).where(BroadcastCampaign.id == campaign_id)
                    c = (await session.execute(stmt)).scalar_one_or_none()
                    if c:
                        c.sent_count = sent_count
                        c.failed_count = failed_count
                        await session.commit()

                # Stream progress event for WebSocket
                await event_bus.publish("broadcast:progress", {
                    "campaign_id": campaign_id,
                    "total": len(targets),
                    "sent": sent_count,
                    "failed": failed_count,
                    "percent": round((sent_count + failed_count) / max(len(targets), 1) * 100, 1),
                })

        # Mark campaign completed
        async with AsyncSessionLocal() as session:
            stmt = select(BroadcastCampaign).where(BroadcastCampaign.id == campaign_id)
            c = (await session.execute(stmt)).scalar_one_or_none()
            if c:
                c.status = "completed"
                c.completed_at = datetime.now(timezone.utc)
                c.sent_count = sent_count
                c.failed_count = failed_count
                await session.commit()

        logger.info(f"Broadcast '{campaign.title}' completed: {sent_count} sent, {failed_count} failed.")
    except Exception as e:
        logger.exception(f"Unhandled error in broadcast worker: {e}")
        async with AsyncSessionLocal() as session:
            stmt = select(BroadcastCampaign).where(BroadcastCampaign.id == camp_uuid)
            c = (await session.execute(stmt)).scalar_one_or_none()
            if c:
                c.status = "failed"
                await session.commit()
    finally:
        await bot.session.close()
