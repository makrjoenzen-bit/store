"""Redis-backed processing dedup with in-memory fallback."""
from __future__ import annotations

from typing import Optional, Set

from loguru import logger

from src.core.redis import event_bus

# 48 hours
DEFAULT_TTL_SECONDS = 48 * 3600


class TxDeduper:
    """SETNX / SET with TTL; falls back to an in-process set if Redis is down."""

    def __init__(self, namespace: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.namespace = namespace
        self.ttl = int(ttl_seconds)
        self._local: Set[str] = set()

    def _key(self, tx_id: str) -> str:
        return f"dedup:{self.namespace}:{tx_id}"

    async def seen_or_mark(self, tx_id: str) -> bool:
        """
        Return True if this tx_id was already processed (caller should skip).
        Return False if newly marked (caller should process).
        """
        if not tx_id:
            return True
        redis = event_bus.redis or getattr(event_bus, "_redis", None)
        if redis is not None:
            try:
                # SET NX EX — 1 if set, 0/None if already exists
                ok = await redis.set(self._key(tx_id), "1", nx=True, ex=self.ttl)
                if ok:
                    return False
                return True
            except Exception as e:
                logger.warning(f"Dedup Redis failed ({self.namespace}): {e}; using memory")

        if tx_id in self._local:
            return True
        self._local.add(tx_id)
        if len(self._local) > 5000:
            self._local = set(list(self._local)[-2000:])
        return False
