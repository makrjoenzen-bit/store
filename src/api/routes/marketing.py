import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db, AsyncSessionLocal
from src.core.models.entities import (
    Coupon, TopUpBonusTier, BroadcastCampaign, BotConfig, Customer, AdminUser, CustomerReview
)
from src.core.models.enums import DiscountType
from src.api.routes.auth import get_current_admin
from src.core.redis import event_bus
from src.workers.broadcast_worker import run_broadcast_task

router = APIRouter(prefix="/marketing", tags=["Marketing, Loyalty & Broadcast"])


def _utc_now():
    return datetime.now(timezone.utc)


# =========================================================================
# 1. Coupons Management
# =========================================================================

class CouponCreateDTO(BaseModel):
    code: str
    discount_type: DiscountType = DiscountType.PERCENT
    value: float
    min_order_usd: float = 0.0
    max_uses: Optional[int] = None
    expires_at: Optional[datetime] = None


@router.get("/coupons")
async def list_coupons(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(Coupon).order_by(Coupon.created_at.desc())
    coupons = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(c.id),
            "code": c.code,
            "discount_type": c.discount_type.value,
            "value": float(c.value),
            "min_order_usd": float(c.min_order_usd),
            "max_uses": c.max_uses,
            "used_count": c.used_count,
            "is_active": c.is_active,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in coupons
    ]


@router.post("/coupons")
async def create_coupon(
    dto: CouponCreateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    clean_code = dto.code.strip().upper()
    existing = (await session.execute(select(Coupon).where(Coupon.code == clean_code))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="كود الكوبون موجود مسبقاً")

    coupon = Coupon(
        code=clean_code,
        discount_type=dto.discount_type,
        value=Decimal(str(dto.value)),
        min_order_usd=Decimal(str(dto.min_order_usd)),
        max_uses=dto.max_uses,
        expires_at=dto.expires_at,
        is_active=True,
    )
    session.add(coupon)
    await session.commit()
    return {"status": "ok", "coupon_id": str(coupon.id)}


@router.put("/coupons/{coupon_id}/toggle")
async def toggle_coupon(
    coupon_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    coupon = (await session.execute(select(Coupon).where(Coupon.id == coupon_id))).scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="الكوبون غير موجود")

    coupon.is_active = not coupon.is_active
    await session.commit()
    return {"status": "ok", "is_active": coupon.is_active}


@router.delete("/coupons/{coupon_id}")
async def delete_coupon(
    coupon_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    coupon = (await session.execute(select(Coupon).where(Coupon.id == coupon_id))).scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="الكوبون غير موجود")

    await session.delete(coupon)
    await session.commit()
    return {"status": "ok"}


# =========================================================================
# 2. Loyalty Settings & Top-Up Bonus Tiers
# =========================================================================

class LoyaltySettingsDTO(BaseModel):
    cashback_percent: float = 2.0
    referral_commission_percent: float = 5.0


class TopUpTierCreateDTO(BaseModel):
    min_deposit_usd: float
    bonus_usd: float


@router.get("/loyalty-settings")
@router.get("/topup-bonus-tiers")
async def get_loyalty_settings(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    # Fetch cashback and referral percent from BotConfig
    cb_cfg = (await session.execute(select(BotConfig).where(BotConfig.key == "cashback_percent"))).scalar_one_or_none()
    ref_cfg = (await session.execute(select(BotConfig).where(BotConfig.key == "referral_commission_percent"))).scalar_one_or_none()

    cashback_percent = float(cb_cfg.value) if cb_cfg and cb_cfg.value is not None else 2.0
    referral_percent = float(ref_cfg.value) if ref_cfg and ref_cfg.value is not None else 5.0

    tiers_stmt = select(TopUpBonusTier).order_by(TopUpBonusTier.min_deposit_usd.asc())
    tiers = (await session.execute(tiers_stmt)).scalars().all()

    return {
        "cashback_percent": cashback_percent,
        "referral_commission_percent": referral_percent,
        "tiers": [
            {
                "id": str(t.id),
                "min_deposit_usd": float(t.min_deposit_usd),
                "bonus_usd": float(t.bonus_usd),
                "is_active": t.is_active,
            }
            for t in tiers
        ]
    }


@router.put("/loyalty-settings")
async def update_loyalty_settings(
    dto: LoyaltySettingsDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    async def set_config(k: str, val: float):
        cfg = (await session.execute(select(BotConfig).where(BotConfig.key == k))).scalar_one_or_none()
        if cfg:
            cfg.value = val
            cfg.updated_by = admin.username
            cfg.updated_at = _utc_now()
        else:
            session.add(BotConfig(key=k, value=val, updated_by=admin.username))

    await set_config("cashback_percent", dto.cashback_percent)
    await set_config("referral_commission_percent", dto.referral_commission_percent)
    await session.commit()
    await event_bus.publish_config_change("loyalty_settings")
    return {"status": "ok"}


@router.post("/topup-bonus-tiers")
async def create_topup_tier(
    dto: TopUpTierCreateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    tier = TopUpBonusTier(
        min_deposit_usd=Decimal(str(dto.min_deposit_usd)),
        bonus_usd=Decimal(str(dto.bonus_usd)),
        is_active=True,
    )
    session.add(tier)
    await session.commit()
    return {"status": "ok", "id": str(tier.id)}


@router.delete("/topup-bonus-tiers/{tier_id}")
async def delete_topup_tier(
    tier_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    tier = (await session.execute(select(TopUpBonusTier).where(TopUpBonusTier.id == tier_id))).scalar_one_or_none()
    if not tier:
        raise HTTPException(status_code=404, detail="البونص غير موجود")
    await session.delete(tier)
    await session.commit()
    return {"status": "ok"}


# =========================================================================
# 3. Broadcast Campaigns (Safe Rate-Limited Broadcast)
# =========================================================================

class BroadcastLaunchDTO(BaseModel):
    title: str
    message_text: str
    image_url: Optional[str] = None
    cta_button_text: Optional[str] = None
    cta_button_url: Optional[str] = None
    target_audience: str = "all"  # 'all', 'buyers_only', 'balance_holders'


@router.get("/reviews")
async def list_customer_reviews(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(CustomerReview).order_by(CustomerReview.created_at.desc())
    res = await session.execute(stmt)
    reviews = res.scalars().all()
    return [
        {
            "id": str(r.id),
            "order_id": str(r.order_id),
            "customer_id": str(r.customer_id),
            "rating": r.rating,
            "comment": r.comment,
            "is_published_to_channel": r.is_published_to_channel,
            "created_at": r.created_at.isoformat()
        }
        for r in reviews
    ]


@router.get("/broadcasts")
async def list_broadcasts(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(BroadcastCampaign).order_by(desc(BroadcastCampaign.created_at)).limit(20)
    campaigns = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "message_text": c.message_text,
            "image_url": c.image_url,
            "cta_button_text": c.cta_button_text,
            "cta_button_url": c.cta_button_url,
            "target_audience": c.target_audience,
            "status": c.status,
            "total_targets": c.total_targets,
            "sent_count": c.sent_count,
            "failed_count": c.failed_count,
            "started_at": c.started_at.isoformat() if c.started_at else None,
            "completed_at": c.completed_at.isoformat() if c.completed_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in campaigns
    ]


@router.post("/broadcasts")
async def launch_broadcast(
    dto: BroadcastLaunchDTO,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    campaign = BroadcastCampaign(
        title=dto.title,
        message_text=dto.message_text,
        image_url=dto.image_url,
        cta_button_text=dto.cta_button_text,
        cta_button_url=dto.cta_button_url,
        target_audience=dto.target_audience,
        status="running",
        started_at=_utc_now(),
    )
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)

    # Launch in background throttled worker at 25 msgs/sec
    background_tasks.add_task(run_broadcast_task, str(campaign.id))

    return {
        "status": "ok",
        "campaign_id": str(campaign.id),
        "message": "تم إطلاق حملة البث الجماعي بأمان بمعدل 25 رسالة/ثانية"
    }


@router.get("/broadcasts/{campaign_id}/progress")
async def get_broadcast_progress(
    campaign_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin)
):
    stmt = select(BroadcastCampaign).where(BroadcastCampaign.id == campaign_id)
    campaign = (await session.execute(stmt)).scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="الحملة غير موجودة")

    return {
        "id": str(campaign.id),
        "status": campaign.status,
        "total_targets": campaign.total_targets,
        "sent_count": campaign.sent_count,
        "failed_count": campaign.failed_count,
        "completed_at": campaign.completed_at.isoformat() if campaign.completed_at else None,
    }


# =========================================================================
# 4. Visual Promo Ad Creator
# =========================================================================

class PromoAdPublishDTO(BaseModel):
    caption: str
    product_id: Optional[str] = None
    image_url: Optional[str] = None
    image_file_id: Optional[str] = None
    store_button_label: str = "🏪 فتح المتجر (الموقع / Mini App)"
    bot_button_label: str = "🤖 الشراء عبر البوت"
    publish_proof_channel: bool = True
    publish_main_channel: bool = True
    broadcast_customers: bool = False
    pin_in_channel: bool = False


@router.post("/ads/publish")
async def publish_promo_ad(
    dto: PromoAdPublishDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.promo_ad_service import promo_ad_service

    try:
        result = await promo_ad_service.publish_promo_ad(
            session,
            caption=dto.caption,
            reviewer=getattr(admin, "username", None) or getattr(admin, "email", None) or f"admin:{admin.id}",
            admin_user_id=admin.id,
            product_id=dto.product_id,
            image_url=dto.image_url,
            image_file_id=dto.image_file_id,
            store_button_label=dto.store_button_label,
            bot_button_label=dto.bot_button_label,
            publish_proof_channel=dto.publish_proof_channel,
            publish_main_channel=dto.publish_main_channel,
            broadcast_customers=dto.broadcast_customers,
            pin_in_channel=dto.pin_in_channel,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"فشل نشر الإعلان: {e}")


# =========================================================================
# 5. Referral Milestone Giveaway Campaigns
# =========================================================================

class ReferralCampaignDTO(BaseModel):
    title: str
    reward_name: str
    required_invites: int = 3
    max_claims_per_user: int = 1
    require_channel_sub: bool = True
    auto_deliver: bool = True
    is_active: bool = True


class ReferralCampaignUpdateDTO(BaseModel):
    title: Optional[str] = None
    reward_name: Optional[str] = None
    required_invites: Optional[int] = None
    max_claims_per_user: Optional[int] = None
    require_channel_sub: Optional[bool] = None
    auto_deliver: Optional[bool] = None
    is_active: Optional[bool] = None


class ReferralStockDTO(BaseModel):
    payloads: List[str]  # one account/key per line item


def _admin_reviewer(admin: AdminUser) -> str:
    return getattr(admin, "username", None) or getattr(admin, "email", None) or f"admin:{admin.id}"


@router.get("/campaigns")
async def list_referral_campaigns(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.referral_campaign_service import referral_campaign_service
    return await referral_campaign_service.list_campaigns(session)


@router.post("/campaigns")
async def create_referral_campaign(
    dto: ReferralCampaignDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.referral_campaign_service import referral_campaign_service
    try:
        return await referral_campaign_service.create_campaign(
            session,
            title=dto.title,
            reward_name=dto.reward_name,
            required_invites=dto.required_invites,
            max_claims_per_user=dto.max_claims_per_user,
            require_channel_sub=dto.require_channel_sub,
            auto_deliver=dto.auto_deliver,
            is_active=dto.is_active,
            reviewer=_admin_reviewer(admin),
            admin_user_id=admin.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/campaigns/{campaign_id}")
async def update_referral_campaign(
    campaign_id: str,
    dto: ReferralCampaignUpdateDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.referral_campaign_service import referral_campaign_service
    try:
        return await referral_campaign_service.update_campaign(
            session,
            campaign_id,
            reviewer=_admin_reviewer(admin),
            admin_user_id=admin.id,
            **dto.model_dump(exclude_unset=True),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/campaigns/{campaign_id}/stock")
async def upload_campaign_stock(
    campaign_id: str,
    dto: ReferralStockDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.referral_campaign_service import referral_campaign_service
    try:
        return await referral_campaign_service.add_stock(
            session,
            campaign_id,
            dto.payloads,
            reviewer=_admin_reviewer(admin),
            admin_user_id=admin.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/campaigns/{campaign_id}/claims")
async def list_campaign_claims(
    campaign_id: str,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.referral_campaign_service import referral_campaign_service
    return await referral_campaign_service.list_claims(session, campaign_id)


# =========================================================================
# 6. Auto Ad-Image Generator + Competitor Radar
# =========================================================================

class GenerateAdImageDTO(BaseModel):
    product_id: Optional[str] = None
    template: str = "promo_flash"  # promo_flash | just_restocked | referral_giveaway
    title: Optional[str] = None
    price_usd: Optional[float] = None
    price_syp: Optional[float] = None
    features: Optional[List[str]] = None
    warranty_days: Optional[int] = None
    stock: Optional[int] = None
    brand_name: str = "Digital Store"
    campaign_line: Optional[str] = None


@router.post("/ads/generate-image")
async def generate_ad_image(
    dto: GenerateAdImageDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    """Generate a high-res PNG promo image (base64) for Dashboard / Admin Bot."""
    import base64
    from src.core.services.ad_image_generator import ad_image_generator
    from src.core.models.entities import MasterProduct
    from src.core.services.pricing_service import pricing_service
    from src.core.services.syp_rate_service import get_sham_cash_rate, usd_to_syp

    title = dto.title
    price = dto.price_usd
    warranty = dto.warranty_days or 30
    stock = dto.stock
    if dto.product_id:
        prod = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == dto.product_id)
        )).scalar_one_or_none()
        if not prod:
            raise HTTPException(status_code=404, detail="المنتج غير موجود")
        title = title or prod.title_ar
        warranty = dto.warranty_days or int(prod.warranty_days or 30)
        pricing = await pricing_service.get_best_supplier_and_price(session, str(prod.id))
        if price is None:
            price = float(pricing.get("final_price") or prod.cached_final_price or 0)
        if stock is None and pricing.get("winner_link") is not None:
            aq = pricing["winner_link"].available_quantity
            stock = int(aq) if aq is not None else None

    if not title or price is None:
        raise HTTPException(status_code=400, detail="title و price_usd مطلوبان أو product_id")

    price_syp = dto.price_syp
    if price_syp is None:
        rate, accept = await get_sham_cash_rate(session)
        if accept:
            price_syp = float(usd_to_syp(Decimal(str(price)), rate))

    png = ad_image_generator.generate(
        template=dto.template,
        title=title,
        price_usd=price,
        price_syp=price_syp,
        features=dto.features,
        warranty_days=warranty,
        stock=stock,
        brand_name=dto.brand_name,
        campaign_line=dto.campaign_line,
    )
    b64 = base64.b64encode(png).decode("ascii")
    return {
        "status": "ok",
        "template": dto.template,
        "content_type": "image/png",
        "size_bytes": len(png),
        "image_base64": b64,
        "data_url": f"data:image/png;base64,{b64}",
    }


@router.get("/radar")
async def competitor_radar_table(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.competitor_radar_service import competitor_radar_service
    return await competitor_radar_service.comparison_table(session)


@router.post("/radar/scan")
async def competitor_radar_scan(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.services.competitor_radar_service import competitor_radar_service
    return await competitor_radar_service.scan_all(session)


class CompetitorSourceDTO(BaseModel):
    name: str
    handle_or_url: str
    source_type: str = "telegram_web"
    keyword_map: List[dict] = []
    is_active: bool = True


@router.get("/radar/sources")
async def list_radar_sources(
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.models.entities import CompetitorSource
    rows = (await session.execute(select(CompetitorSource))).scalars().all()
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "handle_or_url": r.handle_or_url,
            "source_type": r.source_type,
            "keyword_map": r.keyword_map or [],
            "is_active": r.is_active,
        }
        for r in rows
    ]


@router.post("/radar/sources")
async def create_radar_source(
    dto: CompetitorSourceDTO,
    session: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
):
    from src.core.models.entities import CompetitorSource
    row = CompetitorSource(
        name=dto.name,
        handle_or_url=dto.handle_or_url,
        source_type=dto.source_type,
        keyword_map=dto.keyword_map or [],
        is_active=dto.is_active,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"status": "ok", "id": str(row.id)}
