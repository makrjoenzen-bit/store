"""Shared marketing ops — coupons + safe broadcast launch."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.entities import Coupon, BroadcastCampaign, AuditLog
from src.core.models.enums import DiscountType
from src.core.redis import event_bus
from src.core.services.audit_helpers import merge_audit_new_values


def _uid(value: Any):
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _utc_now():
    return datetime.now(timezone.utc)


class MarketingAdminService:
    async def list_coupons(self, session: AsyncSession) -> List[Dict[str, Any]]:
        coupons = (await session.execute(
            select(Coupon).order_by(desc(Coupon.created_at))
        )).scalars().all()
        return [
            {
                "id": str(c.id),
                "code": c.code,
                "discount_type": c.discount_type.value,
                "value": float(c.value),
                "max_uses": c.max_uses,
                "used_count": c.used_count,
                "is_active": c.is_active,
            }
            for c in coupons
        ]

    async def create_coupon(
        self,
        session: AsyncSession,
        *,
        code: str,
        discount_type: DiscountType,
        value: Decimal,
        max_uses: Optional[int] = None,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        clean = code.strip().upper()
        existing = (await session.execute(
            select(Coupon).where(Coupon.code == clean)
        )).scalar_one_or_none()
        if existing:
            raise ValueError("Coupon code already exists")

        coupon = Coupon(
            code=clean,
            discount_type=discount_type,
            value=Decimal(str(value)),
            max_uses=max_uses,
            is_active=True,
        )
        session.add(coupon)
        await session.flush()
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="coupon_create",
            entity_type="coupon",
            entity_id=coupon.id,
            new_values=merge_audit_new_values(reviewer, {
                "code": clean,
                "discount_type": discount_type.value,
                "value": float(value),
                "max_uses": max_uses,
            }),
        ))
        await session.commit()
        return {"status": "ok", "coupon_id": str(coupon.id), "code": clean}

    async def toggle_coupon(
        self,
        session: AsyncSession,
        coupon_id: Any,
        *,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        coupon = (await session.execute(
            select(Coupon).where(Coupon.id == _uid(coupon_id))
        )).scalar_one_or_none()
        if not coupon:
            raise ValueError("Coupon not found")
        old = coupon.is_active
        coupon.is_active = not coupon.is_active
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="coupon_toggle",
            entity_type="coupon",
            entity_id=coupon.id,
            old_values={"is_active": old},
            new_values=merge_audit_new_values(reviewer, {"is_active": coupon.is_active}),
        ))
        await session.commit()
        return {"status": "ok", "is_active": coupon.is_active, "code": coupon.code}

    async def create_broadcast(
        self,
        session: AsyncSession,
        *,
        title: str,
        message_text: str,
        target_audience: str = "all",
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
        launch: bool = True,
        photo_file_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if target_audience not in ("all", "buyers_only", "balance_holders"):
            raise ValueError("Invalid target_audience")
        # Photo-only broadcasts allowed with short/empty caption
        text = (message_text or "").strip()
        if not photo_file_id and len(text) < 3:
            raise ValueError("Broadcast message too short")
        if not text and photo_file_id:
            text = "📢"

        campaign = BroadcastCampaign(
            title=title.strip() or "Admin Broadcast",
            message_text=text,
            image_url=f"tgfile:{photo_file_id}" if photo_file_id else None,
            target_audience=target_audience,
            status="running" if launch else "pending",
            started_at=_utc_now() if launch else None,
        )
        session.add(campaign)
        await session.flush()
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="broadcast_launch",
            entity_type="broadcast_campaign",
            entity_id=campaign.id,
            new_values=merge_audit_new_values(reviewer, {
                "title": campaign.title,
                "target_audience": target_audience,
            }),
        ))
        await session.commit()
        await session.refresh(campaign)

        if launch:
            # Fire-and-forget throttled worker (25 msg/s)
            from src.workers.broadcast_worker import run_broadcast_task
            asyncio.create_task(run_broadcast_task(str(campaign.id)))

        return {
            "status": "ok",
            "campaign_id": str(campaign.id),
            "target_audience": target_audience,
            "message": "Broadcast launched at ~25 msg/s" if launch else "Broadcast created (pending)",
        }


marketing_admin_service = MarketingAdminService()
