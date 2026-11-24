import json
import time
from typing import Callable, Dict, Any, Awaitable, Optional, List, Tuple
from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from loguru import logger

from src.core.redis import event_bus
from src.core.database import AsyncSessionLocal
from src.core.models.entities import BotConfig, Customer
from src.config import settings


# Throttle channel-health alerts (per channel) — seconds
_CHANNEL_ALERT_COOLDOWN = 300
_last_channel_alert: Dict[str, float] = {}


async def get_config_val(key: str, default_val: Any = None) -> Any:
    """Reads configuration with 60s Redis caching."""
    redis = event_bus._redis
    cache_key = f"cache:cfg:{key}"
    if redis:
        val = await redis.get(cache_key)
        if val is not None:
            try:
                return json.loads(val)
            except Exception:
                return val

    async with AsyncSessionLocal() as session:
        stmt = select(BotConfig).where(BotConfig.key == key)
        cfg = (await session.execute(stmt)).scalar_one_or_none()
        result = cfg.value if (cfg and cfg.value is not None) else default_val

    if redis and result is not None:
        await redis.set(cache_key, json.dumps(result), ex=60)
    return result


def _truthy(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "on")
    return bool(val)


def force_sub_keyboard(
    *,
    main_url: Optional[str],
    proof_url: Optional[str],
    need_main: bool,
    need_proof: bool,
    language: str = "ar",
) -> InlineKeyboardMarkup:
    from src.bot.i18n import t, normalize_lang
    lang = normalize_lang(language)
    buttons = []
    if need_main and main_url:
        buttons.append([InlineKeyboardButton(text=t(lang, "force_sub_join"), url=main_url)])
    if need_proof and proof_url:
        buttons.append([InlineKeyboardButton(text=t(lang, "force_sub_join"), url=proof_url)])
    buttons.append([
        InlineKeyboardButton(text=t(lang, "force_sub_verify"), callback_data="verify_sub")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _resolve_user_lang(user_id: int, telegram_lang: Optional[str] = None) -> str:
    """Prefer stored customer.language_code; fall back to Telegram language then ar."""
    from src.bot.i18n import normalize_lang
    try:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(Customer.language_code).where(Customer.telegram_id == user_id)
                )
            ).scalar_one_or_none()
            if row:
                return normalize_lang(row)
    except Exception:
        pass
    return normalize_lang(telegram_lang or "ar")


async def _emit_channel_health_alert(channel_id: Any, error: Exception) -> None:
    """Throttled alert when get_chat_member fails (bot not admin / bad channel id)."""
    key = str(channel_id or "unknown")
    now = time.time()
    last = _last_channel_alert.get(key, 0)
    if now - last < _CHANNEL_ALERT_COOLDOWN:
        return
    _last_channel_alert[key] = now

    err_s = str(error)
    logger.error(
        f"Force-join channel health degraded for {channel_id}: {err_s}"
    )
    # Persist status for Admin Bot / Dashboard settings
    try:
        async with AsyncSessionLocal() as session:
            status_key = "force_sub_channel_health"
            payload = {
                "channel_id": key,
                "ok": False,
                "error": err_s[:300],
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            row = (
                await session.execute(select(BotConfig).where(BotConfig.key == status_key))
            ).scalar_one_or_none()
            if row:
                row.value = payload
            else:
                session.add(BotConfig(key=status_key, value=payload, updated_by="force_sub"))
            await session.commit()
        await event_bus.invalidate_bot_config_caches()
    except Exception as e:
        logger.warning(f"Could not persist channel health: {e}")

    try:
        await event_bus.publish_alert(
            title="Force-Join Channel Health",
            message=(
                f"get_chat_member failed for channel `{key}`.\n"
                f"Likely causes: bot is not admin, or channel ID is invalid.\n"
                f"Error: {err_s[:200]}\n"
                f"Users are fail-open (not locked out) until this is fixed."
            ),
            level="danger",
            meta={"alert_type": "force_sub_channel", "channel_id": key},
        )
    except Exception as e:
        logger.warning(f"Channel health alert publish failed: {e}")


class ForceSubscribeMiddleware(BaseMiddleware):
    """
    Mandatory Channel Subscription Gate for Main + optional Proof/Trust channel.
    """

    VALID_STATUSES = {"creator", "administrator", "member", "restricted"}

    async def _is_member(self, bot: Bot, channel_id: Any, user_id: int) -> Optional[bool]:
        """True/False membership; None = check failed (fail-open)."""
        if not channel_id:
            return True
        try:
            chat_target = int(channel_id) if str(channel_id).lstrip("-").isdigit() else channel_id
            member = await bot.get_chat_member(chat_id=chat_target, user_id=user_id)
            return member.status in self.VALID_STATUSES
        except Exception as e:
            logger.warning(f"Could not verify membership for {user_id} in {channel_id}: {e}")
            await _emit_channel_health_alert(channel_id, e)
            return None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        bot: Bot = data.get("bot")

        if not user or not bot:
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == "verify_sub":
            return await handler(event, data)

        user_id = user.id

        is_enabled = _truthy(await get_config_val("force_subscribe_enabled", False))
        if not is_enabled:
            return await handler(event, data)

        main_channel_id = await get_config_val("main_channel_id", settings.TELEGRAM_PUBLIC_CHANNEL_ID)
        main_channel_url = await get_config_val(
            "main_channel_url", settings.TELEGRAM_MAIN_CHANNEL_URL or "https://t.me"
        )
        proof_channel_id = await get_config_val(
            "proof_channel_id", getattr(settings, "TELEGRAM_PROOF_CHANNEL_ID", None)
        )
        proof_channel_url = await get_config_val(
            "proof_channel_url", settings.TELEGRAM_PROOF_CHANNEL_URL
        )
        # Default True when proof channel id is configured
        force_proof = _truthy(
            await get_config_val("force_sub_proof_channel_enabled", None),
            default=bool(proof_channel_id),
        )

        if not main_channel_id and not (force_proof and proof_channel_id):
            return await handler(event, data)

        redis = event_bus._redis
        need_main = bool(main_channel_id)
        need_proof = bool(force_proof and proof_channel_id)

        # Cache keys
        main_ok = True
        proof_ok = True
        if need_main:
            cache_key = f"cache:forcesub:{user_id}:{main_channel_id}"
            if redis and await redis.get(cache_key) == "1":
                main_ok = True
            else:
                status = await self._is_member(bot, main_channel_id, user_id)
                if status is None:
                    return await handler(event, data)  # fail-open
                main_ok = bool(status)
                if main_ok and redis:
                    await redis.set(cache_key, "1", ex=120)

        if need_proof:
            cache_key = f"cache:forcesub:{user_id}:{proof_channel_id}"
            if redis and await redis.get(cache_key) == "1":
                proof_ok = True
            else:
                status = await self._is_member(bot, proof_channel_id, user_id)
                if status is None:
                    return await handler(event, data)
                proof_ok = bool(status)
                if proof_ok and redis:
                    await redis.set(cache_key, "1", ex=120)

        if main_ok and proof_ok:
            return await handler(event, data)

        # Remember deep-link intent
        if redis and isinstance(event, Message) and event.text:
            parts = (event.text or "").split()
            if len(parts) >= 2:
                param = parts[1]
                if param.startswith("buy_") or param.startswith("prod_"):
                    pending_id = param.split("_", 1)[1].strip()
                    if pending_id:
                        await redis.set(f"pending_buy:{user_id}", pending_id, ex=3600)
                elif param.startswith("ref_"):
                    await redis.set(f"pending_ref:{user_id}", param, ex=3600)

        from html import escape as html_escape
        from src.bot.i18n import t

        lang = await _resolve_user_lang(user_id, getattr(user, "language_code", None))
        gate_text = (
            f"{t(lang, 'force_sub_title')}\n\n"
            f"👋 {html_escape(user.first_name or '')}\n"
            f"{t(lang, 'force_sub_verify')}"
        )
        kb = force_sub_keyboard(
            main_url=main_channel_url,
            proof_url=proof_channel_url,
            need_main=need_main and not main_ok,
            need_proof=need_proof and not proof_ok,
            language=lang,
        )

        if isinstance(event, Message):
            await event.answer(gate_text, parse_mode="HTML", reply_markup=kb)
        elif isinstance(event, CallbackQuery):
            await event.answer(t(lang, "force_sub_fail"), show_alert=True)
            try:
                await event.message.edit_text(gate_text, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await event.message.answer(gate_text, parse_mode="HTML", reply_markup=kb)
        return
