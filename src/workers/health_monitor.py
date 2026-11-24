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
                # ~ every 60s (every other 30s tick): expire unpaid + live countdown + escalations
                if self._loop_count % 2 == 0:
                    await self.sweep_expired_orders()
                    await self.update_manual_delivery_countdowns()
                    await self.check_manual_delivery_escalations()
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

    async def sweep_expired_orders(self):
        """Cancel unpaid / timed-out invoices; refund wallet holds; release stock."""
        try:
            from src.core.services.order_expiry_service import order_expiry_service
            async with AsyncSessionLocal() as session:
                result = await order_expiry_service.sweep_expired(session)
                n = int(result.get("expired_count") or 0)
                if n:
                    logger.info(f"Order expiry sweeper: expired {n} unpaid order(s)")
        except Exception as e:
            logger.error(f"Order expiry sweeper failed: {e}")

    async def update_manual_delivery_countdowns(self):
        """Every ~60s: edit customer live countdown cards; finalize on deliver/cancel."""
        from src.core.services.manual_delivery_status import (
            format_countdown_message,
            format_terminal_message,
            get_manual_sla_minutes,
            remaining_seconds,
        )
        try:
            async with AsyncSessionLocal() as session:
                sla_min = await get_manual_sla_minutes(session)
                now = datetime.now(timezone.utc)
                # Active pending with a status message
                pending = (
                    await session.execute(
                        select(Order)
                        .where(
                            Order.state == OrderState.PENDING_MANUAL_DELIVERY,
                            Order.status_message_id.is_not(None),
                            Order.status_countdown_closed.is_(False),
                        )
                        .options(selectinload(Order.customer))
                        .limit(80)
                    )
                ).scalars().all()
                for order in pending:
                    chat_id = order.status_chat_id or (
                        order.customer.telegram_id if order.customer else None
                    )
                    if not chat_id or not order.status_message_id:
                        continue
                    lang = "ar"
                    if order.customer and order.customer.language_code:
                        lang = str(order.customer.language_code).lower()
                    deadline = order.manual_deadline_at or (
                        (order.updated_at or now) + timedelta(minutes=sla_min)
                    )
                    rem = remaining_seconds(deadline, now)
                    if rem <= 0:
                        if order.status_overdue_notified:
                            continue
                        text = format_countdown_message(
                            order_number=order.order_number,
                            lang=lang,
                            deadline=deadline,
                            sla_minutes=sla_min,
                            now=now,
                            overdue=True,
                        )
                        order.status_overdue_notified = True
                        await event_bus.publish("bot:edit_message", {
                            "order_id": str(order.id),
                            "chat_id": int(chat_id),
                            "message_id": int(order.status_message_id),
                            "text": text,
                            "finalize": False,
                        })
                    else:
                        text = format_countdown_message(
                            order_number=order.order_number,
                            lang=lang,
                            deadline=deadline,
                            sla_minutes=sla_min,
                            now=now,
                        )
                        await event_bus.publish("bot:edit_message", {
                            "order_id": str(order.id),
                            "chat_id": int(chat_id),
                            "message_id": int(order.status_message_id),
                            "text": text,
                            "finalize": False,
                        })

                # Terminal states still holding an open countdown card
                terminal_states = (
                    OrderState.DELIVERED,
                    OrderState.COMPLETED,
                    OrderState.ORDER_FULFILLED,
                    OrderState.CANCELLED,
                    OrderState.REJECTED,
                    OrderState.FAILED_REFUNDED,
                )
                closed = (
                    await session.execute(
                        select(Order)
                        .where(
                            Order.state.in_(terminal_states),
                            Order.status_message_id.is_not(None),
                            Order.status_countdown_closed.is_(False),
                        )
                        .options(selectinload(Order.customer))
                        .limit(40)
                    )
                ).scalars().all()
                for order in closed:
                    chat_id = order.status_chat_id or (
                        order.customer.telegram_id if order.customer else None
                    )
                    if not chat_id or not order.status_message_id:
                        order.status_countdown_closed = True
                        continue
                    lang = "ar"
                    if order.customer and order.customer.language_code:
                        lang = str(order.customer.language_code).lower()
                    delivered = order.state in (
                        OrderState.DELIVERED,
                        OrderState.COMPLETED,
                        OrderState.ORDER_FULFILLED,
                    )
                    await event_bus.publish("bot:edit_message", {
                        "order_id": str(order.id),
                        "chat_id": int(chat_id),
                        "message_id": int(order.status_message_id),
                        "text": format_terminal_message(lang=lang, delivered=delivered),
                        "finalize": True,
                    })
                    order.status_countdown_closed = True

                await session.commit()
        except Exception as e:
            logger.error(f"Manual delivery countdown update failed: {e}")

    async def check_manual_delivery_escalations(self):
        """
        Escalating Admin/Assistant Telegram pings for PENDING_MANUAL_DELIVERY:
          T+30 half reminder · T+50 / T+60+ urgent every 10 minutes.
        T+0 is sent immediately from jit_executor._route_to_manual_delivery.
        """
        from src.core.services.manual_delivery_status import (
            escalation_stage,
            format_escalation_caption,
            get_manual_sla_minutes,
        )
        try:
            async with AsyncSessionLocal() as session:
                sla_min = await get_manual_sla_minutes(session)
                now = datetime.now(timezone.utc)
                rows = (
                    await session.execute(
                        select(Order)
                        .where(Order.state == OrderState.PENDING_MANUAL_DELIVERY)
                        .options(selectinload(Order.master_product), selectinload(Order.customer))
                        .limit(40)
                    )
                ).scalars().all()
                if not rows:
                    return
                redis = event_bus._redis
                for order in rows:
                    started = order.updated_at or order.created_at or now
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    # Prefer deadline-derived start when available
                    if order.manual_deadline_at:
                        dl = order.manual_deadline_at
                        if dl.tzinfo is None:
                            dl = dl.replace(tzinfo=timezone.utc)
                        started = dl - timedelta(minutes=sla_min)
                    elapsed_min = (now - started).total_seconds() / 60.0
                    stage = escalation_stage(elapsed_min)
                    if not stage:
                        continue

                    title = order.master_product.title_ar if order.master_product else "—"
                    if stage == "half":
                        throttle_key = f"esc:manual:{order.id}:half"
                        ttl = 3600 * 6
                        if redis is not None:
                            try:
                                if await redis.get(throttle_key):
                                    continue
                                await redis.set(throttle_key, "1", ex=ttl)
                            except Exception:
                                pass
                        caption = format_escalation_caption(
                            stage="half",
                            order_number=order.order_number,
                            product_title=title,
                            elapsed_minutes=int(elapsed_min),
                        )
                    else:
                        # Urgent: first at 50m, then every 10 minutes
                        throttle_key = f"esc:manual:{order.id}:urgent"
                        if redis is not None:
                            try:
                                if await redis.get(throttle_key):
                                    continue
                                await redis.set(throttle_key, "1", ex=600)
                            except Exception:
                                pass
                        caption = format_escalation_caption(
                            stage="urgent",
                            order_number=order.order_number,
                            product_title=title,
                            elapsed_minutes=int(elapsed_min),
                        )

                    await event_bus.publish("admin:manual_review", {
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "review_kind": "manual_delivery",
                        "caption": caption,
                        "disable_notification": False,
                        "escalation_stage": stage,
                    })
                    await event_bus.publish_alert(
                        title="Manual Delivery Escalation",
                        message=caption,
                        level="danger" if stage == "urgent" else "warning",
                        meta={
                            "order_id": str(order.id),
                            "alert_type": "manual_delivery_escalation",
                            "stage": stage,
                        },
                    )
                logger.debug(f"Manual delivery escalation pass: scanned {len(rows)} order(s)")
        except Exception as e:
            logger.error(f"Manual delivery escalation check failed: {e}")

    # Back-compat alias used by older tests / callers
    async def check_manual_delivery_sla(self):
        await self.check_manual_delivery_escalations()

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
