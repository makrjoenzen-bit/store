"""Warranty / replacement claims — shared by Customer Bot, Admin Bot, Mini App, Dashboard."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.models.entities import (
    WarrantyClaim, Order, Customer, MasterProduct, AuditLog,
)
from src.core.models.enums import OrderState, WalletTxType
from src.core.services.order_service import order_service
from src.core.services.audit_helpers import merge_audit_new_values
from src.core.redis import event_bus


def _uid(value: Any):
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


class WarrantyService:
    def days_remaining(self, order: Order) -> int:
        if not order.warranty_expires_at:
            return 0
        exp = order.warranty_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        delta = exp - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds() // 86400))

    def is_eligible(self, order: Order) -> bool:
        if order.state not in (OrderState.DELIVERED, OrderState.COMPLETED, OrderState.ORDER_FULFILLED):
            # COMPLETED is primary; also allow DELIVERED
            if order.state != OrderState.COMPLETED and not order.delivered_at:
                return False
        if not order.delivered_at and not order.warranty_expires_at:
            return False
        if order.warranty_expires_at:
            exp = order.warranty_expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) <= exp
        # Fallback: delivered_at + product warranty_days
        return False

    async def open_claim(
        self,
        session: AsyncSession,
        *,
        order_id: Any,
        customer_id: Any,
        issue_description: str,
        screenshot_file_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        stmt = (
            select(Order)
            .where(Order.id == _uid(order_id))
            .options(
                selectinload(Order.customer),
                selectinload(Order.master_product),
                selectinload(Order.supplier),
            )
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")
        if str(order.customer_id) != str(_uid(customer_id)):
            raise ValueError("Order does not belong to customer")
        if not self.is_eligible(order):
            raise ValueError("انتهت فترة الضمان لهذا الطلب")

        existing = (await session.execute(
            select(WarrantyClaim).where(
                and_(
                    WarrantyClaim.order_id == order.id,
                    WarrantyClaim.status == "open",
                )
            )
        )).scalar_one_or_none()
        if existing:
            raise ValueError("لديك بالفعل طلب ضمان مفتوح لهذا الطلب")

        claim = WarrantyClaim(
            order_id=order.id,
            customer_id=_uid(customer_id),
            status="open",
            issue_description=(issue_description or "").strip(),
            screenshot_file_id=screenshot_file_id,
            original_delivered=order.delivered_product,
        )
        session.add(claim)
        await session.commit()
        await session.refresh(claim)

        card = self.format_admin_card(order, claim)
        await event_bus.publish("admin:warranty_claim", {
            "claim_id": str(claim.id),
            "order_id": str(order.id),
            "order_number": order.order_number,
            "caption": card,
            "screenshot_file_id": screenshot_file_id,
        })
        return {"status": "ok", "claim_id": str(claim.id), "days_remaining": self.days_remaining(order)}

    def format_admin_card(self, order: Order, claim: WarrantyClaim) -> str:
        cust = order.customer
        user = f"@{cust.telegram_username}" if cust and cust.telegram_username else (cust.first_name if cust else "—")
        supplier = order.supplier.name if order.supplier else "—"
        ext = order.supplier_order_id or "—"
        days = self.days_remaining(order)
        return "\n".join([
            "🛡️ **طلب ضمان / استبدال**",
            "",
            f"🔖 الطلب: `{order.order_number}`",
            f"👤 العميل: {user} · TG `{cust.telegram_id if cust else '—'}`",
            f"📦 المنتج: {order.master_product.title_ar if order.master_product else '—'}",
            f"🔑 الأصلي: `{ (order.delivered_product or '—')[:80] }`",
            f"⏳ متبقي من الضمان: `{days}` يوم",
            f"📝 المشكلة: {claim.issue_description}",
            f"🏭 المورد: **{supplier}**",
            f"🔖 رقم الطلب عند المورد: `{ext}`",
        ])

    async def list_open(self, session: AsyncSession) -> List[Dict[str, Any]]:
        rows = (await session.execute(
            select(WarrantyClaim)
            .where(WarrantyClaim.status == "open")
            .options(
                selectinload(WarrantyClaim.order).selectinload(Order.customer),
                selectinload(WarrantyClaim.order).selectinload(Order.master_product),
                selectinload(WarrantyClaim.order).selectinload(Order.supplier),
            )
            .order_by(WarrantyClaim.created_at.asc())
        )).scalars().all()
        out = []
        for c in rows:
            o = c.order
            out.append({
                "claim_id": str(c.id),
                "order_id": str(c.order_id),
                "order_number": o.order_number if o else None,
                "issue_description": c.issue_description,
                "days_remaining": self.days_remaining(o) if o else 0,
                "supplier_name": o.supplier.name if o and o.supplier else None,
                "external_order_id": o.supplier_order_id if o else None,
                "customer_telegram_id": o.customer.telegram_id if o and o.customer else None,
                "product_title": o.master_product.title_ar if o and o.master_product else None,
                "original_delivered": c.original_delivered,
            })
        return out

    async def deliver_replacement(
        self,
        session: AsyncSession,
        claim_id: Any,
        *,
        replacement_payload: str,
        reviewer: str,
    ) -> Dict[str, Any]:
        claim = await self._get_open(session, claim_id)
        order = claim.order
        claim.status = "resolved"
        claim.replacement_delivered = replacement_payload
        claim.resolved_by = reviewer
        claim.resolved_at = datetime.now(timezone.utc)
        if order:
            order.delivered_product = replacement_payload
            order.delivered_at = datetime.now(timezone.utc)
        session.add(AuditLog(
            admin_user_id=None,
            action="warranty_replace",
            entity_type="warranty_claim",
            entity_id=claim.id,
            old_values={"status": "open"},
            new_values=merge_audit_new_values(reviewer, {"status": "resolved"}),
        ))
        await session.commit()
        if order and order.customer and order.customer.telegram_id:
            await event_bus.publish("bot:deliver_product", {
                "order_id": str(order.id),
                "telegram_id": order.customer.telegram_id,
                "order_number": order.order_number,
                "product_title": order.master_product.title_ar if order.master_product else "بديل الضمان",
                "payload": replacement_payload,
                "quantity": order.quantity or 1,
                "warranty_expires_at": (
                    order.warranty_expires_at.isoformat() if order.warranty_expires_at else None
                ),
            })
        return {"status": "ok", "claim_id": str(claim.id)}

    async def refund_to_wallet(
        self,
        session: AsyncSession,
        claim_id: Any,
        *,
        reviewer: str,
    ) -> Dict[str, Any]:
        claim = await self._get_open(session, claim_id)
        order = claim.order
        if not order:
            raise ValueError("Order missing")
        amount = order.our_price
        await order_service.credit_customer_wallet(
            session=session,
            customer_id=str(order.customer_id),
            amount=amount,
            tx_type=WalletTxType.REFUND_CREDIT,
            description=f"استرداد ضمان للطلب #{order.order_number}",
            order_id=str(order.id),
        )
        claim.status = "refunded"
        claim.resolved_by = reviewer
        claim.resolved_at = datetime.now(timezone.utc)
        session.add(AuditLog(
            admin_user_id=None,
            action="warranty_refund",
            entity_type="warranty_claim",
            entity_id=claim.id,
            old_values={"status": "open"},
            new_values=merge_audit_new_values(reviewer, {"status": "refunded", "amount": float(amount)}),
        ))
        await session.commit()
        if order.customer and order.customer.telegram_id:
            await event_bus.publish("bot:send_message", {
                "telegram_id": order.customer.telegram_id,
                "text": (
                    f"💸 تم استرداد `${amount:.2f}` إلى محفظتك لطلب الضمان "
                    f"`#{order.order_number}`."
                ),
            })
        return {"status": "ok", "refund_amount": float(amount)}

    async def reject(
        self,
        session: AsyncSession,
        claim_id: Any,
        *,
        reason: str,
        reviewer: str,
    ) -> Dict[str, Any]:
        claim = await self._get_open(session, claim_id)
        claim.status = "rejected"
        claim.reject_reason = reason
        claim.resolved_by = reviewer
        claim.resolved_at = datetime.now(timezone.utc)
        session.add(AuditLog(
            admin_user_id=None,
            action="warranty_reject",
            entity_type="warranty_claim",
            entity_id=claim.id,
            old_values={"status": "open"},
            new_values=merge_audit_new_values(reviewer, {"status": "rejected", "reason": reason}),
        ))
        await session.commit()
        order = claim.order
        if order and order.customer and order.customer.telegram_id:
            await event_bus.publish("bot:send_message", {
                "telegram_id": order.customer.telegram_id,
                "text": f"❌ تم رفض طلب الضمان للطلب `#{order.order_number}`\nالسبب: {reason}",
            })
        return {"status": "ok", "claim_id": str(claim.id)}

    async def _get_open(self, session: AsyncSession, claim_id: Any) -> WarrantyClaim:
        claim = (await session.execute(
            select(WarrantyClaim)
            .where(WarrantyClaim.id == _uid(claim_id))
            .options(
                selectinload(WarrantyClaim.order).selectinload(Order.customer),
                selectinload(WarrantyClaim.order).selectinload(Order.master_product),
            )
            .with_for_update()
        )).scalar_one_or_none()
        if not claim:
            raise ValueError("Claim not found")
        if claim.status != "open":
            raise ValueError(f"Claim not open ({claim.status})")
        return claim


warranty_service = WarrantyService()
