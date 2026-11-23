"""Shared backoff helpers for listeners and Admin Bot Telegram sends."""
from __future__ import annotations

import asyncio
import random
from typing import Optional


def compute_backoff_seconds(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 60.0,
    jitter: float = 0.25,
) -> float:
    """Exponential backoff with jitter. attempt is 0-based."""
    delay = min(cap, base * (2 ** max(0, attempt)))
    if jitter > 0:
        delay += random.uniform(0, jitter * delay)
    return delay


async def sleep_backoff(attempt: int, *, base: float = 1.0, cap: float = 60.0) -> float:
    delay = compute_backoff_seconds(attempt, base=base, cap=cap)
    await asyncio.sleep(delay)
    return delay


def extract_flood_retry_after(exc: BaseException) -> Optional[float]:
    """Return Telegram FloodWait / RetryAfter seconds if present."""
    retry = getattr(exc, "retry_after", None)
    if retry is not None:
        try:
            return float(retry)
        except (TypeError, ValueError):
            pass
    # aiogram TelegramRetryAfter wraps retry_after
    cause = getattr(exc, "__cause__", None) or getattr(exc, "cause", None)
    if cause is not None and cause is not exc:
        return extract_flood_retry_after(cause)
    return None
