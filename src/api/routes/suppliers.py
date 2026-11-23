import json
import uuid
from decimal import Decimal
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.models.entities import Supplier, SupplierHealthLog, AdminUser
from src.core.models.enums import SupplierStatus, PaymentNetwork
from src.core.security import encrypt_secret, decrypt_secret
from src.core.adapters.registry import AdapterRegistry
from src.api.routes.auth import get_current_admin
from src.core.redis import event_bus

router = APIRouter(prefix="/suppliers", tags=["Suppliers"])


def _uid(value):
    return uuid.UUID(str(value)) if not isinstance(value, uuid.UUID) else value


class SupplierCreateDTO(BaseModel):
    name: str
    driver_type: str = "ventebot_v1"  # 'ventebot_v1' or 'generic_rest'
    payment_network: PaymentNetwork = PaymentNetwork.USDT_BEP20
    deposit_address: Optional[str] = None
    binance_id: Optional[str] = None
    priority_weight: int = 100
    rate_limit_rpm: int = 60
    min_deposit: Decimal = Decimal("0.0")
    deposit_window_seconds: Optional[int] = 300
    notes: Optional[str] = None
    # Dynamic configuration (API keys, base URL, endpoints)
    config: Dict[str, Any] = Field(default_factory=dict)


class SupplierUpdateDTO(BaseModel):
    name: Optional[str] = None
    status: Optional[SupplierStatus] = None
    priority_weight: Optional[int] = None
    rate_limit_rpm: Optional[int] = None
    min_deposit: Optional[Decimal] = None
    deposit_window_seconds: Optional[int] = None
    deposit_address: Optional[str] = None
    binance_id: Optional[str] = None
    notes: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class SupplierResponseDTO(BaseModel):
    id: str
    name: str
    driver_type: str
    payment_network: str
    status: str
    priority_weight: int
    min_deposit: float
    deposit_window_seconds: Optional[int]
    deposit_address: Optional[str]
    binance_id: Optional[str]
    cached_balance: Optional[float]
    cb_consecutive_failures: int
    notes: Optional[str]
    created_at: str
    quality_penalty_score: int = 0
    warranty_claim_rate_30d: Optional[float] = None
    api_failure_rate_30d: Optional[float] = None
    effective_priority: Optional[int] = None


@router.get("", response_model=List[SupplierResponseDTO])
async def list_suppliers(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(Supplier).order_by(Supplier.priority_weight.desc(), Supplier.created_at.desc())
    res = await session.execute(stmt)
    suppliers = res.scalars().all()

    return [
        SupplierResponseDTO(
            id=str(s.id),
            name=s.name,
            driver_type=s.driver_type,
            payment_network=s.payment_network.value,
            status=s.status.value,
            priority_weight=s.priority_weight,
            min_deposit=float(s.min_deposit or 0),
            deposit_window_seconds=s.deposit_window_seconds,
            deposit_address=s.deposit_address,
            binance_id=s.binance_id,
            cached_balance=float(s.cached_balance) if s.cached_balance is not None else None,
            cb_consecutive_failures=s.cb_consecutive_failures,
            notes=s.notes,
            created_at=s.created_at.isoformat(),
            quality_penalty_score=int(getattr(s, "quality_penalty_score", 0) or 0),
            warranty_claim_rate_30d=(
                float(s.warranty_claim_rate_30d)
                if getattr(s, "warranty_claim_rate_30d", None) is not None else None
            ),
            api_failure_rate_30d=(
                float(s.api_failure_rate_30d)
                if getattr(s, "api_failure_rate_30d", None) is not None else None
            ),
            effective_priority=int(s.priority_weight or 0) - int(getattr(s, "quality_penalty_score", 0) or 0),
        )
        for s in suppliers
    ]


@router.get("/presets")
async def list_supplier_presets(admin: AdminUser = Depends(get_current_admin)):
    """Official adapter presets with base URLs for the Dashboard dropdown."""
    return AdapterRegistry.presets()


@router.get("/quality/leaderboard")
async def supplier_quality_leaderboard(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Rolling 30d reliability leaderboard + auto-demotion side effects."""
    from src.core.services.supplier_quality_service import supplier_quality_service
    return await supplier_quality_service.leaderboard(session)


@router.post("", response_model=SupplierResponseDTO)
async def create_supplier(
    dto: SupplierCreateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    # Auto-decode Lara conn_ codes + fill official base URLs
    normalized = AdapterRegistry.normalize_config(dto.driver_type, dto.config or {})
    encrypted_config = encrypt_secret(json.dumps(normalized))

    supplier = Supplier(
        name=dto.name,
        driver_type=dto.driver_type,
        config_encrypted=encrypted_config,
        payment_network=dto.payment_network,
        deposit_address=dto.deposit_address,
        binance_id=dto.binance_id,
        priority_weight=dto.priority_weight,
        rate_limit_rpm=dto.rate_limit_rpm,
        min_deposit=dto.min_deposit,
        deposit_window_seconds=dto.deposit_window_seconds,
        notes=dto.notes,
        status=SupplierStatus.ACTIVE,
    )
    session.add(supplier)
    await session.commit()
    await session.refresh(supplier)

    await event_bus.publish_config_change("suppliers", str(supplier.id))

    return SupplierResponseDTO(
        id=str(supplier.id),
        name=supplier.name,
        driver_type=supplier.driver_type,
        payment_network=supplier.payment_network.value,
        status=supplier.status.value,
        priority_weight=supplier.priority_weight,
        min_deposit=float(supplier.min_deposit or 0),
        deposit_window_seconds=supplier.deposit_window_seconds,
        deposit_address=supplier.deposit_address,
        binance_id=supplier.binance_id,
        cached_balance=None,
        cb_consecutive_failures=0,
        notes=supplier.notes,
        created_at=supplier.created_at.isoformat(),
    )


@router.put("/{supplier_id}", response_model=SupplierResponseDTO)
async def update_supplier(
    supplier_id: str,
    dto: SupplierUpdateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    """Edit / enable / disable a supplier. Takes effect immediately (no server restart)."""
    stmt = select(Supplier).where(Supplier.id == _uid(supplier_id))
    supplier = (await session.execute(stmt)).scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    if dto.name is not None:
        supplier.name = dto.name
    if dto.status is not None:
        supplier.status = dto.status
    if dto.priority_weight is not None:
        supplier.priority_weight = dto.priority_weight
    if dto.rate_limit_rpm is not None:
        supplier.rate_limit_rpm = dto.rate_limit_rpm
    if dto.min_deposit is not None:
        supplier.min_deposit = dto.min_deposit
    if dto.deposit_window_seconds is not None:
        supplier.deposit_window_seconds = dto.deposit_window_seconds
    if dto.deposit_address is not None:
        supplier.deposit_address = dto.deposit_address
    if dto.binance_id is not None:
        supplier.binance_id = dto.binance_id
    if dto.notes is not None:
        supplier.notes = dto.notes
    if dto.config is not None:
        normalized = AdapterRegistry.normalize_config(supplier.driver_type, dto.config)
        supplier.config_encrypted = encrypt_secret(json.dumps(normalized))

    await session.commit()
    await session.refresh(supplier)
    await event_bus.publish_config_change("suppliers", supplier_id)

    return SupplierResponseDTO(
        id=str(supplier.id),
        name=supplier.name,
        driver_type=supplier.driver_type,
        payment_network=supplier.payment_network.value,
        status=supplier.status.value,
        priority_weight=supplier.priority_weight,
        min_deposit=float(supplier.min_deposit or 0),
        deposit_window_seconds=supplier.deposit_window_seconds,
        deposit_address=supplier.deposit_address,
        binance_id=supplier.binance_id,
        cached_balance=float(supplier.cached_balance) if supplier.cached_balance is not None else None,
        cb_consecutive_failures=supplier.cb_consecutive_failures,
        notes=supplier.notes,
        created_at=supplier.created_at.isoformat(),
    )


@router.post("/{supplier_id}/test-connection")
async def test_supplier_connection(
    supplier_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(Supplier).where(Supplier.id == supplier_id)
    res = await session.execute(stmt)
    supplier = res.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    adapter = AdapterRegistry.create_from_supplier(supplier)
    health = await adapter.check_health()

    # Log the check
    log_entry = SupplierHealthLog(
        supplier_id=supplier.id,
        check_type="manual_test",
        is_success=health.is_alive,
        response_time_ms=health.latency_ms,
        http_status=health.http_status,
        error_message=health.error_message,
    )
    session.add(log_entry)

    if health.is_alive and health.balance_usd is not None:
        supplier.cached_balance = health.balance_usd
    await session.commit()

    return {
        "is_alive": health.is_alive,
        "latency_ms": health.latency_ms,
        "http_status": health.http_status,
        "balance_usd": float(health.balance_usd) if health.balance_usd is not None else None,
        "error_message": health.error_message,
    }


@router.put("/{supplier_id}/reset-circuit-breaker")
async def reset_circuit_breaker(
    supplier_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(Supplier).where(Supplier.id == supplier_id)
    res = await session.execute(stmt)
    supplier = res.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    supplier.status = SupplierStatus.ACTIVE
    supplier.cb_consecutive_failures = 0
    supplier.cb_last_failure_at = None
    await session.commit()
    await event_bus.publish_config_change("suppliers", supplier_id)
    return {"status": "ok", "message": "Circuit breaker reset to ACTIVE"}


@router.get("/{supplier_id}/catalog-for-import")
async def get_supplier_catalog_for_import(
    supplier_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    """Returns enriched live catalog data for the Smart Auto-Fill Import flow."""
    from src.core.models.entities import ProductSupplierLink

    stmt = select(Supplier).where(Supplier.id == supplier_id)
    supplier = (await session.execute(stmt)).scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    adapter = AdapterRegistry.create_from_supplier(supplier)
    products = await adapter.fetch_products()

    # Find already mapped supplier product IDs
    links_stmt = select(ProductSupplierLink).where(ProductSupplierLink.supplier_id == supplier.id)
    links = (await session.execute(links_stmt)).scalars().all()
    mapped_ids = {link.supplier_product_id: str(link.master_product_id) for link in links}

    return [
        {
            "supplier_product_id": p.supplier_product_id,
            "name": p.name,
            "raw_name": p.raw_name,
            "cost": float(p.cost),
            "is_in_stock": p.is_in_stock,
            "available_quantity": p.available_quantity,
            "currency": p.currency,
            "supported_deposit_networks": p.supported_deposit_networks,
            "is_already_mapped": p.supplier_product_id in mapped_ids,
            "linked_master_product_id": mapped_ids.get(p.supplier_product_id),
            "delivery_type": (p.metadata or {}).get("delivery_type") or "instant",
            "requires_customer_email": bool((p.metadata or {}).get("requires_customer_email")),
            "metadata": {
                k: v for k, v in (p.metadata or {}).items()
                if k in ("source", "is_manual", "warranty_info", "desc_ar", "name_ar")
            },
        }
        for p in products
    ]
