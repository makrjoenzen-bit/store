"""Post-fulfillment Arabic execution report card for Admin Telegram."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from loguru import logger

from src.core.models.entities import Order
from src.core.redis import event_bus


def _uid(value: Any):
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _as_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class FulfillmentReportService:
    def format_card(self, data: Dict[str, Any]) -> str:
        outcome = data.get("outcome") or "COMPLETED"
        emoji = "✅" if outcome == "COMPLETED" else "⚠️"
        title = "تقرير تنفيذ ناجح" if outcome == "COMPLETED" else f"تقرير فشل — {outcome}"
        delivery_ms = data.get("delivery_time_ms")
        delivery_line = (
            f"⏱ زمن التسليم: `{delivery_ms} ms`\n" if delivery_ms is not None else ""
        )
        return (
            f"{emoji} **{title}**\n\n"
            f"🔖 الطلب: `{data.get('order_number')}`\n"
            f"👤 العميل: {data.get('customer_label') or '—'}\n"
            f"📦 المنتج: {data.get('product_title') or '—'} × `{data.get('quantity') or 1}`\n\n"
            f"💵 سعر البيع: `${data.get('sale_price', 0):.2f}`\n"
            f"🔌 المورد: {data.get('supplier_name') or '—'}\n"
            f"🏷 تكلفة الجملة: `${data.get('wholesale_cost', 0):.2f}`\n"
            f"⛽ الغاز/الرسوم: `${data.get('gas_fees', 0):.4f}`\n"
            f"📈 **صافي الربح: `${data.get('net_profit', 0):.2f}`**\n"
            f"{delivery_line}"
        )

    def build_payload_from_order(self, order: Order, *, outcome: str) -> Dict[str, Any]:
        sale = Decimal(str(order.our_price or 0))
        cost = Decimal(str(order.supplier_cost or 0))
        gas = Decimal(str(order.gas_fee_actual or 0))
        net = sale - cost - gas
        if order.profit_margin is not None and outcome == "COMPLETED":
            net = Decimal(str(order.profit_margin))

        delivery_ms = None
        created = _as_utc(order.created_at)
        done = _as_utc(order.completed_at or order.delivered_at)
        if created and done:
            delivery_ms = int((done - created).total_seconds() * 1000)

        cust = order.customer
        if cust:
            label = f"@{cust.telegram_username}" if cust.telegram_username else (
                cust.first_name or f"tg:{cust.telegram_id}"
            )
        else:
            label = "—"

        return {
            "outcome": outcome,
            "order_id": str(order.id),
            "order_number": order.order_number,
            "customer_label": label,
            "product_title": order.master_product.title_ar if order.master_product else None,
            "quantity": order.quantity or 1,
            "sale_price": float(sale),
            "supplier_name": order.supplier.name if order.supplier else None,
            "wholesale_cost": float(cost),
            "gas_fees": float(gas),
            "net_profit": float(net),
            "delivery_time_ms": delivery_ms,
        }

    async def dispatch_for_order(
        self,
        session: AsyncSession,
        *,
        order_id: Any,
        outcome: str,
    ) -> Dict[str, Any]:
        order = (await session.execute(
            select(Order)
            .where(Order.id == _uid(order_id))
            .options(
                selectinload(Order.customer),
                selectinload(Order.master_product),
                selectinload(Order.supplier),
            )
        )).scalar_one_or_none()
        if not order:
            raise ValueError("Order not found")

        payload = self.build_payload_from_order(order, outcome=outcome)
        text = self.format_card(payload)
        await event_bus.publish("alerts:stream", {
            "type": "fulfillment_report",
            "title": f"Fulfillment {outcome}",
            "message": text,
            "level": "info" if outcome == "COMPLETED" else "warning",
            "meta": {**payload, "alert_type": "fulfillment_report"},
        })
        # Also dedicated channel for admin live feed rendering
        await event_bus.publish("admin:fulfillment_report", payload)
        logger.info(f"Fulfillment report dispatched for {order.order_number} ({outcome})")
        return payload


fulfillment_report_service = FulfillmentReportService()
