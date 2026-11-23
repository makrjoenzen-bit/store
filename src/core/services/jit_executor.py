import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, List
from sqlalchemy import select, and_, update
from sqlalchemy.orm import selectinload
from loguru import logger

from src.core.database import AsyncSessionLocal
from src.core.models.entities import (
    Order, Supplier, MasterProduct, JitTransaction,
    SupplierCredit, Customer, ProductSupplierLink, HotWallet
)
from src.core.models.enums import (
    OrderState, JitTxState, PaymentNetwork, WalletTxType, SupplierStatus,
    PaymentMethod
)
from src.core.adapters.registry import AdapterRegistry
from src.core.services.order_service import order_service
from src.core.redis import event_bus
from src.outgest.bsc_sender import bsc_sender
from src.outgest.binance_pay_sender import binance_pay_sender
from src.outgest.tron_sender import tron_sender
from src.config import settings

# Keep settle/recovery waits minimal — supplier credit webhooks & lookups are usually instant.
JIT_FUNDING_SETTLE_SECONDS = float(getattr(settings, "JIT_FUNDING_SETTLE_SECONDS", 0.35))
JIT_RECOVERY_LOOKUP_SECONDS = float(getattr(settings, "JIT_RECOVERY_LOOKUP_SECONDS", 0.5))


class JitExecutor:
    """
    Zero-Capital Just-In-Time execution engine.
    Orchestrates real-time supplier quotes, dynamic on-chain/off-chain funding,
    idempotent order placement, stranded deposit tracking, and automatic customer delivery/refund.
    """

    async def execute_order(self, order_id: str):
        """Main entry point invoked by ARQ worker or immediate payment confirmation."""
        lock_key = f"lock:jit:{order_id}"
        # Ownership-token lock: a stale holder whose TTL expired must never delete
        # the lock now owned by a second execution (which would allow double funding).
        lock_token = await event_bus.acquire_lock_with_token(lock_key, ttl_seconds=300)
        if not lock_token:
            logger.warning(f"JIT execution already locked for order {order_id}. Skipping.")
            return

        try:
            import uuid
            order_uuid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
            async with AsyncSessionLocal() as session:
                stmt = (
                    select(Order)
                    .where(Order.id == order_uuid)
                    .options(
                        selectinload(Order.supplier),
                        selectinload(Order.master_product),
                        selectinload(Order.customer),
                        selectinload(Order.invoice)
                    )
                    .with_for_update()
                )
                res = await session.execute(stmt)
                order = res.scalar_one_or_none()

                if not order:
                    logger.error(f"Order {order_id} not found for JIT execution.")
                    return

                if order.state not in (OrderState.PAYMENT_CONFIRMED, OrderState.QUOTE_LOCKED, OrderState.FALLBACK_ROUTING):
                    logger.info(f"Order {order.order_number} state is {order.state}, not ready for JIT funding.")
                    return

                await self._process_jit_cycle(session, order)
        except Exception as e:
            logger.exception(f"Unhandled error in JIT Executor for order {order_id}: {e}")
            await event_bus.publish_alert(
                title="JIT Execution Error",
                message=f"Order {order_id} failed with error: {str(e)}",
                level="danger",
                meta={"order_id": order_id}
            )
        finally:
            await event_bus.release_lock(lock_key, token=lock_token)

    async def _process_jit_cycle(self, session, order: Order):
        # -------------------------------------------------------------
        # STEP 0: Local Stock Pool (local_first / local_only / no-supplier)
        # -------------------------------------------------------------
        from src.core.services.local_stock_service import local_stock_service
        mode = "api_first"
        if order.master_product:
            mode = (getattr(order.master_product, "fulfillment_mode", None) or "api_first").lower()
        qty = order.quantity or 1

        async def _try_local() -> bool:
            payload = await local_stock_service.pop_for_order(
                session,
                product_id=order.master_product_id,
                quantity=qty,
                order_id=order.id,
            )
            if not payload:
                return False
            order.supplier_cost = Decimal("0.0")
            order.gas_fee_actual = Decimal("0.0")
            await session.commit()
            await self.complete_order_fulfillment(session, order, payload)
            await session.commit()
            logger.info(f"Order {order.order_number} fulfilled from local stock pool")
            return True

        try:
            if mode == "local_only":
                if await _try_local():
                    return
                await self._handle_supplier_exhausted(
                    session, order, "Local stock exhausted (local_only mode)."
                )
                return
            if mode == "local_first":
                if await _try_local():
                    return
            if not order.supplier_id:
                if await _try_local():
                    return
        except Exception as e:
            logger.warning(f"Local stock check failed for {order.order_number}: {e}")

        supplier = order.supplier
        if not supplier:
            logger.error(f"Order {order.order_number} has no assigned supplier.")
            await self._handle_supplier_exhausted(session, order, "No supplier assigned.")
            return

        adapter = AdapterRegistry.create_from_supplier(supplier)

        # -------------------------------------------------------------
        # STEP 1: Pre-flight Quote Re-verification
        # -------------------------------------------------------------
        try:
            target_qty = order.quantity or 1
            quote = await adapter.get_quote(order.supplier_product_id, quantity=target_qty)
            if not quote.is_available:
                logger.warning(f"Supplier {supplier.name} out of stock for {order.supplier_product_id} (qty: {target_qty})")
                await self._attempt_fallback_supplier(session, order, "Supplier reported out of stock during pre-flight quote.")
                return

            # Price surge guard: check if cost increased significantly
            if quote.cost > (order.supplier_cost or Decimal("0.0")) + Decimal("0.50"):
                logger.warning(f"Price surged on {supplier.name}: was {order.supplier_cost}, now {quote.cost}")
                await self._attempt_fallback_supplier(session, order, f"Wholesale cost surged to ${quote.cost}")
                return

            order.supplier_cost = quote.cost
            order.supplier_quote_id = quote.quote_id
            await order_service.transition_order_state(
                session, order, OrderState.QUOTE_LOCKED, "jit_executor.preflight_quote_verified"
            )
            await session.commit()
        except Exception as e:
            logger.error(f"Error checking quote with {supplier.name}: {e}")
            await self._attempt_fallback_supplier(session, order, f"Quote API error: {str(e)}")
            return

        # -------------------------------------------------------------
        # STEP 2: Check for Existing Stranded Credit at Supplier
        # -------------------------------------------------------------
        # If we already have unused funds at this supplier from a prior failed order, use it!
        credit_stmt = select(SupplierCredit).where(
            and_(
                SupplierCredit.supplier_id == supplier.id,
                SupplierCredit.is_consumed == False,
                SupplierCredit.amount >= order.supplier_cost
            )
        ).with_for_update()
        credit_res = await session.execute(credit_stmt)
        available_credit = credit_res.scalars().first()

        deposit_confirmed = False
        if available_credit:
            logger.info(f"Using stranded credit {available_credit.id} (${available_credit.amount}) for order {order.order_number}")
            available_credit.is_consumed = True
            available_credit.consumed_by_order_id = order.id
            available_credit.consumed_at = datetime.now(timezone.utc)
            deposit_confirmed = True
            await order_service.transition_order_state(
                session, order, OrderState.JIT_FUNDING_CONFIRMED, "jit_executor.reused_stranded_credit"
            )
            await session.commit()

        # -------------------------------------------------------------
        # STEP 3: Outbound JIT Funding (If no stranded credit)
        # -------------------------------------------------------------
        if not deposit_confirmed:
            funding_ok = await self._execute_outbound_funding(session, order, supplier, adapter)
            if not funding_ok:
                await self._attempt_fallback_supplier(session, order, "Outbound funding to supplier failed.")
                return

        # -------------------------------------------------------------
        # STEP 4: Execute Order Purchase on Supplier API
        # -------------------------------------------------------------
        await order_service.transition_order_state(
            session, order, OrderState.SUPPLIER_ORDER_PLACED, "jit_executor.placing_supplier_order"
        )
        await session.commit()

        place_kwargs: Dict[str, Any] = {}
        if getattr(order, "buyer_email", None):
            place_kwargs["buyer_email"] = order.buyer_email
        if getattr(order, "buyer_activation_id", None):
            place_kwargs["activation_identifier"] = order.buyer_activation_id
        place_kwargs["selling_price"] = float(order.our_price)
        if order.supplier_cost:
            place_kwargs["max_total"] = float(
                (order.supplier_cost * Decimal("1.05")).quantize(Decimal("0.01"))
            )
        if order.master_product and getattr(order.master_product, "requires_customer_email", False):
            place_kwargs["product_metadata"] = {"source": "chatgpt_business"}

        order_res = await adapter.place_order(
            supplier_product_id=order.supplier_product_id,
            quantity=order.quantity or 1,
            idempotency_key=order.idempotency_key,
            **place_kwargs,
        )

        # Handle Timeout -> Recovery Lookup
        if order_res.status == "pending" or (not order_res.is_success and not order_res.error_message):
            logger.warning(f"Supplier call timed out for order {order.order_number}. Initiating recovery lookup.")
            await order_service.transition_order_state(
                session, order, OrderState.SUPPLIER_ORDER_RECOVERY, "jit_executor.timeout_recovery"
            )
            await session.commit()
            await asyncio.sleep(JIT_RECOVERY_LOOKUP_SECONDS)
            order_res = await adapter.lookup_order(order.idempotency_key)

        # -------------------------------------------------------------
        # STEP 5: Async pending delivery OR Instant Delivery OR Error
        # -------------------------------------------------------------
        from src.core.adapters.base import STATUS_PENDING_DELIVERY, STATUS_COMPLETED

        if order_res and order_res.is_success and order_res.status == STATUS_PENDING_DELIVERY:
            order.supplier_order_id = order_res.supplier_order_id
            await order_service.transition_order_state(
                session, order, OrderState.PENDING_SUPPLIER_DELIVERY,
                "jit_executor.pending_supplier_delivery",
            )
            await session.commit()
            if order.customer and order.customer.telegram_id:
                await event_bus.publish("bot:send_message", {
                    "telegram_id": order.customer.telegram_id,
                    "text": (
                        "⏳ تم تأكيد الدفع وإرسال طلبك للمورد — "
                        "سيتم تسليمك المنتج تلقائياً فور تجهيزه"
                    ),
                })
            await event_bus.publish_alert(
                title="طلب بانتظار تسليم المورد",
                message=(
                    f"Order {order.order_number} → PENDING_SUPPLIER_DELIVERY "
                    f"(ext={order.supplier_order_id})"
                ),
                level="info",
                meta={"order_id": str(order.id), "supplier_order_id": order.supplier_order_id},
            )
            logger.info(f"Order {order.order_number} awaiting supplier delivery poll")
            return

        delivered_key = (order_res.delivered_payload or "").strip() if order_res else ""
        if order_res and order_res.is_success and order_res.status == STATUS_COMPLETED and not delivered_key:
            logger.error(
                f"Supplier {supplier.name} returned empty/invalid key for order {order.order_number}"
            )
            order_res.is_success = False
            order_res.error_message = order_res.error_message or "Supplier returned empty/invalid key"

        if order_res and order_res.is_success and delivered_key:
            order.supplier_order_id = order_res.supplier_order_id
            await self.complete_order_fulfillment(session, order, delivered_key)
        else:
            logger.error(
                f"Supplier order failed after deposit: "
                f"{order_res.error_message if order_res else 'no result'}"
            )
            credit = SupplierCredit(
                supplier_id=supplier.id,
                order_id=order.id,
                jit_tx_id=None,
                amount=order.supplier_cost or Decimal("0.0"),
                notes=(
                    f"Order failed after funding: "
                    f"{order_res.error_message if order_res else 'unknown'}"
                ),
            )
            session.add(credit)
            await session.commit()
            await self._attempt_fallback_supplier(
                session, order,
                f"Supplier order rejected: "
                f"{order_res.error_message if order_res else 'unknown'}",
            )

    async def _execute_outbound_funding(self, session, order: Order, supplier: Supplier, adapter) -> bool:
        """Executes USDT BEP-20 or Binance Pay transfer to supplier deposit address."""
        amount_to_send = order.supplier_cost
        if not amount_to_send or amount_to_send <= Decimal("0.0"):
            return True

        if supplier.payment_network == PaymentNetwork.USDT_BEP20:
            try:
                # 1. Fetch dynamic deposit address
                dep_info = await adapter.get_deposit_info(network="usdt_bep20", amount=amount_to_send)
                deposit_target = dep_info.deposit_address

                if not deposit_target:
                    raise ValueError(f"Supplier {supplier.name} returned empty deposit address.")

                # If supplier requires exact strict decimals (e.g., 1.008629 USDT), use it
                exact_amount = dep_info.exact_amount or amount_to_send

                # --- SUB-PHASE 6.3: PRE-BROADCAST NET PROFIT CIRCUIT BREAKER (BSC) ---
                estimated_gas_usd = await bsc_sender.estimate_transfer_fee_usd()
                projected_profit = order.our_price - exact_amount - estimated_gas_usd
                if projected_profit < Decimal("0.10"):
                    logger.critical(
                        f"Financial Circuit Breaker triggered on Order {order.order_number}: "
                        f"Projected profit ${projected_profit:.2f} < $0.10 floor (gas: ${estimated_gas_usd:.2f}). Aborting JIT broadcast."
                    )
                    await order_service.transition_order_state(
                        session, order, OrderState.FAILED_MANUAL_REVIEW,
                        f"Gas fee spike abort: Projected profit ${projected_profit:.2f} below floor (gas: ${estimated_gas_usd:.2f})"
                    )
                    await session.commit()
                    await self._dispatch_failure_report(session, order, "FAILED_MANUAL_REVIEW")
                    await event_bus.publish_alert(
                        title="Negative Margin Circuit Breaker Triggered (BSC)",
                        message=f"Order {order.order_number} aborted: Gas fee spike (${estimated_gas_usd:.2f}) would cause negative/insufficient margin (projected: ${projected_profit:.2f}).",
                        level="danger",
                        meta={"order_id": str(order.id), "projected_profit": float(projected_profit), "gas_fee": float(estimated_gas_usd)}
                    )
                    return False

                # Liquidity guard: never broadcast a payout the hot wallet cannot cover.
                hw = (await session.execute(select(HotWallet))).scalar_one_or_none()
                if hw is not None and hw.usdt_balance < exact_amount:
                    logger.critical(
                        f"Hot wallet liquidity guard for {order.order_number}: "
                        f"need ${exact_amount}, available ${hw.usdt_balance}."
                    )
                    await order_service.transition_order_state(
                        session, order, OrderState.FAILED_MANUAL_REVIEW,
                        f"insufficient_hot_wallet_balance: need {exact_amount} available {hw.usdt_balance}",
                    )
                    await session.commit()
                    await self._dispatch_failure_report(session, order, "FAILED_MANUAL_REVIEW")
                    await event_bus.publish_alert(
                        title="Hot Wallet Insufficient Liquidity",
                        message=f"Order {order.order_number} needs ${exact_amount}, hot wallet has ${hw.usdt_balance}.",
                        level="danger", meta={"order_id": str(order.id)},
                    )
                    return False

                # Record JIT TX
                jit_tx = JitTransaction(
                    order_id=order.id,
                    supplier_id=supplier.id,
                    payment_network=PaymentNetwork.USDT_BEP20,
                    from_address=settings.HOT_WALLET_ADDRESS,
                    to_address=deposit_target,
                    amount=exact_amount,
                    supplier_deposit_id=dep_info.deposit_id,
                    supplier_deposit_address=deposit_target,
                    state=JitTxState.PENDING,
                )
                session.add(jit_tx)
                await session.flush()

                await order_service.transition_order_state(
                    session, order, OrderState.JIT_FUNDING_INITIATED, "jit_executor.broadcasting_bsc_tx"
                )
                await session.commit()

                # 2. Sign and broadcast on BSC
                tx_hash, nonce, gas_bnb = await bsc_sender.send_usdt_jit(deposit_target, exact_amount)
                jit_tx.tx_hash = tx_hash
                jit_tx.nonce = nonce
                jit_tx.gas_fee = gas_bnb
                jit_tx.state = JitTxState.BROADCASTED
                from src.core.services.gas_oracle import gas_fx_oracle
                bnb_usd = await gas_fx_oracle.get_bnb_usd()
                order.gas_fee_actual = gas_bnb * bnb_usd
                await session.commit()

                # 3. Wait for confirmation receipt
                receipt = await bsc_sender.wait_for_tx_receipt(tx_hash, timeout_seconds=90)
                if receipt.get("status") == 1:
                    jit_tx.state = JitTxState.CONFIRMED
                    jit_tx.confirmed_at = datetime.now(timezone.utc)
                    jit_tx.block_number = receipt.get("blockNumber")
                    await order_service.transition_order_state(
                        session, order, OrderState.JIT_FUNDING_CONFIRMED, "jit_executor.bsc_tx_confirmed"
                    )
                    await session.commit()
                    # Brief settle window for supplier balance credit / webhook.
                    await asyncio.sleep(JIT_FUNDING_SETTLE_SECONDS)
                    return True
                else:
                    jit_tx.state = JitTxState.FAILED
                    jit_tx.error_message = "BSC transaction reverted on-chain"
                    await session.commit()
                    return False
            except Exception as e:
                logger.error(f"Error funding supplier {supplier.name} via BSC: {e}")
                return False

        elif supplier.payment_network == PaymentNetwork.USDT_TRC20:
            try:
                # 1. Fetch dynamic TRC-20 deposit address
                dep_info = await adapter.get_deposit_info(network="usdt_trc20", amount=amount_to_send)
                deposit_target = dep_info.deposit_address

                if not deposit_target:
                    raise ValueError(f"Supplier {supplier.name} returned empty TRC-20 deposit address.")

                exact_amount = dep_info.exact_amount or amount_to_send

                # --- SUB-PHASE 6.3: PRE-BROADCAST NET PROFIT CIRCUIT BREAKER (TRON) ---
                estimated_energy_usd = await tron_sender.estimate_transfer_fee_usd()
                projected_profit = order.our_price - exact_amount - estimated_energy_usd
                if projected_profit < Decimal("0.10"):
                    logger.critical(
                        f"Financial Circuit Breaker triggered on Order {order.order_number} (TRON): "
                        f"Projected profit ${projected_profit:.2f} < $0.10 floor (energy: ${estimated_energy_usd:.2f}). Aborting JIT broadcast."
                    )
                    await order_service.transition_order_state(
                        session, order, OrderState.FAILED_MANUAL_REVIEW,
                        f"TRON fee spike abort: Projected profit ${projected_profit:.2f} below floor (energy: ${estimated_energy_usd:.2f})"
                    )
                    await session.commit()
                    await event_bus.publish_alert(
                        title="Negative Margin Circuit Breaker Triggered (TRON)",
                        message=f"Order {order.order_number} aborted: TRON energy spike (${estimated_energy_usd:.2f}) would eliminate margin (projected: ${projected_profit:.2f}).",
                        level="danger",
                        meta={"order_id": str(order.id), "projected_profit": float(projected_profit), "energy_fee": float(estimated_energy_usd)}
                    )
                    return False

                # Record JIT TX
                jit_tx = JitTransaction(
                    order_id=order.id,
                    supplier_id=supplier.id,
                    payment_network=PaymentNetwork.USDT_TRC20,
                    from_address=settings.TRON_HOT_WALLET_ADDRESS,
                    to_address=deposit_target,
                    amount=exact_amount,
                    supplier_deposit_id=dep_info.deposit_id,
                    supplier_deposit_address=deposit_target,
                    state=JitTxState.PENDING,
                )
                session.add(jit_tx)
                await session.flush()

                await order_service.transition_order_state(
                    session, order, OrderState.JIT_FUNDING_INITIATED, "jit_executor.broadcasting_trc20_tx"
                )
                await session.commit()

                # 2. Sign and broadcast on TRON
                tx_hash, trx_fee = await tron_sender.send_usdt_jit(deposit_target, exact_amount)
                jit_tx.tx_hash = tx_hash
                jit_tx.gas_fee = trx_fee
                jit_tx.state = JitTxState.BROADCASTED
                from src.core.services.gas_oracle import gas_fx_oracle
                trx_usd = await gas_fx_oracle.get_trx_usd()
                order.gas_fee_actual = trx_fee * trx_usd
                await session.commit()

                # 3. Wait for confirmation
                receipt = await tron_sender.wait_for_tx_confirmation(tx_hash, timeout_seconds=90)
                if receipt.get("status") == 1:
                    jit_tx.state = JitTxState.CONFIRMED
                    jit_tx.confirmed_at = datetime.now(timezone.utc)
                    jit_tx.block_number = receipt.get("blockNumber")
                    await order_service.transition_order_state(
                        session, order, OrderState.JIT_FUNDING_CONFIRMED, "jit_executor.trc20_tx_confirmed"
                    )
                    await session.commit()
                    await asyncio.sleep(JIT_FUNDING_SETTLE_SECONDS)
                    return True
                else:
                    jit_tx.state = JitTxState.FAILED
                    jit_tx.error_message = "TRC-20 transaction failed or unconfirmed"
                    await session.commit()
                    return False
            except Exception as e:
                logger.error(f"Error funding supplier {supplier.name} via TRC-20: {e}")
                return False

        elif supplier.payment_network == PaymentNetwork.BINANCE_PAY:
            try:
                res = await binance_pay_sender.transfer_to_binance_id(
                    recipient_binance_id=supplier.binance_id,
                    amount_usd=amount_to_send,
                    order_ref=order.order_number
                )
                if res.get("is_success"):
                    await order_service.transition_order_state(
                        session, order, OrderState.JIT_FUNDING_CONFIRMED, "jit_executor.binance_pay_confirmed"
                    )
                    await session.commit()
                    return True
                return False
            except Exception as e:
                logger.error(f"Error funding supplier via Binance Pay: {e}")
                return False

        return False

    async def _attempt_fallback_supplier(self, session, order: Order, reason: str):
        """Attempts to route order to the next cheapest in-stock supplier."""
        order.fallback_attempts += 1
        logger.warning(f"Attempting fallback {order.fallback_attempts}/{order.max_fallback_attempts} for order {order.order_number}. Reason: {reason}")

        if order.fallback_attempts > order.max_fallback_attempts:
            await self._handle_supplier_exhausted(session, order, reason)
            return

        # Find alternative active suppliers linked to this master product (cheapest first)
        from sqlalchemy import or_
        stmt = (
            select(ProductSupplierLink)
            .join(Supplier, ProductSupplierLink.supplier_id == Supplier.id)
            .where(
                and_(
                    ProductSupplierLink.master_product_id == order.master_product_id,
                    ProductSupplierLink.supplier_id != order.supplier_id,
                    ProductSupplierLink.is_in_stock == True,
                    Supplier.status == SupplierStatus.ACTIVE,
                    or_(
                        ProductSupplierLink.available_quantity.is_(None),
                        ProductSupplierLink.available_quantity > 0,
                    ),
                )
            )
            .options(selectinload(ProductSupplierLink.supplier))
            .order_by(ProductSupplierLink.last_known_cost.asc())
        )
        res = await session.execute(stmt)
        next_links = res.scalars().all()

        if not next_links:
            await self._handle_supplier_exhausted(session, order, f"All alternative suppliers exhausted. Last error: {reason}")
            return

        next_link = next_links[0]
        order.supplier_id = next_link.supplier_id
        order.supplier = next_link.supplier
        order.supplier_product_id = next_link.supplier_product_id
        order.supplier_cost = next_link.last_known_cost

        await order_service.transition_order_state(
            session, order, OrderState.FALLBACK_ROUTING, f"fallback_routing to {next_link.supplier.name}"
        )
        await session.commit()

        # Re-fetch order with relationships to prevent greenlet MissingGreenlet error after commit
        refreshed_stmt = (
            select(Order)
            .where(Order.id == order.id)
            .options(
                selectinload(Order.supplier),
                selectinload(Order.master_product),
                selectinload(Order.customer),
                selectinload(Order.invoice)
            )
        )
        order = (await session.execute(refreshed_stmt)).scalar_one()

        # Re-trigger JIT cycle for the fallback supplier
        await self._process_jit_cycle(session, order)

    async def complete_order_fulfillment(self, session, order: Order, delivered_key: str):
        """Shared completion path for instant JIT delivery and async delivery_poller."""
        order.delivered_product = delivered_key
        order.delivered_at = datetime.now(timezone.utc)

        await order_service.transition_order_state(
            session, order, OrderState.ORDER_FULFILLED, "jit_executor.supplier_order_fulfilled"
        )
        await order_service.transition_order_state(
            session, order, OrderState.DELIVERED, "jit_executor.delivered"
        )
        await order_service.transition_order_state(
            session, order, OrderState.COMPLETED, "jit_executor.order_completed"
        )

        actual_profit = (
            order.our_price
            - (order.supplier_cost or Decimal("0.0"))
            - (order.gas_fee_actual or Decimal("0.0"))
        )
        order.profit_margin = actual_profit

        customer_tg_id = order.customer.telegram_id if order.customer else None
        customer_referred_by = order.customer.referred_by if order.customer else None
        if order.customer:
            order.customer.total_orders = (order.customer.total_orders or 0) + 1
            order.customer.total_spent = (order.customer.total_spent or Decimal("0.0")) + order.our_price

        try:
            from src.core.models.entities import BotConfig

            # Per-customer cashback override → VIP tier defaults → global BotConfig
            cashback_percent = 2.0
            if order.customer and getattr(order.customer, "cashback_percent", None) is not None:
                cashback_percent = float(order.customer.cashback_percent)
            else:
                cb_cfg = (
                    await session.execute(select(BotConfig).where(BotConfig.key == "cashback_percent"))
                ).scalar_one_or_none()
                if cb_cfg and cb_cfg.value is not None:
                    cashback_percent = float(cb_cfg.value)
                # VIP tier bump from BotConfig vip_cashback_{tier}
                tier = (order.customer.vip_tier.value if order.customer and order.customer.vip_tier else "STANDARD")
                vip_cfg = (
                    await session.execute(
                        select(BotConfig).where(BotConfig.key == f"vip_cashback_{tier.lower()}")
                    )
                ).scalar_one_or_none()
                if vip_cfg and vip_cfg.value is not None:
                    cashback_percent = float(vip_cfg.value)
            if cashback_percent > 0 and customer_tg_id:
                cashback_amt = (order.our_price * Decimal(str(cashback_percent / 100))).quantize(Decimal("0.01"))
                if cashback_amt > Decimal("0.00"):
                    await order_service.credit_customer_wallet(
                        session=session,
                        customer_id=str(order.customer_id),
                        amount=cashback_amt,
                        tx_type=WalletTxType.CASHBACK,
                        description=f"كاش باك {cashback_percent}% للطلب {order.order_number}",
                        order_id=str(order.id),
                    )
                    await event_bus.publish(
                        "bot:notify_cashback",
                        {
                            "telegram_id": customer_tg_id,
                            "amount": float(cashback_amt),
                            "percent": cashback_percent,
                            "order_number": order.order_number,
                            "message": f"🎁 ربحت ${float(cashback_amt):.2f} كاش باك في محفظتك!",
                        },
                    )

            if customer_referred_by:
                ref_stmt = select(Customer).where(Customer.id == customer_referred_by).with_for_update()
                referrer = (await session.execute(ref_stmt)).scalar_one_or_none()
                if referrer:
                    ref_cfg = (
                        await session.execute(
                            select(BotConfig).where(BotConfig.key == "referral_commission_percent")
                        )
                    ).scalar_one_or_none()
                    ref_percent = float(ref_cfg.value) if ref_cfg and ref_cfg.value is not None else 5.0
                    if ref_percent > 0:
                        comm_amt = (order.our_price * Decimal(str(ref_percent / 100))).quantize(Decimal("0.01"))
                        if comm_amt > Decimal("0.00"):
                            await order_service.credit_customer_wallet(
                                session=session,
                                customer_id=str(referrer.id),
                                amount=comm_amt,
                                tx_type=WalletTxType.REFERRAL_COMMISSION,
                                description=(
                                    f"عمولة إحالة {ref_percent}% من مشتريات العميل "
                                    f"{order.customer.telegram_id if order.customer else 'عميل'} "
                                    f"(طلب {order.order_number})"
                                ),
                                order_id=str(order.id),
                            )
                            referrer.referral_earnings = (
                                (referrer.referral_earnings or Decimal("0.0")) + comm_amt
                            )
                            await event_bus.publish(
                                "bot:notify_referral_commission",
                                {
                                    "telegram_id": referrer.telegram_id,
                                    "amount": float(comm_amt),
                                    "percent": ref_percent,
                                    "order_number": order.order_number,
                                    "referee_name": (
                                        order.customer.first_name
                                        or f"عميل #{str(order.customer.telegram_id)[-4:]}"
                                    ),
                                },
                            )
        except Exception as e:
            logger.error(f"Failed to process cashback/referral for order {order.order_number}: {e}")

        try:
            payment_method_enum = PaymentMethod.USDT_BEP20_DIRECT
            tx_ref_str = None
            if order.invoice:
                payment_method_enum = order.invoice.payment_method or PaymentMethod.USDT_BEP20_DIRECT
                tx_ref_str = order.invoice.tx_hash or order.invoice.sham_cash_tx_ref

            formal_inv = await order_service.generate_formal_invoice(
                session=session,
                order=order,
                payment_method=payment_method_enum,
                tx_ref=tx_ref_str,
            )
            logger.info(f"Generated formal invoice {formal_inv.invoice_number} for order {order.order_number}")
        except Exception as e:
            logger.error(f"Failed to generate formal invoice for {order.order_number}: {e}")

        await session.commit()
        await self._dispatch_telegram_fulfillment(order)

        try:
            payment_meth = "USDT BEP-20"
            if order.invoice and order.invoice.payment_method:
                payment_meth = str(order.invoice.payment_method.value).replace("_", " ").upper()
            from src.core.services.proof_service import publish_purchase_proof
            await publish_purchase_proof({
                "order_number": order.order_number,
                "customer_username": order.customer.telegram_username if order.customer else None,
                "customer_first_name": order.customer.first_name if order.customer else None,
                "product_id": str(order.master_product_id),
                "product_title": order.master_product.title_ar if order.master_product else "",
                "product_emoji": getattr(order.master_product, "custom_emoji", "📦") or "📦",
                "quantity": int(order.quantity or 1),
                "amount_usd": float(order.our_price),
                "payment_method": payment_meth,
                "completed_at": order.completed_at,
            })
        except Exception as e:
            logger.warning(f"Could not publish proof broadcast event: {e}")

        try:
            from src.core.services.fulfillment_report import fulfillment_report_service
            await fulfillment_report_service.dispatch_for_order(
                session, order_id=order.id, outcome="COMPLETED"
            )
        except Exception as e:
            logger.warning(f"Fulfillment report failed for {order.order_number}: {e}")
        try:
            from src.core.services.hot_wallet_sync import sync_hot_wallet_balances
            await sync_hot_wallet_balances()
        except Exception as e:
            logger.warning(f"HotWallet sync after fulfillment failed: {e}")

        logger.info(
            f"Successfully fulfilled order {order.order_number} "
            f"for customer {order.customer.telegram_id if order.customer else '?'}"
        )


    async def _handle_supplier_exhausted(self, session, order: Order, reason: str):
        """Auto-refunds customer wallet balance and triggers admin alerts."""
        order.failure_reason = reason
        await order_service.transition_order_state(
            session, order, OrderState.FAILED_REFUNDED, f"failed_refunded: {reason}"
        )

        # Credit refund back to customer's internal wallet
        await order_service.credit_customer_wallet(
            session=session,
            customer_id=str(order.customer_id),
            amount=order.our_price,
            tx_type=WalletTxType.REFUND_CREDIT,
            description=f"استرداد تلقائي للطلب {order.order_number} - تعذر توفير المنتج",
            order_id=str(order.id)
        )
        await session.commit()

        try:
            from src.core.services.fulfillment_report import fulfillment_report_service
            await fulfillment_report_service.dispatch_for_order(
                session, order_id=order.id, outcome="FAILED_REFUNDED"
            )
        except Exception as e:
            logger.warning(f"Fulfillment failure report failed: {e}")

        # Alert admin and notify user in Telegram
        await event_bus.publish_alert(
            title="Order Failed & Refunded",
            message=f"Order {order.order_number} could not be fulfilled. ${order.our_price} refunded to customer balance.",
            level="warning",
            meta={"order_id": str(order.id), "reason": reason}
        )
        await self._dispatch_telegram_refund_notice(order)

    async def _dispatch_failure_report(self, session, order: Order, outcome: str):
        try:
            from src.core.services.fulfillment_report import fulfillment_report_service
            await fulfillment_report_service.dispatch_for_order(
                session, order_id=order.id, outcome=outcome
            )
        except Exception as e:
            logger.warning(f"Fulfillment report ({outcome}) failed: {e}")

    async def _dispatch_telegram_fulfillment(self, order: Order):
        """Publishes event to Bot to deliver the digital product to customer Telegram DM."""
        warranty_str = None
        if order.warranty_expires_at:
            warranty_str = order.warranty_expires_at.strftime('%Y-%m-%d')

        formal_inv_number = None
        async with AsyncSessionLocal() as session:
            from src.core.models.entities import FormalInvoice
            fi_stmt = select(FormalInvoice.invoice_number).where(FormalInvoice.order_id == order.id)
            formal_inv_number = (await session.execute(fi_stmt)).scalar_one_or_none()

        await event_bus.publish(
            "bot:deliver_product",
            {
                "order_id": str(order.id),
                "telegram_id": order.customer.telegram_id,
                "order_number": order.order_number,
                "product_title": order.master_product.title_ar,
                "payload": order.delivered_product,
                "quantity": order.quantity or 1,
                "warranty_expires_at": warranty_str,
                "formal_invoice_number": formal_inv_number,
            }
        )

    async def _dispatch_telegram_refund_notice(self, order: Order):
        """Publishes event to Bot to notify user of refund."""
        await event_bus.publish(
            "bot:notify_refund",
            {
                "telegram_id": order.customer.telegram_id,
                "order_number": order.order_number,
                "amount": float(order.our_price),
            }
        )


jit_executor = JitExecutor()
