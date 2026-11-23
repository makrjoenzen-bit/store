from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.models.entities import (
    Customer, Order, WalletTransaction, SupportTicket,
    PaymentInvoice, AuditLog, FormalInvoice
)
from src.core.models.enums import (
    WalletTxType, OrderState, TicketStatus, CustomerStatus
)
from src.core.services.order_service import order_service
from src.api.routes.auth import get_current_admin
from src.core.redis import event_bus
from src.config import settings

router = APIRouter(prefix="/customers", tags=["Customer CRM"])


@router.get("")
async def list_customers(
    session: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=10, le=100),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """Searchable, filterable customer list for CRM dashboard."""
    base_stmt = select(Customer)
    count_stmt = select(func.count(Customer.id))

    filters = []
    if search:
        search_term = f"%{search.strip()}%"
        filters.append(
            or_(
                Customer.telegram_username.ilike(search_term),
                Customer.first_name.ilike(search_term),
                Customer.last_name.ilike(search_term),
                func.cast(Customer.telegram_id, String).like(search_term),
            )
        )
    if status and status in ("active", "banned"):
        filters.append(Customer.account_status == status)

    if filters:
        base_stmt = base_stmt.where(and_(*filters))
        count_stmt = count_stmt.where(and_(*filters))

    total = (await session.execute(count_stmt)).scalar_one()

    stmt = (
        base_stmt
        .order_by(Customer.last_active_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    customers = (await session.execute(stmt)).scalars().all()

    # Get active ticket counts per customer
    cust_ids = [c.id for c in customers]
    ticket_counts = {}
    if cust_ids:
        tc_stmt = (
            select(
                SupportTicket.customer_id,
                func.count(SupportTicket.id)
            )
            .where(
                and_(
                    SupportTicket.customer_id.in_(cust_ids),
                    SupportTicket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS])
                )
            )
            .group_by(SupportTicket.customer_id)
        )
        tc_rows = (await session.execute(tc_stmt)).all()
        ticket_counts = {str(r[0]): r[1] for r in tc_rows}

    items = []
    for c in customers:
        items.append({
            "id": str(c.id),
            "telegram_id": c.telegram_id,
            "telegram_username": c.telegram_username,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "wallet_balance": float(c.wallet_balance),
            "total_spent": float(c.total_spent),
            "total_orders": c.total_orders,
            "active_tickets": ticket_counts.get(str(c.id), 0),
            "account_status": c.account_status.value if c.account_status else "active",
            "is_blocked": c.is_blocked,
            "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
            "last_active_at": c.last_active_at.isoformat() if c.last_active_at else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


# We need String imported for the cast in the search filter
from sqlalchemy import String


@router.get("/{customer_id}")
async def get_customer_detail(
    customer_id: str,
    session: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Full 360° customer profile with orders, invoices, ledger, and tickets."""
    stmt = select(Customer).where(Customer.id == customer_id)
    customer = (await session.execute(stmt)).scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Recent orders (last 20)
    orders_stmt = (
        select(Order)
        .where(Order.customer_id == customer_id)
        .options(selectinload(Order.master_product))
        .order_by(Order.created_at.desc())
        .limit(20)
    )
    orders = (await session.execute(orders_stmt)).scalars().all()

    # Wallet ledger (last 30)
    ledger_stmt = (
        select(WalletTransaction)
        .where(WalletTransaction.customer_id == customer_id)
        .order_by(WalletTransaction.created_at.desc())
        .limit(30)
    )
    ledger = (await session.execute(ledger_stmt)).scalars().all()

    # Formal invoices (last 20)
    fi_stmt = (
        select(FormalInvoice)
        .where(FormalInvoice.customer_id == customer_id)
        .order_by(FormalInvoice.created_at.desc())
        .limit(20)
    )
    formal_invoices = (await session.execute(fi_stmt)).scalars().all()

    # Support tickets (last 10)
    tickets_stmt = (
        select(SupportTicket)
        .where(SupportTicket.customer_id == customer_id)
        .order_by(SupportTicket.created_at.desc())
        .limit(10)
    )
    tickets = (await session.execute(tickets_stmt)).scalars().all()

    return {
        "customer": {
            "id": str(customer.id),
            "telegram_id": customer.telegram_id,
            "telegram_username": customer.telegram_username,
            "first_name": customer.first_name,
            "last_name": customer.last_name,
            "wallet_balance": float(customer.wallet_balance),
            "total_spent": float(customer.total_spent),
            "total_orders": customer.total_orders,
            "account_status": customer.account_status.value if customer.account_status else "active",
            "is_blocked": customer.is_blocked,
            "blocked_reason": customer.blocked_reason,
            "first_seen_at": customer.first_seen_at.isoformat() if customer.first_seen_at else None,
            "last_active_at": customer.last_active_at.isoformat() if customer.last_active_at else None,
        },
        "orders": [
            {
                "id": str(o.id),
                "order_number": o.order_number,
                "product_title": o.master_product.title_ar if o.master_product else "",
                "our_price": float(o.our_price),
                "quantity": o.quantity,
                "state": o.state.value if o.state else "",
                "warranty_expires_at": o.warranty_expires_at.isoformat() if o.warranty_expires_at else None,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
        "ledger": [
            {
                "id": str(t.id),
                "tx_type": t.tx_type.value if t.tx_type else "",
                "amount": float(t.amount),
                "balance_after": float(t.balance_after),
                "description": t.description,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in ledger
        ],
        "formal_invoices": [
            {
                "id": str(fi.id),
                "invoice_number": fi.invoice_number,
                "item_title": fi.item_title,
                "net_total": float(fi.net_total),
                "payment_method": fi.payment_method.value if fi.payment_method else "",
                "warranty_expires_at": fi.warranty_expires_at.isoformat() if fi.warranty_expires_at else None,
                "created_at": fi.created_at.isoformat() if fi.created_at else None,
            }
            for fi in formal_invoices
        ],
        "tickets": [
            {
                "id": str(tk.id),
                "subject": tk.subject,
                "status": tk.status.value if tk.status else "",
                "created_at": tk.created_at.isoformat() if tk.created_at else None,
            }
            for tk in tickets
        ],
    }


class BalanceAdjustmentDTO(BaseModel):
    amount: float
    direction: str  # "credit" or "debit"
    reason: str


@router.post("/{customer_id}/adjust-balance")
async def adjust_customer_balance(
    customer_id: str,
    dto: BalanceAdjustmentDTO,
    session: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Admin manual balance adjustment — shared crm_service (Admin Bot parity)."""
    from src.core.services.crm_service import crm_service
    try:
        return await crm_service.adjust_balance(
            session,
            customer_id,
            amount=Decimal(str(dto.amount)),
            direction=dto.direction,
            reason=dto.reason,
            reviewer=admin.username,
            admin_user_id=admin.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class BanActionDTO(BaseModel):
    action: str  # "ban" or "unban"
    reason: Optional[str] = None


@router.post("/{customer_id}/ban")
async def ban_unban_customer(
    customer_id: str,
    dto: BanActionDTO,
    session: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """1-Click Ban/Unban — shared crm_service (Admin Bot parity)."""
    if dto.action not in ("ban", "unban"):
        raise HTTPException(status_code=400, detail="Action must be 'ban' or 'unban'")
    from src.core.services.crm_service import crm_service
    try:
        result = await crm_service.set_ban(
            session,
            customer_id,
            ban=(dto.action == "ban"),
            reason=dto.reason,
            reviewer=admin.username,
            admin_user_id=admin.id,
        )
        return {
            "status": "ok",
            "account_status": result["account_status"],
            "is_blocked": result["is_blocked"],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
