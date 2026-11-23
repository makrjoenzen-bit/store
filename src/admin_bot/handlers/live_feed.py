"""Redis → Admin Telegram live feed. Presentation + dispatch only; no business logic."""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile
from loguru import logger

from src.config import settings
from src.core.redis import event_bus, get_redis_client
from src.core.resilience import extract_flood_retry_after, sleep_backoff
from src.core.services.telegram_file_proxy import safe_download_telegram_file
from src.admin_bot.formatters.alerts import (
    format_order_state_alert,
    format_admin_alert,
    format_ticket_alert,
    format_low_balance_alert,
)
from src.admin_bot.keyboards.main_menu import sham_cash_claim_keyboard, sham_cash_review_keyboard


async def broadcast_to_admins(bot: Bot, text: str, reply_markup=None) -> int:
    """
    Send a card to every whitelisted admin.
    Handles Telegram 429 FloodWait with retry_after sleep; never crashes the feed loop.
    """
    sent = 0
    for admin_id in settings.admin_telegram_id_set:
        attempt = 0
        while attempt < 4:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup,
                )
                sent += 1
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                wait = extract_flood_retry_after(e)
                if wait is not None:
                    logger.warning(
                        f"Admin feed FloodWait for {admin_id}: sleeping {wait}s (attempt {attempt})"
                    )
                    await asyncio.sleep(float(wait) + 0.5)
                    attempt += 1
                    continue
                logger.warning(f"Admin feed send failed to {admin_id}: {e}")
                break
    return sent


async def broadcast_manual_review(bot: Bot, data: dict) -> int:
    """Fan-out Sham Cash / Binance review cards with media to ALL ADMIN_TELEGRAM_IDS."""
    caption = data.get("caption") or "مراجعة يدوية"
    if data.get("fraud_warning"):
        caption = f"{data['fraud_warning']}\n\n{caption}"
    order_id = data.get("order_id")
    # Start with claim lock — actions unlock after claim
    kb = sham_cash_claim_keyboard(str(order_id)) if order_id else None
    media_file_id = data.get("media_file_id")
    media_type = (data.get("media_type") or "photo").lower()
    file_bytes = None
    path_hint = None
    if media_file_id:
        file_bytes, path_hint = await safe_download_telegram_file(media_file_id)

    sent = 0
    for admin_id in settings.admin_telegram_id_set:
        attempt = 0
        while attempt < 4:
            try:
                if file_bytes:
                    filename = "receipt.pdf" if media_type == "document" else "receipt.jpg"
                    if path_hint and "." in path_hint:
                        filename = path_hint.rsplit("/", 1)[-1]
                    buf = BufferedInputFile(file_bytes, filename=filename)
                    if media_type == "document":
                        await bot.send_document(
                            chat_id=admin_id,
                            document=buf,
                            caption=caption,
                            parse_mode="Markdown",
                            reply_markup=kb,
                        )
                    else:
                        await bot.send_photo(
                            chat_id=admin_id,
                            photo=buf,
                            caption=caption,
                            parse_mode="Markdown",
                            reply_markup=kb,
                        )
                else:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=caption,
                        parse_mode="Markdown",
                        reply_markup=kb,
                    )
                sent += 1
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                wait = extract_flood_retry_after(e)
                if wait is not None:
                    await asyncio.sleep(float(wait) + 0.5)
                    attempt += 1
                    continue
                logger.warning(f"manual_review send failed to {admin_id}: {e}")
                break
    return sent


def render_live_event(channel: str, data: dict) -> tuple[Optional[str], object]:
    """
    Pure formatter: (text, optional_reply_markup).
    Used by the Redis listener and by unit tests without a live Telegram session.
    """
    if channel == "orders:live":
        new_state = data.get("new_state")
        text = format_order_state_alert(data)
        kb = None
        if new_state == "sham_cash_review":
            order_id = data.get("order_id")
            if order_id:
                kb = sham_cash_claim_keyboard(str(order_id))
                details = data.get("details") or {}
                order_number = details.get("order_number") or order_id
                supplier = details.get("supplier_name") or data.get("supplier_name")
                supplier_line = f"\n🏭 المورد / التاجر: **{supplier}**" if supplier else ""
                fraud = data.get("fraud_warning") or details.get("fraud_warning") or ""
                fraud_line = f"{fraud}\n\n" if fraud else ""
                text = (
                    f"{fraud_line}"
                    f"⏳ **إيصال شام كاش بانتظار المراجعة**\n\n"
                    f"🔖 الطلب: `{order_number}`{supplier_line}\n"
                    f"اضغط **استلام الطلب** ثم الموافقة أو الرفض:"
                )
        return text, kb

    if channel == "alerts:stream":
        meta = data.get("meta") or {}
        if meta.get("alert_type") == "new_ticket":
            return format_ticket_alert({**data, **meta}), None
        if meta.get("alert_type") == "low_balance":
            return format_low_balance_alert({**data, **meta}), None
        return format_admin_alert(data), None

    if channel == "support:tickets":
        return format_ticket_alert(data), None

    if channel == "admin:fulfillment_report":
        from src.admin_bot.formatters.alerts import format_fulfillment_report
        return format_fulfillment_report(data), None

    return None, None


async def listen_admin_live_feed(bot: Bot):
    """Background Redis pub/sub fan-out to all ADMIN_TELEGRAM_IDS."""
    redis = event_bus.redis or get_redis_client()
    pubsub = redis.pubsub()
    channels = (
        "orders:live",
        "alerts:stream",
        "support:tickets",
        "admin:fulfillment_report",
        "admin:manual_review",
        "admin:warranty_claim",
    )
    await pubsub.subscribe(*channels)
    logger.info(f"Admin Bot live feed subscribed to {channels}")

    consecutive_errors = 0
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            channel = msg.get("channel")
            if isinstance(channel, bytes):
                channel = channel.decode()
            try:
                raw = msg.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode()
                data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception as e:
                logger.warning(f"Admin feed bad JSON on {channel}: {e}")
                continue

            try:
                if channel == "admin:manual_review":
                    await broadcast_manual_review(bot, data)
                    consecutive_errors = 0
                    continue
                if channel == "admin:warranty_claim":
                    from src.admin_bot.keyboards.main_menu import warranty_claim_keyboard
                    cap = data.get("caption") or "طلب ضمان"
                    claim_id = data.get("claim_id")
                    kb_w = warranty_claim_keyboard(str(claim_id)) if claim_id else None
                    await broadcast_to_admins(bot, cap, reply_markup=kb_w)
                    consecutive_errors = 0
                    continue
                text, kb = render_live_event(channel, data)
                if not text:
                    consecutive_errors = 0
                    continue
                await broadcast_to_admins(bot, text, reply_markup=kb)
                consecutive_errors = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Admin feed dispatch error: {e}")
                await sleep_backoff(min(consecutive_errors - 1, 5), base=1.0, cap=30.0)
    except asyncio.CancelledError:
        await pubsub.unsubscribe(*channels)
        await pubsub.close()
        raise
