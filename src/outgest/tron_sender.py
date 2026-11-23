import asyncio
import time
from decimal import Decimal
from typing import Optional, Tuple
from loguru import logger

from src.config import settings


class TronSender:
    """Production TRC-20 outgest engine for broadcasting JIT USDT transfers on TRON."""

    def __init__(self):
        self._client = None
        self.contract_address = settings.USDT_TRC20_CONTRACT_ADDRESS

    def _get_client(self):
        """Lazy-loads tronpy client."""
        if self._client is None:
            from tronpy import Tron
            from tronpy.providers import HTTPProvider

            rpc_url = settings.TRON_RPC_URL or "https://api.trongrid.io"
            if settings.TRON_API_KEY:
                self._client = Tron(HTTPProvider(rpc_url, api_key=settings.TRON_API_KEY))
            else:
                self._client = Tron(HTTPProvider(rpc_url))
        return self._client

    async def get_hot_wallet_balances(self) -> Tuple[Decimal, Decimal]:
        """Returns (usdt_balance, trx_balance)."""
        if not settings.TRON_HOT_WALLET_ADDRESS:
            return Decimal("0.0"), Decimal("0.0")

        def _sync_fetch():
            client = self._get_client()
            addr = settings.TRON_HOT_WALLET_ADDRESS
            
            # TRX balance
            trx_raw = client.get_account_balance(addr)
            trx_balance = Decimal(str(trx_raw))

            # USDT TRC-20 balance (6 decimals)
            contract = client.get_contract(self.contract_address)
            usdt_raw = contract.functions.balanceOf(addr)
            usdt_balance = Decimal(str(usdt_raw)) / Decimal(10 ** 6)

            return usdt_balance, trx_balance

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_fetch)

    async def estimate_transfer_fee_usd(self) -> Decimal:
        """Estimates live TRC-20 energy fee in USD (27.0 TRX × live TRXUSDT)."""
        from src.core.services.gas_oracle import gas_fx_oracle
        trx_usd = await gas_fx_oracle.get_trx_usd()
        return (Decimal("27.0") * trx_usd).quantize(Decimal("0.01"))

    async def send_usdt_jit(
        self,
        recipient_address: str,
        amount_usd: Decimal,
    ) -> Tuple[str, Decimal]:
        """
        Signs and broadcasts USDT TRC-20 transfer to a supplier deposit address.
        Returns (tx_hash_hex, estimated_fee_trx).
        """
        if not settings.TRON_HOT_WALLET_PRIVATE_KEY or not settings.TRON_HOT_WALLET_ADDRESS:
            raise ValueError("TRON hot wallet credentials not configured.")

        def _sync_send():
            from tronpy.keys import PrivateKey

            client = self._get_client()
            priv_key_str = settings.TRON_HOT_WALLET_PRIVATE_KEY
            if priv_key_str.startswith("0x"):
                priv_key_str = priv_key_str[2:]
            priv_key = PrivateKey(bytes.fromhex(priv_key_str))
            from_addr = priv_key.public_key.to_base58check_address()

            contract = client.get_contract(self.contract_address)
            # TRC-20 USDT uses 6 decimals
            amount_units = int(amount_usd * Decimal(10 ** 6))

            tx = (
                contract.functions.transfer(recipient_address, amount_units)
                .with_owner(from_addr)
                .fee_limit(30_000_000)  # 30 TRX max fee limit
                .build()
                .sign(priv_key)
            )
            result = tx.broadcast()
            tx_hash = result.get("txid", "")
            if not tx_hash and isinstance(result, str):
                tx_hash = result
            
            # Estimated energy/bandwidth fee in TRX (standard TRC-20 transfer burns ~13-32 TRX if receiver has no energy)
            estimated_fee_trx = Decimal("27.0")
            logger.info(f"Broadcasted JIT TRC-20 USDT TX {tx_hash} to {recipient_address}")
            return tx_hash, estimated_fee_trx

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_send)

    async def wait_for_tx_confirmation(
        self,
        tx_hash: str,
        timeout_seconds: int = 120,
        required_confirmations: int = 12
    ) -> dict:
        """Polls for transaction confirmation and verifies required block depth on TRON."""
        def _sync_wait():
            client = self._get_client()
            start = time.time()
            while (time.time() - start) < timeout_seconds:
                try:
                    tx_info = client.get_transaction_info(tx_hash)
                    if tx_info:
                        receipt = tx_info.get("receipt", {})
                        res_val = receipt.get("result", "")
                        is_success = (res_val == "SUCCESS" or (not res_val and tx_info.get("id")))
                        tx_block = tx_info.get("blockNumber")
                        
                        if is_success and tx_block:
                            latest_block = client.get_latest_block_number()
                            confirmations = (latest_block - tx_block) + 1
                            if confirmations >= required_confirmations:
                                return {
                                    "status": 1,
                                    "blockNumber": tx_block,
                                    "confirmations": confirmations,
                                    "fee": tx_info.get("fee", 0),
                                    "receipt": receipt,
                                }
                        elif not is_success:
                            return {
                                "status": 0,
                                "blockNumber": tx_block,
                                "fee": tx_info.get("fee", 0),
                                "receipt": receipt,
                            }
                except Exception:
                    pass
                time.sleep(3)
            raise TimeoutError(f"TRON transaction {tx_hash} confirmation timed out after {timeout_seconds}s")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _sync_wait)


tron_sender = TronSender()
