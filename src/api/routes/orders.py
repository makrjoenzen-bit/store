import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.models.entities import (
    Order, PaymentInvoice, Customer, MasterProduct,
    Supplier, OrderStateLog, AdminUser
)
from src.core.models.enums import OrderState, InvoiceStatus, WalletTxType
from src.core.services.order_service import order_service
from src.core.services.jit_executor import jit_executor
from src.core.services.sham_cash_service import sham_cash_service
from src.core.services.order_admin_service import order_admin_service
from src.core.services.telegram_file_proxy import download_telegram_file
from src.api.routes.auth import get_current_admin
from src.core.redis import event_bus

router = APIRouter(prefix="/orders", tags=["Orders & Payments"])


def _uid(value):
    return uuid.UUID(str(value)) if not isinstance(value, uuid.UUID) else value


@router.get("")
async def list_orders(
    state: Optional[str] = None,
    limit: int = Query(50, le=100),
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = (
        select(Order)
        .options(
            selectinload(Order.customer),
            selectinload(Order.master_product),
            selectinload(Order.supplier),
            selectinload(Order.invoice),
        )
        .order_by(desc(Order.created_at))
        .limit(limit)
        .offset(offset)
    )
    if state:
        stmt = stmt.where(Order.state == state)

    res = await session.execute(stmt)
    orders = res.scalars().all()

    return [
        {
            "id": str(o.id),
            "order_number": o.order_number,
            "customer_telegram_id": o.customer.telegram_id if o.customer else None,
            "customer_username": o.customer.telegram_username if o.customer else None,
            "product_title": o.master_product.title_ar if o.master_product else None,
            "supplier_name": o.supplier.name if o.supplier else "N/A",
            "winning_supplier": o.supplier.name if o.supplier else None,
            "state": o.state.value,
            "supplier_cost": float(o.supplier_cost) if o.supplier_cost else None,
            "our_price": float(o.our_price),
            "profit_margin": float(o.profit_margin) if o.profit_margin else None,
            "gas_fee_actual": float(o.gas_fee_actual) if o.gas_fee_actual else None,
            "payment_method": o.invoice.payment_method.value if o.invoice else None,
            "tx_hash": (o.invoice.tx_hash if o.invoice else None),
            "delivered_product": o.delivered_product,
            "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
            "completed_at": o.completed_at.isoformat() if o.completed_at else None,
            "created_at": o.created_at.isoformat(),
        }
        for o in orders
    ]


@router.get("/{order_id}")
async def get_order_details(
    order_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = (
        select(Order)
        .where(Order.id == _uid(order_id))
        .options(
            selectinload(Order.customer),
            selectinload(Order.master_product),
            selectinload(Order.supplier),
            selectinload(Order.invoice),
            selectinload(Order.state_logs),
            selectinload(Order.jit_transactions),
        )
    )
    res = await session.execute(stmt)
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    timeline = [
        {
            "from_state": log.from_state.value if log.from_state else None,
            "to_state": log.to_state.value,
            "trigger_source": log.trigger_source,
            "details": log.details,
            "created_at": log.created_at.isoformat(),
        }
        for log in sorted(order.state_logs, key=lambda x: x.created_at)
    ]

    jit_txs = [
        {
            "id": str(tx.id),
            "network": tx.payment_network.value,
            "tx_hash": tx.tx_hash,
            "amount": float(tx.amount),
            "state": tx.state.value,
            "created_at": tx.created_at.isoformat(),
        }
        for tx in order.jit_transactions
    ]

    return {
        "id": str(order.id),
        "order_number": order.order_number,
        "customer": {
            "telegram_id": order.customer.telegram_id,
            "username": order.customer.telegram_username,
            "name": f"{order.customer.first_name or ''} {order.customer.last_name or ''}".strip(),
            "wallet_balance": float(order.customer.wallet_balance),
        },
        "product": {
            "sku": order.master_product.internal_sku,
            "title_ar": order.master_product.title_ar,
            "title_en": order.master_product.title_en,
        },
        "supplier": {
            "name": order.supplier.name if order.supplier else None,
            "cost": float(order.supplier_cost) if order.supplier_cost else None,
        },
        "state": order.state.value,
        "financials": {
            "our_price": float(order.our_price),
            "supplier_cost": float(order.supplier_cost or 0),
            "profit_margin": float(order.profit_margin or 0),
            "gas_fee_actual": float(order.gas_fee_actual or 0),
        },
        "delivered_payload": order.delivered_product,
        "timeline": timeline,
        "jit_transactions": jit_txs,
    }


# ---------------- Retry JIT + 1-Click Refund ----------------
@router.post("/{order_id}/retry-jit")
async def retry_jit_order(
    order_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Safely re-enqueue FAILED_MANUAL_REVIEW (or payment_confirmed) into ARQ JIT."""
    try:
        return await order_admin_service.retry_jit(
            session,
            order_id,
            reviewer=admin.username,
            admin_user_id=admin.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


class OrderRefundDTO(BaseModel):
    reason: str = "Admin 1-click refund"


@router.post("/{order_id}/refund")
async def refund_order_to_wallet(
    order_id: str,
    dto: OrderRefundDTO = OrderRefundDTO(),
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Atomic wallet refund → FAILED_REFUNDED (double-refund rejected)."""
    try:
        return await order_admin_service.refund_to_wallet(
            session,
            order_id,
            reviewer=admin.username,
            admin_user_id=admin.id,
            reason=dto.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------------- Sham Cash Queue & Actions ----------------
@router.get("/sham-cash/pending")
async def list_pending_sham_cash_invoices(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    """Returns queue of customers who uploaded receipts for manual Sham Cash verification."""
    return await sham_cash_service.list_pending(session)


@router.get("/{order_id}/receipt")
async def get_order_receipt(
    order_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Proxy Telegram receipt (photo/PDF) for Dashboard preview/download."""
    stmt = (
        select(Order)
        .where(Order.id == _uid(order_id))
        .options(selectinload(Order.invoice))
    )
    order = (await session.execute(stmt)).scalar_one_or_none()
    if not order or not order.invoice or not order.invoice.sham_cash_receipt_photo_file_id:
        raise HTTPException(status_code=404, detail="Receipt not found")
    file_id = order.invoice.sham_cash_receipt_photo_file_id
    rtype = (order.invoice.receipt_type or "photo").lower()
    try:
        content, path = await download_telegram_file(file_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Telegram file fetch failed: {e}")
    media = "application/pdf" if rtype == "pdf" or (path and str(path).endswith(".pdf")) else "image/jpeg"
    filename = f"receipt_{order.order_number}.{'pdf' if 'pdf' in media else 'jpg'}"
    return Response(
        content=content,
        media_type=media,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/{order_id}/sham-cash-approve")
async def approve_sham_cash_payment(
    order_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    """Admin confirms Sham Cash receipt -> triggers automated JIT executor immediately."""
    try:
        return await sham_cash_service.approve(
            session,
            order_id=order_id,
            reviewer=admin.username,
            admin_user_id=admin.id,
            trigger_jit=True,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{order_id}/sham-cash-reject")
async def reject_sham_cash_payment(
    order_id: str,
    reason: str = Query("Receipt invalid or payment not received"),
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    try:
        return await sham_cash_service.reject(
            session,
            order_id=order_id,
            reviewer=admin.username,
            reason=reason,
            admin_user_id=admin.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# Dedicated /sham-cash alias router
sham_cash_router = APIRouter(prefix="/sham-cash", tags=["Sham Cash Admin"])
sham_cash_router.add_api_route("/pending", list_pending_sham_cash_invoices, methods=["GET"])
sham_cash_router.add_api_route("/{order_id}/approve", approve_sham_cash_payment, methods=["POST"])
sham_cash_router.add_api_route("/{order_id}/reject", reject_sham_cash_payment, methods=["POST"])
