"""Sync HotWallet DB rows from live chain balances (BSC + TRON)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from loguru import logger

from src.config import settings
from src.core.database import AsyncSessionLocal
from src.core.models.entities import HotWallet


async def sync_hot_wallet_balances() -> dict:
    """
    Refresh HotWallet.usdt_balance / bnb_balance from chain RPCs.
    Maintains one row per configured address (BSC and/or TRON).
    """
    result = {"bsc": None, "tron": None}
    async with AsyncSessionLocal() as session:
        # --- BSC ---
        if settings.HOT_WALLET_ADDRESS:
            try:
                from src.outgest.bsc_sender import bsc_sender
                usdt, bnb = await bsc_sender.get_hot_wallet_balances()
                row = (await session.execute(
                    select(HotWallet).where(HotWallet.address == settings.HOT_WALLET_ADDRESS)
                )).scalar_one_or_none()
                if not row:
                    row = HotWallet(address=settings.HOT_WALLET_ADDRESS)
                    session.add(row)
                row.usdt_balance = usdt
                row.bnb_balance = bnb
                row.last_synced_at = datetime.now(timezone.utc)
                result["bsc"] = {"usdt": float(usdt), "bnb": float(bnb)}
            except Exception as e:
                logger.warning(f"BSC hot wallet sync failed: {e}")

        # --- TRON ---
        if settings.TRON_HOT_WALLET_ADDRESS:
            try:
                from src.outgest.tron_sender import tron_sender
                usdt, trx = await tron_sender.get_hot_wallet_balances()
                row = (await session.execute(
                    select(HotWallet).where(HotWallet.address == settings.TRON_HOT_WALLET_ADDRESS)
                )).scalar_one_or_none()
                if not row:
                    row = HotWallet(address=settings.TRON_HOT_WALLET_ADDRESS)
                    session.add(row)
                row.usdt_balance = usdt
                # Store TRX in bnb_balance column as chain-native gas token balance
                row.bnb_balance = trx
                row.last_synced_at = datetime.now(timezone.utc)
                result["tron"] = {"usdt": float(usdt), "trx": float(trx)}
            except Exception as e:
                logger.warning(f"TRON hot wallet sync failed: {e}")

        await session.commit()
    return result


async def bump_hot_wallet_usdt(address: str, delta: Decimal) -> None:
    """Best-effort DB ledger bump after a confirmed inbound (until next full sync)."""
    if not address or delta == 0:
        return
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(HotWallet).where(HotWallet.address == address)
        )).scalar_one_or_none()
        if not row:
            row = HotWallet(address=address, usdt_balance=max(Decimal("0"), delta))
            session.add(row)
        else:
            row.usdt_balance = (row.usdt_balance or Decimal("0")) + delta
        row.last_synced_at = datetime.now(timezone.utc)
        await session.commit()
