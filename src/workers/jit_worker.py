from arq.connections import RedisSettings
from loguru import logger

from src.config import settings
from src.core.services.jit_executor import jit_executor
from src.workers.delivery_poller import poll_pending_deliveries

try:
    from arq.cron import cron as _arq_cron
    _DELIVERY_CRON = [_arq_cron(poll_pending_deliveries, second={0, 15, 30, 45})]
except Exception:  # pragma: no cover
    _DELIVERY_CRON = []


async def execute_jit_order_task(ctx: dict, order_id: str):
    """ARQ async job entrypoint for executing JIT orders."""
    logger.info(f"Worker picked up JIT order job: {order_id}")
    await jit_executor.execute_order(order_id)
    return {"status": "completed", "order_id": order_id}


class WorkerSettings:
    functions = [execute_jit_order_task, poll_pending_deliveries]
    cron_jobs = _DELIVERY_CRON
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 20
    job_timeout = 300  # 5 minutes
    keep_result = 3600
