"""
Supplier health monitor + retention + abandoned-checkout reminders + low-balance alerts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import select, delete, and_
from sqlalchemy.orm import selectinload
from loguru import logger

from src.config import settings
from src.core.database import AsyncSessionLocal
from src.core.models.entities import (
    Supplier, SupplierHealthLog, AuditLog, PaymentInvoice, Order, Customer,
)
from src.core.models.enums import SupplierStatus, InvoiceStatus, OrderState
from src.core.adapters.registry import AdapterRegistry
from src.core.redis import event_bus


class SupplierHealthMonitor:
    def __init__(self):
        self._loop_count = 0

    async def run(self):
        logger.info("Health monitor started (30s): suppliers, low-balance, checkout reminders.")
        while True:
            try:
                self._loop_count += 1
                await self.check_all_suppliers()
                await self.send_abandoned_checkout_reminders()
                # ~ every 1h (120 * 30s): renewal reminders
                if self._loop_count % 120 == 0:
                    await self.send_subscription_renewal_reminders()
                if self._loop_count % 2880 == 0:
                    await self.run_retention_cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitor cycle: {e}")
            await asyncio.sleep(30)

    async def check_all_suppliers(self):
        redis = event_bus._redis
        threshold = Decimal(str(getattr(settings, "SUPPLIER_BALANCE_ALERT_USD", 15) or 15))
        async with AsyncSessionLocal() as session:
            stmt = select(Supplier).where(
                Supplier.status.in_([
                    SupplierStatus.ACTIVE,
                    SupplierStatus.CIRCUIT_OPEN,
                    SupplierStatus.HALF_OPEN,
                ])
            )
            suppliers = (await session.execute(stmt)).scalars().all()

            for s in suppliers:
                if s.status == SupplierStatus.CIRCUIT_OPEN:
                    cooldown = timedelta(seconds=s.cb_cooldown_seconds)
                    last = s.cb_last_failure_at
                    if last and last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if last and (datetime.now(timezone.utc) - last) >= cooldown:
                        s.status = SupplierStatus.HALF_OPEN

                adapter = AdapterRegistry.create_from_supplier(s)
                health = await adapter.check_health()

                if redis:
                    redis_key = f"metrics:latency:{s.id}"
                    pipe = redis.pipeline()
                    pipe.lpush(redis_key, health.latency_ms)
                    pipe.ltrim(redis_key, 0, 99)
                    pipe.set(f"metrics:alive:{s.id}", "1" if health.is_alive else "0", ex=90)
                    await pipe.execute()

                should_log_to_db = (not health.is_alive) or (self._loop_count % 10 == 0)
                if should_log_to_db:
                    session.add(SupplierHealthLog(
                        supplier_id=s.id,
                        check_type="auto_poll",
                        is_success=health.is_alive,
                        response_time_ms=health.latency_ms,
                        http_status=health.http_status,
                        error_message=health.error_message,
                    ))

                if health.is_alive:
                    if s.status == SupplierStatus.HALF_OPEN:
                        s.status = SupplierStatus.ACTIVE
                    s.cb_consecutive_failures = 0
                    if health.balance_usd is not None:
                        s.cached_balance = health.balance_usd
                        s.cached_balance_at = datetime.now(timezone.utc)
                        await self._maybe_alert_low_supplier_balance(
                            redis, s, Decimal(str(health.balance_usd)), threshold
                        )
                else:
                    s.cb_consecutive_failures += 1
                    s.cb_last_failure_at = datetime.now(timezone.utc)
                    if (
                        s.cb_consecutive_failures >= s.cb_failure_threshold
                        and s.status != SupplierStatus.CIRCUIT_OPEN
                    ):
                        s.status = SupplierStatus.CIRCUIT_OPEN
                        await event_bus.publish_alert(
                            title="Supplier Circuit Breaker Open",
                            message=(
                                f"Supplier {s.name} failed {s.cb_consecutive_failures} "
                                "consecutive checks. Temporarily disabled."
                            ),
                            level="danger",
                            meta={"supplier_id": str(s.id)},
                        )

            await session.commit()

    async def _maybe_alert_low_supplier_balance(
        self, redis, supplier: Supplier, balance: Decimal, threshold: Decimal
    ):
        if balance >= threshold:
            return
        # Debounce: at most once per 6 hours per supplier
        if redis:
            key = f"alert:supplier_low_bal:{supplier.id}"
            if await redis.get(key):
                return
            await redis.set(key, "1", ex=6 * 3600)

        await event_bus.publish_alert(
            title="Low Supplier API Balance",
            message=(
                f"رصيد مورد {supplier.name} انخفض إلى ${balance:.2f} "
                f"(الحد ${threshold:.2f}). قد يتوقف التوريد الفوري قريباً."
            ),
            level="warning",
            meta={
                "alert_type": "low_balance",
                "kind": "supplier",
                "supplier_id": str(supplier.id),
                "supplier_name": supplier.name,
                "balance": float(balance),
                "threshold": float(threshold),
            },
        )

    async def send_subscription_renewal_reminders(self):
        """Phase 16 — one Arabic renewal DM for subscriptions expiring in ~3 days."""
        try:
            from src.core.services.renewal_reminder_service import renewal_reminder_service
            async with AsyncSessionLocal() as session:
                n = await renewal_reminder_service.send_due_reminders(session)
                if n:
                    logger.info(f"Sent {n} subscription renewal reminders")
        except Exception as e:
            logger.warning(f"Renewal reminder pass failed: {e}")

    async def send_abandoned_checkout_reminders(self):
        """ONE Arabic reminder at CHECKOUT_REMINDER_MINUTES with pay/cancel buttons."""
        reminder_after = int(getattr(settings, "CHECKOUT_REMINDER_MINUTES", 15) or 15)
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=reminder_after + 2)
        cutoff = now - timedelta(minutes=reminder_after)

        async with AsyncSessionLocal() as session:
            invoices = (await session.execute(
                select(PaymentInvoice)
                .where(
                    and_(
                        PaymentInvoice.status == InvoiceStatus.PENDING,
                        PaymentInvoice.reminder_sent == False,  # noqa: E712
                        PaymentInvoice.created_at <= cutoff,
                        PaymentInvoice.created_at >= window_start,
                        PaymentInvoice.expires_at > now,
                    )
                )
                .options(
                    selectinload(PaymentInvoice.customer),
                    selectinload(PaymentInvoice.order).selectinload(Order.master_product),
                )
                .limit(40)
            )).scalars().all()

            for inv in invoices:
                if not inv.customer or not inv.customer.telegram_id:
                    inv.reminder_sent = True
                    continue
                if inv.order and inv.order.state not in (
                    OrderState.CREATED, OrderState.AWAITING_PAYMENT, OrderState.SHAM_CASH_REVIEW,
                ):
                    inv.reminder_sent = True
                    continue

                order = inv.order
                product = order.master_product.title_ar if order and order.master_product else "طلبك"
                order_id = str(order.id) if order else ""
                amount = inv.unique_amount or inv.amount_expected
                text = (
                    f"⏳ **تذكير لطيف:** فاتورتك لا تزال بانتظار الدفع\n\n"
                    f"📦 {product}\n"
                    f"💵 المبلغ: `{amount}` USDT\n"
                    f"🔖 `{order.order_number if order else '—'}`\n\n"
                    f"أمامك وقت لإكمال الدفع قبل انتهاء صلاحية الفاتورة."
                )
                await event_bus.publish("bot:checkout_reminder", {
                    "telegram_id": inv.customer.telegram_id,
                    "text": text,
                    "order_id": order_id,
                    "invoice_id": str(inv.id),
                })
                inv.reminder_sent = True
                logger.info(f"Checkout reminder sent for invoice {inv.id}")

            await session.commit()

    async def run_retention_cleanup(self):
        logger.info("Executing automated database retention cleanup...")
        async with AsyncSessionLocal() as session:
            try:
                cutoff_14d = datetime.now(timezone.utc) - timedelta(days=14)
                hl_del = await session.execute(
                    delete(SupplierHealthLog).where(SupplierHealthLog.created_at < cutoff_14d)
                )
                cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)
                aud_del = await session.execute(
                    delete(AuditLog).where(AuditLog.created_at < cutoff_30d)
                )
                cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)
                inv_del = await session.execute(
                    delete(PaymentInvoice).where(
                        (PaymentInvoice.status == InvoiceStatus.EXPIRED)
                        & (PaymentInvoice.created_at < cutoff_7d)
                    )
                )
                await session.commit()
                logger.info(
                    f"Retention: {hl_del.rowcount} health, {aud_del.rowcount} audit, "
                    f"{inv_del.rowcount} invoices pruned."
                )
            except Exception as e:
                logger.error(f"Error during retention cleanup: {e}")


if __name__ == "__main__":
    asyncio.run(SupplierHealthMonitor().run())
