import uuid
from decimal import Decimal
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.models.entities import PricingRule, MasterProduct, ProductSupplierLink, Supplier, AdminUser
from src.core.models.enums import PricingScope, PricingRuleType, SupplierStatus
from src.core.services.pricing_service import pricing_service
from src.api.routes.auth import get_current_admin
from src.core.redis import event_bus

router = APIRouter(prefix="/pricing", tags=["Pricing Engine"])


def _uid(value):
    if value is None:
        return None
    return uuid.UUID(str(value)) if not isinstance(value, uuid.UUID) else value


class PricingRuleCreateDTO(BaseModel):
    scope: PricingScope
    scope_id: Optional[str] = None
    rule_type: PricingRuleType
    value: Decimal
    gas_buffer_enabled: bool = True
    gas_buffer_fixed: Optional[Decimal] = Decimal("0.08")
    priority: int = 0
    is_active: bool = True


@router.get("/rules")
async def list_pricing_rules(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(PricingRule).order_by(PricingRule.scope.asc(), PricingRule.priority.desc())
    res = await session.execute(stmt)
    rules = res.scalars().all()
    return [
        {
            "id": str(r.id),
            "scope": r.scope.value,
            "scope_id": str(r.scope_id) if r.scope_id else None,
            "rule_type": r.rule_type.value,
            "value": float(r.value),
            "gas_buffer_enabled": r.gas_buffer_enabled,
            "gas_buffer_fixed": float(r.gas_buffer_fixed) if r.gas_buffer_fixed else None,
            "priority": r.priority,
            "is_active": r.is_active,
        }
        for r in rules
    ]


@router.post("/rules")
async def create_pricing_rule(
    dto: PricingRuleCreateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    rule = PricingRule(
        scope=dto.scope,
        scope_id=_uid(dto.scope_id),
        rule_type=dto.rule_type,
        value=dto.value,
        gas_buffer_enabled=dto.gas_buffer_enabled,
        gas_buffer_fixed=dto.gas_buffer_fixed,
        priority=dto.priority,
        is_active=dto.is_active,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await event_bus.publish_config_change("pricing_rules", str(rule.id))
    return {"status": "ok", "id": str(rule.id)}


@router.put("/rules/{rule_id}")
async def update_pricing_rule(
    rule_id: str,
    dto: PricingRuleCreateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(PricingRule).where(PricingRule.id == _uid(rule_id))
    res = await session.execute(stmt)
    rule = res.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Pricing rule not found")

    rule.scope = dto.scope
    rule.scope_id = _uid(dto.scope_id)
    rule.rule_type = dto.rule_type
    rule.value = dto.value
    rule.gas_buffer_enabled = dto.gas_buffer_enabled
    rule.gas_buffer_fixed = dto.gas_buffer_fixed
    rule.priority = dto.priority
    rule.is_active = dto.is_active

    await session.commit()
    await session.refresh(rule)
    # Invalidate cache and broadcast to Dashboard + Telegram Bot via Redis Pub/Sub
    await event_bus.publish_config_change("pricing_rules", str(rule.id))
    return {"status": "ok", "id": str(rule.id)}


@router.delete("/rules/{rule_id}")
async def delete_pricing_rule(
    rule_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = delete(PricingRule).where(PricingRule.id == _uid(rule_id))
    await session.execute(stmt)
    await session.commit()
    await event_bus.publish_config_change("pricing_rules", rule_id)
    return {"status": "ok", "message": "Rule deleted"}


@router.get("/matrix")
async def get_price_comparison_matrix(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    """
    Live Price & Stock Comparison Matrix.
    Displays every master SKU, all connected suppliers with their current wholesale costs,
    which supplier is currently winning, and our computed retail price.
    """
    # Fetch all master products with their supplier links
    stmt = (
        select(MasterProduct)
        .options(
            selectinload(MasterProduct.supplier_links).selectinload(ProductSupplierLink.supplier)
        )
        .where(MasterProduct.is_active == True)
        .order_by(MasterProduct.sort_order.asc())
    )
    res = await session.execute(stmt)
    products = res.scalars().all()

    matrix_rows = []
    for p in products:
        pricing_info = await pricing_service.get_best_supplier_and_price(session, str(p.id))

        winner_link = pricing_info.get("winner_link")
        suppliers_breakdown = []
        for link in p.supplier_links:
            suppliers_breakdown.append({
                "supplier_id": str(link.supplier_id),
                "supplier_name": link.supplier.name if link.supplier else "Unknown",
                "cost": float(link.last_known_cost) if link.last_known_cost is not None else None,
                "is_in_stock": link.is_in_stock,
                "available_quantity": link.available_quantity,
                "is_active": link.supplier.status == SupplierStatus.ACTIVE if link.supplier else False,
                "is_winning": bool(winner_link and str(link.id) == str(winner_link.id)),
            })

        matrix_rows.append({
            "master_product_id": str(p.id),
            "internal_sku": p.internal_sku,
            "title_ar": p.title_ar,
            "title_en": p.title_en,
            "is_available": pricing_info["is_available"],
            "winning_supplier": pricing_info["supplier"].name if pricing_info.get("supplier") else None,
            "winning_supplier_id": str(pricing_info["supplier"].id) if pricing_info.get("supplier") else None,
            "wholesale_cost": float(pricing_info["wholesale_cost"]) if pricing_info.get("wholesale_cost") else None,
            "final_price": float(pricing_info["final_price"]) if pricing_info.get("final_price") else None,
            "profit_margin": float(pricing_info["profit_margin"]) if pricing_info.get("profit_margin") else None,
            "gas_buffer": float(pricing_info["gas_buffer"]) if pricing_info.get("gas_buffer") else None,
            "suppliers": suppliers_breakdown,
        })

    return matrix_rows
