"""Reliable JIT enqueue — prefers ARQ worker, falls back to in-process task."""
from __future__ import annotations

import asyncio
from loguru import logger

from src.config import settings


async def enqueue_jit_order(order_id: str) -> None:
    """
    Enqueue JIT fulfillment for an order.
    Primary: ARQ Redis job (survives process restart).
    Fallback: asyncio.create_task when ARQ/redis pool is unavailable (tests/dev).
    """
    order_id = str(order_id)
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        try:
            await redis.enqueue_job("execute_jit_order_task", order_id)
            logger.info(f"JIT order {order_id} enqueued via ARQ")
            return
        finally:
            await redis.close()
    except Exception as e:
        logger.warning(f"ARQ enqueue failed for {order_id} ({e}); falling back to in-process task")

    from src.core.services.jit_executor import jit_executor
    asyncio.create_task(jit_executor.execute_order(order_id))
