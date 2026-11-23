"""Atomic order claim/lock for Staff+Owner manual review (Sham Cash / Binance)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.models.entities import Order, AuditLog
from src.core.services.audit_helpers import merge_audit_new_values
from src.config import settings


def _uid(value: Any):
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class OrderClaimService:
    async def claim(
        self,
        session: AsyncSession,
        order_id: Any,
        *,
        telegram_id: int,
        username: Optional[str] = None,
        reviewer: str = "staff",
    ) -> Dict[str, Any]:
        stmt = (
            select(Order)
            .where(Order.id == _uid(order_id))
            .with_for_update()
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")

        now = datetime.now(timezone.utc)
        if order.claimed_by_telegram_id and int(order.claimed_by_telegram_id) != int(telegram_id):
            raise ValueError(
                f"Order already claimed by @{order.claimed_by_username or order.claimed_by_telegram_id}"
            )

        order.claimed_by_telegram_id = int(telegram_id)
        order.claimed_by_username = (username or "").lstrip("@") or None
        order.claimed_at = now
        session.add(AuditLog(
            admin_user_id=None,
            action="order_claim",
            entity_type="order",
            entity_id=order.id,
            old_values={},
            new_values=merge_audit_new_values(reviewer, {
                "claimed_by_telegram_id": int(telegram_id),
                "claimed_by_username": order.claimed_by_username,
            }),
        ))
        await session.commit()
        return {
            "status": "ok",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "claimed_by_telegram_id": int(telegram_id),
            "claimed_by_username": order.claimed_by_username,
        }

    async def release(
        self,
        session: AsyncSession,
        order_id: Any,
        *,
        telegram_id: int,
        reviewer: str = "owner",
        force: bool = False,
    ) -> Dict[str, Any]:
        stmt = select(Order).where(Order.id == _uid(order_id)).with_for_update()
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")
        is_owner = settings.is_owner(telegram_id)
        if not force and not is_owner:
            if order.claimed_by_telegram_id and int(order.claimed_by_telegram_id) != int(telegram_id):
                raise ValueError("Only claimer or Owner can unlock")
        old = order.claimed_by_telegram_id
        order.claimed_by_telegram_id = None
        order.claimed_by_username = None
        order.claimed_at = None
        session.add(AuditLog(
            admin_user_id=None,
            action="order_unclaim",
            entity_type="order",
            entity_id=order.id,
            old_values={"claimed_by_telegram_id": old},
            new_values=merge_audit_new_values(reviewer, {"claimed_by_telegram_id": None}),
        ))
        await session.commit()
        return {"status": "ok", "order_id": str(order.id), "unlocked": True}

    def can_act(self, order: Order, telegram_id: int) -> bool:
        if settings.is_owner(telegram_id):
            return True
        if not order.claimed_by_telegram_id:
            return False
        return int(order.claimed_by_telegram_id) == int(telegram_id)


order_claim_service = OrderClaimService()
