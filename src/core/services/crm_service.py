"""Shared CRM + Support desk ops — Dashboard + Admin Bot."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select, or_, and_, cast, String, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.models.entities import (
    Customer, SupportTicket, TicketMessage, Order, AuditLog,
)
from src.core.models.enums import (
    WalletTxType, TicketStatus, CustomerStatus,
)
from src.core.services.order_service import order_service
from src.core.services.order_admin_service import order_admin_service
from src.core.redis import event_bus
from src.core.services.audit_helpers import merge_audit_new_values


def _uid(value: Any):
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _as_utc(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class CrmService:
    async def search_customer(self, session: AsyncSession, query: str) -> Dict[str, Any]:
        q = (query or "").strip().lstrip("@")
        if not q:
            raise ValueError("Search query required")

        filters = [Customer.telegram_username.ilike(f"%{q}%")]
        if q.isdigit():
            filters.append(Customer.telegram_id == int(q))
        filters.append(cast(Customer.telegram_id, String).like(f"%{q}%"))

        customer = (await session.execute(
            select(Customer).where(or_(*filters)).limit(1)
        )).scalar_one_or_none()
        if not customer:
            raise ValueError("Customer not found")
        return self._serialize_customer(customer)

    def _serialize_customer(self, c: Customer) -> Dict[str, Any]:
        return {
            "id": str(c.id),
            "telegram_id": c.telegram_id,
            "telegram_username": c.telegram_username,
            "first_name": c.first_name,
            "wallet_balance": float(c.wallet_balance or 0),
            "total_spent": float(c.total_spent or 0),
            "total_orders": c.total_orders or 0,
            "vip_tier": c.vip_tier.value if c.vip_tier else "standard",
            "is_blocked": bool(c.is_blocked),
            "account_status": c.account_status.value if c.account_status else "active",
            "blocked_reason": c.blocked_reason,
        }

    async def adjust_balance(
        self,
        session: AsyncSession,
        customer_id: Any,
        *,
        amount: Decimal,
        direction: str,
        reason: str,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        if direction not in ("credit", "debit"):
            raise ValueError("Direction must be credit or debit")
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValueError("Amount must be positive")
        if not reason or len(reason.strip()) < 3:
            raise ValueError("Reason is required (min 3 chars)")

        customer = (await session.execute(
            select(Customer).where(Customer.id == _uid(customer_id)).with_for_update()
        )).scalar_one_or_none()
        if not customer:
            raise ValueError("Customer not found")

        if direction == "credit":
            tx = await order_service.credit_customer_wallet(
                session=session,
                customer_id=str(customer.id),
                amount=amount,
                tx_type=WalletTxType.ADMIN_CREDIT,
                description=f"[Admin: {reviewer}] {reason}",
            )
        else:
            if customer.wallet_balance < amount:
                raise ValueError(f"Insufficient balance: ${customer.wallet_balance}")
            tx = await order_service.debit_customer_wallet(
                session=session,
                customer_id=str(customer.id),
                amount=amount,
                tx_type=WalletTxType.ADMIN_DEBIT,
                description=f"[Admin: {reviewer}] {reason}",
            )

        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action=f"balance_{direction}",
            entity_type="customer",
            entity_id=customer.id,
            old_values={
                "wallet_balance": float(
                    tx.balance_after - amount if direction == "credit" else tx.balance_after + amount
                )
            },
            new_values=merge_audit_new_values(reviewer, {
                "wallet_balance": float(tx.balance_after),
                "amount": float(amount),
                "reason": reason,
            }),
        ))
        await session.commit()

        direction_ar = "إضافة رصيد" if direction == "credit" else "خصم من الرصيد"
        sign = "+" if direction == "credit" else "-"
        try:
            await event_bus.publish("bot:send_message", {
                "telegram_id": customer.telegram_id,
                "text": (
                    f"💰 **تحديث الرصيد من الإدارة:**\n\n"
                    f"📌 **العملية:** {direction_ar}\n"
                    f"💵 **المبلغ:** `{sign}${amount:.2f} USD`\n"
                    f"📝 **السبب:** {reason}\n"
                    f"👛 **رصيدك الجديد:** `${tx.balance_after:.2f} USD`"
                ),
            })
        except Exception:
            pass

        return {
            "status": "ok",
            "customer_id": str(customer.id),
            "new_balance": float(tx.balance_after),
            "tx_id": str(tx.id),
        }

    async def set_ban(
        self,
        session: AsyncSession,
        customer_id: Any,
        *,
        ban: bool,
        reason: Optional[str] = None,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        customer = (await session.execute(
            select(Customer).where(Customer.id == _uid(customer_id))
        )).scalar_one_or_none()
        if not customer:
            raise ValueError("Customer not found")

        old = customer.account_status.value if customer.account_status else "active"
        if ban:
            customer.is_blocked = True
            customer.account_status = CustomerStatus.BANNED
            customer.blocked_reason = reason or "Banned by admin"
            try:
                redis = event_bus._redis
                if redis:
                    await redis.set(f"ban:user:{customer.telegram_id}", "1", ex=86400 * 365)
            except Exception:
                pass
        else:
            customer.is_blocked = False
            customer.account_status = CustomerStatus.ACTIVE
            customer.blocked_reason = None
            try:
                redis = event_bus._redis
                if redis:
                    await redis.delete(f"ban:user:{customer.telegram_id}")
            except Exception:
                pass

        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="customer_ban" if ban else "customer_unban",
            entity_type="customer",
            entity_id=customer.id,
            old_values={"account_status": old},
            new_values=merge_audit_new_values(reviewer, {
                "account_status": "banned" if ban else "active",
                "reason": reason,
            }),
        ))
        await session.commit()
        return self._serialize_customer(customer)

    async def list_open_tickets(self, session: AsyncSession, limit: int = 20) -> List[Dict[str, Any]]:
        tickets = (await session.execute(
            select(SupportTicket)
            .where(SupportTicket.status.in_([
                TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER,
            ]))
            .options(
                selectinload(SupportTicket.customer),
                selectinload(SupportTicket.order),
            )
            .order_by(desc(SupportTicket.created_at))
            .limit(limit)
        )).scalars().all()

        now = datetime.now(timezone.utc)
        out = []
        for t in tickets:
            expired = False
            if t.order and t.order.warranty_expires_at:
                if now > _as_utc(t.order.warranty_expires_at):
                    expired = True
            out.append({
                "id": str(t.id),
                "short_id": str(t.id)[:8],
                "subject": t.subject,
                "status": t.status.value,
                "customer_name": t.customer.first_name if t.customer else None,
                "telegram_id": t.customer.telegram_id if t.customer else None,
                "order_number": t.order.order_number if t.order else None,
                "order_id": str(t.order_id) if t.order_id else None,
                "is_warranty_expired": expired,
            })
        return out

    async def reply_ticket(
        self,
        session: AsyncSession,
        ticket_id: Any,
        *,
        message_text: str,
        reviewer: str = "admin",
        mark_resolved: bool = False,
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        ticket = (await session.execute(
            select(SupportTicket)
            .where(SupportTicket.id == _uid(ticket_id))
            .options(selectinload(SupportTicket.customer))
        )).scalar_one_or_none()
        if not ticket:
            raise ValueError("Ticket not found")

        session.add(TicketMessage(
            ticket_id=ticket.id,
            sender_type="admin",
            sender_name=reviewer,
            message_text=message_text,
        ))
        if mark_resolved:
            ticket.status = TicketStatus.RESOLVED
            ticket.resolved_at = datetime.now(timezone.utc)
        else:
            ticket.status = TicketStatus.IN_PROGRESS
        ticket.updated_at = datetime.now(timezone.utc)

        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="ticket_reply",
            entity_type="support_ticket",
            entity_id=ticket.id,
            new_values=merge_audit_new_values(reviewer, {"mark_resolved": mark_resolved}),
        ))
        await session.commit()

        await event_bus.publish("bot:ticket_reply", {
            "telegram_id": ticket.customer.telegram_id,
            "ticket_id": str(ticket.id),
            "reply_text": message_text,
        })
        return {"status": "ok", "ticket_id": str(ticket.id)}

    async def close_ticket(
        self,
        session: AsyncSession,
        ticket_id: Any,
        *,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        ticket = (await session.execute(
            select(SupportTicket).where(SupportTicket.id == _uid(ticket_id))
        )).scalar_one_or_none()
        if not ticket:
            raise ValueError("Ticket not found")
        ticket.status = TicketStatus.CLOSED
        ticket.resolved_at = datetime.now(timezone.utc)
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="ticket_close",
            entity_type="support_ticket",
            entity_id=ticket.id,
            new_values=merge_audit_new_values(reviewer),
        ))
        await session.commit()
        return {"status": "ok", "ticket_id": str(ticket.id)}

    async def resolve_refund_ticket(
        self,
        session: AsyncSession,
        ticket_id: Any,
        *,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        ticket = (await session.execute(
            select(SupportTicket)
            .where(SupportTicket.id == _uid(ticket_id))
            .options(selectinload(SupportTicket.order), selectinload(SupportTicket.customer))
        )).scalar_one_or_none()
        if not ticket:
            raise ValueError("Ticket not found")
        if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
            raise ValueError("Ticket already resolved")
        if not ticket.order_id:
            raise ValueError("Ticket has no associated order")

        oid = ticket.order_id
        tid = ticket.id
        result = await order_admin_service.refund_to_wallet(
            session,
            oid,
            reviewer=reviewer,
            admin_user_id=admin_user_id,
            reason=f"Support ticket #{str(tid)[:8]}",
        )
        # Fresh load after refund commit
        await session.refresh(ticket) if ticket in session else None
        ticket = (await session.execute(
            select(SupportTicket).where(SupportTicket.id == tid)
        )).scalar_one_or_none()
        if ticket:
            ticket.status = TicketStatus.RESOLVED
            ticket.resolved_at = datetime.now(timezone.utc)
            session.add(TicketMessage(
                ticket_id=ticket.id,
                sender_type="system",
                sender_name="System Automation",
                message_text=(
                    f"تم إصدار تعويض ${result['refund_amount']} وإغلاق التذكرة بواسطة {reviewer}."
                ),
            ))
            await session.commit()
        return {**result, "ticket_id": str(tid)}


crm_service = CrmService()
