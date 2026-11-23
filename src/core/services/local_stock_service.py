"""Local encrypted inventory pool — atomic pop with SKIP LOCKED."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.entities import LocalStockItem, MasterProduct, AuditLog
from src.core.security import encrypt_secret, decrypt_secret
from src.core.services.audit_helpers import merge_audit_new_values


def _uid(value: Any):
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class LocalStockService:
    async def available_count(self, session: AsyncSession, product_id: Any) -> int:
        q = await session.execute(
            select(func.count(LocalStockItem.id)).where(
                and_(
                    LocalStockItem.master_product_id == _uid(product_id),
                    LocalStockItem.is_sold == False,  # noqa: E712
                )
            )
        )
        return int(q.scalar_one() or 0)

    async def bulk_add(
        self,
        session: AsyncSession,
        product_id: Any,
        lines: List[str],
        *,
        reviewer: str = "admin",
    ) -> Dict[str, Any]:
        pid = _uid(product_id)
        prod = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == pid)
        )).scalar_one_or_none()
        if not prod:
            raise ValueError("Product not found")
        added = 0
        for raw in lines:
            line = (raw or "").strip()
            if not line:
                continue
            session.add(LocalStockItem(
                master_product_id=pid,
                payload_encrypted=encrypt_secret(line),
                created_by=reviewer,
            ))
            added += 1
        session.add(AuditLog(
            admin_user_id=None,
            action="local_stock_bulk_add",
            entity_type="master_product",
            entity_id=pid,
            old_values={},
            new_values=merge_audit_new_values(reviewer, {"added": added}),
        ))
        await session.commit()
        return {
            "status": "ok",
            "added": added,
            "available": await self.available_count(session, pid),
        }

    async def pop_for_order(
        self,
        session: AsyncSession,
        *,
        product_id: Any,
        quantity: int,
        order_id: Any,
    ) -> Optional[str]:
        """
        Atomically reserve `quantity` unsold rows (FOR UPDATE SKIP LOCKED),
        decrypt, mark sold. Returns joined payload or None if insufficient stock.
        """
        qty = max(int(quantity or 1), 1)
        pid = _uid(product_id)
        oid = _uid(order_id)

        # Dialect-friendly SKIP LOCKED via execution_options + with_for_update
        stmt = (
            select(LocalStockItem)
            .where(
                and_(
                    LocalStockItem.master_product_id == pid,
                    LocalStockItem.is_sold == False,  # noqa: E712
                )
            )
            .order_by(LocalStockItem.created_at.asc())
            .limit(qty)
            .with_for_update(skip_locked=True)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        if len(rows) < qty:
            return None

        now = datetime.now(timezone.utc)
        payloads = []
        for row in rows:
            payloads.append(decrypt_secret(row.payload_encrypted))
            row.is_sold = True
            row.sold_order_id = oid
            row.sold_at = now
        return "\n".join(payloads)

    async def has_stock(self, session: AsyncSession, product_id: Any, quantity: int = 1) -> bool:
        return await self.available_count(session, product_id) >= max(int(quantity or 1), 1)


local_stock_service = LocalStockService()
