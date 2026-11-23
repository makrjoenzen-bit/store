"""
Live Gas & FX price oracle — BNBUSDT / TRXUSDT from Binance public ticker.
Cached in Redis (300s TTL). Conservative fallbacks keep Profit Guard safe when offline.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

import httpx
from loguru import logger

from src.core.redis import event_bus, get_redis_client

# Conservative high-side fallbacks (overestimate gas USD → safer circuit breaker)
FALLBACK_BNB_USD = Decimal("650.0")
FALLBACK_TRX_USD = Decimal("0.20")
# Extra safety when using a stale/fallback quote
FALLBACK_SAFETY_MULT = Decimal("1.10")
CACHE_TTL_SECONDS = 300
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"


class GasFXOracle:
    """Async FX oracle for native gas tokens used in JIT fee accounting."""

    def __init__(self, ttl: int = CACHE_TTL_SECONDS):
        self.ttl = ttl

    def _redis(self):
        return event_bus.redis or get_redis_client()

    async def _cached_get(self, key: str) -> Optional[Decimal]:
        try:
            redis = self._redis()
            raw = await redis.get(key)
            if raw is not None:
                return Decimal(str(raw))
        except Exception:
            pass
        return None

    async def _cached_set(self, key: str, value: Decimal) -> None:
        try:
            redis = self._redis()
            await redis.set(key, str(value), ex=self.ttl)
        except Exception as e:
            logger.debug(f"GasFXOracle cache set failed: {e}")

    async def _fetch_binance_price(self, symbol: str) -> Optional[Decimal]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(BINANCE_TICKER_URL, params={"symbol": symbol})
                if res.status_code != 200:
                    logger.warning(f"Binance ticker {symbol} HTTP {res.status_code}")
                    return None
                data = res.json()
                price = Decimal(str(data.get("price") or "0"))
                if price <= 0:
                    return None
                return price
        except Exception as e:
            logger.warning(f"Binance ticker {symbol} failed: {e}")
            return None

    async def get_bnb_usd(self) -> Decimal:
        """Live BNB/USD with Redis cache; conservative fallback on failure."""
        cached = await self._cached_get("fx:bnb_usdt")
        if cached is not None:
            return cached
        live = await self._fetch_binance_price("BNBUSDT")
        if live is not None:
            await self._cached_set("fx:bnb_usdt", live)
            return live
        return (FALLBACK_BNB_USD * FALLBACK_SAFETY_MULT).quantize(Decimal("0.01"))

    async def get_trx_usd(self) -> Decimal:
        """Live TRX/USD with Redis cache; conservative fallback on failure."""
        cached = await self._cached_get("fx:trx_usdt")
        if cached is not None:
            return cached
        live = await self._fetch_binance_price("TRXUSDT")
        if live is not None:
            await self._cached_set("fx:trx_usdt", live)
            return live
        return (FALLBACK_TRX_USD * FALLBACK_SAFETY_MULT).quantize(Decimal("0.0001"))


gas_fx_oracle = GasFXOracle()
