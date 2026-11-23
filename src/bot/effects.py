"""Telegram native message effects + custom emoji helpers with safe fallbacks."""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

# Official-ish Telegram message_effect_id values (Bot API 7.0+)
EFFECT_CELEBRATION = "5046509860389126442"  # confetti / party
EFFECT_FIRE = "5104841245755180586"
EFFECT_HEARTS = "5159385139981059251"
EFFECT_THUMBS = "5107584321108051014"

# Configurable custom emoji map — falls back to Unicode if Premium unavailable
CUSTOM_EMOJI = {
    "sparkle": ("5368324170671202286", "✨"),
    "cart": ("5379748062124056162", "🛒"),
    "check": ("5199749315580460316", "✅"),
    "wallet": ("5413707247667630707", "💳"),
    "gift": ("5434144690511290129", "🎁"),
}


def tg_emoji(key: str) -> str:
    eid, fallback = CUSTOM_EMOJI.get(key, (None, "•"))
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback


async def send_with_effect(
    bot_or_message: Any,
    *,
    chat_id: Optional[int] = None,
    text: str,
    effect_id: Optional[str] = None,
    reply_markup=None,
    parse_mode: str = "HTML",
    **kwargs,
):
    """
    send_message / answer with optional message_effect_id.
    Falls back to plain send if Telegram rejects the effect.
    """
    payload = dict(kwargs)
    payload["text"] = text
    payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if effect_id:
        payload["message_effect_id"] = effect_id

    try:
        if chat_id is not None and hasattr(bot_or_message, "send_message"):
            return await bot_or_message.send_message(chat_id=chat_id, **payload)
        if hasattr(bot_or_message, "answer"):
            return await bot_or_message.answer(**payload)
        if hasattr(bot_or_message, "bot") and chat_id is not None:
            return await bot_or_message.bot.send_message(chat_id=chat_id, **payload)
    except Exception as e:
        logger.debug(f"message_effect rejected/unavailable ({effect_id}): {e}")
        payload.pop("message_effect_id", None)
        if chat_id is not None and hasattr(bot_or_message, "send_message"):
            return await bot_or_message.send_message(chat_id=chat_id, **payload)
        if hasattr(bot_or_message, "answer"):
            return await bot_or_message.answer(**payload)
    return None
