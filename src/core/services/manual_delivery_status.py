"""
Manual-fulfillment customer countdown + multi-admin escalation helpers.
Pure formatting / recipient resolution — Telegram I/O lives in bot & health_monitor.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Iterable, Optional, Set

from sqlalchemy import select

from src.config import settings


DEFAULT_SLA_MINUTES = 60
PROGRESS_WIDTH = 10

# Synced for Admin Bot ACL (assistants listed in escalation_telegram_ids)
_ACL_ESCALATION_CACHE: Set[int] = set()


def parse_telegram_id_csv(raw: Any) -> Set[int]:
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        out: Set[int] = set()
        for x in raw:
            try:
                out.add(int(x))
            except (TypeError, ValueError):
                continue
        return out
    text = str(raw).strip()
    if not text:
        return set()
    out = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


async def get_manual_sla_minutes(session) -> int:
    """BotConfig `manual_delivery_sla_minutes` → settings → 60."""
    from src.core.models.entities import BotConfig

    row = (
        await session.execute(
            select(BotConfig.value).where(BotConfig.key == "manual_delivery_sla_minutes")
        )
    ).scalar_one_or_none()
    if row is not None and str(row).strip() != "":
        try:
            n = int(float(row))
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    return int(getattr(settings, "MANUAL_DELIVERY_SLA_MINUTES", DEFAULT_SLA_MINUTES) or DEFAULT_SLA_MINUTES)


async def get_escalation_telegram_ids(session=None) -> Set[int]:
    """
    BotConfig `escalation_telegram_ids` (comma-separated) ∪ ADMIN_TELEGRAM_IDS.
    Falls back to admin set when escalation list is empty.
    """
    ids = set(settings.admin_telegram_id_set)
    raw = None
    if session is not None:
        from src.core.models.entities import BotConfig

        raw = (
            await session.execute(
                select(BotConfig.value).where(BotConfig.key == "escalation_telegram_ids")
            )
        ).scalar_one_or_none()
    else:
        try:
            from src.core.redis import get_redis_client

            redis = get_redis_client()
            if redis is not None:
                cached = await redis.get("botcfg:escalation_telegram_ids")
                if cached is not None:
                    raw = cached
        except Exception:
            raw = None
    extra = parse_telegram_id_csv(raw)
    if extra:
        ids |= extra
    return ids


async def cache_escalation_ids(raw: Any) -> None:
    """Persist escalation CSV to Redis + in-memory ACL cache."""
    global _ACL_ESCALATION_CACHE
    ids = parse_telegram_id_csv(raw)
    _ACL_ESCALATION_CACHE = set(ids)
    try:
        from src.core.redis import get_redis_client

        redis = get_redis_client()
        if redis is None:
            return
        text = raw if isinstance(raw, str) else ",".join(str(x) for x in ids)
        await redis.set("botcfg:escalation_telegram_ids", text or "")
    except Exception:
        pass


def progress_bar(elapsed_ratio: float, width: int = PROGRESS_WIDTH) -> str:
    ratio = max(0.0, min(1.0, float(elapsed_ratio)))
    filled = int(round(ratio * width))
    filled = max(0, min(width, filled))
    pct = int(round(ratio * 100))
    return f"[{'█' * filled}{'░' * (width - filled)}] {pct}%"


def hourglass_emoji(now: Optional[datetime] = None) -> str:
    ts = (now or datetime.now(timezone.utc)).timestamp()
    return "⏳" if int(ts) // 60 % 2 == 0 else "⌛"


def remaining_seconds(deadline: datetime, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return int((deadline - now).total_seconds())


def format_countdown_message(
    *,
    order_number: str,
    lang: str,
    deadline: datetime,
    sla_minutes: int,
    now: Optional[datetime] = None,
    overdue: bool = False,
) -> str:
    """Customer live status card (AR/EN) with progress bar."""
    now = now or datetime.now(timezone.utc)
    is_en = str(lang or "ar").lower().startswith("en")
    rem = remaining_seconds(deadline, now)
    sla_sec = max(int(sla_minutes) * 60, 1)
    elapsed = 1.0 - (rem / sla_sec) if rem > 0 else 1.0
    bar = progress_bar(elapsed)
    icon = hourglass_emoji(now)
    rem_min = max(0, (rem + 59) // 60)  # ceil minutes while positive

    if overdue or rem <= 0:
        if is_en:
            return (
                "We sincerely apologize for the delay ⏳ "
                "Our management has been urgently alerted and your order will be delivered shortly."
            )
        return (
            "نأسف جداً للتأخير ⏳ تم إرسال تنبيه عاجل للإدارة الآن "
            "وسيتم تسليم طلبك بأسرع وقت ممكن."
        )

    if is_en:
        return (
            f"{icon} Preparing your order\n\n"
            f"🔖 Order: `{order_number}`\n"
            f"✅ Payment received safely\n"
            f"⏱️ Estimated time left: ~{rem_min} min\n"
            f"{bar}\n\n"
            f"We are carefully preparing your order — delivery within the estimated window."
        )
    return (
        f"{icon} جاري تجهيز طلبك\n\n"
        f"🔖 الطلب: `{order_number}`\n"
        f"✅ تم استلام الدفع بنجاح وأمان\n"
        f"⏱️ الوقت المتبقي تقريباً: {rem_min} دقيقة\n"
        f"{bar}\n\n"
        f"نجهّز طلبك بعناية — التسليم خلال المدة المقدّرة."
    )


def format_terminal_message(*, lang: str, delivered: bool) -> str:
    is_en = str(lang or "ar").lower().startswith("en")
    if delivered:
        return "✅ Your order was delivered successfully!" if is_en else "✅ تم تسليم طلبك بنجاح!"
    return "❌ Your order was cancelled." if is_en else "❌ تم إلغاء الطلب"


def escalation_stage(elapsed_minutes: float) -> Optional[str]:
    """
    Return stage key for admin pings:
      - None if < 30
      - "half" at >= 30
      - "urgent" at >= 50 (then every 10 min via Redis throttle)
    """
    if elapsed_minutes >= 50:
        return "urgent"
    if elapsed_minutes >= 30:
        return "half"
    return None


def format_escalation_caption(
    *,
    stage: str,
    order_number: str,
    product_title: str,
    elapsed_minutes: int,
) -> str:
    if stage == "half":
        return (
            f"⏰ تذكير: مرّ 30 دقيقة على الطلب #{order_number}\n\n"
            f"📦 {product_title}\n"
            f"مضى ~{elapsed_minutes} دقيقة — لا يزال بانتظار التسليم اليدوي."
        )
    return (
        f"🚨 عاجل: طلب #{order_number} اقترب/تجاوز الساعة!\n\n"
        f"📦 {product_title}\n"
        f"مضى ~{elapsed_minutes} دقيقة بدون تسليم — تصرّف فوراً."
    )


def deadline_from_now(sla_minutes: int, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(minutes=int(sla_minutes))
