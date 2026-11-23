"""Sham Cash USD ↔ SYP display helpers (BotConfig-backed)."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.entities import BotConfig


DEFAULT_USD_TO_SYP = Decimal("11500")


async def get_config_value(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = (await session.execute(select(BotConfig).where(BotConfig.key == key))).scalar_one_or_none()
    if row is None or row.value is None:
        return default
    return row.value


async def get_sham_cash_rate(session: AsyncSession) -> Tuple[Decimal, bool]:
    """Returns (usd_to_syp_rate, accept_syp_enabled)."""
    rate_raw = await get_config_value(session, "sham_cash_usd_to_syp_rate", float(DEFAULT_USD_TO_SYP))
    accept_raw = await get_config_value(session, "sham_cash_accept_syp", True)
    try:
        rate = Decimal(str(rate_raw))
    except Exception:
        rate = DEFAULT_USD_TO_SYP
    if rate <= 0:
        rate = DEFAULT_USD_TO_SYP
    accept = True
    if isinstance(accept_raw, bool):
        accept = accept_raw
    elif isinstance(accept_raw, str):
        accept = accept_raw.lower() in ("true", "1", "yes", "on")
    return rate, bool(accept)


def usd_to_syp(amount_usd: Decimal | float | str, rate: Decimal) -> Decimal:
    amt = Decimal(str(amount_usd))
    return (amt * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def format_syp(amount_syp: Decimal) -> str:
    """40,250 ل.س style with thousands separators."""
    n = int(amount_syp)
    return f"{n:,} ل.س"


def format_usd_and_syp(amount_usd: Decimal | float | str, rate: Decimal) -> str:
    usd = Decimal(str(amount_usd)).quantize(Decimal("0.01"))
    syp = usd_to_syp(usd, rate)
    return f"${usd} ≈ {format_syp(syp)}"


async def format_sham_cash_amount_line(session: AsyncSession, amount_usd) -> Optional[str]:
    rate, accept = await get_sham_cash_rate(session)
    if not accept:
        return None
    return format_usd_and_syp(amount_usd, rate)
