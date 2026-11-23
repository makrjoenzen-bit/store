"""
Supplier Reliability Scoring — rolling 30d metrics + auto-demotion.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List

from loguru import logger
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.models.entities import (
    Supplier, Order, WarrantyClaim, SupplierHealthLog, AuditLog,
)
from src.core.models.enums import OrderState
from src.core.redis import event_bus


class SupplierQualityService:
    async def compute_and_apply(self, session: AsyncSession) -> List[Dict[str, Any]]:
        since = datetime.now(timezone.utc) - timedelta(days=30)
        suppliers = (await session.execute(select(Supplier))).scalars().all()
        leaderboard: List[Dict[str, Any]] = []
        claim_max = float(settings.SUPPLIER_WARRANTY_CLAIM_RATE_MAX)
        fail_max = float(settings.SUPPLIER_API_FAILURE_RATE_MAX)
        min_orders = int(settings.SUPPLIER_QUALITY_MIN_ORDERS)

        for s in suppliers:
            metrics = await self._metrics_for(session, s, since)
            s.warranty_claim_rate_30d = Decimal(str(metrics["warranty_claim_rate"]))
            s.api_failure_rate_30d = Decimal(str(metrics["api_failure_rate"]))

            demoted = False
            reason = None
            if metrics["total_orders"] >= min_orders:
                if metrics["warranty_claim_rate"] > claim_max:
                    demoted = True
                    reason = "warranty"
                    penalty = max(int(s.quality_penalty_score or 0), 50)
                    penalty = min(200, penalty + int(metrics["warranty_claim_rate"]))
                    s.quality_penalty_score = penalty
                elif metrics["api_failure_rate"] > fail_max:
                    demoted = True
                    reason = "api_failure"
                    penalty = max(int(s.quality_penalty_score or 0), 40)
                    penalty = min(200, penalty + int(metrics["api_failure_rate"]))
                    s.quality_penalty_score = penalty

            row = {
                "supplier_id": str(s.id),
                "name": s.name,
                **metrics,
                "quality_penalty_score": int(s.quality_penalty_score or 0),
                "effective_priority": int(s.priority_weight or 0) - int(s.quality_penalty_score or 0),
                "demoted": demoted,
                "demote_reason": reason,
            }
            leaderboard.append(row)

            if demoted:
                rate = (
                    metrics["warranty_claim_rate"]
                    if reason == "warranty"
                    else metrics["api_failure_rate"]
                )
                msg = (
                    f"⚠️ تحذير جودة: نسبة أعطال المورد {s.name} وصلت إلى "
                    f"{rate:.1f}% — تم خفض أولويته تلقائياً."
                )
                await event_bus.publish_alert(
                    title="تحذير جودة مورد",
                    message=msg,
                    level="warning",
                    meta={
                        "alert_type": "supplier_quality_demotion",
                        "supplier_id": str(s.id),
                        "supplier_name": s.name,
                        "rate": rate,
                        "reason": reason,
                    },
                )
                session.add(AuditLog(
                    action="supplier_quality_demotion",
                    entity_type="supplier",
                    entity_id=s.id,
                    new_values={
                        "quality_penalty_score": s.quality_penalty_score,
                        "warranty_claim_rate_30d": float(s.warranty_claim_rate_30d or 0),
                        "api_failure_rate_30d": float(s.api_failure_rate_30d or 0),
                        "reason": reason,
                        "message": msg,
                    },
                ))
                logger.warning(msg)

        await session.commit()
        leaderboard.sort(key=lambda r: (-r["effective_priority"], r["warranty_claim_rate"]))
        return leaderboard

    async def _metrics_for(
        self, session: AsyncSession, supplier: Supplier, since: datetime
    ) -> Dict[str, Any]:
        orders = (await session.execute(
            select(Order).where(
                and_(Order.supplier_id == supplier.id, Order.created_at >= since)
            )
        )).scalars().all()
        total = len(orders)
        completed_ids = [
            o.id for o in orders
            if o.state in (
                OrderState.COMPLETED,
                OrderState.ORDER_FULFILLED,
                OrderState.DELIVERED,
            )
            or o.delivered_at
        ]
        claim_count = 0
        if completed_ids:
            claim_count = (await session.execute(
                select(func.count()).select_from(WarrantyClaim).where(
                    WarrantyClaim.order_id.in_(completed_ids)
                )
            )).scalar() or 0

        logs = (await session.execute(
            select(SupplierHealthLog).where(
                and_(
                    SupplierHealthLog.supplier_id == supplier.id,
                    SupplierHealthLog.created_at >= since,
                )
            )
        )).scalars().all()

        fail_n = sum(1 for lg in logs if not lg.is_success)
        probes = len(logs)
        api_failure_rate = (fail_n / probes * 100.0) if probes else 0.0

        durations = []
        for o in orders:
            if o.delivered_at and o.created_at:
                d0, d1 = o.created_at, o.delivered_at
                if d0.tzinfo is None:
                    d0 = d0.replace(tzinfo=timezone.utc)
                if d1.tzinfo is None:
                    d1 = d1.replace(tzinfo=timezone.utc)
                durations.append((d1 - d0).total_seconds())
        avg_delivery = sum(durations) / len(durations) if durations else None

        net_profit = Decimal("0")
        for o in orders:
            if o.our_price is not None and o.supplier_cost is not None:
                net_profit += Decimal(str(o.our_price)) - Decimal(str(o.supplier_cost))

        claim_rate = 0.0
        if completed_ids:
            claim_rate = claim_count / len(completed_ids) * 100.0

        return {
            "total_orders": total,
            "api_failure_rate": round(api_failure_rate, 2),
            "warranty_claim_rate": round(claim_rate, 2),
            "avg_delivery_seconds": round(avg_delivery, 1) if avg_delivery is not None else None,
            "net_profit_contribution": float(net_profit.quantize(Decimal("0.01"))),
        }

    async def leaderboard(self, session: AsyncSession) -> List[Dict[str, Any]]:
        return await self.compute_and_apply(session)


supplier_quality_service = SupplierQualityService()
