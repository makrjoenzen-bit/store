import random
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple
from sqlalchemy import select, and_, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.entities import (
    Order, PaymentInvoice, Customer, MasterProduct,
    OrderStateLog, WalletTransaction, HotWallet,
    FormalInvoice, InvoiceSequenceCounter, ProductSupplierLink
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod, WalletTxType
)
from src.core.redis import event_bus
from src.config import settings


class OrderService:
    """Manages order creation, payment invoices, state transitions, and ledger adjustments."""

    @staticmethod
    def generate_order_number() -> str:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_suffix = f"{random.randint(1000, 9999)}"
        return f"ORD-{date_str}-{rand_suffix}"

    @staticmethod
    def generate_unique_invoice_amount(base_amount: Decimal) -> Decimal:
        """
        Adds a random 4-decimal fraction to uniquely identify the customer's transfer.
        Prefer generate_unique_invoice_amount_safe() under concurrency.
        Expanded range: 0.0001 .. 0.0999 (vs legacy 0.0101 .. 0.0999).
        """
        unique_fraction = Decimal(str(random.randint(1, 999))) / Decimal("10000")
        unique_total = base_amount + unique_fraction
        return unique_total.quantize(Decimal("0.0001"))

    async def generate_unique_invoice_amount_safe(
        self,
        session: AsyncSession,
        base_amount: Decimal,
        payment_method: PaymentMethod,
        *,
        max_attempts: int = 20,
    ) -> Decimal:
        """Retry until unique_amount is free among currently PENDING invoices for this rail."""
        from src.core.models.entities import PaymentInvoice

        for _ in range(max_attempts):
            candidate = self.generate_unique_invoice_amount(base_amount)
            clash = (
                await session.execute(
                    select(PaymentInvoice.id).where(
                        and_(
                            PaymentInvoice.status == InvoiceStatus.PENDING,
                            PaymentInvoice.payment_method == payment_method,
                            PaymentInvoice.unique_amount == candidate,
                        )
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if clash is None:
                return candidate
        # Last resort: uuid-derived 4dp fraction
        import uuid as _uuid
        noise = Decimal(str((int(_uuid.uuid4().int % 999) + 1))) / Decimal("10000")
        return (base_amount + noise).quantize(Decimal("0.0001"))

    async def create_order(
        self,
        session: AsyncSession,
        customer_id: Any,
        master_product_id: Any,
        supplier_id: Optional[Any],
        supplier_product_id: Optional[str],
        supplier_cost: Decimal,
        our_price: Decimal,
        profit_margin: Decimal,
        payment_method: PaymentMethod,
        quantity: int = 1,
        buyer_email: Optional[str] = None,
        buyer_activation_id: Optional[str] = None,
    ) -> Tuple[Order, PaymentInvoice]:
        """Creates order and associated payment invoice."""
        if isinstance(customer_id, str):
            customer_id = uuid.UUID(customer_id)
        if isinstance(master_product_id, str):
            master_product_id = uuid.UUID(master_product_id)
        if isinstance(supplier_id, str):
            supplier_id = uuid.UUID(supplier_id)

        order_id = uuid.uuid4()
        idempotency_key = f"ord-{order_id.hex}"
        order_number = self.generate_order_number()

        order = Order(
            id=order_id,
            order_number=order_number,
            customer_id=customer_id,
            master_product_id=master_product_id,
            supplier_id=supplier_id,
            supplier_product_id=supplier_product_id,
            supplier_cost=supplier_cost,
            our_price=our_price,
            profit_margin=profit_margin,
            quantity=quantity,
            idempotency_key=idempotency_key,
            state=OrderState.CREATED,
            buyer_email=(buyer_email or "").strip() or None,
            buyer_activation_id=(buyer_activation_id or "").strip() or None,
        )
        session.add(order)

        # Build Invoice — unique micro-amount for auto-match on crypto / Binance rails
        unique_amount = None
        deposit_addr = None
        if payment_method == PaymentMethod.USDT_BEP20_DIRECT:
            unique_amount = await self.generate_unique_invoice_amount_safe(
                session, our_price, payment_method
            )
            deposit_addr = settings.HOT_WALLET_ADDRESS
        elif payment_method == PaymentMethod.USDT_TRC20_DIRECT:
            unique_amount = await self.generate_unique_invoice_amount_safe(
                session, our_price, payment_method
            )
            deposit_addr = settings.TRON_HOT_WALLET_ADDRESS or None
        elif payment_method == PaymentMethod.BINANCE_PAY:
            unique_amount = await self.generate_unique_invoice_amount_safe(
                session, our_price, payment_method
            )
            deposit_addr = (settings.BINANCE_PAY_MERCHANT_ID or "").strip() or None

        invoice = PaymentInvoice(
            customer_id=customer_id,
            order_id=order_id,
            payment_method=payment_method,
            amount_expected=our_price,
            unique_amount=unique_amount,
            deposit_address=deposit_addr,
            status=InvoiceStatus.PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.INVOICE_TIMEOUT_MINUTES),
        )
        session.add(invoice)
        await session.flush()

        order.invoice_id = invoice.id

        # Transition order to awaiting_payment
        await self.transition_order_state(
            session=session,
            order=order,
            new_state=OrderState.AWAITING_PAYMENT,
            trigger_source="order_service.create_order",
            details={"invoice_id": str(invoice.id), "method": payment_method.value}
        )

        await session.commit()
        return order, invoice

    async def transition_order_state(
        self,
        session: AsyncSession,
        order: Order,
        new_state: OrderState,
        trigger_source: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """Atomically transitions order state and appends to immutable order_state_log."""
        old_state = order.state
        order.state = new_state
        order.updated_at = datetime.now(timezone.utc)
        if new_state == OrderState.COMPLETED:
            order.completed_at = datetime.now(timezone.utc)

        log_entry = OrderStateLog(
            order_id=order.id,
            from_state=old_state,
            to_state=new_state,
            trigger_source=trigger_source,
            details=details or {},
        )
        session.add(log_entry)
        await session.flush()

        # Publish state change on Redis event bus
        await event_bus.publish_order_state(
            order_id=str(order.id),
            new_state=new_state.value,
            details={
                "order_number": order.order_number,
                "old_state": old_state.value if old_state else None,
                "trigger": trigger_source,
                "details": details or {},
            }
        )

    async def _acquire_wallet_lock(self, customer_id: uuid.UUID) -> Optional[str]:
        """
        Serializes EVERY wallet mutation across all entry points (Mini App checkout,
        Telegram Bot, Reseller API, Admin Refund, JIT cashback/refund listeners).
        Retries briefly, then fails closed rather than proceeding unlocked.
        """
        import asyncio
        from src.core.redis import event_bus

        if not event_bus.redis:
            return None
        lock_key = f"lock:wallet:{str(customer_id)}"
        token = await event_bus.acquire_lock_with_token(lock_key, ttl_seconds=15)
        if token is None:
            for _ in range(30):
                await asyncio.sleep(0.1)
                token = await event_bus.acquire_lock_with_token(lock_key, ttl_seconds=15)
                if token is not None:
                    break
        if token is None:
            raise ValueError("Wallet is currently locked by another operation. Please retry.")
        return token

    async def _fresh_wallet_snapshot(
        self,
        session: AsyncSession,
        customer_id: uuid.UUID
    ) -> Customer:
        """
        Re-reads the customer row from the DB *inside* the distributed lock and under a
        SELECT ... FOR UPDATE row lock, discarding any stale identity-map snapshot held
        by the caller's session. This makes the balance used for the debit/credit exact.
        """
        cust_stmt = select(Customer).where(Customer.id == customer_id).with_for_update()
        res = await session.execute(cust_stmt.execution_options(populate_existing=True))
        return res.scalar_one()

    async def credit_customer_wallet(
        self,
        session: AsyncSession,
        customer_id: Any,
        amount: Decimal,
        tx_type: WalletTxType,
        description: str,
        order_id: Optional[Any] = None,
        auto_lock: bool = True
    ) -> WalletTransaction:
        """Atomically credits customer balance with distributed Redis lock and appends to immutable ledger."""
        from src.core.redis import event_bus

        if isinstance(customer_id, str):
            customer_id = uuid.UUID(customer_id)
        if isinstance(order_id, str):
            order_id = uuid.UUID(order_id)

        lock_token = await self._acquire_wallet_lock(customer_id) if auto_lock else None
        try:
            # Atomic increment: never rely on a stale ORM snapshot for a credit.
            stmt = (
                update(Customer)
                .where(Customer.id == customer_id)
                .values(wallet_balance=Customer.wallet_balance + amount)
                .returning(Customer.wallet_balance)
            )
            balance_after = (await session.execute(stmt)).scalar_one_or_none()
            if balance_after is None:
                raise ValueError(f"Customer {customer_id} not found")
            await self._fresh_wallet_snapshot(session, customer_id)

            tx = WalletTransaction(
                customer_id=customer_id,
                tx_type=tx_type,
                amount=amount,
                balance_after=balance_after,
                order_id=order_id,
                description=description,
            )
            session.add(tx)
            await session.flush()

            # Live Proof channel: wallet top-ups only (never leak supplier/wholesale)
            if tx_type == WalletTxType.CUSTOMER_DEPOSIT:
                try:
                    from src.core.services.proof_service import publish_deposit_proof
                    cust = (await session.execute(
                        select(Customer).where(Customer.id == customer_id)
                    )).scalar_one_or_none()
                    await publish_deposit_proof({
                        "tx_ref": str(tx.id),
                        "customer_username": getattr(cust, "telegram_username", None),
                        "customer_first_name": getattr(cust, "first_name", None),
                        "amount_usd": float(amount),
                        "payment_method": "Wallet Top-Up",
                        "method": "Wallet Top-Up",
                    })
                except Exception:
                    pass
            return tx
        finally:
            if lock_token and event_bus.redis:
                await event_bus.release_lock(f"lock:wallet:{str(customer_id)}", token=lock_token)

    async def debit_customer_wallet(
        self,
        session: AsyncSession,
        customer_id: Any,
        amount: Decimal,
        tx_type: WalletTxType,
        description: str,
        order_id: Optional[Any] = None,
        auto_lock: bool = True
    ) -> WalletTransaction:
        """Atomically debits customer balance with distributed Redis lock and insufficient funds check."""
        from src.core.redis import event_bus

        if isinstance(customer_id, str):
            customer_id = uuid.UUID(customer_id)
        if isinstance(order_id, str):
            order_id = uuid.UUID(order_id)

        lock_token = await self._acquire_wallet_lock(customer_id) if auto_lock else None
        try:
            # Guarded atomic UPDATE: the balance check and the write happen in ONE
            # statement, so a stale ORM snapshot (or a lock released just before the
            # caller commits) can never produce a negative balance / double spend.
            stmt = (
                update(Customer)
                .where(Customer.id == customer_id, Customer.wallet_balance >= amount)
                .values(wallet_balance=Customer.wallet_balance - amount)
                .returning(Customer.wallet_balance)
            )
            balance_after = (await session.execute(stmt)).scalar_one_or_none()
            if balance_after is None:
                existing = (await session.execute(
                    select(Customer.id).where(Customer.id == customer_id)
                )).scalar_one_or_none()
                if existing is None:
                    raise ValueError(f"Customer {customer_id} not found")
                customer = await self._fresh_wallet_snapshot(session, customer_id)
                raise ValueError(
                    f"Insufficient funds: available {customer.wallet_balance}, requested {amount}"
                )

            # Refresh the caller's ORM instance so subsequent reads see the new balance.
            await self._fresh_wallet_snapshot(session, customer_id)

            tx = WalletTransaction(
                customer_id=customer_id,
                tx_type=tx_type,
                amount=-amount,
                balance_after=balance_after,
                order_id=order_id,
                description=description,
            )
            session.add(tx)
            await session.flush()
            return tx
        finally:
            if lock_token and event_bus.redis:
                await event_bus.release_lock(f"lock:wallet:{str(customer_id)}", token=lock_token)

    async def reserve_supplier_stock(
        self,
        session: AsyncSession,
        link_id: Any,
        quantity: int,
    ) -> bool:
        """
        Atomically decrement available stock. A guarded UPDATE makes concurrent buyers
        compete at the database level: once the last unit is taken, later requests get
        zero rows back instead of all passing a stale Python-side stock check.
        Returns True when the reservation succeeded (or stock is unlimited).
        """
        import uuid as _uuid
        if isinstance(link_id, str):
            link_id = _uuid.UUID(link_id)
        link = (await session.execute(
            select(ProductSupplierLink).where(ProductSupplierLink.id == link_id).with_for_update()
        )).scalar_one_or_none()
        if not link or not link.is_in_stock:
            return False
        if link.available_quantity is None:  # unlimited (∞) stock
            return True
        stmt = (
            update(ProductSupplierLink)
            .where(
                and_(
                    ProductSupplierLink.id == link_id,
                    ProductSupplierLink.available_quantity >= quantity,
                )
            )
            .values(available_quantity=ProductSupplierLink.available_quantity - quantity)
            .returning(ProductSupplierLink.available_quantity)
        )
        return (await session.execute(stmt)).scalar_one_or_none() is not None

    async def release_supplier_stock(
        self, session: AsyncSession, link_id: Any, quantity: int
    ) -> None:
        """Restores a reservation after a failed post-reservation step."""
        import uuid as _uuid
        if isinstance(link_id, str):
            link_id = _uuid.UUID(link_id)
        await session.execute(
            update(ProductSupplierLink)
            .where(
                and_(
                    ProductSupplierLink.id == link_id,
                    ProductSupplierLink.available_quantity.isnot(None),
                )
            )
            .values(available_quantity=ProductSupplierLink.available_quantity + quantity)
        )

    async def generate_formal_invoice(
        self,
        session: AsyncSession,
        order: Order,
        payment_method: PaymentMethod,
        tx_ref: Optional[str] = None,
    ) -> FormalInvoice:
        """
        Generates a sequential formal invoice (INV-YYYY-NNNNN) on order completion.
        Also sets warranty_expires_at on the order based on product.warranty_days.
        """
        year = datetime.now(timezone.utc).year

        # Atomic sequence counter
        seq_stmt = select(InvoiceSequenceCounter).where(
            InvoiceSequenceCounter.year == year
        ).with_for_update()
        seq_row = (await session.execute(seq_stmt)).scalar_one_or_none()

        if seq_row:
            seq_row.last_sequence += 1
            seq_num = seq_row.last_sequence
        else:
            new_seq = InvoiceSequenceCounter(year=year, last_sequence=1)
            session.add(new_seq)
            seq_num = 1

        invoice_number = f"INV-{year}-{seq_num:05d}"

        # Set warranty expiration. Read the product explicitly when the caller did not
        # eager-load the relationship (otherwise async lazy loading raises MissingGreenlet).
        from sqlalchemy import inspect as sa_inspect
        if "master_product" in sa_inspect(order).unloaded:
            product = await session.get(MasterProduct, order.master_product_id)
        else:
            product = order.master_product
        warranty_days = getattr(product, "warranty_days", 30) or 30
        warranty_dt = datetime.now(timezone.utc) + timedelta(days=warranty_days)
        order.warranty_expires_at = warranty_dt

        c_id = order.customer_id if isinstance(order.customer_id, uuid.UUID) else uuid.UUID(str(order.customer_id))
        o_id = order.id if isinstance(order.id, uuid.UUID) else uuid.UUID(str(order.id))

        formal_inv = FormalInvoice(
            invoice_number=invoice_number,
            customer_id=c_id,
            order_id=o_id,
            item_title=product.title_ar if product else "منتج رقمي",
            quantity=order.quantity or 1,
            unit_price=order.our_price / max(order.quantity or 1, 1),
            discount=Decimal("0.0"),
            net_total=order.our_price,
            payment_method=payment_method,
            tx_id_or_ref=tx_ref,
            warranty_expires_at=warranty_dt,
        )
        session.add(formal_inv)
        await session.flush()
        return formal_inv


order_service = OrderService()
