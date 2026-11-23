"""Notify-me-when-back-in-stock subscriptions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, List

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.models.entities import StockAlertSubscription, Customer, MasterProduct
from src.core.redis import event_bus


def _uid(value: Any):
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class StockAlertService:
    async def subscribe(
        self, session: AsyncSession, *, customer_id: Any, product_id: Any
    ) -> dict:
        cid, pid = _uid(customer_id), _uid(product_id)
        existing = (await session.execute(
            select(StockAlertSubscription).where(
                and_(
                    StockAlertSubscription.customer_id == cid,
                    StockAlertSubscription.master_product_id == pid,
                )
            )
        )).scalar_one_or_none()
        if existing:
            existing.is_active = True
            existing.notified_at = None
            await session.commit()
            return {"status": "ok", "subscribed": True, "id": str(existing.id)}
        row = StockAlertSubscription(
            customer_id=cid, master_product_id=pid, is_active=True
        )
        session.add(row)
        await session.commit()
        return {"status": "ok", "subscribed": True, "id": str(row.id)}

    async def notify_restocked_products(
        self, session: AsyncSession, product_ids: List[Any]
    ) -> int:
        """Fire alerts for active subscriptions on products that just came back in stock."""
        if not product_ids:
            return 0
        pids = [_uid(p) for p in product_ids]
        rows = (await session.execute(
            select(StockAlertSubscription)
            .where(
                and_(
                    StockAlertSubscription.master_product_id.in_(pids),
                    StockAlertSubscription.is_active == True,  # noqa: E712
                )
            )
        )).scalars().all()

        notified = 0
        for row in rows:
            cust = (await session.execute(
                select(Customer).where(Customer.id == row.customer_id)
            )).scalar_one_or_none()
            prod = (await session.execute(
                select(MasterProduct).where(MasterProduct.id == row.master_product_id)
            )).scalar_one_or_none()
            if not cust or not cust.telegram_id or not prod:
                continue
            await event_bus.publish("bot:stock_alert", {
                "telegram_id": cust.telegram_id,
                "product_id": str(prod.id),
                "product_title": prod.title_ar,
                "emoji": getattr(prod, "custom_emoji", "✨") or "✨",
            })
            row.is_active = False
            row.notified_at = datetime.now(timezone.utc)
            notified += 1
        if notified:
            await session.commit()
        return notified


stock_alert_service = StockAlertService()
