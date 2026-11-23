import csv
import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.models.entities import (
    Customer, WalletTransaction,
)
from src.core.services.treasury_service import treasury_service
from src.api.routes.auth import get_current_admin

router = APIRouter(prefix="/treasury", tags=["Treasury & Accounting"])


@router.get("/summary")
async def get_treasury_summary(
    session: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Real-time Treasury Solvency Dashboard — shared with Admin Bot via treasury_service."""
    return await treasury_service.get_summary(session)


@router.get("/ledger")
async def get_wallet_ledger(
    session: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
    tx_type: Optional[str] = Query(None),
):
    """Paginated immutable wallet transaction ledger."""
    stmt = select(WalletTransaction).join(Customer, WalletTransaction.customer_id == Customer.id)

    if tx_type:
        stmt = stmt.where(WalletTransaction.tx_type == tx_type)

    total_stmt = select(func.count(WalletTransaction.id))
    if tx_type:
        total_stmt = total_stmt.where(WalletTransaction.tx_type == tx_type)
    total = (await session.execute(total_stmt)).scalar_one()

    stmt = stmt.order_by(WalletTransaction.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    rows = (await session.execute(stmt)).scalars().all()

    # Gather customer names
    cust_ids = list(set(str(r.customer_id) for r in rows))
    cust_map = {}
    if cust_ids:
        c_stmt = select(Customer).where(Customer.id.in_(cust_ids))
        custs = (await session.execute(c_stmt)).scalars().all()
        cust_map = {str(c.id): c for c in custs}

    items = []
    for r in rows:
        c = cust_map.get(str(r.customer_id))
        items.append({
            "id": str(r.id),
            "customer_id": str(r.customer_id),
            "customer_name": (c.telegram_username or c.first_name or str(c.telegram_id)) if c else "",
            "tx_type": r.tx_type.value if r.tx_type else "",
            "amount": float(r.amount),
            "balance_after": float(r.balance_after),
            "description": r.description,
            "order_id": str(r.order_id) if r.order_id else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


@router.get("/export-csv")
async def export_treasury_csv(
    session: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Export full wallet transaction ledger as downloadable CSV."""
    stmt = (
        select(WalletTransaction, Customer)
        .join(Customer, WalletTransaction.customer_id == Customer.id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(10000)
    )
    rows = (await session.execute(stmt)).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "TX_ID", "Date", "Customer_TG_ID", "Customer_Username",
        "TX_Type", "Amount_USD", "Balance_After", "Description", "Order_ID"
    ])
    for tx, cust in rows:
        writer.writerow([
            str(tx.id),
            tx.created_at.isoformat() if tx.created_at else "",
            cust.telegram_id,
            cust.telegram_username or "",
            tx.tx_type.value if tx.tx_type else "",
            float(tx.amount),
            float(tx.balance_after),
            tx.description,
            str(tx.order_id) if tx.order_id else "",
        ])

    output.seek(0)
    filename = f"treasury_ledger_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
