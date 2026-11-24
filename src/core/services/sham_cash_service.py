"""Shared Sham Cash admin operations used by Dashboard API and Admin Bot."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, and_, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.models.entities import (
    Order, PaymentInvoice, AuditLog,
)
from src.core.models.enums import OrderState, InvoiceStatus, PaymentMethod
from src.core.services.order_service import order_service
from src.core.services.jit_queue import enqueue_jit_order
from src.core.redis import event_bus
from src.core.services.audit_helpers import merge_audit_new_values


def _uid(value: Any):
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def normalize_tx_ref(tx_ref: Optional[str]) -> str:
    return re.sub(r"\s+", "", (tx_ref or "").strip())


class ShamCashService:
    async def find_duplicate_receipt(
        self,
        session: AsyncSession,
        *,
        receipt_sha256: Optional[str],
        tx_ref: Optional[str],
        exclude_invoice_id: Any = None,
    ) -> Optional[Tuple[str, str]]:
        """
        Returns (order_id, order_number) of a prior invoice sharing hash or tx_ref,
        or None if clean.
        """
        clauses = []
        sha = (receipt_sha256 or "").strip()
        tx = normalize_tx_ref(tx_ref)
        if sha:
            clauses.append(
                or_(
                    PaymentInvoice.receipt_sha256 == sha,
                    PaymentInvoice.receipt_file_sha256 == sha,
                )
            )
        if tx and len(tx) >= 4:
            clauses.append(PaymentInvoice.sham_cash_tx_ref == tx)
        if not clauses:
            return None

        stmt = (
            select(PaymentInvoice)
            .where(or_(*clauses))
            .options(selectinload(PaymentInvoice.order))
            .order_by(PaymentInvoice.created_at.asc())
        )
        if exclude_invoice_id:
            stmt = stmt.where(PaymentInvoice.id != _uid(exclude_invoice_id))

        rows = (await session.execute(stmt)).scalars().all()
        for inv in rows:
            if inv.order and inv.order.order_number:
                return str(inv.order_id), inv.order.order_number
        return None

    def fraud_banner(self, old_order_number: Optional[str]) -> Optional[str]:
        if not old_order_number:
            return None
        return (
            f"🚨 تحذير احتيال: هذا الإيصال/رقم الحوالة استُخدم سابقاً في الطلب "
            f"#{old_order_number}!"
        )

    async def list_pending(self, session: AsyncSession) -> List[Dict[str, Any]]:
        stmt = (
            select(PaymentInvoice)
            .where(
                and_(
                    PaymentInvoice.payment_method == PaymentMethod.SHAM_CASH,
                    PaymentInvoice.status == InvoiceStatus.PENDING,
                    PaymentInvoice.sham_cash_receipt_photo_file_id.isnot(None),
                )
            )
            .options(
                selectinload(PaymentInvoice.customer),
                selectinload(PaymentInvoice.order).selectinload(Order.master_product),
                selectinload(PaymentInvoice.order).selectinload(Order.supplier),
            )
            .order_by(PaymentInvoice.created_at.asc())
        )
        invoices = (await session.execute(stmt)).scalars().all()
        out = []
        for inv in invoices:
            order = inv.order
            cust = inv.customer
            rtype = (inv.receipt_type or "photo").lower()
            if rtype not in ("photo", "pdf"):
                rtype = "photo"
            dup_num = inv.duplicate_of_order_number
            if not dup_num:
                # Live re-check against other invoices
                found = await self.find_duplicate_receipt(
                    session,
                    receipt_sha256=inv.receipt_file_sha256 or inv.receipt_sha256,
                    tx_ref=inv.sham_cash_tx_ref,
                    exclude_invoice_id=inv.id,
                )
                if found:
                    dup_num = found[1]
            out.append({
                "invoice_id": str(inv.id),
                "order_id": str(inv.order_id),
                "order_number": order.order_number if order else None,
                "product_title": (
                    order.master_product.title_ar
                    if order and order.master_product else None
                ),
                "quantity": int(order.quantity or 1) if order else 1,
                "customer_telegram_id": cust.telegram_id if cust else None,
                "customer_username": cust.telegram_username if cust else None,
                "customer_name": (
                    f"{cust.first_name or ''} {cust.last_name or ''}".strip()
                    if cust else ""
                ),
                "amount_expected": float(inv.amount_expected),
                "tx_ref": inv.sham_cash_tx_ref,
                "receipt_file_id": inv.sham_cash_receipt_photo_file_id,
                "receipt_type": rtype,
                "receipt_file_sha256": inv.receipt_file_sha256 or inv.receipt_sha256,
                "receipt_sha256": inv.receipt_sha256 or inv.receipt_file_sha256,
                "fraud_warning": self.fraud_banner(dup_num),
                "duplicate_of_order_number": dup_num,
                "claimed_by_telegram_id": order.claimed_by_telegram_id if order else None,
                "claimed_by_username": order.claimed_by_username if order else None,
                # ADMIN/STAFF ONLY — never render on customer surfaces
                "supplier_name": order.supplier.name if order and order.supplier else None,
                "pdf_forensics": inv.pdf_forensics_json,
                "created_at": inv.created_at.isoformat() if inv.created_at else None,
            })
        return out

    async def list_recent_orders(self, session: AsyncSession, limit: int = 15) -> List[Dict[str, Any]]:
        stmt = (
            select(Order)
            .options(
                selectinload(Order.customer),
                selectinload(Order.master_product),
                selectinload(Order.supplier),
            )
            .order_by(desc(Order.created_at))
            .limit(limit)
        )
        orders = (await session.execute(stmt)).scalars().all()
        return [
            {
                "id": str(o.id),
                "order_number": o.order_number,
                "state": o.state.value,
                "our_price": float(o.our_price),
                "product_title": o.master_product.title_ar if o.master_product else None,
                "supplier_name": o.supplier.name if o.supplier else None,
                "customer_tg": o.customer.telegram_id if o.customer else None,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ]

    async def approve(
        self,
        session: AsyncSession,
        order_id: Any,
        reviewer: str,
        admin_user_id: Optional[Any] = None,
        trigger_jit: bool = True,
    ) -> Dict[str, Any]:
        """⚡ Confirm payment and trigger API/JIT (or local stock) fulfillment."""
        stmt = (
            select(Order)
            .where(Order.id == _uid(order_id))
            .options(selectinload(Order.invoice), selectinload(Order.customer))
            .with_for_update()
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")

        old_state = order.state.value if order.state else None
        invoice = order.invoice
        if invoice:
            invoice.status = InvoiceStatus.CONFIRMED
            invoice.confirmed_at = datetime.now(timezone.utc)
            invoice.sham_cash_reviewed_by = reviewer
            invoice.sham_cash_reviewed_at = datetime.now(timezone.utc)

        await order_service.transition_order_state(
            session, order, OrderState.PAYMENT_CONFIRMED,
            f"admin_approved_sham_cash by {reviewer}",
        )

        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="sham_cash_approve_api",
            entity_type="order",
            entity_id=order.id,
            old_values={"state": old_state},
            new_values=merge_audit_new_values(reviewer, {"state": OrderState.PAYMENT_CONFIRMED.value, "mode": "api"}),
        ))
        await session.commit()

        if order.customer and order.customer.telegram_id:
            await event_bus.publish("bot:send_message", {
                "telegram_id": order.customer.telegram_id,
                "text": (
                    "✅ تم تأكيد الدفع — جاري تأمين وتسليم منتجك الآن...\n\n"
                    f"🔖 رقم الطلب: <code>{order.order_number}</code>"
                ),
                "effect_id": "5046509860389126442",  # celebration confetti
            })

        if trigger_jit:
            await enqueue_jit_order(str(order.id))

        return {
            "status": "ok",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "state": OrderState.PAYMENT_CONFIRMED.value,
            "mode": "api",
            "message": "Payment approved. JIT funding & fulfillment initiated.",
        }

    async def approve_manual_delivery(
        self,
        session: AsyncSession,
        order_id: Any,
        *,
        delivered_payload: str,
        reviewer: str,
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """✍️ Confirm payment and deliver pasted account/key (no supplier API)."""
        from src.core.services.jit_executor import jit_executor

        stmt = (
            select(Order)
            .where(Order.id == _uid(order_id))
            .options(
                selectinload(Order.invoice),
                selectinload(Order.customer),
                selectinload(Order.master_product),
            )
            .with_for_update()
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")
        payload = (delivered_payload or "").strip()
        if not payload:
            raise ValueError("Empty delivery payload")

        already_paid = order.state in (
            OrderState.PENDING_MANUAL_DELIVERY,
            OrderState.PAYMENT_CONFIRMED,
            OrderState.FAILED_MANUAL_REVIEW,
            OrderState.QUOTE_LOCKED,
            OrderState.FALLBACK_ROUTING,
        )

        invoice = order.invoice
        if invoice and invoice.status != InvoiceStatus.CONFIRMED:
            invoice.status = InvoiceStatus.CONFIRMED
            invoice.confirmed_at = datetime.now(timezone.utc)
            invoice.sham_cash_reviewed_by = reviewer
            invoice.sham_cash_reviewed_at = datetime.now(timezone.utc)

        if not already_paid or order.state == OrderState.SHAM_CASH_REVIEW:
            await order_service.transition_order_state(
                session, order, OrderState.PAYMENT_CONFIRMED,
                f"admin_manual_approve by {reviewer}",
            )
        # Zero supplier cost for manual local paste
        if order.supplier_cost is None:
            order.supplier_cost = 0
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="manual_deliver",
            entity_type="order",
            entity_id=order.id,
            old_values={"state": order.state.value if order.state else None},
            new_values=merge_audit_new_values(reviewer, {"mode": "manual_paste"}),
        ))
        await session.commit()

        # Re-load with relationships for fulfillment
        order = (await session.execute(
            select(Order)
            .where(Order.id == order.id)
            .options(
                selectinload(Order.customer),
                selectinload(Order.master_product),
                selectinload(Order.supplier),
                selectinload(Order.invoice),
            )
        )).scalar_one()
        await jit_executor.complete_order_fulfillment(session, order, payload)
        await session.commit()

        return {
            "status": "ok",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "mode": "manual",
            "message": "Product delivered manually.",
        }

    async def reject(
        self,
        session: AsyncSession,
        order_id: Any,
        reviewer: str,
        reason: str = "Receipt invalid or payment not received",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        stmt = (
            select(Order)
            .where(Order.id == _uid(order_id))
            .options(selectinload(Order.invoice), selectinload(Order.customer))
            .with_for_update()
        )
        order = (await session.execute(stmt)).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")

        old_state = order.state.value if order.state else None
        invoice = order.invoice
        if invoice:
            invoice.status = InvoiceStatus.REJECTED
            invoice.sham_cash_reviewed_by = reviewer

        await order_service.transition_order_state(
            session, order, OrderState.REJECTED, f"sham_cash_rejected: {reason}",
        )

        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="sham_cash_reject",
            entity_type="order",
            entity_id=order.id,
            old_values={"state": old_state},
            new_values=merge_audit_new_values(reviewer, {
                "state": OrderState.REJECTED.value,
                "reason": reason,
            }),
        ))
        await session.commit()

        if order.customer and order.customer.telegram_id:
            await event_bus.publish("bot:send_message", {
                "telegram_id": order.customer.telegram_id,
                "text": (
                    f"❌ تم رفض العملية\n\n"
                    f"🔖 الطلب: `{order.order_number}`\n"
                    f"📝 السبب: {reason}"
                ),
            })

        return {
            "status": "ok",
            "order_id": str(order.id),
            "order_number": order.order_number,
            "state": OrderState.REJECTED.value,
            "reason": reason,
            "message": "Payment rejected.",
        }


sham_cash_service = ShamCashService()
