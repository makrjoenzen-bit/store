"""
Async pending-delivery poller.
Polls supplier adapters for orders in PENDING_SUPPLIER_DELIVERY every ~15s.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.core.adapters.base import STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING_DELIVERY
from src.core.adapters.registry import AdapterRegistry
from src.core.database import AsyncSessionLocal
from src.core.models.entities import Order
from src.core.models.enums import OrderState
from src.core.redis import event_bus
from src.core.services.jit_executor import jit_executor


class DeliveryPoller:
    def __init__(self, interval_seconds: float = 15.0):
        self.interval_seconds = interval_seconds

    async def run(self):
        logger.info(f"Delivery poller started (every {self.interval_seconds}s)")
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Delivery poller cycle error: {e}")
            await asyncio.sleep(self.interval_seconds)

    async def poll_once(self) -> int:
        """Returns number of orders resolved this cycle."""
        resolved = 0
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Order)
                .where(Order.state == OrderState.PENDING_SUPPLIER_DELIVERY)
                .options(
                    selectinload(Order.supplier),
                    selectinload(Order.master_product),
                    selectinload(Order.customer),
                    selectinload(Order.invoice),
                )
                .limit(50)
            )
            orders = (await session.execute(stmt)).scalars().all()

        for order in orders:
            try:
                ok = await self._poll_order(str(order.id))
                if ok:
                    resolved += 1
            except Exception as e:
                logger.error(f"Poll failed for order {order.order_number}: {e}")
        return resolved

    async def _poll_order(self, order_id: str) -> bool:
        lock_key = f"lock:delivery_poll:{order_id}"
        token = await event_bus.acquire_lock_with_token(lock_key, ttl_seconds=60)
        if not token:
            return False
        try:
            async with AsyncSessionLocal() as session:
                import uuid
                oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
                order = (
                    await session.execute(
                        select(Order)
                        .where(Order.id == oid)
                        .options(
                            selectinload(Order.supplier),
                            selectinload(Order.master_product),
                            selectinload(Order.customer),
                            selectinload(Order.invoice),
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if not order or order.state != OrderState.PENDING_SUPPLIER_DELIVERY:
                    return False
                if not order.supplier or not order.supplier_order_id:
                    return False

                adapter = AdapterRegistry.create_from_supplier(order.supplier)
                result = await adapter.get_order_status(order.supplier_order_id)

                if result.status == STATUS_COMPLETED:
                    payload = (result.delivered_payload or "").strip()
                    if not payload:
                        logger.warning(
                            f"Order {order.order_number} completed at supplier but empty payload"
                        )
                        return False
                    await jit_executor.complete_order_fulfillment(session, order, payload)
                    return True

                if result.status == STATUS_FAILED:
                    await jit_executor._handle_supplier_exhausted(
                        session, order,
                        f"Supplier refunded/failed pending order: {result.error_message}",
                    )
                    return True

                # Still pending
                if result.status not in (STATUS_PENDING_DELIVERY, "pending", "not_found"):
                    logger.debug(
                        f"Order {order.order_number} poll status={result.status}"
                    )
                return False
        finally:
            await event_bus.release_lock(lock_key, token=token)


delivery_poller = DeliveryPoller()


async def poll_pending_deliveries(ctx: dict = None):
    """ARQ-compatible entrypoint."""
    n = await delivery_poller.poll_once()
    return {"resolved": n, "at": datetime.now(timezone.utc).isoformat()}
