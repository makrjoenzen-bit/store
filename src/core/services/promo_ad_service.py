"""
Visual Promo Ad Creator — shared by Dashboard API and Dedicated Admin Bot.
Publishes photo+caption+2 CTA buttons to Proof Channel, Main Channel, and/or customer broadcast.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.models.entities import (
    AuditLog, BotConfig, BroadcastCampaign, MasterProduct,
)
from src.core.redis import event_bus
from src.core.services.audit_helpers import merge_audit_new_values


def _uid(value: Any):
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now():
    return datetime.now(timezone.utc)


async def _cfg(session: AsyncSession, key: str, default=None):
    row = (await session.execute(select(BotConfig).where(BotConfig.key == key))).scalar_one_or_none()
    if row is None or row.value is None:
        return default
    return row.value


def build_promo_keyboard(
    *,
    bot_username: str,
    product_id: Optional[str],
    miniapp_url: str,
    store_button_label: str = "🏪 فتح المتجر (الموقع / Mini App)",
    bot_button_label: str = "🤖 الشراء عبر البوت",
) -> InlineKeyboardMarkup:
    if product_id:
        bot_url = f"https://t.me/{bot_username}?start=buy_{product_id}"
    else:
        bot_url = f"https://t.me/{bot_username}"
    store_url = (miniapp_url or settings.MINIAPP_URL or bot_url).strip() or bot_url
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=store_button_label[:64], url=store_url)],
        [InlineKeyboardButton(text=bot_button_label[:64], url=bot_url)],
    ])


class PromoAdService:
    async def publish_promo_ad(
        self,
        session: AsyncSession,
        *,
        caption: str,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
        product_id: Optional[str] = None,
        image_url: Optional[str] = None,
        image_file_id: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        image_filename: str = "promo.jpg",
        store_button_label: str = "🏪 فتح المتجر (الموقع / Mini App)",
        bot_button_label: str = "🤖 الشراء عبر البوت",
        publish_proof_channel: bool = True,
        publish_main_channel: bool = True,
        broadcast_customers: bool = False,
        pin_in_channel: bool = False,
        bot_token: Optional[str] = None,
        bot_username: Optional[str] = None,
    ) -> Dict[str, Any]:
        caption = (caption or "").strip()
        if not caption:
            raise ValueError("Ad caption is required")

        if product_id:
            prod = (await session.execute(
                select(MasterProduct).where(MasterProduct.id == _uid(product_id))
            )).scalar_one_or_none()
            if not prod:
                raise ValueError("Target product not found")

        token = bot_token or settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not configured")

        bot = Bot(token=token)
        delivery = {
            "proof_channel": 0,
            "main_channel": 0,
            "broadcast_campaign_id": None,
            "pinned": False,
        }
        try:
            me = await bot.get_me()
            uname = (bot_username or me.username or "shop").lstrip("@")
            miniapp = await _cfg(session, "miniapp_url", settings.MINIAPP_URL)
            kb = build_promo_keyboard(
                bot_username=uname,
                product_id=str(product_id) if product_id else None,
                miniapp_url=str(miniapp or settings.MINIAPP_URL),
                store_button_label=store_button_label,
                bot_button_label=bot_button_label,
            )

            proof_id = await _cfg(session, "proof_channel_id", settings.TELEGRAM_PROOF_CHANNEL_ID)
            main_id = await _cfg(session, "main_channel_id", settings.TELEGRAM_PUBLIC_CHANNEL_ID)

            async def _send(chat_id) -> Optional[int]:
                if not chat_id:
                    return None
                target = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
                try:
                    if image_bytes:
                        msg = await bot.send_photo(
                            chat_id=target,
                            photo=BufferedInputFile(image_bytes, filename=image_filename),
                            caption=caption,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=kb,
                        )
                    elif image_file_id:
                        msg = await bot.send_photo(
                            chat_id=target,
                            photo=image_file_id,
                            caption=caption,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=kb,
                        )
                    elif image_url:
                        msg = await bot.send_photo(
                            chat_id=target,
                            photo=image_url,
                            caption=caption,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=kb,
                        )
                    else:
                        msg = await bot.send_message(
                            chat_id=target,
                            text=caption,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=kb,
                        )
                    if pin_in_channel:
                        try:
                            await bot.pin_chat_message(
                                chat_id=target,
                                message_id=msg.message_id,
                                disable_notification=True,
                            )
                            delivery["pinned"] = True
                        except Exception as pe:
                            logger.warning(f"Promo pin failed: {pe}")
                    return msg.message_id
                except Exception as e:
                    logger.error(f"Promo send to {chat_id} failed: {e}")
                    return None

            if publish_proof_channel:
                mid = await _send(proof_id)
                if mid:
                    delivery["proof_channel"] = 1
            if publish_main_channel:
                mid = await _send(main_id)
                if mid:
                    delivery["main_channel"] = 1

            if broadcast_customers:
                # Prefer bot deep-link as CTA for mass DM; store URL as secondary via caption
                cta_url = (
                    f"https://t.me/{uname}?start=buy_{product_id}"
                    if product_id
                    else f"https://t.me/{uname}"
                )
                campaign = BroadcastCampaign(
                    title=f"Promo Ad {_utc_now().strftime('%Y-%m-%d %H:%M')}",
                    message_text=caption,
                    image_url=image_url or (f"tgfile:{image_file_id}" if image_file_id else None),
                    cta_button_text=bot_button_label[:64],
                    cta_button_url=cta_url,
                    target_audience="all",
                    status="pending",
                )
                session.add(campaign)
                await session.flush()
                delivery["broadcast_campaign_id"] = str(campaign.id)
                from src.workers.broadcast_worker import run_broadcast_task
                import asyncio
                asyncio.create_task(run_broadcast_task(str(campaign.id)))

            session.add(AuditLog(
                admin_user_id=_uid(admin_user_id) if admin_user_id else None,
                action="promo_ad_publish",
                entity_type="promo_ad",
                entity_id=_uid(product_id) if product_id else None,
                new_values=merge_audit_new_values(reviewer, {
                    "product_id": str(product_id) if product_id else None,
                    "publish_proof_channel": publish_proof_channel,
                    "publish_main_channel": publish_main_channel,
                    "broadcast_customers": broadcast_customers,
                    "pin_in_channel": pin_in_channel,
                    "delivery": delivery,
                    "caption_preview": caption[:120],
                }),
            ))
            await session.commit()
            await event_bus.publish_config_change("marketing", "promo_ad")
            return {"status": "ok", "delivery": delivery}
        finally:
            await bot.session.close()


promo_ad_service = PromoAdService()
