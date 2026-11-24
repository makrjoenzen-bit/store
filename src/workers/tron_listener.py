"""
TRC-20 inbound USDT listener — polls TronGrid for transfers TO our hot wallet,
then confirms invoices via shared payment_verification_service.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import httpx
from loguru import logger

from src.config import settings
from src.core.database import AsyncSessionLocal
from src.core.dedup import TxDeduper
from src.core.models.enums import PaymentMethod
from src.core.resilience import sleep_backoff
from src.core.services.payment_verification import payment_verification_service


class TronListenerWorker:
    def __init__(self):
        self.hot_wallet = (settings.TRON_HOT_WALLET_ADDRESS or "").strip()
        self.contract = settings.USDT_TRC20_CONTRACT_ADDRESS
        self.rpc = (settings.TRON_RPC_URL or "https://api.trongrid.io").rstrip("/")
        self.api_key = settings.TRON_API_KEY
        self._dedup = TxDeduper("tron_trc20")
        self._fingerprint: Optional[str] = None
        self._fail_streak = 0

    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.api_key:
            h["TRON-PRO-API-KEY"] = self.api_key
        return h

    async def run(self):
        if not self.hot_wallet:
            logger.warning("TRON_HOT_WALLET_ADDRESS not set. TRC-20 listener idling.")
            return

        logger.info(f"TRON TRC-20 listener started. Watching USDT → {self.hot_wallet}")
        while True:
            try:
                await self.poll_once()
                self._fail_streak = 0
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._fail_streak += 1
                logger.error(f"TRON listener error: {e}")
                await sleep_backoff(min(self._fail_streak - 1, 6), base=2.0, cap=60.0)

    async def poll_once(self):
        url = f"{self.rpc}/v1/accounts/{self.hot_wallet}/transactions/trc20"
        params = {
            "only_to": "true",
            "limit": 50,
            "contract_address": self.contract,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(url, params=params, headers=self._headers())
            if res.status_code != 200:
                logger.warning(f"TronGrid TRC20 poll HTTP {res.status_code}: {res.text[:200]}")
                return
            payload = res.json()

        rows = payload.get("data") or []
        # Process oldest-first among page
        for row in reversed(rows):
            await self._process_transfer(row)

    async def _process_transfer(self, row: dict):
        tx_id = str(row.get("transaction_id") or row.get("txID") or "")
        if not tx_id or await self._dedup.seen_or_mark(tx_id):
            return

        to_addr = (row.get("to") or "").strip()
        if to_addr and to_addr.lower() != self.hot_wallet.lower():
            return

        # value is in sun units (6 decimals for USDT)
        raw_val = row.get("value") or row.get("quant") or "0"
        try:
            received = Decimal(str(raw_val)) / Decimal("1000000")
        except Exception:
            return
        if received <= 0:
            return

        block_num = None
        try:
            block_num = int(row.get("block_timestamp") or 0) or None
        except Exception:
            pass

        logger.info(f"Detected inbound TRC-20 USDT: {received} (TX: {tx_id})")
        async with AsyncSessionLocal() as session:
            await payment_verification_service.process_inbound_transfer(
                session,
                methods=[PaymentMethod.USDT_TRC20_DIRECT],
                amount=received,
                tx_ref=tx_id,
                block_number=block_num,
                trigger_prefix="tron_listener",
                sync_wallet_address=self.hot_wallet,
            )


if __name__ == "__main__":
    asyncio.run(TronListenerWorker().run())
