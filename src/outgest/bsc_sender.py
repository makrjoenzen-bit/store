import asyncio
from decimal import Decimal
from typing import Optional, Tuple
from web3 import AsyncWeb3, AsyncHTTPProvider
from eth_account import Account
from loguru import logger

from src.config import settings
from src.core.redis import event_bus

# Standard BEP-20 minimal ABI
BEP20_MIN_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
]


class BscSender:
    """Production Web3.py outgest engine for broadcasting JIT USDT transfers on BSC."""

    def __init__(self):
        self.w3 = AsyncWeb3(AsyncHTTPProvider(settings.BSC_RPC_URL))
        self.contract_address = AsyncWeb3.to_checksum_address(settings.USDT_BEP20_CONTRACT_ADDRESS)
        self.contract = self.w3.eth.contract(address=self.contract_address, abi=BEP20_MIN_ABI)
        self._decimals: Optional[int] = None

    async def get_decimals(self) -> int:
        if self._decimals is None:
            try:
                self._decimals = await self.contract.functions.decimals().call()
            except Exception:
                # BSC USDT (Bridged from Binance) is typically 18 decimals
                self._decimals = 18
        return self._decimals

    async def get_hot_wallet_balances(self) -> Tuple[Decimal, Decimal]:
        """Returns (usdt_balance, bnb_balance)."""
        if not settings.HOT_WALLET_ADDRESS:
            return Decimal("0.0"), Decimal("0.0")

        address = AsyncWeb3.to_checksum_address(settings.HOT_WALLET_ADDRESS)
        decimals = await self.get_decimals()

        raw_usdt = await self.contract.functions.balanceOf(address).call()
        usdt_balance = Decimal(raw_usdt) / Decimal(10 ** decimals)

        raw_bnb = await self.w3.eth.get_balance(address)
        bnb_balance = Decimal(raw_bnb) / Decimal(10 ** 18)

        return usdt_balance, bnb_balance

    async def estimate_transfer_fee_usd(self) -> Decimal:
        """Estimates live BEP-20 transfer fee in USD based on current BSC gas + FX oracle."""
        try:
            from src.core.services.gas_oracle import gas_fx_oracle
            gas_price = await self.w3.eth.gas_price
            fee_bnb = (Decimal(65000) * Decimal(gas_price)) / Decimal(10 ** 18)
            bnb_price_usd = await gas_fx_oracle.get_bnb_usd()
            return (fee_bnb * bnb_price_usd).quantize(Decimal("0.0001"))
        except Exception as e:
            logger.warning(f"Failed to query BSC gas price for fee estimation: {e}")
            return Decimal("0.08")  # Safe fallback estimate

    async def resync_and_recover_nonce(self) -> int:
        """Recovers and forces Redis nonce counter to match live on-chain pending count."""
        if not settings.HOT_WALLET_ADDRESS:
            return 0
        from_address = AsyncWeb3.to_checksum_address(settings.HOT_WALLET_ADDRESS)
        on_chain_nonce = await self.w3.eth.get_transaction_count(from_address, "pending")
        await event_bus.sync_wallet_nonce(from_address, on_chain_nonce)
        logger.info(f"Synchronized hot wallet {from_address} nonce to on-chain pending count: {on_chain_nonce}")
        return on_chain_nonce

    async def send_usdt_jit(
        self,
        recipient_address: str,
        amount_usd: Decimal,
        gas_price_multiplier: float = 1.0,
    ) -> Tuple[str, int, Decimal]:
        """
        Signs and broadcasts USDT BEP20 transfer to a supplier deposit address.
        Returns (tx_hash_hex, nonce_used, estimated_gas_fee_bnb).
        """
        if not settings.HOT_WALLET_PRIVATE_KEY or not settings.HOT_WALLET_ADDRESS:
            raise ValueError("Hot wallet credentials not configured.")

        account = Account.from_key(settings.HOT_WALLET_PRIVATE_KEY)
        from_address = AsyncWeb3.to_checksum_address(account.address)
        to_address = AsyncWeb3.to_checksum_address(recipient_address)
        decimals = await self.get_decimals()

        # Convert to token integer units
        amount_units = int(amount_usd * Decimal(10 ** decimals))

        # Query on-chain nonce for synchronization check
        on_chain_nonce = await self.w3.eth.get_transaction_count(from_address, "pending")
        # Grab atomic sequential nonce from Redis event bus
        nonce = await event_bus.get_next_wallet_nonce(from_address, on_chain_nonce)

        # Gas calculations
        current_gas_price = await self.w3.eth.gas_price
        if gas_price_multiplier > 1.0:
            current_gas_price = int(current_gas_price * gas_price_multiplier)

        # Build transfer transaction
        tx_data = await self.contract.functions.transfer(to_address, amount_units).build_transaction({
            "chainId": settings.BSC_CHAIN_ID,
            "from": from_address,
            "gas": 65000,
            "gasPrice": current_gas_price,
            "nonce": nonce,
        })

        # Local sign
        signed_tx = self.w3.eth.account.sign_transaction(tx_data, private_key=settings.HOT_WALLET_PRIVATE_KEY)

        try:
            tx_hash_bytes = await self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
            tx_hash_hex = self.w3.to_hex(tx_hash_bytes)
            gas_fee_bnb = (Decimal(65000) * Decimal(current_gas_price)) / Decimal(10 ** 18)
            logger.info(f"Broadcasted JIT USDT TX {tx_hash_hex} to {to_address} (nonce: {nonce})")
            return tx_hash_hex, nonce, gas_fee_bnb
        except Exception as e:
            logger.error(f"Failed to broadcast TX with nonce {nonce}: {e}")
            # Re-sync Redis nonce with on-chain count
            refetched_nonce = await self.w3.eth.get_transaction_count(from_address, "pending")
            await event_bus.sync_wallet_nonce(from_address, refetched_nonce)
            raise e

    async def wait_for_tx_receipt(
        self,
        tx_hash: str,
        timeout_seconds: int = 120,
        required_confirmations: int = 3
    ) -> dict:
        """Polls for transaction receipt and waits for required safe block confirmations."""
        start = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start) < timeout_seconds:
            try:
                receipt = await self.w3.eth.get_transaction_receipt(tx_hash)
                if receipt is not None:
                    status = receipt.get("status")
                    if status != 1:
                        # Transaction reverted on-chain immediately
                        return dict(receipt)

                    tx_block = receipt.get("blockNumber")
                    if tx_block:
                        current_block = await self.w3.eth.block_number
                        confirmations = (current_block - tx_block) + 1
                        if confirmations >= required_confirmations:
                            res = dict(receipt)
                            res["confirmations"] = confirmations
                            return res
            except Exception:
                pass
            await asyncio.sleep(2.5)
        raise TimeoutError(f"Transaction receipt/confirmation for {tx_hash} timed out after {timeout_seconds}s")


bsc_sender = BscSender()
