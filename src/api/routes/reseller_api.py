import hashlib
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from src.core.database import get_db
from src.core.models.entities import Customer, MasterProduct, Order, ProductSupplierLink, PricingRule
from src.core.models.enums import OrderState, PaymentMethod, WalletTxType, VipTier, PricingScope
from src.core.services.pricing_service import pricing_service
from src.core.services.order_service import order_service
from src.core.services.jit_queue import enqueue_jit_order

router = APIRouter(prefix="/api/v1/reseller", tags=["B2B Reseller API"])


def get_reseller_discount_percent(vip_tier: VipTier) -> Decimal:
    """Wholesale discount percentages based on Reseller VIP Tier."""
    if vip_tier == VipTier.WHOLESALE:
        return Decimal("8.0")
    elif vip_tier == VipTier.GOLD:
        return Decimal("5.0")
    elif vip_tier == VipTier.SILVER:
        return Decimal("2.0")
    return Decimal("0.0")


async def get_reseller_customer(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_db)
) -> Customer:
    """Authenticates Reseller via X-API-Key header or Authorization: Bearer <key> (hashed with SHA-256)."""
    raw_key = x_api_key
    if not raw_key:
        auth_hdr = request.headers.get("Authorization")
        if auth_hdr and auth_hdr.startswith("Bearer "):
            raw_key = auth_hdr.split(" ", 1)[1]

    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing 'X-API-Key' or 'Authorization: Bearer <key>' header."
        )

    key_hash = hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()
    stmt = select(Customer).where(
        Customer.api_key_hash == key_hash,
        Customer.api_enabled == True
    )
    customer = (await session.execute(stmt)).scalar_one_or_none()

    if not customer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API Key."
        )

    if customer.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reseller account has been suspended."
        )

    # Per-key token bucket: 60 requests / minute, shared across all reseller endpoints.
    from src.core.redis import event_bus
    if event_bus.redis:
        allowed = await event_bus.check_and_consume_supplier_quota(
            f"reseller:{str(customer.id)}", rate_limit_rpm=60
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Reseller API rate limit exceeded. Please slow down."
            )

    return customer


# =========================================================================
# 1. Reseller Balance & Tier
# =========================================================================
@router.get("/balance")
async def get_reseller_balance(
    reseller: Customer = Depends(get_reseller_customer)
):
    discount = get_reseller_discount_percent(reseller.vip_tier)
    return {
        "reseller_id": str(reseller.id),
        "telegram_id": reseller.telegram_id,
        "wallet_balance_usdt": float(reseller.wallet_balance),
        "vip_tier": reseller.vip_tier.value,
        "wholesale_discount_percent": float(discount),
        "total_spent_usdt": float(reseller.total_spent or 0),
        "total_orders": reseller.total_orders or 0,
    }


# =========================================================================
# 2. Reseller Products & Dynamic Wholesale Pricing
# =========================================================================
@router.get("/products")
async def get_reseller_products(
    reseller: Customer = Depends(get_reseller_customer),
    session: AsyncSession = Depends(get_db)
):
    discount_pct = get_reseller_discount_percent(reseller.vip_tier)
    incentive_rate = await pricing_service.get_incentive_rate(session)

    from src.core.services.pricing_service import pick_cheapest_sellable_link
    from src.core.models.entities import ProductSupplierLink
    # Eager load supplier links (+ supplier) to eliminate N+1 queries
    stmt = (
        select(MasterProduct)
        .where(MasterProduct.is_active == True)
        .options(
            selectinload(MasterProduct.supplier_links).selectinload(ProductSupplierLink.supplier)
        )
        .order_by(MasterProduct.sort_order.asc())
    )
    products = (await session.execute(stmt)).scalars().all()

    # Pre-fetch pricing rules in a single batch query
    rules_stmt = select(PricingRule).where(PricingRule.is_active == True).order_by(PricingRule.priority.desc())
    all_rules = (await session.execute(rules_stmt)).scalars().all()

    catalog = []
    for p in products:
        winner_link = pick_cheapest_sellable_link(list(p.supplier_links))
        if winner_link:
            supplier_cost = winner_link.last_known_cost
            is_in_stock = True
            avail_stock = winner_link.available_quantity
        else:
            supplier_cost = Decimal("0.0")
            is_in_stock = False
            avail_stock = 0

        # Match rule in-memory
        matching_rule = None
        for r in all_rules:
            if r.scope == PricingScope.PRODUCT and r.scope_id == p.id:
                matching_rule = r
                break
        if not matching_rule:
            for r in all_rules:
                if r.scope == PricingScope.CATEGORY and r.scope_id == p.category_id:
                    matching_rule = r
                    break
        if not matching_rule:
            for r in all_rules:
                if r.scope == PricingScope.GLOBAL:
                    matching_rule = r
                    break

        retail_price, _, _ = pricing_service.calculate_price_from_cost(supplier_cost, matching_rule)

        # Apply Reseller Tier Discount with the shared profit guard (cashback + referral
        # are credited by the JIT executor on this channel too).
        discount_amt = (retail_price * (discount_pct / Decimal("100.0"))).quantize(Decimal("0.01"))
        discount_amt = pricing_service.clamp_total_discount(
            gross_total=retail_price,
            requested_discount=discount_amt,
            supplier_cost=supplier_cost,
            incentive_rate=incentive_rate,
        )
        reseller_price = pricing_service.enforce_profit_floor(
            retail_price, retail_price - discount_amt, supplier_cost, incentive_rate
        )

        catalog.append({
            "product_id": str(p.id),
            "title": p.title_en or p.title_ar,
            "title_ar": p.title_ar,
            "retail_price_usd": float(retail_price),
            "reseller_price_usd": float(reseller_price),
            "discount_percent": float(discount_pct),
            "is_in_stock": is_in_stock,
            "available_stock": avail_stock,
            "winning_supplier": winner_link.supplier.name if winner_link and winner_link.supplier else None,
            "warranty_days": getattr(p, "warranty_days", 30) or 30,
        })

    return {
        "reseller_tier": reseller.vip_tier.value,
        "discount_applied": float(discount_pct),
        "products": catalog,
        "count": len(catalog)
    }


# =========================================================================
# 3. Reseller Instant Order Placement
# =========================================================================
class ResellerOrderRequestDTO(BaseModel):
    product_id: str
    quantity: int = Field(1, ge=1, le=50)
    custom_ref: Optional[str] = None


@router.post("/orders")
async def place_reseller_order(
    dto: ResellerOrderRequestDTO,
    reseller: Customer = Depends(get_reseller_customer),
    session: AsyncSession = Depends(get_db)
):
    prod_stmt = select(MasterProduct).where(MasterProduct.id == dto.product_id, MasterProduct.is_active == True)
    product = (await session.execute(prod_stmt)).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found or inactive.")

    pricing_info = await pricing_service.get_best_supplier_and_price(session, dto.product_id)
    if not pricing_info.get("is_available"):
        raise HTTPException(status_code=400, detail="Product is currently out of stock.")

    winner_link = pricing_info["winner_link"]
    if winner_link.available_quantity and dto.quantity > winner_link.available_quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Requested quantity ({dto.quantity}) exceeds available stock ({winner_link.available_quantity})."
        )

    retail_unit = pricing_info["final_price"]
    supplier_unit = pricing_info["supplier_cost"]

    # Calculate Reseller Discount
    discount_pct = get_reseller_discount_percent(reseller.vip_tier)
    incentive_rate = await pricing_service.get_incentive_rate(session)
    discount_unit = (retail_unit * (discount_pct / Decimal("100.0"))).quantize(Decimal("0.01"))
    discount_unit = pricing_service.clamp_total_discount(
        gross_total=retail_unit,
        requested_discount=discount_unit,
        supplier_cost=supplier_unit,
        incentive_rate=incentive_rate,
    )
    reseller_unit = pricing_service.enforce_profit_floor(
        retail_unit, retail_unit - discount_unit, supplier_unit, incentive_rate
    )

    total_cost_reseller = (reseller_unit * Decimal(dto.quantity)).quantize(Decimal("0.01"))
    total_supplier_cost = (supplier_unit * Decimal(dto.quantity)).quantize(Decimal("0.01"))
    # Guard the order total as well (rounding on quantity must not erode the floor).
    total_cost_reseller = max(
        total_cost_reseller,
        pricing_service.profit_guard_min_payable(
            retail_unit * Decimal(dto.quantity), total_supplier_cost, incentive_rate
        ),
    )

    # Verify Reseller Wallet Balance
    if reseller.wallet_balance < total_cost_reseller:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": "insufficient_wallet_balance",
                "message": "Insufficient wallet funds. Please top up your wallet via the Mini App.",
                "required_usdt": float(total_cost_reseller),
                "current_balance_usdt": float(reseller.wallet_balance),
                "deficit_usdt": float(total_cost_reseller - reseller.wallet_balance)
            }
        )

    # Atomically reserve stock, then debit Reseller balance. Guarded UPDATE prevents
    # overselling the last units when several resellers buy simultaneously.
    if not await order_service.reserve_supplier_stock(session, winner_link.id, dto.quantity):
        raise HTTPException(status_code=400, detail="Product went out of stock during checkout.")
    try:
        await order_service.debit_customer_wallet(
            session=session,
            customer_id=str(reseller.id),
            amount=total_cost_reseller,
            tx_type=WalletTxType.PURCHASE_DEBIT,
            description=f"B2B Reseller API Order: {product.title_ar} (qty: {dto.quantity})"
        )
    except ValueError:
        await order_service.release_supplier_stock(session, winner_link.id, dto.quantity)
        await session.commit()
        raise HTTPException(status_code=402, detail="Insufficient reseller wallet balance.")

    # Create Order
    order, _ = await order_service.create_order(
        session=session,
        customer_id=str(reseller.id),
        master_product_id=str(product.id),
        supplier_id=str(winner_link.supplier_id),
        supplier_product_id=winner_link.supplier_product_id,
        supplier_cost=total_supplier_cost,
        our_price=total_cost_reseller,
        profit_margin=total_cost_reseller - total_supplier_cost,
        payment_method=PaymentMethod.INTERNAL_BALANCE,
        quantity=dto.quantity
    )

    await order_service.transition_order_state(
        session, order, OrderState.PAYMENT_CONFIRMED, "b2b_reseller_api_payment"
    )
    await session.commit()

    await enqueue_jit_order(str(order.id))

    # Wait up to 3 seconds for instant supplier delivery
    for _ in range(6):
        await asyncio.sleep(0.5)
        await session.refresh(order)
        if order.state in (OrderState.COMPLETED, OrderState.DELIVERED):
            break

    return {
        "status": "success",
        "order_id": str(order.id),
        "order_number": order.order_number,
        "custom_ref": dto.custom_ref,
        "state": order.state.value,
        "product_title": product.title_ar,
        "quantity": dto.quantity,
        "unit_price_usdt": float(reseller_unit),
        "total_debited_usdt": float(total_cost_reseller),
        "delivered_payload": order.delivered_product if order.state == OrderState.COMPLETED else None,
        "warranty_expires_at": order.warranty_expires_at.strftime("%Y-%m-%d") if order.warranty_expires_at else None,
        "remaining_wallet_balance": float(reseller.wallet_balance),
    }
