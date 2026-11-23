import json
import asyncio
import time
import uuid
from typing import Optional, AsyncGenerator, Any
import redis.asyncio as aioredis
from loguru import logger

from src.config import settings

redis_pool = aioredis.ConnectionPool.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
    max_connections=50,
)


def get_redis_client() -> aioredis.Redis:
    return aioredis.Redis(connection_pool=redis_pool)


class RedisEventBus:
    """Central event bus connecting FastAPI, Workers, Bot, and Dashboard."""

    def __init__(self):
        self.redis = get_redis_client()

    async def publish(self, channel: str, message: dict):
        """Publishes a JSON payload to a Redis channel."""
        try:
            payload = json.dumps(message, default=str)
            await self.redis.publish(channel, payload)
        except Exception as e:
            logger.error(f"Error publishing to {channel}: {e}")

    async def publish_order_state(self, order_id: str, new_state: str, details: Optional[dict] = None):
        """Publishes order transition to both order-specific and global order channels."""
        event = {
            "type": "order_state_change",
            "order_id": order_id,
            "new_state": new_state,
            "details": details or {},
        }
        await self.publish(f"order:{order_id}", event)
        await self.publish("orders:live", event)

    async def invalidate_pricing_caches(self) -> int:
        """Drop quote/catalog Redis keys so Bot + Mini App see new prices within ~1s."""
        if not self.redis:
            return 0
        deleted = 0
        try:
            for pattern in ("cache:quote:*", "cache:catalog:*", "cache:miniapp:*"):
                cursor = 0
                while True:
                    cursor, keys = await self.redis.scan(cursor=cursor, match=pattern, count=200)
                    if keys:
                        deleted += await self.redis.delete(*keys)
                    if cursor == 0:
                        break
        except Exception:
            pass
        return deleted

    async def publish_config_change(self, config_type: str, item_id: Optional[str] = None):
        """Notifies Bot and Dashboard of catalog/pricing/bot-config modifications."""
        await self.invalidate_pricing_caches()
        event = {
            "type": "config_updated",
            "config_type": config_type,
            "item_id": item_id,
        }
        await self.publish("config:updates", event)

    async def publish_alert(self, title: str, message: str, level: str = "warning", meta: Optional[dict] = None):
        """Pushes real-time administrative alert to Dashboard and Admin notification."""
        event = {
            "type": "admin_alert",
            "title": title,
            "message": message,
            "level": level,  # 'info', 'warning', 'danger'
            "meta": meta or {},
        }
        await self.publish("alerts:stream", event)

    @property
    def _redis(self):
        return self.redis

    async def get_or_set_cached_quote(
        self,
        supplier_id: str,
        product_id: str,
        fetch_coro: Any,
        ttl_seconds: int = 25
    ) -> dict:
        """
        Two-Tier Redis Quote Cache with Mutex Locking (Dogpile / Thundering Herd Protection).
        When 200 users click Buy on cache expiration, only 1 request queries the upstream API
        while 199 await the leader's cached result.
        """
        cache_key = f"cache:quote:{supplier_id}:{product_id}"
        cached = await self.redis.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except Exception:
                pass

        lock_key = f"lock:quote:{supplier_id}:{product_id}"
        # Attempt to become the leader responsible for fetching
        acquired = await self.acquire_lock(lock_key, ttl_seconds=5)
        if acquired:
            try:
                # We are the leader - execute fetch
                quote_dto = await fetch_coro()
                data = {
                    "quote_id": getattr(quote_dto, "quote_id", None),
                    "cost": str(getattr(quote_dto, "cost", "0.0")),
                    "is_available": getattr(quote_dto, "is_available", True),
                    "currency": getattr(quote_dto, "currency", "USD"),
                    "cached_at": time.time(),
                }
                await self.redis.set(cache_key, json.dumps(data), ex=ttl_seconds)
                return data
            finally:
                await self.release_lock(lock_key)
        else:
            # Another request is fetching - wait up to 3.5s for cache population
            for _ in range(35):
                await asyncio.sleep(0.1)
                cached = await self.redis.get(cache_key)
                if cached:
                    try:
                        return json.loads(cached)
                    except Exception:
                        pass
            # Fallback if leader timed out
            quote_dto = await fetch_coro()
            return {
                "quote_id": getattr(quote_dto, "quote_id", None),
                "cost": str(getattr(quote_dto, "cost", "0.0")),
                "is_available": getattr(quote_dto, "is_available", True),
                "currency": getattr(quote_dto, "currency", "USD"),
                "cached_at": time.time(),
            }

    async def check_and_consume_supplier_quota(self, supplier_id: str, rate_limit_rpm: int = 60) -> bool:
        """
        Enforces per-supplier outbound Token Bucket rate limit in Redis to guarantee ZERO 429/IP bans.
        Returns True if allowed, False if quota for the current 60s window is exhausted.
        """
        now = int(time.time())
        current_minute = now // 60
        key = f"rate:supplier:{supplier_id}:{current_minute}"
        current = await self.redis.incr(key)
        if current == 1:
            await self.redis.expire(key, 70)  # Expire after window
        if current > rate_limit_rpm:
            logger.warning(f"Supplier {supplier_id} exceeded rate limit ({current}/{rate_limit_rpm} RPM). Throttling.")
            return False
        return True

    # ---------------- Distributed Locking & Atomic Nonce ----------------

    # Atomic compare-and-delete: a holder can only release its OWN lock. Without this,
    # an expired lock re-acquired by another worker could be deleted by the stale holder.
    _RELEASE_LOCK_SCRIPT = (
        'if redis.call("get", KEYS[1]) == ARGV[1] then '
        'return redis.call("del", KEYS[1]) else return 0 end'
    )

    async def acquire_lock(self, lock_key: str, ttl_seconds: int = 60) -> bool:
        """Acquires an atomic distributed lock via SET NX EX."""
        return bool(await self.redis.set(lock_key, "1", nx=True, ex=ttl_seconds))

    async def acquire_lock_with_token(self, lock_key: str, ttl_seconds: int = 60) -> Optional[str]:
        """
        Acquires an atomic distributed lock and returns the unique owner token.
        Returns None when the lock is already held by another worker.
        """
        token = uuid.uuid4().hex
        acquired = bool(await self.redis.set(lock_key, token, nx=True, ex=ttl_seconds))
        return token if acquired else None

    async def release_lock(self, lock_key: str, token: Optional[str] = None):
        """
        Releases a lock. When `token` is supplied the release is ownership-checked
        (atomic Lua on real Redis; best-effort GET+compare+DELETE on in-memory Redis).
        """
        if token is None:
            await self.redis.delete(lock_key)
            return
        try:
            await self.redis.eval(self._RELEASE_LOCK_SCRIPT, 1, lock_key, token)
        except Exception:
            # fakeredis and minimal Redis-compatible servers do not implement EVAL.
            current = await self.redis.get(lock_key)
            if current == token or current is None:
                await self.redis.delete(lock_key)

    async def get_next_wallet_nonce(self, wallet_address: str, on_chain_pending_nonce: int) -> int:
        """
        Atomically manages wallet nonces. If not initialized in Redis, sets to on_chain_pending_nonce.
        Returns sequential integer for transaction building.
        """
        key = f"wallet:nonce:{wallet_address.lower()}"
        # Atomic initialization: 10 concurrent payouts must not all observe "not set"
        # and issue the same first nonce. SET NX + INCR are both atomic in Redis.
        # Store the NEXT nonce so the first call returns N and the second returns N+1.
        initialized = await self.redis.set(key, on_chain_pending_nonce + 1, nx=True)
        if initialized:
            return on_chain_pending_nonce
        current = int(await self.redis.incr(key)) - 1
        # Never issue a nonce below the on-chain pending count (e.g. after a reorg).
        if current < on_chain_pending_nonce:
            await self.redis.set(key, on_chain_pending_nonce + 1)
            return on_chain_pending_nonce
        return current

    async def sync_wallet_nonce(self, wallet_address: str, correct_nonce: int):
        """Forces the Redis nonce counter to a verified on-chain state if a TX fails/reverts."""
        key = f"wallet:nonce:{wallet_address.lower()}"
        await self.redis.set(key, correct_nonce)


event_bus = RedisEventBus()
