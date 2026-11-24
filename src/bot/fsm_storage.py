"""Shared FSM storage factory — Redis in production, MemoryStorage in tests."""
from __future__ import annotations

import os

from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from src.config import settings


def build_fsm_storage() -> BaseStorage:
    """
    Prefer RedisStorage so container restarts keep checkout / receipt FSM.
    Fall back to MemoryStorage for unit tests or when Redis URL is unusable.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or (settings.ENVIRONMENT or "").lower() == "test":
        return MemoryStorage()

    url = (settings.REDIS_URL or "").strip()
    if not url:
        logger.warning("REDIS_URL empty — FSM using MemoryStorage")
        return MemoryStorage()

    try:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(url)
        logger.info("FSM storage: RedisStorage")
        return storage
    except Exception as e:
        logger.warning(f"RedisStorage unavailable ({e}); FSM using MemoryStorage")
        return MemoryStorage()
