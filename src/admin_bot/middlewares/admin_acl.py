"""Owner vs Staff ACL — Owners full access; Staff limited to review/warranty/support."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, Set

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery, Update
from loguru import logger

from src.config import settings


# Reply keyboard labels Staff may use
STAFF_ALLOWED_TEXTS: Set[str] = {
    "🧾 طلبات المراجعة (شام كاش / باينانس)",
    "🧾 الطلبات وشام كاش",  # legacy label still routed
    "🛡️ طلبات الضمان والاستبدال",
    "🛟 تذاكر الدعم الفني",
    "👥 العملاء والدعم",  # support tickets live here historically
}

# Callback prefixes Staff may use
STAFF_ALLOWED_CB_PREFIXES = (
    "adm:orders",
    "adm:home",
    "adm_sc_",
    "adm:claim:",
    "adm:unclaim:",
    "adm:manual:",
    "adm:proof:",
    "adm:warranty",
    "adm:crm",  # support tickets
    "adm:ticket",
    "noop",
)

# Hard-denied for Staff (Owner-only)
OWNER_ONLY_TEXTS: Set[str] = {
    "📊 الخزينة والأرباح",
    "🔌 الموردين والـ APIs",
    "📦 المنتجات والأسعار",
    "📢 التسويق والبث",
}
OWNER_ONLY_CB_PREFIXES = (
    "adm:treasury",
    "adm:suppliers",
    "adm:sup:",
    "adm:products",
    "adm:prod:",
    "adm:marketing",
    "adm:settings",
    "adm:cfg",
    "adm:cfg:",
    "adm:cat:",
)


def _escalation_id_set_sync() -> Set[int]:
    """Best-effort sync read of cached escalation assistants (Redis)."""
    try:
        from src.core.redis import event_bus
        redis = getattr(event_bus, "_redis", None) or getattr(event_bus, "redis", None)
        if redis is None:
            return set()
        # Prefer sync get if available; otherwise empty (async path covers alerts)
        getter = getattr(redis, "get", None)
        if getter is None:
            return set()
        # aioredis get is async — cannot call here; use module cache if set
        from src.core.services import manual_delivery_status as mds
        cached = getattr(mds, "_ACL_ESCALATION_CACHE", None)
        if cached:
            return set(cached)
    except Exception:
        pass
    return set()


def is_whitelisted_admin(telegram_user_id: Optional[int]) -> bool:
    if telegram_user_id is None:
        return False
    tid = int(telegram_user_id)
    if tid in settings.admin_telegram_id_set:
        return True
    return tid in _escalation_id_set_sync()


def is_owner(telegram_user_id: Optional[int]) -> bool:
    if telegram_user_id is None:
        return False
    return settings.is_owner(int(telegram_user_id))


def is_staff_only(telegram_user_id: Optional[int]) -> bool:
    if telegram_user_id is None:
        return False
    tid = int(telegram_user_id)
    if settings.is_staff_only(tid):
        return True
    # Escalation assistants get Staff-level access (manual delivery / review only)
    if tid in _escalation_id_set_sync() and not settings.is_owner(tid):
        return True
    return False


def staff_may_access(text: Optional[str] = None, callback_data: Optional[str] = None) -> bool:
    if text:
        if text in OWNER_ONLY_TEXTS:
            return False
        if text in STAFF_ALLOWED_TEXTS:
            return True
        # Unknown reply text → deny for staff
        return False
    if callback_data:
        for p in OWNER_ONLY_CB_PREFIXES:
            if callback_data.startswith(p):
                return False
        for p in STAFF_ALLOWED_CB_PREFIXES:
            if callback_data.startswith(p):
                return True
        return False
    return False


class AdminAclMiddleware(BaseMiddleware):
    """Whitelist + Staff RBAC hard-deny for Treasury/Pricing/API/Broadcast."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        user_id = getattr(user, "id", None) if user else None

        if user_id is None:
            if isinstance(event, Message) and event.from_user:
                user_id = event.from_user.id
            elif isinstance(event, CallbackQuery) and event.from_user:
                user_id = event.from_user.id
            elif isinstance(event, Update):
                if event.message and event.message.from_user:
                    user_id = event.message.from_user.id
                elif event.callback_query and event.callback_query.from_user:
                    user_id = event.callback_query.from_user.id

        if not is_whitelisted_admin(user_id):
            # Cold cache after restart — try loading escalation assistants once
            try:
                from src.core.services.manual_delivery_status import (
                    _ACL_ESCALATION_CACHE,
                    warm_escalation_acl_cache,
                )
                if not _ACL_ESCALATION_CACHE:
                    await warm_escalation_acl_cache(force=False)
            except Exception:
                pass
            if not is_whitelisted_admin(user_id):
                logger.warning(
                    f"Admin Bot ACL DENY: telegram_id={user_id} not in ADMIN/OWNER/STAFF IDs"
                )
                await self._deny(event, "⛔ غير مصرح لك باستخدام بوت الإدارة.")
                return None

        # Inject role into handler data
        data["is_owner"] = is_owner(user_id)
        data["is_staff_only"] = is_staff_only(user_id)

        if is_staff_only(user_id) and not is_owner(user_id):
            text = None
            cb = None
            if isinstance(event, Message):
                text = event.text
            elif isinstance(event, CallbackQuery):
                cb = event.data
            # Allow FSM free-text after staff started an allowed flow (claim paste etc.)
            # Deny only clearly owner-only menu hits
            if text and text in OWNER_ONLY_TEXTS:
                await self._deny(event, "⛔ هذه الشاشة للمالك فقط (Owner).")
                return None
            if cb:
                for p in OWNER_ONLY_CB_PREFIXES:
                    if cb.startswith(p):
                        await self._deny(event, "⛔ غير مصرح — صلاحيات الموظف لا تشمل هذا القسم.", alert=True)
                        return None

        return await handler(event, data)

    async def _deny(self, event, msg: str, alert: bool = False):
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(msg)
            elif isinstance(event, Update):
                if event.callback_query:
                    await event.callback_query.answer(msg, show_alert=True)
                elif event.message:
                    await event.message.answer(msg)
            elif hasattr(event, "answer") and callable(event.answer):
                try:
                    await event.answer(msg, show_alert=True)
                except TypeError:
                    await event.answer(msg)
        except Exception as e:
            logger.debug(f"ACL deny reply failed: {e}")
