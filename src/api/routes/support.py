from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.models.entities import SupportTicket, TicketMessage, Order, Customer, AdminUser, WalletTransaction
from src.core.models.enums import TicketStatus, OrderState, WalletTxType
from src.core.services.order_service import order_service
from src.core.redis import event_bus
from src.api.routes.auth import get_current_admin

router = APIRouter(prefix="/support", tags=["Support & Helpdesk"])


def _as_utc(value):
    """SQLite returns naive datetimes; never compare them with aware UTC now."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class TicketReplyDTO(BaseModel):
    message_text: str
    mark_as_resolved: bool = False


class TicketMessageResponseDTO(BaseModel):
    id: str
    sender_type: str
    sender_name: Optional[str]
    message_text: str
    attachment_file_id: Optional[str]
    created_at: str


class TicketSummaryDTO(BaseModel):
    id: str
    ticket_short_id: str
    customer_id: str
    customer_name: str
    telegram_id: int
    telegram_username: Optional[str]
    order_id: Optional[str]
    order_number: Optional[str]
    subject: str
    status: str
    priority: int
    created_at: str
    last_message_text: Optional[str] = None
    is_warranty_expired: bool = False
    warranty_expires_at: Optional[str] = None


@router.get("/tickets", response_model=List[TicketSummaryDTO])
async def list_tickets(
    status_filter: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = (
        select(SupportTicket)
        .options(
            selectinload(SupportTicket.customer),
            selectinload(SupportTicket.order),
            selectinload(SupportTicket.messages)
        )
        .order_by(desc(SupportTicket.created_at))
    )
    if status_filter:
        stmt = stmt.where(SupportTicket.status == status_filter)

    res = await session.execute(stmt)
    tickets = res.scalars().all()

    output = []
    now_utc = datetime.now(timezone.utc)
    for t in tickets:
        last_msg = t.messages[-1].message_text if t.messages else None
        is_expired = False
        w_exp_iso = None
        if t.order and t.order.warranty_expires_at:
            w_exp_iso = t.order.warranty_expires_at.isoformat()
            w_dt = t.order.warranty_expires_at if t.order.warranty_expires_at.tzinfo else t.order.warranty_expires_at.replace(tzinfo=timezone.utc)
            if now_utc > w_dt:
                is_expired = True

        output.append(
            TicketSummaryDTO(
                id=str(t.id),
                ticket_short_id=str(t.id)[:8],
                customer_id=str(t.customer_id),
                customer_name=t.customer.first_name or "Unknown",
                telegram_id=t.customer.telegram_id,
                telegram_username=t.customer.telegram_username,
                order_id=str(t.order_id) if t.order_id else None,
                order_number=t.order.order_number if t.order else None,
                subject=t.subject,
                status=t.status.value,
                priority=t.priority,
                created_at=t.created_at.isoformat(),
                last_message_text=last_msg,
                is_warranty_expired=is_expired,
                warranty_expires_at=w_exp_iso,
            )
        )
    return output


@router.get("/tickets/{ticket_id}")
async def get_ticket_detail(
    ticket_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.id == ticket_id)
        .options(
            selectinload(SupportTicket.customer),
            selectinload(SupportTicket.order).selectinload(Order.master_product),
            selectinload(SupportTicket.messages)
        )
    )
    ticket = (await session.execute(stmt)).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Support ticket not found")

    messages_data = [
        {
            "id": str(m.id),
            "sender_type": m.sender_type,
            "sender_name": m.sender_name,
            "message_text": m.message_text,
            "attachment_file_id": m.attachment_file_id,
            "created_at": m.created_at.isoformat(),
        }
        for m in sorted(ticket.messages, key=lambda x: x.created_at)
    ]

    order_info = None
    is_expired = False
    if ticket.order:
        if ticket.order.warranty_expires_at:
            if datetime.now(timezone.utc) > _as_utc(ticket.order.warranty_expires_at):
                is_expired = True

        order_info = {
            "id": str(ticket.order.id),
            "order_number": ticket.order.order_number,
            "product_title": ticket.order.master_product.title_ar if ticket.order.master_product else "",
            "our_price": float(ticket.order.our_price),
            "delivered_product": ticket.order.delivered_product,
            "delivered_at": ticket.order.delivered_at.isoformat() if ticket.order.delivered_at else None,
            "warranty_expires_at": ticket.order.warranty_expires_at.isoformat() if ticket.order.warranty_expires_at else None,
            "is_warranty_expired": is_expired,
            "state": ticket.order.state.value,
        }

    return {
        "id": str(ticket.id),
        "ticket_short_id": str(ticket.id)[:8],
        "subject": ticket.subject,
        "status": ticket.status.value,
        "priority": ticket.priority,
        "is_warranty_expired": is_expired,
        "customer": {
            "id": str(ticket.customer.id),
            "name": ticket.customer.first_name,
            "telegram_id": ticket.customer.telegram_id,
            "telegram_username": ticket.customer.telegram_username,
            "wallet_balance": float(ticket.customer.wallet_balance),
        },
        "order": order_info,
        "messages": messages_data,
        "created_at": ticket.created_at.isoformat(),
    }


@router.post("/tickets/{ticket_id}/reply")
async def reply_to_ticket(
    ticket_id: str,
    dto: TicketReplyDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.id == ticket_id)
        .options(selectinload(SupportTicket.customer))
    )
    ticket = (await session.execute(stmt)).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    msg = TicketMessage(
        ticket_id=ticket.id,
        sender_type="admin",
        sender_name=admin.display_name or admin.username or "Admin Support",
        message_text=dto.message_text,
    )
    session.add(msg)

    if dto.mark_as_resolved:
        ticket.status = TicketStatus.RESOLVED
        ticket.resolved_at = datetime.now(timezone.utc)
    else:
        ticket.status = TicketStatus.IN_PROGRESS

    ticket.updated_at = datetime.now(timezone.utc)
    await session.commit()

    # Tunnel reply directly to customer's Telegram DM via Redis event bus
    await event_bus.publish(
        "bot:ticket_reply",
        {
            "telegram_id": ticket.customer.telegram_id,
            "ticket_id": str(ticket.id),
            "reply_text": dto.message_text,
        }
    )

    return {"status": "ok", "message": "Reply dispatched to customer Telegram"}


@router.post("/tickets/{ticket_id}/resolve-refund")
async def resolve_ticket_with_wallet_refund(
    ticket_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    """1-Click Refund & Close: Credits customer wallet balance, marks order refunded, and resolves ticket."""
    import uuid
    t_uuid = uuid.UUID(ticket_id) if isinstance(ticket_id, str) else ticket_id
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.id == t_uuid)
        .options(selectinload(SupportTicket.customer), selectinload(SupportTicket.order))
    )
    ticket = (await session.execute(stmt)).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if not ticket.order:
        raise HTTPException(status_code=400, detail="Ticket has no associated order to refund")

    order = ticket.order

    # --- Refund safety gates (double-refund / unpaid-order / replay protection) ---
    # A refund is only legal once, on a ticket that is still open, for an order that
    # was actually paid for and is not already refunded.
    if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
        raise HTTPException(status_code=409, detail="Ticket already resolved; refund already processed")
    unpaid_states = (OrderState.CREATED, OrderState.AWAITING_PAYMENT,
                     OrderState.SHAM_CASH_REVIEW, OrderState.QUOTE_LOCKED)
    if order.state in unpaid_states:
        raise HTTPException(status_code=400, detail="Order was never paid; refund is not allowed")
    if order.state in (OrderState.FAILED_REFUNDED,):
        raise HTTPException(status_code=409, detail="Order already refunded")

    # Distributed guard spanning check + act, so two concurrent admin tabs cannot both pass.
    lock_key = f"lock:refund:{str(order.id)}"
    lock_token = await event_bus.acquire_lock_with_token(lock_key, ttl_seconds=30)
    if not lock_token:
        raise HTTPException(status_code=409, detail="A refund for this order is already in progress")

    refund_amount = order.our_price

    try:
        # Re-read committed state under the lock (defense in depth against stale ORM data).
        fresh = (await session.execute(
            select(Order.state).where(Order.id == order.id).with_for_update()
        )).scalar_one_or_none()
        if fresh is None or fresh in (OrderState.FAILED_REFUNDED,) or fresh in unpaid_states:
            raise HTTPException(status_code=409, detail="Order state changed; refund rejected")

        # --- Anti money-farming: reverse cashback + referral credited for this order ---
        # Without this, a referring customer could buy -> refund -> repeat and mint
        # free referral/cashback balance on every cycle.
        incentive_rows = (await session.execute(
            select(WalletTransaction).where(
                and_(
                    WalletTransaction.order_id == order.id,
                    WalletTransaction.tx_type.in_(
                        [WalletTxType.CASHBACK, WalletTxType.REFERRAL_COMMISSION]
                    ),
                    WalletTransaction.amount > 0,
                )
            )
        )).scalars().all()
        clawed_back = Decimal("0.00")
        for incentive_tx in incentive_rows:
            # Never touch the lazy relationship in async context; query the balance directly.
            remaining = (await session.execute(
                select(Customer.wallet_balance).where(Customer.id == incentive_tx.customer_id)
            )).scalar_one_or_none() or Decimal("0.00")
            reversible = min(incentive_tx.amount, max(Decimal("0.00"), remaining))
            if reversible > 0:
                await order_service.debit_customer_wallet(
                    session=session,
                    customer_id=str(incentive_tx.customer_id),
                    amount=reversible,
                    tx_type=WalletTxType.INCENTIVE_CLAWBACK,
                    description=f"استرداد كاش باك/عمولة إحالة ملغية للطلب #{order.order_number}",
                    order_id=str(order.id),
                )
                clawed_back += reversible

        # 1. Credit wallet
        await order_service.credit_customer_wallet(
            session=session,
            customer_id=str(ticket.customer.id),
            amount=refund_amount,
            tx_type=WalletTxType.REFUND_CREDIT,
            description=f"تعويض فوري من الدعم للتذكرة #T-{str(ticket.id)[:8]} (طلب #{order.order_number})",
            order_id=str(order.id),
        )

        # 2. Mark order and ticket resolved via the state-machine service (auditable log)
        await order_service.transition_order_state(
            session, order, OrderState.FAILED_REFUNDED,
            f"admin_refund by {admin.username} ticket #{str(ticket.id)[:8]}",
        )
        ticket.status = TicketStatus.RESOLVED
        ticket.resolved_at = datetime.now(timezone.utc)

        # Add resolution message in thread
        system_msg = TicketMessage(
            ticket_id=ticket.id,
            sender_type="system",
            sender_name="System Automation",
            message_text=(
                f"تم إصدار تعويض مالي فوري بقيمة ${refund_amount} إلى رصيد محفظة العميل "
                f"وإغلاق التذكرة بنجاح من قبل {admin.display_name or 'Admin'}."
            ),
        )
        session.add(system_msg)
        await session.commit()

        # 3. Notify user on Telegram
        await event_bus.publish(
            "bot:notify_refund",
            {
                "telegram_id": ticket.customer.telegram_id,
                "order_number": order.order_number,
                "amount": float(refund_amount),
            },
        )

        return {
            "status": "ok",
            "refund_amount": float(refund_amount),
            "incentives_clawed_back": float(clawed_back),
            "message": f"Successfully refunded ${refund_amount} and closed ticket.",
        }
    finally:
        await event_bus.release_lock(lock_key, token=lock_token)
