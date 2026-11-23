"""Shared product + pricing admin ops — Dashboard + Admin Bot."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.models.entities import (
    MasterProduct, ProductSupplierLink, PricingRule, AuditLog, Category,
)
from src.core.models.enums import PricingScope, PricingRuleType
from src.core.services.pricing_service import pricing_service
from src.core.redis import event_bus
from src.core.services.audit_helpers import merge_audit_new_values


def _uid(value: Any):
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class ProductAdminService:
    async def list_products(
        self, session: AsyncSession, *, offset: int = 0, limit: int = 15
    ) -> List[Dict[str, Any]]:
        products = (await session.execute(
            select(MasterProduct)
            .options(selectinload(MasterProduct.supplier_links))
            .order_by(MasterProduct.sort_order.asc(), MasterProduct.created_at.desc())
            .offset(offset)
            .limit(limit)
        )).scalars().all()

        out = []
        for p in products:
            pricing = await pricing_service.get_best_supplier_and_price(session, str(p.id))
            winner = pricing.get("supplier")
            out.append({
                "id": str(p.id),
                "internal_sku": p.internal_sku,
                "title_ar": p.title_ar,
                "is_active": p.is_active,
                "warranty_days": p.warranty_days or 30,
                "final_price": float(pricing["final_price"]) if pricing.get("final_price") is not None else None,
                "wholesale_cost": float(pricing["wholesale_cost"]) if pricing.get("wholesale_cost") is not None else None,
                "winning_supplier": winner.name if winner else None,
                "is_available": pricing.get("is_available", False),
                "stock": (pricing.get("winner_link").available_quantity
                          if pricing.get("winner_link") else None),
            })
        return out

    async def get_product(self, session: AsyncSession, product_id: Any) -> Dict[str, Any]:
        prod = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == _uid(product_id))
        )).scalar_one_or_none()
        if not prod:
            raise ValueError("Product not found")
        pricing = await pricing_service.get_best_supplier_and_price(session, str(prod.id))
        rule = await pricing_service.get_active_pricing_rule(
            session, product_id=str(prod.id),
            category_id=str(prod.category_id) if prod.category_id else None,
        )
        winner = pricing.get("supplier")
        return {
            "id": str(prod.id),
            "internal_sku": prod.internal_sku,
            "title_ar": prod.title_ar,
            "title_en": prod.title_en,
            "description_ar": prod.description_ar,
            "custom_description_ar": prod.custom_description_ar,
            "warranty_days": prod.warranty_days or 30,
            "is_active": prod.is_active,
            "final_price": float(pricing["final_price"]) if pricing.get("final_price") is not None else None,
            "wholesale_cost": float(pricing["wholesale_cost"]) if pricing.get("wholesale_cost") is not None else None,
            "winning_supplier": winner.name if winner else None,
            "pricing_rule": {
                "id": str(rule.id) if rule else None,
                "rule_type": rule.rule_type.value if rule else None,
                "value": float(rule.value) if rule else None,
                "scope": rule.scope.value if rule else None,
            } if rule else None,
        }

    async def list_categories(self, session: AsyncSession) -> List[Dict[str, Any]]:
        rows = (await session.execute(
            select(Category).order_by(Category.sort_order.asc(), Category.name_ar.asc())
        )).scalars().all()
        return [
            {
                "id": str(c.id),
                "name_ar": c.name_ar,
                "name_en": c.name_en,
                "icon_emoji": c.icon_emoji or "📦",
                "is_active": c.is_active,
            }
            for c in rows
        ]

    async def create_category(
        self,
        session: AsyncSession,
        *,
        name_ar: str,
        name_en: Optional[str] = None,
        icon_emoji: str = "📦",
        reviewer: str = "admin",
    ) -> Dict[str, Any]:
        cat = Category(
            name_ar=(name_ar or "").strip(),
            name_en=(name_en or name_ar or "").strip(),
            icon_emoji=icon_emoji or "📦",
            is_active=True,
            sort_order=0,
        )
        session.add(cat)
        await session.commit()
        await session.refresh(cat)
        await event_bus.publish_config_change("categories", str(cat.id))
        return {"id": str(cat.id), "name_ar": cat.name_ar, "name_en": cat.name_en}

    async def create_product(
        self,
        session: AsyncSession,
        *,
        title_ar: str,
        title_en: Optional[str] = None,
        category_id: Optional[Any] = None,
        retail_price: Decimal,
        wholesale_cost: Optional[Decimal] = None,
        description_ar: Optional[str] = None,
        warranty_days: int = 30,
        reviewer: str = "admin",
    ) -> Dict[str, Any]:
        sku = f"SKU-{uuid.uuid4().hex[:8].upper()}"
        prod = MasterProduct(
            internal_sku=sku,
            title_ar=(title_ar or "").strip(),
            title_en=(title_en or title_ar or "").strip(),
            category_id=_uid(category_id) if category_id else None,
            description_ar=description_ar,
            custom_description_ar=description_ar,
            warranty_days=int(warranty_days or 30),
            is_active=True,
            cached_final_price=Decimal(str(retail_price)),
            cached_best_price=Decimal(str(wholesale_cost or 0)),
            cached_in_stock=True,
        )
        session.add(prod)
        await session.flush()
        # Floor pricing rule = retail price
        session.add(PricingRule(
            scope=PricingScope.PRODUCT,
            scope_id=prod.id,
            rule_type=PricingRuleType.FLOOR,
            value=Decimal(str(retail_price)),
            gas_buffer_enabled=False,
            is_active=True,
            priority=100,
        ))
        session.add(AuditLog(
            action="product_create",
            entity_type="master_product",
            entity_id=prod.id,
            new_values=merge_audit_new_values(reviewer, {
                "title_ar": prod.title_ar,
                "retail_price": float(retail_price),
                "sku": sku,
            }),
        ))
        await session.commit()
        await event_bus.publish_config_change("products", str(prod.id))
        return await self.get_product(session, prod.id)

    async def delete_product(
        self,
        session: AsyncSession,
        product_id: Any,
        *,
        reviewer: str = "admin",
    ) -> Dict[str, Any]:
        """Soft-delete: deactivate so order history FKs stay intact."""
        prod = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == _uid(product_id))
        )).scalar_one_or_none()
        if not prod:
            raise ValueError("Product not found")
        prod.is_active = False
        prod.is_published_to_channel = False
        session.add(AuditLog(
            action="product_delete",
            entity_type="master_product",
            entity_id=prod.id,
            new_values=merge_audit_new_values(reviewer, {
                "title_ar": prod.title_ar,
                "deleted": True,
                "soft": True,
            }),
        ))
        await session.commit()
        await event_bus.publish_config_change("products", str(prod.id))
        return {"status": "ok", "deleted": str(prod.id), "soft": True}

    async def update_product(
        self,
        session: AsyncSession,
        product_id: Any,
        *,
        title_ar: Optional[str] = None,
        description_ar: Optional[str] = None,
        warranty_days: Optional[int] = None,
        is_active: Optional[bool] = None,
        photo_file_id: Optional[str] = None,
        photo_url: Optional[str] = None,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        prod = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == _uid(product_id))
        )).scalar_one_or_none()
        if not prod:
            raise ValueError("Product not found")

        old = {
            "title_ar": prod.title_ar,
            "description_ar": prod.description_ar,
            "warranty_days": prod.warranty_days,
            "is_active": prod.is_active,
            "photo_file_id": prod.photo_file_id,
        }
        if title_ar is not None:
            prod.title_ar = title_ar.strip()
        if description_ar is not None:
            prod.description_ar = description_ar
            prod.custom_description_ar = description_ar
        if warranty_days is not None:
            prod.warranty_days = int(warranty_days)
        if is_active is not None:
            prod.is_active = bool(is_active)
        if photo_file_id is not None:
            prod.photo_file_id = photo_file_id
        if photo_url is not None:
            prod.photo_url = photo_url

        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="product_update",
            entity_type="master_product",
            entity_id=prod.id,
            old_values=old,
            new_values=merge_audit_new_values(reviewer, {
                "title_ar": prod.title_ar,
                "description_ar": prod.description_ar,
                "warranty_days": prod.warranty_days,
                "is_active": prod.is_active,
                "photo_file_id": prod.photo_file_id,
            }),
        ))
        await session.commit()
        await event_bus.publish_config_change("products", str(prod.id))
        return await self.get_product(session, prod.id)

    async def upsert_product_pricing_rule(
        self,
        session: AsyncSession,
        product_id: Any,
        *,
        rule_type: PricingRuleType,
        value: Decimal,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        pid = _uid(product_id)
        prod = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == pid)
        )).scalar_one_or_none()
        if not prod:
            raise ValueError("Product not found")

        rule = (await session.execute(
            select(PricingRule).where(
                PricingRule.scope == PricingScope.PRODUCT,
                PricingRule.scope_id == pid,
                PricingRule.is_active == True,  # noqa: E712
            ).order_by(PricingRule.priority.desc()).limit(1)
        )).scalar_one_or_none()

        old_vals = None
        if rule:
            old_vals = {"rule_type": rule.rule_type.value, "value": float(rule.value)}
            rule.rule_type = rule_type
            rule.value = Decimal(str(value))
            rule.gas_buffer_enabled = False
        else:
            rule = PricingRule(
                scope=PricingScope.PRODUCT,
                scope_id=pid,
                rule_type=rule_type,
                value=Decimal(str(value)),
                gas_buffer_enabled=False,
                is_active=True,
                priority=100,
            )
            session.add(rule)

        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="pricing_rule_upsert",
            entity_type="master_product",
            entity_id=pid,
            old_values=old_vals,
            new_values=merge_audit_new_values(reviewer, {
                "rule_type": rule_type.value,
                "value": float(value),
            }),
        ))
        await session.commit()
        await event_bus.publish_config_change("pricing", str(pid))
        await event_bus.publish_config_change("products", str(pid))
        return await self.get_product(session, pid)


product_admin_service = ProductAdminService()
