from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Any, List
from fastapi import APIRouter, Depends
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.entities import (
    Order, Supplier, Customer, SupplierCredit, AdminUser
)
from src.core.models.enums import OrderState, SupplierStatus
from src.outgest.bsc_sender import bsc_sender
from src.api.routes.auth import get_current_admin

router = APIRouter(prefix="/dashboard", tags=["Dashboard Analytics"])


@router.get("/metrics")
async def get_dashboard_metrics(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    """Computes real-time KPI metrics, hot wallet funds, and supplier statistics."""
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    # Today's Completed Orders
    orders_stmt = select(
        func.count(Order.id).label("total_orders"),
        func.coalesce(func.sum(Order.our_price), Decimal("0.0")).label("revenue"),
        func.coalesce(func.sum(Order.profit_margin), Decimal("0.0")).label("profit")
    ).where(
        and_(
            Order.state == OrderState.COMPLETED,
            Order.completed_at >= today_start
        )
    )
    res = await session.execute(orders_stmt)
    today_stats = res.one()

    # Active Suppliers Count
    sup_stmt = select(
        func.count(Supplier.id).filter(Supplier.status == SupplierStatus.ACTIVE).label("active"),
        func.count(Supplier.id).label("total")
    )
    sup_res = await session.execute(sup_stmt)
    sup_counts = sup_res.one()

    # Hot Wallet balances on BSC
    try:
        usdt_bal, bnb_bal = await bsc_sender.get_hot_wallet_balances()
    except Exception:
        usdt_bal, bnb_bal = Decimal("0.0"), Decimal("0.0")

    # Stranded Credits (Funds sitting at suppliers from past failed orders)
    credits_stmt = select(
        func.coalesce(func.sum(SupplierCredit.amount), Decimal("0.0"))
    ).where(SupplierCredit.is_consumed == False)
    credits_res = await session.execute(credits_stmt)
    stranded_funds = credits_res.scalar() or Decimal("0.0")

    return {
        "kpis": {
            "revenue_today": float(today_stats.revenue),
            "profit_today": float(today_stats.profit),
            "orders_today": today_stats.total_orders,
            "active_suppliers": f"{sup_counts.active}/{sup_counts.total}",
        },
        "hot_wallet": {
            "usdt_balance": float(usdt_bal),
            "bnb_balance": float(bnb_bal),
            "min_bnb_reserve": 0.005,
            "min_usdt_reserve": 5.0,
            "is_gas_healthy": bnb_bal >= Decimal("0.005"),
        },
        "stranded_supplier_credits": float(stranded_funds),
    }
