"""Shared treasury solvency calculations used by Dashboard API and Admin Bot."""
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.entities import (
    Customer, Order, WalletTransaction, HotWallet,
    SupplierCredit, PaymentInvoice,
)
from src.core.models.enums import OrderState, InvoiceStatus, WalletTxType, PaymentMethod


def _utc_now():
    return datetime.now(timezone.utc)


class TreasuryService:
    async def get_summary(self, session: AsyncSession) -> Dict[str, Any]:
        """
        Real-time Treasury Solvency Dashboard payload.
        Total Assets = Hot Wallet USDT + Active Supplier Credits + Confirmed Sham Cash Collected
        Total Liabilities = Sum of all customer wallet balances
        Net Withdrawable = Assets - Liabilities
        """
        now = _utc_now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        wallets = (await session.execute(select(HotWallet))).scalars().all()
        total_hot_wallet_usdt = sum(float(w.usdt_balance) for w in wallets) if wallets else 0.0

        total_supplier_credits = float((await session.execute(
            select(func.coalesce(func.sum(SupplierCredit.amount), 0)).where(
                SupplierCredit.is_consumed == False
            )
        )).scalar_one())

        total_sham_cash_collected = float((await session.execute(
            select(func.coalesce(func.sum(PaymentInvoice.amount_received), 0)).where(
                and_(
                    PaymentInvoice.payment_method == PaymentMethod.SHAM_CASH,
                    PaymentInvoice.status == InvoiceStatus.CONFIRMED,
                )
            )
        )).scalar_one())

        total_assets = total_hot_wallet_usdt + total_supplier_credits + total_sham_cash_collected
        total_liabilities = float((await session.execute(
            select(func.coalesce(func.sum(Customer.wallet_balance), 0))
        )).scalar_one())
        net_withdrawable = total_assets - total_liabilities

        async def get_profit_in_window(after_dt: Optional[datetime]):
            where_clause = [Order.state == OrderState.COMPLETED]
            if after_dt:
                where_clause.append(Order.completed_at >= after_dt)
            row = (await session.execute(
                select(
                    func.coalesce(func.sum(Order.profit_margin), 0),
                    func.coalesce(func.sum(Order.gas_fee_actual), 0),
                    func.count(Order.id),
                ).where(and_(*where_clause))
            )).one()
            gross_profit = float(row[0])
            gas_expenses = float(row[1])
            return {
                "gross_profit": round(gross_profit, 2),
                "gas_expenses": round(gas_expenses, 2),
                "net_profit": round(gross_profit - gas_expenses, 2),
                "order_count": int(row[2]),
            }

        total_revenue = float((await session.execute(
            select(func.coalesce(func.sum(Order.our_price), 0)).where(
                Order.state == OrderState.COMPLETED
            )
        )).scalar_one())

        total_refunds = float((await session.execute(
            select(func.coalesce(func.sum(func.abs(WalletTransaction.amount)), 0)).where(
                WalletTransaction.tx_type == WalletTxType.REFUND_CREDIT
            )
        )).scalar_one())

        return {
            "total_assets": round(total_assets, 2),
            "hot_wallet_usdt": round(total_hot_wallet_usdt, 2),
            "supplier_credits": round(total_supplier_credits, 2),
            "sham_cash_collected": round(total_sham_cash_collected, 2),
            "total_liabilities": round(total_liabilities, 2),
            "net_withdrawable": round(net_withdrawable, 2),
            "total_revenue": round(total_revenue, 2),
            "total_refunds": round(total_refunds, 2),
            "profit_today": await get_profit_in_window(today_start),
            "profit_7d": await get_profit_in_window(week_ago),
            "profit_30d": await get_profit_in_window(month_ago),
            "profit_all_time": await get_profit_in_window(None),
        }


treasury_service = TreasuryService()
