"""Shared order admin ops: retry JIT + 1-click refund (Dashboard + Admin Bot)."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.models.entities import Order, Customer, WalletTransaction, AuditLog
from src.core.models.enums import OrderState, WalletTxType
from src.core.services.order_service import order_service
from src.core.services.jit_queue import enqueue_jit_order
from src.core.redis import event_bus
from src.core.services.audit_helpers import merge_audit_new_values


def _uid(value: Any):
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


RETRYABLE = {
    OrderState.FAILED_MANUAL_REVIEW,
    OrderState.PAYMENT_CONFIRMED,
    OrderState.FALLBACK_ROUTING,
}

UNPAID = {
    OrderState.CREATED,
    OrderState.AWAITING_PAYMENT,
    OrderState.SHAM_CASH_REVIEW,
    OrderState.QUOTE_LOCKED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
}


class OrderAdminService:
    async def find_by_number(self, session: AsyncSession, order_number: str) -> Dict[str, Any]:
        onum = (order_number or "").strip().lstrip("#")
        order = (await session.execute(
            select(Order)
            .where(Order.order_number == onum)
            .options(
                selectinload(Order.customer),
                selectinload(Order.master_product),
                selectinload(Order.supplier),
            )
        )).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")
        return self._serialize(order)

    async def get(self, session: AsyncSession, order_id: Any) -> Dict[str, Any]:
        order = (await session.execute(
            select(Order)
            .where(Order.id == _uid(order_id))
            .options(
                selectinload(Order.customer),
                selectinload(Order.master_product),
                selectinload(Order.supplier),
            )
        )).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")
        return self._serialize(order)

    def _serialize(self, order: Order) -> Dict[str, Any]:
        return {
            "id": str(order.id),
            "order_number": order.order_number,
            "state": order.state.value,
            "our_price": float(order.our_price),
            "supplier_cost": float(order.supplier_cost or 0),
            "profit_margin": float(order.profit_margin or 0),
            "gas_fee_actual": float(order.gas_fee_actual or 0),
            "quantity": order.quantity or 1,
            "customer_telegram_id": order.customer.telegram_id if order.customer else None,
            "customer_username": order.customer.telegram_username if order.customer else None,
            "customer_name": (order.customer.first_name if order.customer else None),
            "product_title": order.master_product.title_ar if order.master_product else None,
            "supplier_name": order.supplier.name if order.supplier else None,
            "can_retry_jit": order.state in RETRYABLE,
            "can_refund": order.state not in UNPAID and order.state != OrderState.FAILED_REFUNDED,
        }

    async def retry_jit(
        self,
        session: AsyncSession,
        order_id: Any,
        *,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        order = (await session.execute(
            select(Order).where(Order.id == _uid(order_id)).with_for_update()
        )).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")
        if order.state not in RETRYABLE:
            raise ValueError(f"Order state {order.state.value} is not retryable")

        old_state = order.state.value
        if order.state == OrderState.FAILED_MANUAL_REVIEW:
            await order_service.transition_order_state(
                session, order, OrderState.PAYMENT_CONFIRMED,
                f"admin_retry_jit by {reviewer}",
            )

        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="order_retry_jit",
            entity_type="order",
            entity_id=order.id,
            old_values={"state": old_state},
            new_values=merge_audit_new_values(reviewer, {"state": order.state.value}),
        ))
        await session.commit()
        await enqueue_jit_order(str(order.id))
        return {
            "status": "ok",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "state": order.state.value,
            "message": "JIT re-enqueued",
        }

    async def refund_to_wallet(
        self,
        session: AsyncSession,
        order_id: Any,
        *,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
        reason: str = "Admin 1-click refund",
    ) -> Dict[str, Any]:
        order = (await session.execute(
            select(Order)
            .where(Order.id == _uid(order_id))
            .options(selectinload(Order.customer))
            .with_for_update()
        )).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")
        if order.state in UNPAID:
            raise ValueError("Order was never paid; refund is not allowed")
        if order.state == OrderState.FAILED_REFUNDED:
            raise ValueError("Order already refunded")
        if order.state == OrderState.COMPLETED:
            # Allow admin override refund of completed only via explicit path —
            # default: allow for support-style recovery (clawback incentives)
            pass

        lock_key = f"lock:refund:{str(order.id)}"
        lock_token = await event_bus.acquire_lock_with_token(lock_key, ttl_seconds=30)
        if not lock_token:
            raise ValueError("A refund for this order is already in progress")

        try:
            fresh = (await session.execute(
                select(Order.state).where(Order.id == order.id).with_for_update()
            )).scalar_one_or_none()
            if fresh is None or fresh == OrderState.FAILED_REFUNDED or fresh in UNPAID:
                raise ValueError("Order state changed; refund rejected")

            # Claw back cashback / referral incentives for this order
            incentive_rows = (await session.execute(
                select(WalletTransaction).where(
                    and_(
                        WalletTransaction.order_id == order.id,
                        WalletTransaction.tx_type.in_(
                            [WalletTxType.CASHBACK, WalletTxType.REFERRAL_COMMISSION]
                        ),
                        WalletTransaction.amount > 0,
                    )
                )
            )).scalars().all()
            clawed = Decimal("0")
            for incentive_tx in incentive_rows:
                remaining = (await session.execute(
                    select(Customer.wallet_balance).where(Customer.id == incentive_tx.customer_id)
                )).scalar_one_or_none() or Decimal("0")
                reversible = min(incentive_tx.amount, max(Decimal("0"), remaining))
                if reversible > 0:
                    await order_service.debit_customer_wallet(
                        session=session,
                        customer_id=str(incentive_tx.customer_id),
                        amount=reversible,
                        tx_type=WalletTxType.INCENTIVE_CLAWBACK,
                        description=f"استرداد كاش باك/عمولة ملغية للطلب #{order.order_number}",
                        order_id=str(order.id),
                    )
                    clawed += reversible

            refund_amount = order.our_price
            await order_service.credit_customer_wallet(
                session=session,
                customer_id=str(order.customer_id),
                amount=refund_amount,
                tx_type=WalletTxType.REFUND_CREDIT,
                description=f"استرداد إداري للطلب #{order.order_number}: {reason}",
                order_id=str(order.id),
            )
            await order_service.transition_order_state(
                session, order, OrderState.FAILED_REFUNDED,
                f"admin_refund by {reviewer}",
            )
            session.add(AuditLog(
                admin_user_id=_uid(admin_user_id) if admin_user_id else None,
                action="order_refund",
                entity_type="order",
                entity_id=order.id,
                old_values={"state": fresh.value if hasattr(fresh, "value") else str(fresh)},
                new_values=merge_audit_new_values(reviewer, {
                    "state": OrderState.FAILED_REFUNDED.value,
                    "refund_amount": float(refund_amount),
                    "reason": reason,
                }),
            ))
            await session.commit()

            if order.customer and order.customer.telegram_id:
                await event_bus.publish("bot:notify_refund", {
                    "telegram_id": order.customer.telegram_id,
                    "order_number": order.order_number,
                    "amount": float(refund_amount),
                })

            # Post-fulfillment failure report
            try:
                from src.core.services.fulfillment_report import fulfillment_report_service
                await fulfillment_report_service.dispatch_for_order(
                    session, order_id=order.id, outcome="FAILED_REFUNDED"
                )
            except Exception:
                pass

            return {
                "status": "ok",
                "order_id": str(order.id),
                "order_number": order.order_number,
                "refund_amount": float(refund_amount),
                "incentives_clawed_back": float(clawed),
                "state": OrderState.FAILED_REFUNDED.value,
            }
        finally:
            await event_bus.release_lock(lock_key, token=lock_token)


order_admin_service = OrderAdminService()
