"""Shared supplier admin ops — Dashboard API + Admin Bot (no duplicated logic)."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models.entities import (
    Supplier, SupplierHealthLog, ProductSupplierLink, AuditLog, MasterProduct,
)
from src.core.models.enums import SupplierStatus, PaymentNetwork, PricingScope, PricingRuleType
from src.core.models.entities import PricingRule
from src.core.security import encrypt_secret
from src.core.adapters.registry import AdapterRegistry
from src.core.redis import event_bus
from src.core.services.audit_helpers import merge_audit_new_values


def _uid(value: Any):
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _serialize(s: Supplier, product_count: int = 0) -> Dict[str, Any]:
    return {
        "id": str(s.id),
        "name": s.name,
        "driver_type": s.driver_type,
        "payment_network": s.payment_network.value if s.payment_network else None,
        "status": s.status.value if s.status else None,
        "status_emoji": "🟢" if s.status == SupplierStatus.ACTIVE else "🔴",
        "priority_weight": s.priority_weight,
        "cached_balance": float(s.cached_balance) if s.cached_balance is not None else None,
        "product_count": product_count,
        "cb_consecutive_failures": s.cb_consecutive_failures,
        "deposit_address": s.deposit_address,
        "notes": s.notes,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


class SupplierAdminService:
    async def list_suppliers(self, session: AsyncSession) -> List[Dict[str, Any]]:
        suppliers = (await session.execute(
            select(Supplier).order_by(Supplier.priority_weight.desc(), Supplier.created_at.desc())
        )).scalars().all()
        counts = {}
        if suppliers:
            rows = (await session.execute(
                select(ProductSupplierLink.supplier_id, func.count(ProductSupplierLink.id))
                .group_by(ProductSupplierLink.supplier_id)
            )).all()
            counts = {str(r[0]): r[1] for r in rows}
        return [_serialize(s, counts.get(str(s.id), 0)) for s in suppliers]

    async def create_supplier(
        self,
        session: AsyncSession,
        *,
        name: str,
        driver_type: str,
        config: Dict[str, Any],
        payment_network: PaymentNetwork = PaymentNetwork.USDT_BEP20,
        deposit_address: Optional[str] = None,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        supplier = Supplier(
            name=name.strip(),
            driver_type=driver_type.strip(),
            config_encrypted=encrypt_secret(json.dumps(config or {})),
            payment_network=payment_network,
            deposit_address=deposit_address,
            status=SupplierStatus.ACTIVE,
        )
        session.add(supplier)
        await session.flush()
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="supplier_create",
            entity_type="supplier",
            entity_id=supplier.id,
            new_values=merge_audit_new_values(reviewer, {"name": name, "driver_type": driver_type}),
        ))
        await session.commit()
        await session.refresh(supplier)
        await event_bus.publish_config_change("suppliers", str(supplier.id))
        return _serialize(supplier, 0)

    async def test_connection(self, session: AsyncSession, supplier_id: Any) -> Dict[str, Any]:
        supplier = (await session.execute(
            select(Supplier).where(Supplier.id == _uid(supplier_id))
        )).scalar_one_or_none()
        if not supplier:
            raise ValueError("Supplier not found")

        adapter = AdapterRegistry.create_from_supplier(supplier)
        health = await adapter.check_health()
        session.add(SupplierHealthLog(
            supplier_id=supplier.id,
            check_type="manual_test",
            is_success=health.is_alive,
            response_time_ms=health.latency_ms,
            http_status=health.http_status,
            error_message=health.error_message,
        ))
        if health.is_alive and health.balance_usd is not None:
            supplier.cached_balance = health.balance_usd
        await session.commit()
        return {
            "supplier_id": str(supplier.id),
            "is_alive": health.is_alive,
            "latency_ms": health.latency_ms,
            "http_status": health.http_status,
            "balance_usd": float(health.balance_usd) if health.balance_usd is not None else None,
            "error_message": health.error_message,
            "status_emoji": "🟢" if health.is_alive else "🔴",
        }

    async def set_status(
        self,
        session: AsyncSession,
        supplier_id: Any,
        *,
        active: bool,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        supplier = (await session.execute(
            select(Supplier).where(Supplier.id == _uid(supplier_id))
        )).scalar_one_or_none()
        if not supplier:
            raise ValueError("Supplier not found")
        old = supplier.status.value
        supplier.status = SupplierStatus.ACTIVE if active else SupplierStatus.INACTIVE
        if active:
            supplier.cb_consecutive_failures = 0
            supplier.cb_last_failure_at = None
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="supplier_toggle",
            entity_type="supplier",
            entity_id=supplier.id,
            old_values={"status": old},
            new_values=merge_audit_new_values(reviewer, {"status": supplier.status.value}),
        ))
        await session.commit()
        await event_bus.publish_config_change("suppliers", str(supplier.id))
        return _serialize(supplier)

    async def get_catalog(self, session: AsyncSession, supplier_id: Any) -> List[Dict[str, Any]]:
        supplier = (await session.execute(
            select(Supplier).where(Supplier.id == _uid(supplier_id))
        )).scalar_one_or_none()
        if not supplier:
            raise ValueError("Supplier not found")
        adapter = AdapterRegistry.create_from_supplier(supplier)
        products = await adapter.fetch_products()
        links = (await session.execute(
            select(ProductSupplierLink).where(ProductSupplierLink.supplier_id == supplier.id)
        )).scalars().all()
        mapped = {link.supplier_product_id: str(link.master_product_id) for link in links}
        return [
            {
                "supplier_product_id": p.supplier_product_id,
                "name": p.name,
                "raw_name": p.raw_name,
                "cost": float(p.cost),
                "is_in_stock": p.is_in_stock,
                "available_quantity": p.available_quantity,
                "is_already_mapped": p.supplier_product_id in mapped,
                "linked_master_product_id": mapped.get(p.supplier_product_id),
            }
            for p in products
        ]

    async def cherry_pick_create(
        self,
        session: AsyncSession,
        *,
        supplier_id: Any,
        supplier_product_id: str,
        title_ar: str,
        pricing_rule_type: PricingRuleType = PricingRuleType.PERCENTAGE,
        pricing_value: Decimal = Decimal("20"),
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        import hashlib

        catalog = await self.get_catalog(session, supplier_id)
        source = next((p for p in catalog if p["supplier_product_id"] == supplier_product_id), None)
        if not source:
            raise ValueError("Product not found in supplier catalog")

        sku_hash = hashlib.md5(f"{supplier_id}:{supplier_product_id}".encode()).hexdigest()[:6].upper()
        internal_sku = f"IMP-{sku_hash}"
        existing = (await session.execute(
            select(MasterProduct).where(MasterProduct.internal_sku == internal_sku)
        )).scalar_one_or_none()
        if existing:
            internal_sku = f"IMP-{sku_hash}-{str(uuid.uuid4())[:4].upper()}"

        cost = Decimal(str(source["cost"]))
        prod = MasterProduct(
            internal_sku=internal_sku,
            title_ar=title_ar.strip(),
            title_en=source.get("raw_name") or source.get("name") or title_ar,
            is_active=True,
            cached_best_price=cost,
            cached_in_stock=bool(source.get("is_in_stock")),
        )
        session.add(prod)
        await session.flush()

        session.add(ProductSupplierLink(
            master_product_id=prod.id,
            supplier_id=_uid(supplier_id),
            supplier_product_id=supplier_product_id,
            supplier_product_name=source.get("raw_name") or source.get("name"),
            last_known_cost=cost,
            is_in_stock=bool(source.get("is_in_stock")),
            available_quantity=source.get("available_quantity"),
        ))
        session.add(PricingRule(
            scope=PricingScope.PRODUCT,
            scope_id=prod.id,
            rule_type=pricing_rule_type,
            value=Decimal(str(pricing_value)),
            gas_buffer_enabled=False,
            is_active=True,
            priority=100,
        ))
        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="product_cherry_pick_create",
            entity_type="master_product",
            entity_id=prod.id,
            new_values=merge_audit_new_values(reviewer, {
                "title_ar": title_ar,
                "supplier_id": str(supplier_id),
                "supplier_product_id": supplier_product_id,
            }),
        ))
        await session.commit()
        await event_bus.publish_config_change("products", str(prod.id))
        return {
            "status": "ok",
            "product_id": str(prod.id),
            "internal_sku": internal_sku,
            "title_ar": prod.title_ar,
            "wholesale_cost": float(cost),
        }

    async def cherry_pick_map(
        self,
        session: AsyncSession,
        *,
        supplier_id: Any,
        supplier_product_id: str,
        master_product_id: Any,
        reviewer: str = "admin",
        admin_user_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        catalog = await self.get_catalog(session, supplier_id)
        source = next((p for p in catalog if p["supplier_product_id"] == supplier_product_id), None)
        if not source:
            raise ValueError("Product not found in supplier catalog")

        prod = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == _uid(master_product_id))
        )).scalar_one_or_none()
        if not prod:
            raise ValueError("Master product not found")

        cost = Decimal(str(source["cost"]))
        link = (await session.execute(
            select(ProductSupplierLink).where(
                ProductSupplierLink.master_product_id == prod.id,
                ProductSupplierLink.supplier_id == _uid(supplier_id),
                ProductSupplierLink.supplier_product_id == supplier_product_id,
            )
        )).scalar_one_or_none()
        if not link:
            link = ProductSupplierLink(
                master_product_id=prod.id,
                supplier_id=_uid(supplier_id),
                supplier_product_id=supplier_product_id,
            )
            session.add(link)
        link.supplier_product_name = source.get("raw_name") or source.get("name")
        link.last_known_cost = cost
        link.is_in_stock = bool(source.get("is_in_stock"))
        link.available_quantity = source.get("available_quantity")

        session.add(AuditLog(
            admin_user_id=_uid(admin_user_id) if admin_user_id else None,
            action="product_cherry_pick_map",
            entity_type="master_product",
            entity_id=prod.id,
            new_values=merge_audit_new_values(reviewer, {
                "supplier_id": str(supplier_id),
                "supplier_product_id": supplier_product_id,
            }),
        ))
        await session.commit()
        await event_bus.publish_config_change("products", str(prod.id))
        return {"status": "ok", "product_id": str(prod.id), "wholesale_cost": float(cost)}


supplier_admin_service = SupplierAdminService()
