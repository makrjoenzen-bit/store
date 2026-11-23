import hmac
import hashlib
import json
import urllib.parse
import secrets
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, and_, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from loguru import logger

from src.core.database import get_db
from src.core.models.entities import (
    Customer, Category, MasterProduct, Order, PaymentInvoice,
    WalletTransaction, SupportTicket, TicketMessage, Coupon,
    FormalInvoice, TopUpBonusTier, BotConfig, PricingRule, ProductSupplierLink
)
from src.core.models.enums import (
    OrderState, PaymentMethod, InvoiceStatus, WalletTxType,
    DiscountType, VipTier, TicketStatus, PricingScope
)
from src.core.services.pricing_service import pricing_service
from src.core.services.order_service import order_service
from src.core.services.jit_queue import enqueue_jit_order
from src.core.redis import event_bus
from src.config import settings


def _resolve_deposit(method: PaymentMethod) -> str:
    """Deposit address / Pay ID for invoice rails. Never invent a fake Binance ID."""
    if method == PaymentMethod.USDT_BEP20_DIRECT:
        return settings.HOT_WALLET_ADDRESS or ""
    if method == PaymentMethod.USDT_TRC20_DIRECT:
        return (settings.TRON_HOT_WALLET_ADDRESS or "").strip()
    if method == PaymentMethod.BINANCE_PAY:
        return (settings.BINANCE_PAY_MERCHANT_ID or "").strip()
    if method == PaymentMethod.SHAM_CASH:
        return "0991234567"
    return ""

router = APIRouter(prefix="/miniapp", tags=["Telegram Mini App"])


def _utc_now():
    return datetime.now(timezone.utc)


def _normalize_utc_dt(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_vip_tier(total_spent: Decimal) -> VipTier:
    spent = float(total_spent or 0)
    if spent >= 2000.0:
        return VipTier.WHOLESALE
    elif spent >= 500.0:
        return VipTier.GOLD
    elif spent >= 100.0:
        return VipTier.SILVER
    return VipTier.STANDARD


def verify_telegram_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
) -> Optional[dict]:
    """
    Strict HMAC-SHA256 signature verification for Telegram WebApp initData.
    Rejects missing/invalid hash and stale auth_date.
    """
    if not init_data:
        return None

    try:
        parsed = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
        data_dict = dict(parsed)
        received_hash = data_dict.pop("hash", None)
        if not received_hash:
            return None

        auth_date_raw = data_dict.get("auth_date")
        if auth_date_raw:
            try:
                auth_ts = int(auth_date_raw)
                age = int(datetime.now(timezone.utc).timestamp()) - auth_ts
                if age < 0 or age > int(max_age_seconds):
                    logger.warning("Telegram initData auth_date expired/invalid")
                    return None
            except ValueError:
                return None

        sorted_items = sorted(data_dict.items())
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)

        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if hmac.compare_digest(computed_hash, received_hash):
            user_json = data_dict.get("user")
            if user_json:
                return json.loads(user_json)
        return None
    except Exception as e:
        logger.warning(f"Error validating Telegram initData: {e}")
        return None


async def get_current_miniapp_customer(
    request: Request,
    session: AsyncSession = Depends(get_db)
) -> Customer:
    """
    Authenticates Mini App user via X-Telegram-Init-Data header or query param.
    Fallback for localhost/dev: X-Dev-Telegram-Id header when ENVIRONMENT=development.
    """
    init_data = request.headers.get("X-Telegram-Init-Data") or request.query_params.get("initData")
    dev_tg_id = request.headers.get("X-Dev-Telegram-Id")

    tg_user = None

    if init_data:
        if settings.TELEGRAM_BOT_TOKEN:
            tg_user = verify_telegram_init_data(init_data, settings.TELEGRAM_BOT_TOKEN)
        if not tg_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or tampered Telegram WebApp authentication."
            )

    # Dev preview fallback ONLY when ENVIRONMENT == 'development' and no init_data was attempted
    if not tg_user and settings.ENVIRONMENT == "development" and not init_data:
        dev_id = int(dev_tg_id) if (dev_tg_id and dev_tg_id.isdigit()) else 123456789
        tg_user = {
            "id": dev_id,
            "username": "dev_preview_user",
            "first_name": "Dev User",
            "last_name": "Preview",
            "language_code": "ar"
        }

    if not tg_user or "id" not in tg_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Telegram WebApp authentication."
        )

    tg_id = tg_user["id"]
    stmt = select(Customer).where(Customer.telegram_id == tg_id)
    customer = (await session.execute(stmt)).scalar_one_or_none()

    if not customer:
        customer = Customer(
            telegram_id=tg_id,
            telegram_username=tg_user.get("username"),
            first_name=tg_user.get("first_name", "Telegram User"),
            last_name=tg_user.get("last_name"),
            language_code=tg_user.get("language_code", "ar"),
            wallet_balance=Decimal("0.0"),
            total_spent=Decimal("0.0"),
            total_deposited=Decimal("0.0"),
            total_orders=0,
            vip_tier=VipTier.STANDARD,
        )
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
    else:
        # Auto recalculate VIP tier
        new_tier = compute_vip_tier(customer.total_spent)
        if customer.vip_tier != new_tier:
            customer.vip_tier = new_tier
            await session.commit()

    if customer.is_blocked:
        raise HTTPException(status_code=403, detail="Customer account is suspended.")

    return customer


# =========================================================================
# 1. Profile & Status
# =========================================================================
class LanguageUpdateDTO(BaseModel):
    language_code: str = Field(..., pattern="^(ar|en)$")


@router.put("/language")
async def update_miniapp_language(
    dto: LanguageUpdateDTO,
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db),
):
    customer.language_code = dto.language_code
    await session.commit()
    return {"status": "ok", "language_code": customer.language_code}


@router.get("/profile")
async def get_miniapp_profile(
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    # Fetch active pending invoices (AWAITING_PAYMENT)
    now_utc = _utc_now()
    inv_stmt = (
        select(PaymentInvoice)
        .where(
            and_(
                PaymentInvoice.customer_id == customer.id,
                PaymentInvoice.status == InvoiceStatus.PENDING,
                PaymentInvoice.expires_at > now_utc
            )
        )
        .order_by(desc(PaymentInvoice.created_at))
    )
    pending_invoices = (await session.execute(inv_stmt)).scalars().all()

    inv_list = []
    for inv in pending_invoices:
        exp_dt = _normalize_utc_dt(inv.expires_at)
        rem_sec = max(0, int((exp_dt - now_utc).total_seconds())) if exp_dt else 1800
        inv_list.append({
            "id": str(inv.id),
            "payment_method": inv.payment_method.value,
            "amount_expected": float(inv.amount_expected),
            "unique_amount": float(inv.unique_amount) if inv.unique_amount else float(inv.amount_expected),
            "deposit_address": inv.deposit_address,
            "currency": inv.currency,
            "status": inv.status.value,
            "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
            "remaining_seconds": rem_sec,
            "order_id": str(inv.order_id) if inv.order_id else None,
        })

    # Read bot channels & admin support from BotConfig
    main_url_cfg = (await session.execute(select(BotConfig.value).where(BotConfig.key == "main_channel_url"))).scalar_one_or_none()
    proof_url_cfg = (await session.execute(select(BotConfig.value).where(BotConfig.key == "proof_channel_url"))).scalar_one_or_none()

    # Calculate next VIP tier target
    spent = float(customer.total_spent or 0)
    next_tier = None
    target_spent = 0.0
    progress_pct = 100.0

    if customer.vip_tier == VipTier.STANDARD:
        next_tier = "SILVER"
        target_spent = 100.0
        progress_pct = min(100.0, (spent / target_spent) * 100.0)
    elif customer.vip_tier == VipTier.SILVER:
        next_tier = "GOLD"
        target_spent = 500.0
        progress_pct = min(100.0, ((spent - 100.0) / 400.0) * 100.0)
    elif customer.vip_tier == VipTier.GOLD:
        next_tier = "WHOLESALE"
        target_spent = 2000.0
        progress_pct = min(100.0, ((spent - 500.0) / 1500.0) * 100.0)

    bot_username = "DigitalStoreBot"
    return {
        "customer": {
            "id": str(customer.id),
            "telegram_id": customer.telegram_id,
            "telegram_username": customer.telegram_username,
            "first_name": customer.first_name,
            "last_name": customer.last_name,
            "language_code": customer.language_code or "ar",
            "wallet_balance": float(customer.wallet_balance),
            "total_deposited": float(customer.total_deposited or 0),
            "total_spent": float(customer.total_spent or 0),
            "completed_orders_count": customer.total_orders or 0,
            "vip_tier": customer.vip_tier.value,
            "vip_progress": {
                "next_tier": next_tier,
                "target_spent": target_spent,
                "progress_percent": round(progress_pct, 1)
            },
            "referral_code": customer.referral_code or str(customer.telegram_id),
            "referral_earnings": float(customer.referral_earnings or 0),
            "referral_count": customer.referral_count or 0,
            "api_enabled": bool(customer.api_enabled),
            "api_key_prefix": customer.api_key_prefix,
        },
        "pending_invoices": inv_list,
        "config": {
            "main_channel_url": main_url_cfg or settings.TELEGRAM_MAIN_CHANNEL_URL,
            "proof_channel_url": proof_url_cfg or settings.TELEGRAM_PROOF_CHANNEL_URL,
            "admin_telegram_id": settings.ADMIN_TELEGRAM_ID,
            "bot_username": bot_username,
        }
    }


# =========================================================================
# 2. Catalog & Products
# =========================================================================
@router.get("/catalog")
async def get_miniapp_catalog(
    session: AsyncSession = Depends(get_db)
):
    # Fetch Categories
    cat_stmt = select(Category).where(Category.is_active == True).order_by(Category.sort_order.asc())
    categories = (await session.execute(cat_stmt)).scalars().all()

    # Fetch Master Products with eager-loaded supplier links (+ supplier status)
    from src.core.services.pricing_service import pick_cheapest_sellable_link, link_is_sellable
    prod_stmt = (
        select(MasterProduct)
        .where(MasterProduct.is_active == True)
        .options(
            selectinload(MasterProduct.supplier_links).selectinload(ProductSupplierLink.supplier)
        )
        .order_by(MasterProduct.sort_order.asc())
    )
    products = (await session.execute(prod_stmt)).scalars().all()
    # Hide products with no sellable active supplier link.
    products = [p for p in products if any(link_is_sellable(l) for l in p.supplier_links)]

    # Pre-fetch active pricing rules in a single batch query
    rules_stmt = select(PricingRule).where(PricingRule.is_active == True).order_by(PricingRule.priority.desc())
    all_rules = (await session.execute(rules_stmt)).scalars().all()

    # Group products and calculate dynamic pricing & stock in-memory (0 extra DB queries!)
    cat_counts: Dict[str, int] = {}
    products_out = []

    for p in products:
        c_id = str(p.category_id)
        cat_counts[c_id] = cat_counts.get(c_id, 0) + 1

        winner_link = pick_cheapest_sellable_link(list(p.supplier_links))
        if winner_link:
            wholesale_cost = winner_link.last_known_cost
            is_in_stock = True
            available_stock = winner_link.available_quantity
            is_unlimited = winner_link.available_quantity is None
        else:
            wholesale_cost = Decimal("0.0")
            is_in_stock = False
            available_stock = 0
            is_unlimited = False

        # Match rule for this product / category / global
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

        final_price, _, _ = pricing_service.calculate_price_from_cost(wholesale_cost, matching_rule)

        products_out.append({
            "id": str(p.id),
            "category_id": c_id,
            "title_ar": p.title_ar,
            "title_en": p.title_en,
            "description_ar": getattr(p, "custom_description_ar", None) or p.description_ar or "",
            "custom_emoji": getattr(p, "custom_emoji", "📦") or "📦",
            "photo_url": p.photo_url,
            "retail_price": float(final_price),
            "is_in_stock": is_in_stock,
            "available_stock": available_stock,
            "is_unlimited": is_unlimited,
            "warranty_days": getattr(p, "warranty_days", 30) or 30,
            "requires_customer_email": bool(
                getattr(p, "requires_customer_email", False)
                or getattr(p, "requires_customer_input", False)
            ),
            "requires_customer_input": bool(getattr(p, "requires_customer_input", False)),
            "customer_input_type": getattr(p, "customer_input_type", None) or "email",
            "customer_input_label_ar": getattr(p, "customer_input_label_ar", None),
            "brand_family": getattr(p, "brand_family", None),
            "delivery_type_label": getattr(p, "delivery_type_label", None),
            "is_featured": bool(getattr(p, "is_featured", False)),
            "search_keywords": getattr(p, "search_keywords", None),
            "sort_order": p.sort_order or 0,
        })

    cats_out = []
    for c in categories:
        cats_out.append({
            "id": str(c.id),
            "name_ar": c.name_ar,
            "name_en": getattr(c, "name_en", c.name_ar) or c.name_ar,
            "icon_emoji": c.icon_emoji or "📁",
            "product_count": cat_counts.get(str(c.id), 0)
        })

    return {
        "categories": cats_out,
        "products": products_out,
        "total_products": len(products_out)
    }


# =========================================================================
# 3. Cart Checkout & Payment Orchestration
# =========================================================================
class CartItemDTO(BaseModel):
    product_id: str
    quantity: int = Field(1, ge=1, le=100)


class CheckoutRequestDTO(BaseModel):
    items: List[CartItemDTO]
    pay_via_wallet: bool = True
    coupon_code: Optional[str] = None
    payment_method: Optional[str] = "usdt_bep20"
    buyer_email: Optional[str] = None  # required when any product.requires_customer_email


@router.post("/cart/checkout")
async def checkout_cart(
    dto: CheckoutRequestDTO,
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    if not dto.items:
        raise HTTPException(status_code=400, detail="Cart is empty.")

    # Merge duplicate cart lines for the same product (quantity is additive) so a
    # double-tap on "add to cart" cannot create two separate rows/charges.
    merged = {}
    for item in dto.items:
        key = str(item.product_id)
        if key in merged:
            merged[key].quantity = min(100, merged[key].quantity + item.quantity)
        else:
            merged[key] = CartItemDTO(product_id=item.product_id, quantity=item.quantity)
    dto.items = list(merged.values())

    # 1. Price items and check stock
    total_price = Decimal("0.0")
    total_supplier_cost = Decimal("0.0")
    order_items_prepared = []

    for item in dto.items:
        import uuid
        prod_uuid = uuid.UUID(item.product_id) if isinstance(item.product_id, str) else item.product_id
        prod_stmt = select(MasterProduct).where(MasterProduct.id == prod_uuid, MasterProduct.is_active == True)
        prod = (await session.execute(prod_stmt)).scalar_one_or_none()
        if not prod:
            raise HTTPException(status_code=400, detail=f"Product {item.product_id} not found.")
        if getattr(prod, "requires_customer_email", False) and not (dto.buyer_email or "").strip():
            raise HTTPException(
                status_code=400,
                detail=f"المنتج '{prod.title_ar}' يتطلب بريدك الإلكتروني قبل الدفع.",
            )

        pricing_info = await pricing_service.get_best_supplier_and_price(session, prod_uuid)
        if not pricing_info.get("is_available"):
            raise HTTPException(status_code=400, detail=f"المنتج '{prod.title_ar}' غير متوفر بالمخزون حالياً.")

        winner_link = pricing_info["winner_link"]
        if winner_link.available_quantity and item.quantity > winner_link.available_quantity:
            raise HTTPException(
                status_code=400,
                detail=f"الكمية المتاحة من '{prod.title_ar}' هي {winner_link.available_quantity} فقط."
            )

        unit_price = pricing_info["final_price"]
        unit_cost = pricing_info.get("supplier_cost") or pricing_info.get("wholesale_cost") or Decimal("0.0")
        item_total = unit_price * Decimal(item.quantity)
        item_cost_total = unit_cost * Decimal(item.quantity)

        total_price += item_total
        total_supplier_cost += item_cost_total

        order_items_prepared.append({
            "product_id": prod.id,
            "link_id": winner_link.id,
            "supplier_id": winner_link.supplier_id,
            "supplier_product_id": winner_link.supplier_product_id,
            "unit_price": unit_price,
            "total_price": item_total,
            "unit_cost": unit_cost,
            "total_cost": item_cost_total,
            "quantity": item.quantity,
        })

    # 2. Evaluate VIP Tier Discount & Promo Coupon Stacking
    vip_discount_percent = Decimal("0.0")
    if customer.vip_tier == VipTier.WHOLESALE:
        vip_discount_percent = Decimal("8.0")
    elif customer.vip_tier == VipTier.GOLD:
        vip_discount_percent = Decimal("5.0")
    elif customer.vip_tier == VipTier.SILVER:
        vip_discount_percent = Decimal("2.0")

    vip_discount_amount = (total_price * (vip_discount_percent / Decimal("100.0"))).quantize(Decimal("0.01"))
    coupon_discount_amount = Decimal("0.0")
    applied_coupon_id = None
    coupon_error = None

    if dto.coupon_code:
        now_utc = _utc_now()
        code = dto.coupon_code.strip().upper()
        coup_stmt = select(Coupon).where(Coupon.code == code, Coupon.is_active == True)
        coup = (await session.execute(coup_stmt)).scalar_one_or_none()
        if not coup:
            coupon_error = "كود الخصم غير صحيح أو غير مفعّل."
        elif coup.valid_from and now_utc < _normalize_utc_dt(coup.valid_from):
            coupon_error = "هذا الكوبون لم يبدأ تفعيله بعد."
        elif coup.valid_until and now_utc > _normalize_utc_dt(coup.valid_until):
            coupon_error = "انتهت صلاحية هذا الكوبون."
        elif coup.max_uses and (coup.used_count or 0) >= coup.max_uses:
            coupon_error = "وصل هذا الكوبون إلى الحد الأقصى من مرات الاستخدام."
        elif total_price < (coup.min_order_usd or Decimal("0.0")):
            coupon_error = f"الحد الأدنى للطلب هو ${coup.min_order_usd:.2f}."
        else:
            if coup.discount_type == DiscountType.PERCENT:
                calc_discount = (total_price * Decimal(str(coup.discount_value / 100))).quantize(Decimal("0.01"))
            else:
                calc_discount = Decimal(str(coup.discount_value)).quantize(Decimal("0.01"))
            coupon_discount_amount = calc_discount
            applied_coupon_id = coup.id
            coup.used_count = (coup.used_count or 0) + 1

    # Circuit breaker: caps the FULL stack (VIP + coupon) against cashback, referral
    # commission and network gas, so net margin can never go negative on any channel.
    incentive_rate = await pricing_service.get_incentive_rate(session)
    requested_discounts = vip_discount_amount + coupon_discount_amount
    total_discounts = pricing_service.clamp_total_discount(
        gross_total=total_price,
        requested_discount=requested_discounts,
        supplier_cost=total_supplier_cost,
        incentive_rate=incentive_rate,
    )
    if total_discounts < requested_discounts:
        # Preserve coupon priority, then trim the VIP portion of the stack.
        if coupon_discount_amount >= total_discounts:
            coupon_discount_amount = total_discounts
            vip_discount_amount = Decimal("0.0")
        else:
            vip_discount_amount = total_discounts - coupon_discount_amount

    discount_amount = total_discounts
    final_payable = pricing_service.enforce_profit_floor(
        gross_total=total_price,
        payable=max(Decimal("0.01"), total_price - discount_amount),
        supplier_cost=total_supplier_cost,
        incentive_rate=incentive_rate,
    )
    # If the floor had to lift the price above the discounted list price, the applied
    # discount is zero (never advertise a negative discount).
    discount_amount = max(Decimal("0.00"), total_price - final_payable)

    # 3. Pay via Wallet
    if dto.pay_via_wallet:
        reserved = []
        try:
            # Atomically reserve stock for every line BEFORE taking money. This closes
            # the oversell race where 10 buyers all pass a stale Python stock check.
            for it in order_items_prepared:
                ok = await order_service.reserve_supplier_stock(
                    session, it["link_id"], it["quantity"]
                )
                if not ok:
                    raise HTTPException(
                        status_code=400,
                        detail="المنتج نفد من المخزون أثناء إتمام الطلب. يرجى التحديث والمحاولة مجدداً."
                    )
                reserved.append(it)

            # Atomic wallet debit handled centrally by order_service with Redis distributed lock & row lock
            await order_service.debit_customer_wallet(
                session=session,
                customer_id=str(customer.id),
                amount=final_payable,
                tx_type=WalletTxType.PURCHASE_DEBIT,
                description=f"شراء فوري من المتجر التفاعلي Mini App ({len(order_items_prepared)} منتجات)"
            )
        except HTTPException:
            for it in reserved:
                await order_service.release_supplier_stock(session, it["link_id"], it["quantity"])
            await session.commit()
            raise
        except ValueError as err:
            for it in reserved:
                await order_service.release_supplier_stock(session, it["link_id"], it["quantity"])
            await session.commit()
            deficit = max(Decimal("0.01"), final_payable - customer.wallet_balance)
            return {
                "status": "insufficient_balance",
                "deficit": float(deficit),
                "total_required": float(final_payable),
                "current_balance": float(customer.wallet_balance),
                "message": f"ينقصك ${deficit:.2f} لإتمام هذا الطلب: {err}"
            }

        created_orders = []
        for it in order_items_prepared:
            order, _ = await order_service.create_order(
                session=session,
                customer_id=str(customer.id),
                master_product_id=str(it["product_id"]),
                supplier_id=str(it["supplier_id"]),
                supplier_product_id=it["supplier_product_id"],
                supplier_cost=it["total_cost"],
                our_price=it["total_price"],
                profit_margin=it["total_price"] - it["total_cost"],
                payment_method=PaymentMethod.INTERNAL_BALANCE,
                quantity=it["quantity"],
                buyer_email=dto.buyer_email,
                buyer_activation_id=dto.buyer_email,
            )
            if applied_coupon_id:
                order.coupon_id = applied_coupon_id
                order.discount_amount = discount_amount

            await order_service.transition_order_state(
                session, order, OrderState.PAYMENT_CONFIRMED, "miniapp_instant_wallet_purchase"
            )
            created_orders.append(order)

        await session.commit()

        for ord_obj in created_orders:
            await enqueue_jit_order(str(ord_obj.id))

        return {
            "status": "paid",
            "message": "تم الدفع بنجاح وجاري التسليم الفوري!",
            "order_ids": [str(o.id) for o in created_orders],
            "total_paid": float(final_payable),
            "discount_applied": float(discount_amount),
            "coupon_error": coupon_error,
            "remaining_balance": float(customer.wallet_balance)
        }

    # 4. Generate Top-Up or Order Payment Invoice
    method_enum = PaymentMethod.USDT_BEP20_DIRECT
    curr = "USDT"

    if dto.payment_method == "usdt_trc20":
        method_enum = PaymentMethod.USDT_TRC20_DIRECT
    elif dto.payment_method == "binance_pay":
        method_enum = PaymentMethod.BINANCE_PAY
    elif dto.payment_method == "sham_cash":
        method_enum = PaymentMethod.SHAM_CASH

    dep_address = _resolve_deposit(method_enum)
    if method_enum in (
        PaymentMethod.USDT_TRC20_DIRECT,
        PaymentMethod.BINANCE_PAY,
        PaymentMethod.USDT_BEP20_DIRECT,
    ) and not dep_address:
        raise HTTPException(
            status_code=503,
            detail=f"Payment rail {method_enum.value} is not configured",
        )

    unique_amt = order_service.generate_unique_invoice_amount(final_payable)
    expires_at = _utc_now() + timedelta(minutes=settings.INVOICE_TIMEOUT_MINUTES or 30)

    invoice = PaymentInvoice(
        customer_id=customer.id,
        payment_method=method_enum,
        amount_expected=final_payable,
        unique_amount=unique_amt,
        currency=curr,
        deposit_address=dep_address,
        status=InvoiceStatus.PENDING,
        expires_at=expires_at
    )
    session.add(invoice)
    await session.commit()
    await session.refresh(invoice)

    return {
        "status": "invoice_created",
        "invoice": {
            "id": str(invoice.id),
            "payment_method": method_enum.value,
            "amount_expected": float(final_payable),
            "unique_amount": float(unique_amt),
            "deposit_address": dep_address,
            "currency": curr,
            "expires_at": expires_at.isoformat(),
            "remaining_seconds": 1800,
            "coupon_error": coupon_error,
        }
    }


# =========================================================================
# 4. Direct Wallet Top-Up Invoice
# =========================================================================
class TopUpRequestDTO(BaseModel):
    amount: float = Field(..., gt=0.5, le=5000)
    network: str = "usdt_bep20"  # usdt_bep20, usdt_trc20, binance_pay, sham_cash


@router.post("/wallet/topup")
async def create_wallet_topup(
    dto: TopUpRequestDTO,
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    amt = Decimal(str(round(dto.amount, 2)))
    method_enum = PaymentMethod.USDT_BEP20_DIRECT
    curr = "USDT"

    if dto.network == "usdt_trc20":
        method_enum = PaymentMethod.USDT_TRC20_DIRECT
    elif dto.network == "binance_pay":
        method_enum = PaymentMethod.BINANCE_PAY
    elif dto.network == "sham_cash":
        method_enum = PaymentMethod.SHAM_CASH

    dep_address = _resolve_deposit(method_enum)
    if method_enum in (
        PaymentMethod.USDT_TRC20_DIRECT,
        PaymentMethod.BINANCE_PAY,
        PaymentMethod.USDT_BEP20_DIRECT,
    ) and not dep_address:
        raise HTTPException(
            status_code=503,
            detail=f"Payment rail {method_enum.value} is not configured",
        )

    unique_amt = order_service.generate_unique_invoice_amount(amt)
    expires_at = _utc_now() + timedelta(minutes=30)

    inv = PaymentInvoice(
        customer_id=customer.id,
        payment_method=method_enum,
        amount_expected=amt,
        unique_amount=unique_amt,
        currency=curr,
        deposit_address=dep_address,
        status=InvoiceStatus.PENDING,
        expires_at=expires_at
    )
    session.add(inv)
    await session.commit()
    await session.refresh(inv)

    return {
        "status": "ok",
        "invoice": {
            "id": str(inv.id),
            "payment_method": method_enum.value,
            "amount_expected": float(amt),
            "unique_amount": float(unique_amt),
            "deposit_address": dep_address,
            "currency": curr,
            "expires_at": expires_at.isoformat(),
            "remaining_seconds": 1800,
        }
    }


# =========================================================================
# 5. Live Payment Status & Cancel
# =========================================================================
@router.get("/payments/{invoice_id}/status")
async def get_payment_status(
    invoice_id: str,
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    import uuid
    try:
        inv_uuid = uuid.UUID(invoice_id) if isinstance(invoice_id, str) else invoice_id
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Invoice not found.")

    stmt = select(PaymentInvoice).where(PaymentInvoice.id == inv_uuid, PaymentInvoice.customer_id == customer.id)
    inv = (await session.execute(stmt)).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    now_utc = _utc_now()
    exp_dt = _normalize_utc_dt(inv.expires_at)
    rem_sec = max(0, int((exp_dt - now_utc).total_seconds())) if exp_dt else 0

    if rem_sec == 0 and inv.status == InvoiceStatus.PENDING:
        inv.status = InvoiceStatus.EXPIRED
        await session.commit()

    return {
        "id": str(inv.id),
        "status": inv.status.value,
        "amount_expected": float(inv.amount_expected),
        "unique_amount": float(inv.unique_amount) if inv.unique_amount else float(inv.amount_expected),
        "tx_hash": inv.tx_hash,
        "remaining_seconds": rem_sec,
        "is_confirmed": inv.status == InvoiceStatus.CONFIRMED,
        "is_expired": inv.status == InvoiceStatus.EXPIRED,
    }


@router.post("/payments/{invoice_id}/cancel")
async def cancel_payment_invoice(
    invoice_id: str,
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    import uuid
    try:
        inv_uuid = uuid.UUID(invoice_id) if isinstance(invoice_id, str) else invoice_id
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Invoice not found.")

    stmt = select(PaymentInvoice).where(PaymentInvoice.id == inv_uuid, PaymentInvoice.customer_id == customer.id)
    inv = (await session.execute(stmt)).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    if inv.status == InvoiceStatus.PENDING:
        inv.status = InvoiceStatus.EXPIRED
        await session.commit()

    return {"status": "ok", "message": "Invoice cancelled."}


# =========================================================================
# 6. Wallet History & Ledger
# =========================================================================
@router.get("/wallet/history")
async def get_wallet_history(
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    tx_stmt = (
        select(WalletTransaction)
        .where(WalletTransaction.customer_id == customer.id)
        .order_by(desc(WalletTransaction.created_at))
        .limit(50)
    )
    txs = (await session.execute(tx_stmt)).scalars().all()

    out = []
    for t in txs:
        out.append({
            "id": str(t.id),
            "amount": float(t.amount),
            "balance_after": float(t.balance_after),
            "tx_type": t.tx_type.value,
            "description": t.description,
            "order_id": str(t.order_id) if t.order_id else None,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    return {
        "wallet_balance": float(customer.wallet_balance),
        "total_deposited": float(customer.total_deposited or 0),
        "total_spent": float(customer.total_spent or 0),
        "transactions": out
    }


# =========================================================================
# 7. Customer Orders & Dispute Tickets
# =========================================================================
@router.get("/orders")
async def get_miniapp_orders(
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Order)
        .where(Order.customer_id == customer.id)
        .options(selectinload(Order.master_product))
        .order_by(desc(Order.created_at))
        .limit(50)
    )
    orders = (await session.execute(stmt)).scalars().all()

    # Batch-load formal invoice numbers in ONE query (avoids an N+1 loop per order).
    formal_invoice_numbers = {}
    order_ids = [o.id for o in orders]
    if order_ids:
        fi_stmt = select(FormalInvoice.order_id, FormalInvoice.invoice_number).where(
            FormalInvoice.order_id.in_(order_ids)
        )
        formal_invoice_numbers = {
            str(row[0]): row[1] for row in (await session.execute(fi_stmt)).all()
        }

    now_utc = _utc_now()
    orders_out = []
    for o in orders:
        fi_num = formal_invoice_numbers.get(str(o.id))

        is_warr_active = bool(o.warranty_expires_at and o.warranty_expires_at > now_utc)

        orders_out.append({
            "id": str(o.id),
            "order_number": o.order_number,
            "product_title": o.master_product.title_ar if o.master_product else "اشتراك رقمي",
            "product_emoji": getattr(o.master_product, "custom_emoji", "📦") or "📦",
            "quantity": o.quantity or 1,
            "our_price": float(o.our_price),
            "state": o.state.value,
            "delivered_product": o.delivered_product,
            "warranty_expires_at": o.warranty_expires_at.strftime("%Y-%m-%d") if o.warranty_expires_at else None,
            "is_warranty_active": is_warr_active,
            "formal_invoice_number": fi_num,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        })

    return {"orders": orders_out}


@router.get("/orders/{order_id}")
async def get_miniapp_order_detail(
    order_id: str,
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    import uuid
    o_uuid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
    stmt = (
        select(Order)
        .where(Order.id == o_uuid)
        .options(selectinload(Order.master_product))
    )
    order = (await session.execute(stmt)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found.")
    if order.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not own this order.")

    return {
        "id": str(order.id),
        "order_number": order.order_number,
        "product_title": order.master_product.title_ar if order.master_product else "",
        "state": order.state.value,
        "our_price": float(order.our_price),
        "delivered_product": order.delivered_product,
    }


class OpenTicketDTO(BaseModel):
    order_id: Optional[str] = None
    subject: str
    message: str


@router.post("/tickets")
async def create_support_ticket(
    dto: OpenTicketDTO,
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    import uuid
    # One open dispute per order/customer. Re-opening the same dispute must return the
    # existing ticket (or be rejected) instead of creating unlimited duplicate tickets.
    dup_stmt = select(SupportTicket).where(
        and_(
            SupportTicket.customer_id == customer.id,
            SupportTicket.status.in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.AWAITING_CUSTOMER]),
        )
    )
    if dto.order_id:
        try:
            dup_stmt = dup_stmt.where(SupportTicket.order_id == uuid.UUID(dto.order_id))
        except (ValueError, AttributeError):
            pass
    existing = (await session.execute(dup_stmt.order_by(SupportTicket.created_at.desc()))).scalars().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"يوجد تذكرة مفتوحة بالفعل لهذا الطلب: #T-{str(existing.id)[:8]}",
        )

    target_order_id = None
    if dto.order_id:
        o_uuid = uuid.UUID(dto.order_id) if isinstance(dto.order_id, str) else dto.order_id
        order_stmt = select(Order).where(Order.id == o_uuid)
        order = (await session.execute(order_stmt)).scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")
        if order.customer_id != customer.id:
            raise HTTPException(status_code=403, detail="Forbidden: You cannot open a ticket on another customer's order.")
        target_order_id = order.id

    ticket = SupportTicket(
        customer_id=customer.id,
        order_id=target_order_id,
        subject=dto.subject,
        status=TicketStatus.OPEN,
        priority=1
    )
    session.add(ticket)
    await session.flush()

    msg = TicketMessage(
        ticket_id=ticket.id,
        sender_type="customer",
        sender_name=customer.first_name or f"User {customer.telegram_id}",
        message_text=dto.message
    )
    session.add(msg)
    await session.commit()

    return {"status": "ok", "ticket_id": str(ticket.id), "message": "تم إرسال تذكرتك بنجاح لفريق الدعم."}


@router.get("/tickets/{ticket_id}")
async def get_miniapp_ticket_detail(
    ticket_id: str,
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    import uuid
    t_uuid = uuid.UUID(ticket_id) if isinstance(ticket_id, str) else ticket_id
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.id == t_uuid)
        .options(selectinload(SupportTicket.messages))
    )
    ticket = (await session.execute(stmt)).scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    if ticket.customer_id != customer.id:
        raise HTTPException(status_code=403, detail="Forbidden: You do not own this ticket.")

    return {
        "id": str(ticket.id),
        "subject": ticket.subject,
        "status": ticket.status.value,
        "created_at": ticket.created_at.isoformat(),
        "messages": [
            {
                "sender_type": m.sender_type,
                "sender_name": m.sender_name,
                "message_text": m.message_text,
                "created_at": m.created_at.isoformat()
            }
            for m in ticket.messages
        ]
    }


# =========================================================================
# 8. B2B Reseller API Key Generation
# =========================================================================
@router.post("/reseller/generate-key")
async def generate_reseller_key(
    customer: Customer = Depends(get_current_miniapp_customer),
    session: AsyncSession = Depends(get_db)
):
    raw_key = f"sk_live_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    prefix = raw_key[:12] + "..."

    customer.api_key_hash = key_hash
    customer.api_key_prefix = prefix
    customer.api_enabled = True
    await session.commit()

    return {
        "status": "ok",
        "api_key": raw_key,
        "prefix": prefix,
        "message": "انسخ مفتاحك الآن، لن يتم عرضه مرة أخرى لأسباب أمنية."
    }
