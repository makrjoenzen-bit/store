from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.core.database import get_db
from src.core.models.entities import (
    Order, Supplier, Customer, SupplierCredit, AdminUser, PaymentInvoice,
)
from src.core.models.enums import OrderState, SupplierStatus, InvoiceStatus, PaymentMethod
from src.outgest.bsc_sender import bsc_sender
from src.api.routes.auth import get_current_admin

router = APIRouter(prefix="/dashboard", tags=["Dashboard Analytics"])


@router.get("/metrics")
async def get_dashboard_metrics(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Real KPIs from PostgreSQL (+ live hot-wallet balances when available)."""
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    orders_stmt = select(
        func.count(Order.id).label("total_orders"),
        func.coalesce(func.sum(Order.our_price), Decimal("0.0")).label("revenue"),
        func.coalesce(func.sum(Order.profit_margin), Decimal("0.0")).label("profit"),
    ).where(
        and_(
            Order.state == OrderState.COMPLETED,
            Order.completed_at >= today_start,
        )
    )
    today_stats = (await session.execute(orders_stmt)).one()

    # All orders created today (any state) for ops volume
    orders_created_today = (await session.execute(
        select(func.count(Order.id)).where(Order.created_at >= today_start)
    )).scalar() or 0

    active_customers = (await session.execute(
        select(func.count(Customer.id)).where(Customer.total_orders > 0)
    )).scalar() or 0

    total_customers = (await session.execute(select(func.count(Customer.id)))).scalar() or 0

    sup_stmt = select(
        func.count(Supplier.id).filter(Supplier.status == SupplierStatus.ACTIVE).label("active"),
        func.count(Supplier.id).label("total"),
    )
    sup_counts = (await session.execute(sup_stmt)).one()

    pending_sham = (await session.execute(
        select(func.count(PaymentInvoice.id)).where(
            and_(
                PaymentInvoice.payment_method == PaymentMethod.SHAM_CASH,
                PaymentInvoice.status == InvoiceStatus.PENDING,
                PaymentInvoice.sham_cash_receipt_photo_file_id.isnot(None),
            )
        )
    )).scalar() or 0

    pending_review_orders = (await session.execute(
        select(func.count(Order.id)).where(
            Order.state.in_([OrderState.SHAM_CASH_REVIEW, OrderState.FAILED_MANUAL_REVIEW])
        )
    )).scalar() or 0

    try:
        usdt_bal, bnb_bal = await bsc_sender.get_hot_wallet_balances()
    except Exception:
        usdt_bal, bnb_bal = Decimal("0.0"), Decimal("0.0")

    min_bnb = Decimal(str(getattr(settings, "MIN_BNB_GAS_RESERVE", 0.005) or 0.005))
    min_usdt = Decimal(str(getattr(settings, "MIN_USDT_RESERVE", 5.0) or 5.0))

    stranded = (await session.execute(
        select(func.coalesce(func.sum(SupplierCredit.amount), Decimal("0.0"))).where(
            SupplierCredit.is_consumed == False  # noqa: E712
        )
    )).scalar() or Decimal("0.0")

    return {
        "kpis": {
            "revenue_today": float(today_stats.revenue),
            "profit_today": float(today_stats.profit),
            "orders_today": int(orders_created_today),
            "completed_orders_today": int(today_stats.total_orders),
            "active_users": int(active_customers),
            "total_customers": int(total_customers),
            "pending_topups": int(pending_sham),
            "pending_reviews": int(pending_review_orders),
            "supplier_count": int(sup_counts.total),
            "active_suppliers": int(sup_counts.active),
            "active_suppliers_label": f"{sup_counts.active}/{sup_counts.total}",
        },
        "hot_wallet": {
            "usdt_balance": float(usdt_bal),
            "bnb_balance": float(bnb_bal),
            "min_bnb_reserve": float(min_bnb),
            "min_usdt_reserve": float(min_usdt),
            "is_gas_healthy": bnb_bal >= min_bnb,
        },
        "stranded_supplier_credits": float(stranded),
    }


@router.get("/recent-orders")
async def recent_orders(
    limit: int = Query(15, ge=1, le=50),
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> List[Dict[str, Any]]:
    rows = (await session.execute(
        select(Order)
        .options(
            selectinload(Order.master_product),
            selectinload(Order.customer),
        )
        .order_by(desc(Order.created_at))
        .limit(limit)
    )).scalars().all()
    out = []
    for o in rows:
        cust = o.customer
        out.append({
            "id": str(o.id),
            "order_number": o.order_number,
            "state": o.state.value if o.state else None,
            "our_price": float(o.our_price or 0),
            "product_title": o.master_product.title_ar if o.master_product else None,
            "customer": (
                f"@{cust.telegram_username}" if cust and cust.telegram_username
                else (cust.first_name if cust else None)
            ),
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })
    return out
