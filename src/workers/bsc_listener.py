"""
BSC inbound USDT listener — watches Transfer events TO our hot wallet,
then confirms invoices via shared payment_verification_service.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from web3 import AsyncWeb3, AsyncHTTPProvider
from loguru import logger

from src.config import settings
from src.core.database import AsyncSessionLocal
from src.core.models.enums import PaymentMethod
from src.core.resilience import sleep_backoff
from src.core.services.payment_verification import payment_verification_service
from src.outgest.bsc_sender import BEP20_MIN_ABI, bsc_sender


class BscListenerWorker:
    """Listens to on-chain Transfer events for USDT BEP20 on BSC and auto-confirms customer payments."""

    def __init__(self):
        self.w3 = AsyncWeb3(AsyncHTTPProvider(settings.BSC_RPC_URL))
        self.usdt_address = None
        self.contract = None
        self.hot_wallet = None
        self.last_scanned_block = None
        self._fail_streak = 0
        self._disabled_reason = None

        contract_raw = (settings.USDT_BEP20_CONTRACT_ADDRESS or "").strip()
        try:
            if not contract_raw or len(contract_raw) < 10:
                raise ValueError("USDT_BEP20_CONTRACT_ADDRESS missing or too short")
            self.usdt_address = AsyncWeb3.to_checksum_address(contract_raw)
            self.contract = self.w3.eth.contract(address=self.usdt_address, abi=BEP20_MIN_ABI)
        except Exception as e:
            self._disabled_reason = f"Invalid USDT_BEP20_CONTRACT_ADDRESS: {e}"
            logger.warning(f"BSC listener disabled at init — {self._disabled_reason}")

        try:
            if settings.HOT_WALLET_ADDRESS:
                self.hot_wallet = AsyncWeb3.to_checksum_address(settings.HOT_WALLET_ADDRESS)
        except Exception as e:
            self._disabled_reason = f"Invalid HOT_WALLET_ADDRESS: {e}"
            logger.warning(f"BSC listener disabled at init — {self._disabled_reason}")
            self.hot_wallet = None

    async def run(self):
        if self._disabled_reason or not self.contract or not self.usdt_address:
            logger.warning(
                f"BSC listener idling (misconfigured contract). reason={self._disabled_reason}"
            )
            while True:
                await asyncio.sleep(3600)
            return

        if not self.hot_wallet:
            logger.warning("HOT_WALLET_ADDRESS not configured. BSC listener idling.")
            return

        logger.info(f"BSC Payment Listener started. Watching incoming USDT to {self.hot_wallet}...")
        self.last_scanned_block = await self._resume_last_scanned_block()

        transfer_topic = self.w3.keccak(text="Transfer(address,address,uint256)").hex()
        to_topic = "0x" + self.hot_wallet[2:].lower().zfill(64)

        while True:
            try:
                latest_block = await self.w3.eth.block_number
                if latest_block > self.last_scanned_block:
                    from_block = self.last_scanned_block + 1
                    to_block = min(latest_block, from_block + 50)

                    logs = await self.w3.eth.get_logs({
                        "address": self.usdt_address,
                        "fromBlock": from_block,
                        "toBlock": to_block,
                        "topics": [transfer_topic, None, to_topic],
                    })

                    for log in logs:
                        await self._process_transfer_log(log)

                    self.last_scanned_block = to_block
                    await self._persist_last_scanned_block(to_block)

                self._fail_streak = 0
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._fail_streak += 1
                logger.error(f"Error in BSC listener loop: {e}")
                await sleep_backoff(min(self._fail_streak - 1, 6), base=2.0, cap=60.0)

    async def _resume_last_scanned_block(self) -> int:
        """Resume from Redis cursor; cap lookback if cursor is missing/stale."""
        latest = await self.w3.eth.block_number
        safe_lookback = 2000  # ~1.5h on BSC
        default_start = max(0, latest - 5)
        try:
            from src.core.redis import event_bus
            redis = event_bus.redis or getattr(event_bus, "_redis", None)
            if redis is not None:
                raw = await redis.get("bsc:last_scanned_block")
                if raw is not None:
                    saved = int(raw)
                    floor = max(0, latest - safe_lookback)
                    resumed = max(saved, floor)
                    if resumed < latest:
                        logger.info(
                            f"BSC listener resumed from block {resumed} "
                            f"(saved={saved}, latest={latest})"
                        )
                        return resumed
        except Exception as e:
            logger.warning(f"Could not read bsc:last_scanned_block from Redis: {e}")
        return default_start

    async def _persist_last_scanned_block(self, block: int) -> None:
        try:
            from src.core.redis import event_bus
            redis = event_bus.redis or getattr(event_bus, "_redis", None)
            if redis is not None:
                await redis.set("bsc:last_scanned_block", str(int(block)))
        except Exception as e:
            logger.warning(f"Could not persist bsc:last_scanned_block: {e}")

    async def _process_transfer_log(self, log):
        tx_hash = log["transactionHash"].hex()
        block_num = log["blockNumber"]
        raw_val = int(log["data"].hex(), 16)
        decimals = await bsc_sender.get_decimals()
        received_usdt = Decimal(raw_val) / Decimal(10 ** decimals)

        logger.info(f"Detected incoming USDT on-chain: {received_usdt} USDT (TX: {tx_hash})")

        async with AsyncSessionLocal() as session:
            await payment_verification_service.process_inbound_transfer(
                session,
                methods=[PaymentMethod.USDT_BEP20_DIRECT],
                amount=received_usdt,
                tx_ref=tx_hash,
                block_number=block_num,
                trigger_prefix="bsc_listener",
                sync_wallet_address=settings.HOT_WALLET_ADDRESS,
            )


if __name__ == "__main__":
    asyncio.run(BscListenerWorker().run())
