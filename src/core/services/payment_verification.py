"""
Shared crypto/Binance payment confirmation used by BSC/TRON/Binance listeners
and Admin Bot manual fallback — single source of truth (no duplicated business logic).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from loguru import logger

from src.core.models.entities import PaymentInvoice, Order
from src.core.models.enums import InvoiceStatus, OrderState, PaymentMethod, WalletTxType
from src.core.services.order_service import order_service
from src.core.services.jit_queue import enqueue_jit_order
from src.core.redis import event_bus


class PaymentVerificationService:
    async def find_invoices(
        self,
        session: AsyncSession,
        methods: List[PaymentMethod],
        hours: int = 24,
    ) -> List[PaymentInvoice]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        stmt = (
            select(PaymentInvoice)
            .where(
                and_(
                    PaymentInvoice.payment_method.in_(methods),
                    PaymentInvoice.status.in_([InvoiceStatus.PENDING, InvoiceStatus.EXPIRED]),
                    PaymentInvoice.created_at >= cutoff,
                )
            )
            .options(
                selectinload(PaymentInvoice.order).selectinload(Order.master_product),
                selectinload(PaymentInvoice.customer),
            )
        )
        return list((await session.execute(stmt)).scalars().all())

    async def is_tx_already_processed(self, session: AsyncSession, tx_ref: str) -> bool:
        if not tx_ref:
            return False
        existing = (await session.execute(
            select(PaymentInvoice).where(PaymentInvoice.tx_hash == tx_ref)
        )).scalar_one_or_none()
        return existing is not None

    def match_exact(self, invoices: List[PaymentInvoice], amount: Decimal, tol: Decimal = Decimal("0.0005")):
        for inv in invoices:
            if inv.unique_amount and abs(inv.unique_amount - amount) < tol:
                return inv
        return None

    def match_underpay(self, invoices: List[PaymentInvoice], amount: Decimal):
        for inv in invoices:
            if inv.status == InvoiceStatus.PENDING and inv.unique_amount:
                diff = inv.unique_amount - amount
                if Decimal("0.0005") <= diff <= Decimal("2.00"):
                    return inv
        return None

    def match_overpay(self, invoices: List[PaymentInvoice], amount: Decimal):
        for inv in invoices:
            if inv.status == InvoiceStatus.PENDING and inv.unique_amount:
                excess = amount - inv.unique_amount
                if Decimal("0.05") <= excess <= Decimal("100.00"):
                    return inv
        return None

    async def confirm_exact_payment(
        self,
        session: AsyncSession,
        invoice: PaymentInvoice,
        *,
        amount: Decimal,
        tx_ref: str,
        block_number: Optional[int] = None,
        trigger_source: str,
        sync_wallet_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Confirm PENDING invoice → PAYMENT_CONFIRMED + enqueue JIT."""
        now = datetime.now(timezone.utc)
        order = invoice.order
        invoice.status = InvoiceStatus.CONFIRMED
        invoice.amount_received = amount
        invoice.tx_hash = tx_ref
        if block_number is not None:
            invoice.block_number = block_number
        invoice.confirmed_at = now

        await order_service.transition_order_state(
            session, order, OrderState.PAYMENT_CONFIRMED, trigger_source
        )
        await session.commit()

        if sync_wallet_address:
            from src.core.services.hot_wallet_sync import bump_hot_wallet_usdt
            await bump_hot_wallet_usdt(sync_wallet_address, amount)

        await enqueue_jit_order(str(order.id))

        if invoice.customer and invoice.customer.telegram_id:
            await event_bus.publish("bot:send_message", {
                "telegram_id": invoice.customer.telegram_id,
                "text": (
                    f"⚡ تم تأكيد الدفع — جاري تأمين وتسليم منتجك الآن...\n\n"
                    f"🔖 رقم الطلب: `{order.order_number}`\n"
                    f"💰 المبلغ: `${amount}`"
                ),
            })

        return {"status": "confirmed", "order_id": str(order.id), "order_number": order.order_number}

    async def handle_late_payment(
        self,
        session: AsyncSession,
        invoice: PaymentInvoice,
        *,
        amount: Decimal,
        tx_ref: str,
        block_number: Optional[int] = None,
        sync_wallet_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        customer = invoice.customer
        order = invoice.order
        invoice.amount_received = amount
        invoice.tx_hash = tx_ref
        if block_number is not None:
            invoice.block_number = block_number
        invoice.confirmed_at = datetime.now(timezone.utc)

        await order_service.credit_customer_wallet(
            session=session,
            customer_id=str(customer.id),
            amount=amount,
            tx_type=WalletTxType.LATE_PAYMENT_CREDITED,
            description=f"شحن تلقائي - وصول تحويل متأخر للطلب {order.order_number}",
            order_id=str(order.id),
        )
        await session.commit()

        if sync_wallet_address:
            from src.core.services.hot_wallet_sync import bump_hot_wallet_usdt
            await bump_hot_wallet_usdt(sync_wallet_address, amount)

        await event_bus.publish("bot:send_message", {
            "telegram_id": customer.telegram_id,
            "text": (
                f"⚠️ **إشعار وصول دفعة متأخرة:**\n\n"
                f"وصلنا مبلغ `${amount:.2f} USDT` للطلب `#{order.order_number}` بعد انتهاء صلاحية الفاتورة.\n"
                f"🛡 تم إيداع المبلغ بالكامل في **محفظتك داخل البوت**."
            ),
        })
        return {"status": "late_credited", "order_id": str(order.id)}

    async def handle_underpayment(
        self,
        session: AsyncSession,
        invoice: PaymentInvoice,
        *,
        amount: Decimal,
        tx_ref: str,
        block_number: Optional[int] = None,
        sync_wallet_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        customer = invoice.customer
        order = invoice.order
        shortfall = invoice.unique_amount - amount
        invoice.status = InvoiceStatus.PARTIAL
        invoice.amount_received = amount
        invoice.tx_hash = tx_ref
        if block_number is not None:
            invoice.block_number = block_number

        await order_service.credit_customer_wallet(
            session=session,
            customer_id=str(customer.id),
            amount=amount,
            tx_type=WalletTxType.PARTIAL_PAYMENT_CREDITED,
            description=f"إيداع دفعة جزئية للطلب #{order.order_number}",
            order_id=str(order.id),
        )
        await session.commit()

        if sync_wallet_address:
            from src.core.services.hot_wallet_sync import bump_hot_wallet_usdt
            await bump_hot_wallet_usdt(sync_wallet_address, amount)

        await event_bus.publish("bot:send_message", {
            "telegram_id": customer.telegram_id,
            "text": (
                f"⚠️ **تنبيه: استلام دفعة جزئية ناقصة!**\n\n"
                f"وصل: `${amount:.2f}` · المطلوب: `${invoice.unique_amount:.4f}` · "
                f"المتبقي: `${shortfall:.2f}`\n"
                f"✅ تمت إضافة المبلغ المستلم إلى محفظتك."
            ),
        })
        return {"status": "partial", "shortfall": float(shortfall)}

    async def handle_overpayment(
        self,
        session: AsyncSession,
        invoice: PaymentInvoice,
        *,
        amount: Decimal,
        tx_ref: str,
        block_number: Optional[int] = None,
        trigger_source: str,
        sync_wallet_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        customer = invoice.customer
        order = invoice.order
        excess = amount - invoice.unique_amount
        now = datetime.now(timezone.utc)
        invoice.status = InvoiceStatus.CONFIRMED
        invoice.amount_received = amount
        invoice.tx_hash = tx_ref
        if block_number is not None:
            invoice.block_number = block_number
        invoice.confirmed_at = now

        await order_service.credit_customer_wallet(
            session=session,
            customer_id=str(customer.id),
            amount=excess,
            tx_type=WalletTxType.OVERPAYMENT_CREDITED,
            description=f"إيداع المبلغ الزائد عن الطلب #{order.order_number}",
            order_id=str(order.id),
        )
        await order_service.transition_order_state(
            session, order, OrderState.PAYMENT_CONFIRMED, trigger_source
        )
        await session.commit()

        if sync_wallet_address:
            from src.core.services.hot_wallet_sync import bump_hot_wallet_usdt
            await bump_hot_wallet_usdt(sync_wallet_address, amount)

        await enqueue_jit_order(str(order.id))
        await event_bus.publish("bot:send_message", {
            "telegram_id": customer.telegram_id,
            "text": (
                f"🎉 **تم تأكيد طلبك `#{order.order_number}` وجاري تسليمه آلياً!**\n\n"
                f"💰 المبلغ الزائد `${excess:.2f}` أُضيف إلى محفظتك."
            ),
        })
        return {"status": "confirmed_overpay", "excess": float(excess)}

    async def process_inbound_transfer(
        self,
        session: AsyncSession,
        *,
        methods: List[PaymentMethod],
        amount: Decimal,
        tx_ref: str,
        block_number: Optional[int] = None,
        trigger_prefix: str,
        sync_wallet_address: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Full exact/late/under/over/unmatched pipeline for one inbound transfer."""
        if await self.is_tx_already_processed(session, tx_ref):
            return {"status": "duplicate", "tx_ref": tx_ref}

        invoices = await self.find_invoices(session, methods)
        matched = self.match_exact(invoices, amount)
        if matched:
            if matched.status == InvoiceStatus.PENDING:
                return await self.confirm_exact_payment(
                    session, matched, amount=amount, tx_ref=tx_ref,
                    block_number=block_number,
                    trigger_source=f"{trigger_prefix}.confirmed_{tx_ref[:12]}",
                    sync_wallet_address=sync_wallet_address,
                )
            if matched.status == InvoiceStatus.EXPIRED:
                return await self.handle_late_payment(
                    session, matched, amount=amount, tx_ref=tx_ref,
                    block_number=block_number, sync_wallet_address=sync_wallet_address,
                )

        under = self.match_underpay(invoices, amount)
        if under:
            return await self.handle_underpayment(
                session, under, amount=amount, tx_ref=tx_ref,
                block_number=block_number, sync_wallet_address=sync_wallet_address,
            )

        over = self.match_overpay(invoices, amount)
        if over:
            return await self.handle_overpayment(
                session, over, amount=amount, tx_ref=tx_ref,
                block_number=block_number,
                trigger_source=f"{trigger_prefix}.overpaid_{tx_ref[:12]}",
                sync_wallet_address=sync_wallet_address,
            )

        logger.warning(f"Unmatched inbound {amount} tx={tx_ref}")
        await event_bus.publish_alert(
            title="Unmatched Crypto Payment",
            message=f"Received ${amount} with no matching invoice. Ref: {tx_ref}",
            level="warning",
            meta={"tx_ref": tx_ref, "amount": float(amount), "methods": [m.value for m in methods]},
        )
        return {"status": "unmatched", "tx_ref": tx_ref, "amount": float(amount)}

    async def confirm_binance_by_pay_order_id(
        self,
        session: AsyncSession,
        *,
        pay_order_id: str,
        amount: Optional[Decimal] = None,
        order_id: Optional[Any] = None,
        reviewer: str = "binance_auto",
    ) -> Dict[str, Any]:
        """
        Confirm a BINANCE_PAY invoice by Pay Order ID (auto matcher or Admin fallback).
        When order_id is provided, targets that invoice; else matches pending by unique_amount.
        """
        if await self.is_tx_already_processed(session, pay_order_id):
            return {"status": "duplicate", "tx_ref": pay_order_id}

        invoice = None
        if order_id:
            import uuid
            oid = order_id if isinstance(order_id, uuid.UUID) else uuid.UUID(str(order_id))
            invoice = (await session.execute(
                select(PaymentInvoice)
                .where(PaymentInvoice.order_id == oid)
                .options(
                    selectinload(PaymentInvoice.order),
                    selectinload(PaymentInvoice.customer),
                )
            )).scalar_one_or_none()
        elif amount is not None:
            invoices = await self.find_invoices(session, [PaymentMethod.BINANCE_PAY])
            invoice = self.match_exact(invoices, amount)

        if not invoice or not invoice.order:
            raise ValueError("No matching Binance Pay invoice found")

        if invoice.status != InvoiceStatus.PENDING:
            raise ValueError(f"Invoice not pending (state={invoice.status})")

        received = amount if amount is not None else (invoice.unique_amount or invoice.amount_expected)
        return await self.confirm_exact_payment(
            session, invoice,
            amount=Decimal(str(received)),
            tx_ref=str(pay_order_id),
            trigger_source=f"binance_pay.confirmed_by_{reviewer}",
        )


payment_verification_service = PaymentVerificationService()
