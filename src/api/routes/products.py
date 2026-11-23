import uuid
from decimal import Decimal
from typing import List, Optional, Dict, Any
import base64
import os
import uuid as uuid_mod
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.models.entities import MasterProduct, Category, ProductSupplierLink, Supplier, AdminUser
from src.core.adapters.registry import AdapterRegistry
from src.core.services.pricing_service import pricing_service
from src.api.routes.auth import get_current_admin
from src.core.redis import event_bus

router = APIRouter(prefix="/products", tags=["Products"])

_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "dashboard" / "public" / "uploads" / "products"


def _uid(value: Any):
    if value is None:
        return None
    return uuid.UUID(str(value)) if not isinstance(value, uuid.UUID) else value


# ---------------- Category DTOs ----------------
class CategoryDTO(BaseModel):
    id: Optional[str] = None
    name_ar: str
    name_en: str
    icon_emoji: str = "📦"
    sort_order: int = 0
    is_active: bool = True


# ---------------- Product DTOs ----------------
class MasterProductCreateDTO(BaseModel):
    category_id: Optional[str] = None
    internal_sku: str
    title_ar: str
    title_en: str
    description_ar: Optional[str] = None
    description_en: Optional[str] = None
    photo_url: Optional[str] = None
    custom_emoji: str = "✨"
    custom_button_label_ar: str = "🛒 شراء الآن"
    custom_description_ar: Optional[str] = None
    custom_description_en: Optional[str] = None
    warranty_days: int = 30
    is_active: bool = True
    is_published_to_channel: bool = False
    sort_order: int = 0


class MasterProductUpdateDTO(BaseModel):
    category_id: Optional[str] = None
    title_ar: Optional[str] = None
    title_en: Optional[str] = None
    description_ar: Optional[str] = None
    description_en: Optional[str] = None
    photo_url: Optional[str] = None
    custom_emoji: Optional[str] = None
    custom_button_label_ar: Optional[str] = None
    custom_description_ar: Optional[str] = None
    custom_description_en: Optional[str] = None
    warranty_days: Optional[int] = None
    is_active: Optional[bool] = None
    is_published_to_channel: Optional[bool] = None
    sort_order: Optional[int] = None
    # Phase 16
    brand_family: Optional[str] = None
    delivery_type_label: Optional[str] = None
    is_featured: Optional[bool] = None
    subscription_duration_days: Optional[int] = None
    search_keywords: Optional[str] = None
    fulfillment_mode: Optional[str] = None
    requires_customer_email: Optional[bool] = None
    requires_customer_input: Optional[bool] = None
    customer_input_type: Optional[str] = None
    customer_input_label_ar: Optional[str] = None


class ProductMappingDTO(BaseModel):
    supplier_id: str
    supplier_product_id: str
    supplier_product_name: Optional[str] = None
    last_known_cost: Optional[Decimal] = None
    is_in_stock: bool = True
    available_quantity: Optional[int] = None
    priority_override: Optional[int] = None


class SmartImportDTO(BaseModel):
    supplier_id: str
    supplier_product_id: str
    title_ar: str
    title_en: Optional[str] = None
    description_ar: Optional[str] = None
    custom_emoji: str = "✨"
    custom_button_label_ar: str = "🛒 شراء الآن"
    category_id: Optional[str] = None
    is_published_to_channel: bool = True
    # Pricing synced to Bot / Mini App / Channel immediately
    pricing_mode: Optional[str] = "fixed"  # percentage | fixed | floor
    pricing_value: Optional[float] = 5.0
    # Optional: map to existing master product instead of creating
    map_to_product_id: Optional[str] = None
    photo_url: Optional[str] = None
    warranty_days: Optional[int] = None
    requires_customer_email: Optional[bool] = None


# ---------------- Categories Endpoints ----------------
@router.get("/categories", response_model=List[CategoryDTO])
async def list_categories(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(Category).order_by(Category.sort_order.asc(), Category.created_at.asc())
    res = await session.execute(stmt)
    return [
        CategoryDTO(
            id=str(c.id),
            name_ar=c.name_ar,
            name_en=c.name_en,
            icon_emoji=c.icon_emoji,
            sort_order=c.sort_order,
            is_active=c.is_active,
        )
        for c in res.scalars().all()
    ]


@router.post("/categories", response_model=CategoryDTO)
async def create_category(
    dto: CategoryDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    cat = Category(
        name_ar=dto.name_ar,
        name_en=dto.name_en,
        icon_emoji=dto.icon_emoji,
        sort_order=dto.sort_order,
        is_active=dto.is_active,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    await event_bus.publish_config_change("categories", str(cat.id))
    dto.id = str(cat.id)
    return dto


# ---------------- Master Products Endpoints ----------------
@router.get("")
async def list_products(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = (
        select(MasterProduct)
        .options(
            selectinload(MasterProduct.category),
            selectinload(MasterProduct.supplier_links).selectinload(ProductSupplierLink.supplier)
        )
        .order_by(MasterProduct.sort_order.asc(), MasterProduct.created_at.desc())
    )
    res = await session.execute(stmt)
    products = res.scalars().all()

    output = []
    for p in products:
        pricing_info = await pricing_service.get_best_supplier_and_price(session, str(p.id))
        winner = pricing_info.get("supplier")
        winner_link = pricing_info.get("winner_link")
        links_data = [
            {
                "id": str(link.id),
                "supplier_id": str(link.supplier_id),
                "supplier_name": link.supplier.name if link.supplier else "Unknown",
                "supplier_product_id": link.supplier_product_id,
                "supplier_product_name": link.supplier_product_name,
                "last_known_cost": float(link.last_known_cost) if link.last_known_cost is not None else None,
                "is_in_stock": link.is_in_stock,
                "available_quantity": link.available_quantity,
                "is_winning": bool(
                    winner_link and str(link.id) == str(winner_link.id)
                ),
            }
            for link in p.supplier_links
        ]
        output.append({
            "id": str(p.id),
            "internal_sku": p.internal_sku,
            "title_ar": p.title_ar,
            "title_en": p.title_en,
            "description_ar": p.description_ar,
            "custom_emoji": getattr(p, "custom_emoji", "✨") or "✨",
            "custom_button_label_ar": getattr(p, "custom_button_label_ar", "🛒 شراء الآن") or "🛒 شراء الآن",
            "custom_description_ar": getattr(p, "custom_description_ar", None),
            "warranty_days": getattr(p, "warranty_days", 30) or 30,
            "category_id": str(p.category_id) if p.category_id else None,
            "category_name": p.category.name_ar if p.category else None,
            "cached_best_price": float(p.cached_best_price) if p.cached_best_price is not None else None,
            "cached_final_price": float(
                pricing_info["final_price"] if pricing_info.get("final_price") is not None
                else (p.cached_final_price if p.cached_final_price is not None else 0)
            ) if (pricing_info.get("final_price") is not None or p.cached_final_price is not None) else None,
            "cached_in_stock": pricing_info.get("is_available", p.cached_in_stock),
            "winning_supplier": winner.name if winner else None,
            "winning_supplier_id": str(winner.id) if winner else None,
            "winning_wholesale_cost": float(pricing_info["wholesale_cost"]) if pricing_info.get("wholesale_cost") is not None else None,
            "is_active": p.is_active,
            "is_published_to_channel": p.is_published_to_channel,
            "supplier_links": links_data,
        })
    return output


@router.post("")
async def create_product(
    dto: MasterProductCreateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    prod = MasterProduct(
        category_id=dto.category_id,
        internal_sku=dto.internal_sku.upper().strip(),
        title_ar=dto.title_ar,
        title_en=dto.title_en,
        description_ar=dto.description_ar,
        description_en=dto.description_en,
        custom_emoji=dto.custom_emoji,
        custom_button_label_ar=dto.custom_button_label_ar,
        custom_description_ar=dto.custom_description_ar or dto.description_ar,
        custom_description_en=dto.custom_description_en,
        warranty_days=dto.warranty_days,
        photo_url=dto.photo_url,
        is_active=dto.is_active,
        is_published_to_channel=dto.is_published_to_channel,
        sort_order=dto.sort_order,
    )
    session.add(prod)
    await session.commit()
    await session.refresh(prod)
    await event_bus.publish_config_change("products", str(prod.id))
    return {"status": "ok", "id": str(prod.id)}


@router.put("/{product_id}")
async def update_product(
    product_id: str,
    dto: MasterProductUpdateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    """Headless CMS update: custom Arabic title/copy/emoji/warranty — takes effect immediately."""
    stmt = select(MasterProduct).where(MasterProduct.id == _uid(product_id))
    prod = (await session.execute(stmt)).scalar_one_or_none()
    if not prod:
        raise HTTPException(status_code=404, detail="Master Product not found")

    for field in (
        "category_id", "title_ar", "title_en", "description_ar", "description_en",
        "photo_url", "custom_emoji", "custom_button_label_ar",
        "custom_description_ar", "custom_description_en", "warranty_days",
        "is_active", "is_published_to_channel", "sort_order",
        "brand_family", "delivery_type_label", "is_featured",
        "subscription_duration_days", "search_keywords", "fulfillment_mode",
        "requires_customer_email", "requires_customer_input",
        "customer_input_type", "customer_input_label_ar",
    ):
        val = getattr(dto, field, None)
        if val is not None:
            if field == "category_id":
                val = _uid(val)
            setattr(prod, field, val)

    await session.commit()
    await session.refresh(prod)
    await event_bus.publish_config_change("products", product_id)
    return {
        "status": "ok",
        "id": str(prod.id),
        "title_ar": prod.title_ar,
        "custom_emoji": prod.custom_emoji,
        "custom_description_ar": prod.custom_description_ar,
        "warranty_days": prod.warranty_days,
    }


class LocalStockBulkDTO(BaseModel):
    lines: str  # one key/account per line


@router.get("/{product_id}/local-stock")
async def get_local_stock_count(
    product_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.local_stock_service import local_stock_service
    n = await local_stock_service.available_count(session, product_id)
    return {"product_id": product_id, "available": n}


@router.post("/{product_id}/local-stock")
async def bulk_add_local_stock(
    product_id: str,
    dto: LocalStockBulkDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.local_stock_service import local_stock_service
    lines = (dto.lines or "").splitlines()
    return await local_stock_service.bulk_add(
        session, product_id, lines, reviewer=admin.username,
    )


@router.post("/upload-image")
async def upload_product_image(
    file: UploadFile = File(...),
    admin: AdminUser = Depends(get_current_admin),
):
    """Store a product image under dashboard/public/uploads and return a public URL path."""
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image uploads allowed")
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 5MB)")
    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    elif "gif" in content_type:
        ext = ".gif"
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid_mod.uuid4().hex}{ext}"
    path = _UPLOAD_DIR / name
    path.write_bytes(data)
    url = f"/uploads/products/{name}"
    return {"status": "ok", "photo_url": url, "url": url}


@router.post("/import-from-supplier")
async def import_product_from_supplier(
    dto: SmartImportDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Smart Auto-Fill Import: Creates/maps MasterProduct + pricing rule, then invalidates caches."""
    import hashlib
    import uuid
    from src.core.models.entities import PricingRule
    from src.core.models.enums import PricingScope, PricingRuleType

    stmt = select(Supplier).where(Supplier.id == dto.supplier_id)
    supplier = (await session.execute(stmt)).scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    adapter = AdapterRegistry.create_from_supplier(supplier)
    catalog = await adapter.fetch_products()

    source_item = None
    for item in catalog:
        if item.supplier_product_id == dto.supplier_product_id:
            source_item = item
            break

    if not source_item:
        raise HTTPException(status_code=404, detail="Product not found in live supplier catalog")

    mode = (dto.pricing_mode or "fixed").lower()
    rule_type = {
        "percentage": PricingRuleType.PERCENTAGE,
        "percent": PricingRuleType.PERCENTAGE,
        "fixed": PricingRuleType.FIXED,
        "floor": PricingRuleType.FLOOR,
    }.get(mode, PricingRuleType.FIXED)
    rule_value = Decimal(str(dto.pricing_value if dto.pricing_value is not None else 5.0))

    if dto.map_to_product_id:
        prod = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == _uid(dto.map_to_product_id))
        )).scalar_one_or_none()
        if not prod:
            raise HTTPException(status_code=404, detail="Master product not found")
        internal_sku = prod.internal_sku
    else:
        sku_hash = hashlib.md5(f"{dto.supplier_id}:{dto.supplier_product_id}".encode()).hexdigest()[:6].upper()
        internal_sku = f"IMP-{sku_hash}"
        existing = (await session.execute(
            select(MasterProduct).where(MasterProduct.internal_sku == internal_sku)
        )).scalar_one_or_none()
        if existing:
            internal_sku = f"IMP-{sku_hash}-{str(uuid.uuid4())[:4].upper()}"

        requires_email = dto.requires_customer_email
        if requires_email is None:
            requires_email = bool((source_item.metadata or {}).get("requires_customer_email"))
        prod = MasterProduct(
            category_id=dto.category_id,
            internal_sku=internal_sku,
            title_ar=dto.title_ar,
            title_en=dto.title_en or source_item.raw_name or source_item.name,
            description_ar=dto.description_ar,
            photo_url=dto.photo_url,
            custom_emoji=dto.custom_emoji,
            custom_button_label_ar=dto.custom_button_label_ar,
            custom_description_ar=dto.description_ar,
            is_active=True,
            is_published_to_channel=dto.is_published_to_channel,
            warranty_days=int(dto.warranty_days) if dto.warranty_days is not None else 30,
            requires_customer_email=bool(requires_email),
            cached_best_price=source_item.cost,
            cached_in_stock=source_item.is_in_stock,
        )
        session.add(prod)
        await session.flush()

        session.add(PricingRule(
            scope=PricingScope.PRODUCT,
            scope_id=prod.id,
            rule_type=rule_type,
            value=rule_value,
            gas_buffer_enabled=False,
            is_active=True,
            priority=100,
        ))

    if dto.map_to_product_id and dto.photo_url and not prod.photo_url:
        prod.photo_url = dto.photo_url
    if dto.map_to_product_id and dto.requires_customer_email is not None:
        prod.requires_customer_email = bool(dto.requires_customer_email)

    link = (await session.execute(
        select(ProductSupplierLink).where(
            and_(
                ProductSupplierLink.master_product_id == prod.id,
                ProductSupplierLink.supplier_id == supplier.id,
                ProductSupplierLink.supplier_product_id == dto.supplier_product_id,
            )
        )
    )).scalar_one_or_none()
    if not link:
        link = ProductSupplierLink(
            master_product_id=prod.id,
            supplier_id=supplier.id,
            supplier_product_id=dto.supplier_product_id,
        )
        session.add(link)
    link.supplier_product_name = source_item.raw_name or source_item.name
    link.last_known_cost = source_item.cost
    link.is_in_stock = source_item.is_in_stock
    link.available_quantity = source_item.available_quantity

    await session.commit()
    await session.refresh(prod)
    await event_bus.publish_config_change("products", str(prod.id))

    return {
        "status": "ok",
        "product_id": str(prod.id),
        "internal_sku": internal_sku,
        "wholesale_cost": float(source_item.cost),
        "is_in_stock": source_item.is_in_stock,
        "pricing_mode": mode,
        "pricing_value": float(rule_value),
        "message": "Product successfully imported and linked",
    }


@router.post("/{product_id}/mappings")
async def map_supplier_product(
    product_id: str,
    dto: ProductMappingDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    # Verify product and supplier exist
    prod_uuid = _uid(product_id)
    supplier_uuid = _uid(dto.supplier_id)
    prod_stmt = select(MasterProduct).where(MasterProduct.id == prod_uuid)
    prod = (await session.execute(prod_stmt)).scalar_one_or_none()
    if not prod:
        raise HTTPException(status_code=404, detail="Master Product not found")

    # Upsert mapping
    link_stmt = select(ProductSupplierLink).where(
        and_(
            ProductSupplierLink.master_product_id == prod_uuid,
            ProductSupplierLink.supplier_id == supplier_uuid,
            ProductSupplierLink.supplier_product_id == dto.supplier_product_id
        )
    )
    link = (await session.execute(link_stmt)).scalar_one_or_none()
    effective_in_stock = dto.is_in_stock
    if dto.available_quantity is not None and dto.available_quantity <= 0:
        effective_in_stock = False

    if not link:
        link = ProductSupplierLink(
            master_product_id=prod_uuid,
            supplier_id=supplier_uuid,
            supplier_product_id=dto.supplier_product_id,
            supplier_product_name=dto.supplier_product_name,
            last_known_cost=dto.last_known_cost,
            is_in_stock=effective_in_stock,
            available_quantity=dto.available_quantity,
            priority_override=dto.priority_override,
        )
        session.add(link)
    else:
        link.supplier_product_name = dto.supplier_product_name or link.supplier_product_name
        if dto.last_known_cost is not None:
            link.last_known_cost = dto.last_known_cost
        link.is_in_stock = effective_in_stock
        if dto.available_quantity is not None:
            link.available_quantity = dto.available_quantity
        link.priority_override = dto.priority_override

    await session.commit()
    await event_bus.publish_config_change("product_mappings", product_id)
    return {"status": "ok", "message": "Supplier product mapped successfully"}


@router.delete("/{product_id}/mappings/{link_id}")
async def unmap_supplier_product(
    product_id: str,
    link_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = delete(ProductSupplierLink).where(
        and_(
            ProductSupplierLink.id == link_id,
            ProductSupplierLink.master_product_id == product_id
        )
    )
    await session.execute(stmt)
    await session.commit()
    await event_bus.publish_config_change("product_mappings", product_id)
    return {"status": "ok", "message": "Mapping removed"}


@router.get("/supplier-catalog/{supplier_id}")
async def browse_supplier_catalog(
    supplier_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    """Pulls raw product catalog directly from supplier API."""
    stmt = select(Supplier).where(Supplier.id == supplier_id)
    supplier = (await session.execute(stmt)).scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    adapter = AdapterRegistry.create_from_supplier(supplier)
    products = await adapter.fetch_products()

    # Get already mapped supplier product IDs
    links_stmt = select(ProductSupplierLink.supplier_product_id).where(ProductSupplierLink.supplier_id == supplier_id)
    mapped_ids = set((await session.execute(links_stmt)).scalars().all())

    return [
        {
            "supplier_product_id": p.supplier_product_id,
            "name": p.name,
            "cost": float(p.cost),
            "is_in_stock": p.is_in_stock,
            "currency": p.currency,
            "is_already_mapped": p.supplier_product_id in mapped_ids,
        }
        for p in products
    ]
