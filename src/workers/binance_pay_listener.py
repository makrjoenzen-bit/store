"""
Binance Pay inbound poller — Spot API GET /sapi/v1/pay/transactions every few seconds.
Matches unique_amount (and optional note/order_number) then confirms via payment_verification_service.
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from loguru import logger

from src.config import settings
from src.core.database import AsyncSessionLocal
from src.core.dedup import TxDeduper
from src.core.models.enums import PaymentMethod
from src.core.resilience import sleep_backoff
from src.core.services.payment_verification import payment_verification_service
from src.outgest.binance_pay_reader import binance_pay_reader


class BinancePayListenerWorker:
    def __init__(self):
        self._dedup = TxDeduper("binance_pay")
        self.poll_seconds = float(getattr(settings, "BINANCE_PAY_POLL_SECONDS", 5) or 5)
        self._fail_streak = 0

    async def run(self):
        if not binance_pay_reader.is_configured:
            logger.warning(
                "Binance Spot API keys not configured. "
                "Binance Pay listener idling (Admin Bot manual Pay Order ID fallback still works)."
            )
            while True:
                await asyncio.sleep(60)

        logger.info("Binance Pay listener started (Spot /sapi/v1/pay/transactions)")
        while True:
            try:
                await self.poll_once()
                self._fail_streak = 0
                await asyncio.sleep(self.poll_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._fail_streak += 1
                logger.error(f"Binance Pay listener error: {e}")
                await sleep_backoff(min(self._fail_streak - 1, 6), base=2.0, cap=60.0)

    async def poll_once(self):
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - 2 * 3600 * 1000  # last 2 hours
        rows = await binance_pay_reader.list_transactions(
            start_ms=start_ms, end_ms=end_ms, limit=100
        )
        for row in rows:
            await self._process_row(row)

    async def _process_row(self, row: dict):
        tx_id = row.get("transaction_id") or ""
        if not tx_id or await self._dedup.seen_or_mark(tx_id):
            return

        if (row.get("currency") or "USDT").upper() not in ("USDT", "USD"):
            return

        amount = Decimal(str(row.get("amount") or 0))
        if amount <= 0:
            return

        logger.info(f"Binance Pay inbound candidate: {amount} id={tx_id} note={row.get('note')}")
        async with AsyncSessionLocal() as session:
            # Prefer exact unique_amount match on pending BINANCE_PAY invoices
            result = await payment_verification_service.process_inbound_transfer(
                session,
                methods=[PaymentMethod.BINANCE_PAY],
                amount=amount,
                tx_ref=tx_id,
                trigger_prefix="binance_pay_listener",
            )
            if result.get("status") == "unmatched" and row.get("note"):
                from src.core.models.enums import InvoiceStatus

                note = str(row.get("note") or "")
                invoices = await payment_verification_service.find_invoices(
                    session, [PaymentMethod.BINANCE_PAY]
                )
                for inv in invoices:
                    if inv.status != InvoiceStatus.PENDING or not inv.order:
                        continue
                    if inv.order.order_number and inv.order.order_number in note:
                        await payment_verification_service.confirm_exact_payment(
                            session, inv,
                            amount=amount,
                            tx_ref=tx_id,
                            trigger_source=f"binance_pay_listener.note_{tx_id[:10]}",
                        )
                        break


if __name__ == "__main__":
    asyncio.run(BinancePayListenerWorker().run())
