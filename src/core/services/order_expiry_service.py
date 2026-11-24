"""Expire unpaid / timed-out payment invoices and cancel their orders safely."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.core.models.entities import Order, PaymentInvoice, ProductSupplierLink, Customer
from src.core.models.enums import InvoiceStatus, OrderState, WalletTxType, PaymentMethod
from src.core.redis import event_bus
from src.core.services.order_service import order_service


# Unpaid Sham Cash without receipt past this age is treated as abandoned
_SHAM_ABANDON_MINUTES_DEFAULT = 60

_EXPIREABLE_STATES = (
    OrderState.CREATED,
    OrderState.AWAITING_PAYMENT,
    OrderState.SHAM_CASH_REVIEW,
)


class OrderExpiryService:
    async def sweep_expired(self, session: AsyncSession, *, limit: int = 40) -> Dict[str, Any]:
        """
        Cancel unpaid orders whose invoice is past expires_at, or Sham Cash
        awaiting receipt past the abandon TTL. Uses FOR UPDATE SKIP LOCKED.
        """
        now = datetime.now(timezone.utc)
        sham_ttl = int(
            getattr(settings, "SHAM_CASH_ABANDON_MINUTES", None) or _SHAM_ABANDON_MINUTES_DEFAULT
        )
        sham_cutoff = now - timedelta(minutes=sham_ttl)

        # Candidate invoices (pending + expired clock OR sham abandon)
        stmt = (
            select(PaymentInvoice)
            .where(
                PaymentInvoice.status == InvoiceStatus.PENDING,
                or_(
                    and_(
                        PaymentInvoice.expires_at.isnot(None),
                        PaymentInvoice.expires_at < now,
                    ),
                    and_(
                        PaymentInvoice.payment_method == PaymentMethod.SHAM_CASH,
                        PaymentInvoice.created_at < sham_cutoff,
                        or_(
                            PaymentInvoice.sham_cash_receipt_photo_file_id.is_(None),
                            PaymentInvoice.sham_cash_receipt_photo_file_id == "",
                        ),
                    ),
                ),
            )
            .options(
                selectinload(PaymentInvoice.order).selectinload(Order.customer),
                selectinload(PaymentInvoice.customer),
            )
            .order_by(PaymentInvoice.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )

        try:
            invoices = (await session.execute(stmt)).scalars().all()
        except Exception as e:
            # SQLite / dialects without SKIP LOCKED — degrade gracefully
            logger.debug(f"SKIP LOCKED unsupported ({e}); falling back to plain lock/no-lock")
            stmt_fb = (
                select(PaymentInvoice)
                .where(
                    PaymentInvoice.status == InvoiceStatus.PENDING,
                    or_(
                        and_(
                            PaymentInvoice.expires_at.isnot(None),
                            PaymentInvoice.expires_at < now,
                        ),
                        and_(
                            PaymentInvoice.payment_method == PaymentMethod.SHAM_CASH,
                            PaymentInvoice.created_at < sham_cutoff,
                            or_(
                                PaymentInvoice.sham_cash_receipt_photo_file_id.is_(None),
                                PaymentInvoice.sham_cash_receipt_photo_file_id == "",
                            ),
                        ),
                    ),
                )
                .options(
                    selectinload(PaymentInvoice.order).selectinload(Order.customer),
                    selectinload(PaymentInvoice.customer),
                )
                .order_by(PaymentInvoice.created_at.asc())
                .limit(limit)
            )
            try:
                invoices = (await session.execute(stmt_fb.with_for_update())).scalars().all()
            except Exception:
                invoices = (await session.execute(stmt_fb)).scalars().all()

        expired: List[str] = []
        for inv in invoices:
            try:
                result = await self._expire_one(session, inv, now=now)
                if result:
                    expired.append(result)
            except Exception as e:
                logger.error(f"Failed to expire invoice {inv.id}: {e}")
                await session.rollback()

        if expired:
            await session.commit()
        return {"expired_count": len(expired), "order_ids": expired}

    async def expire_invoice(
        self,
        session: AsyncSession,
        invoice: PaymentInvoice,
        *,
        reason: str = "invoice_expired",
    ) -> Optional[str]:
        """Public helper for tests / manual expiry of a single invoice row (caller commits)."""
        return await self._expire_one(session, invoice, now=datetime.now(timezone.utc), reason=reason)

    async def _expire_one(
        self,
        session: AsyncSession,
        inv: PaymentInvoice,
        *,
        now: datetime,
        reason: str = "invoice_expired",
    ) -> Optional[str]:
        if inv.status != InvoiceStatus.PENDING:
            return None

        order: Optional[Order] = inv.order
        # Never expire invoices tied to already-paid / fulfilled orders
        if order is not None and order.state not in _EXPIREABLE_STATES:
            return None

        refunded = Decimal("0")
        order_id_str: Optional[str] = None

        if order is not None:
            # Lock order row
            locked = (
                await session.execute(
                    select(Order)
                    .where(Order.id == order.id)
                    .options(selectinload(Order.customer))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not locked:
                inv.status = InvoiceStatus.EXPIRED
                return None
            order = locked
            order_id_str = str(order.id)

            # Release supplier stock reservation (best-effort when link exists)
            await self._release_stock(session, order)

            reserved = Decimal(str(order.wallet_portion_reserved or 0))
            if reserved > 0:
                await order_service.credit_customer_wallet(
                    session=session,
                    customer_id=str(order.customer_id),
                    amount=reserved,
                    tx_type=WalletTxType.SPLIT_PAYMENT_REFUND,
                    description=f"إرجاع رصيد محجوز بعد انتهاء صلاحية الطلب #{order.order_number}",
                    order_id=str(order.id),
                )
                refunded = reserved
                order.wallet_portion_reserved = Decimal("0.0")

            inv.status = InvoiceStatus.EXPIRED
            await order_service.transition_order_state(
                session, order, OrderState.CANCELLED, reason
            )

            customer = order.customer
            if customer and customer.telegram_id:
                await self._notify_customer(
                    customer,
                    order_number=order.order_number,
                    refunded=refunded,
                )
            return order_id_str

        # Standalone top-up invoice (no order)
        inv.status = InvoiceStatus.EXPIRED
        cust = inv.customer
        if cust and cust.telegram_id:
            lang = (cust.language_code or "ar").lower()
            text = (
                "⏰ Your unpaid top-up invoice expired. You can request a new top-up anytime."
                if lang.startswith("en")
                else "⏰ انتهت صلاحية فاتورة الشحن غير المدفوعة. يمكنك طلب شحن جديد في أي وقت."
            )
            await event_bus.publish("bot:send_message", {
                "telegram_id": cust.telegram_id,
                "text": text,
            })
        return str(inv.id)

    async def _release_stock(self, session: AsyncSession, order: Order) -> None:
        if not order.supplier_id or not order.supplier_product_id:
            return
        link = (
            await session.execute(
                select(ProductSupplierLink).where(
                    ProductSupplierLink.supplier_id == order.supplier_id,
                    ProductSupplierLink.supplier_product_id == order.supplier_product_id,
                    ProductSupplierLink.master_product_id == order.master_product_id,
                )
            )
        ).scalar_one_or_none()
        if not link:
            return
        if link.available_quantity is None:
            return
        qty = int(order.quantity or 1)
        await order_service.release_supplier_stock(session, link.id, qty)

    async def _notify_customer(
        self,
        customer: Customer,
        *,
        order_number: str,
        refunded: Decimal,
    ) -> None:
        lang = (customer.language_code or "ar").lower()
        if lang.startswith("en"):
            text = (
                f"⏰ Order `{order_number}` expired because payment was not completed in time.\n"
                f"The order was cancelled."
            )
            if refunded > 0:
                text += f"\n💰 ${refunded:.2f} held from your wallet was refunded."
        else:
            text = (
                f"⏰ انتهت صلاحية الطلب `{order_number}` لعدم إتمام الدفع في الوقت المحدد.\n"
                f"تم إلغاء الطلب تلقائياً."
            )
            if refunded > 0:
                text += f"\n💰 تم إرجاع `${refunded:.2f}` المحجوزة إلى محفظتك."
        try:
            await event_bus.publish("bot:send_message", {
                "telegram_id": customer.telegram_id,
                "text": text,
            })
        except Exception as e:
            logger.warning(f"Expire notify failed for {customer.telegram_id}: {e}")


order_expiry_service = OrderExpiryService()
