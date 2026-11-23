import asyncio
import json
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import select

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    AdminUser, Category, MasterProduct, Supplier,
    ProductSupplierLink, PricingRule, BotConfig, Customer,
    Coupon, TopUpBonusTier, FormalInvoice, CustomerReview
)
from src.core.models.enums import (
    SupplierStatus, PaymentNetwork, PricingScope, PricingRuleType,
    DiscountType, VipTier, CustomerStatus
)
from src.core.security import get_password_hash, encrypt_secret
from loguru import logger


async def seed():
    logger.info("Validating and creating all database tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables validated.")

    async with AsyncSessionLocal() as session:
        # 1. Admin User
        admin_check = await session.execute(select(AdminUser).where(AdminUser.username == "admin"))
        if not admin_check.scalar_one_or_none():
            admin = AdminUser(
                username="admin",
                password_hash=get_password_hash("admin123456"),
                display_name="Master Administrator",
                role="admin",
            )
            session.add(admin)
            logger.info("Created default admin user: admin / admin123456")

        # 2. Categories
        cat_check = await session.execute(select(Category))
        categories = cat_check.scalars().all()
        if not categories:
            cat_ai = Category(
                name_ar="اشتراكات الذكاء الاصطناعي",
                name_en="AI Subscriptions",
                icon_emoji="🤖",
                sort_order=1,
            )
            cat_software = Category(
                name_ar="مفاتيح البرامج والتفعيل",
                name_en="Software & Licenses",
                icon_emoji="💻",
                sort_order=2,
            )
            cat_vpn = Category(
                name_ar="شبكات وخوادم VPN",
                name_en="VPN & Security",
                icon_emoji="🛡️",
                sort_order=3,
            )
            session.add_all([cat_ai, cat_software, cat_vpn])
            await session.flush()
        else:
            cat_ai = categories[0]
            cat_software = categories[1] if len(categories) > 1 else categories[0]

        # 3. Master Products
        prod_check = await session.execute(select(MasterProduct))
        products = prod_check.scalars().all()
        if not products:
            prod_chatgpt = MasterProduct(
                category_id=cat_ai.id,
                internal_sku="CHATGPT-PLUS-1M",
                title_ar="اشتراك ChatGPT Plus (شهر كامل)",
                title_en="ChatGPT Plus (1 Month)",
                description_ar="اشتراك رسمي مع الوصول إلى نماذج GPT-4o وDALL-E 3 وتحليل البيانات بدون انقطاع.",
                custom_emoji="🤖",
                is_active=True,
                is_published_to_channel=True,
                sort_order=1,
            )
            prod_office = MasterProduct(
                category_id=cat_software.id,
                internal_sku="OFFICE-365-PRO",
                title_ar="مفتاح تفعيل Office 365 ProPlus",
                title_en="Office 365 ProPlus License Key",
                description_ar="مفتاح رقمي أصلي لتفعيل حزمة أوفيس على حتى 5 أجهزة مع مساحة سحابية 1TB.",
                custom_emoji="💻",
                is_active=True,
                is_published_to_channel=True,
                sort_order=2,
            )
            session.add_all([prod_chatgpt, prod_office])
            await session.flush()
        else:
            prod_chatgpt = products[0]
            prod_office = products[1] if len(products) > 1 else products[0]

        # 4. Supplier: VenteBot Wholesale
        sup_check = await session.execute(select(Supplier).where(Supplier.driver_type == "ventebot_v1"))
        ventebot = sup_check.scalar_one_or_none()
        if not ventebot:
            ventebot_config = {
                "base_url": "https://api.ventebot.com/v1.2",
                "api_key": "sample_reseller_api_key_ventebot",
                "timeout_seconds": 15.0,
            }
            ventebot = Supplier(
                name="VenteBot Wholesale",
                driver_type="ventebot_v1",
                config_encrypted=encrypt_secret(json.dumps(ventebot_config)),
                payment_network=PaymentNetwork.USDT_BEP20,
                status=SupplierStatus.ACTIVE,
                priority_weight=100,
                min_deposit=Decimal("0.0713"),
                deposit_window_seconds=300,
                notes="Validated API v1.2.0 with JIT USDTBSC deposits and strict idempotency.",
            )
            session.add(ventebot)
            await session.flush()

        # 5. Product Mappings
        links_check = await session.execute(select(ProductSupplierLink))
        if not links_check.scalars().first():
            link_chatgpt = ProductSupplierLink(
                master_product_id=prod_chatgpt.id,
                supplier_id=ventebot.id,
                supplier_product_id="10",
                supplier_product_name="ChatGPT Plus 1M Account/Key",
                last_known_cost=Decimal("18.00"),
                is_in_stock=True,
                available_quantity=25,
            )
            link_office = ProductSupplierLink(
                master_product_id=prod_office.id,
                supplier_id=ventebot.id,
                supplier_product_id="42",
                supplier_product_name="MS Office 365 Lifetime Account",
                last_known_cost=Decimal("3.80"),
                is_in_stock=True,
                available_quantity=50,
            )
            session.add_all([link_chatgpt, link_office])

        # 6. Global Pricing Rule (+ $3.50 fixed margin, gas buffer enabled)
        rule_check = await session.execute(select(PricingRule).where(PricingRule.scope == PricingScope.GLOBAL))
        if not rule_check.scalar_one_or_none():
            rule = PricingRule(
                scope=PricingScope.GLOBAL,
                rule_type=PricingRuleType.FIXED,
                value=Decimal("3.50"),
                gas_buffer_enabled=True,
                gas_buffer_fixed=Decimal("0.08"),
                priority=0,
                is_active=True,
            )
            session.add(rule)

        # 7. Loyalty & Bot Configs (Phase 8A & 8B)
        default_configs = [
            ("welcome_message_ar", "أهلاً بك في المتجر الرقمي الآلي! 🌟\nنوفر لك اشتراكات الذكاء الاصطناعي والتراخيص بأسعار الجملة مع تسليم فوري."),
            ("terms_and_faq_ar", "📜 **الشروط وسياسة الضمان:**\n1️⃣ التسليم فوري وآلي فور تأكيد الدفع.\n2️⃣ ضمان تشغيلي كامل لكافة الحسابات والمفاتيح.\n3️⃣ تعويض فوري أو استرداد رصيد المحفظة عند أي عطل."),
            ("sham_cash_account_number", "0991234567"),
            ("sham_cash_instructions_ar", "يرجى تحويل المبلغ إلى حساب شام كاش: 0991234567 ثم رفع الإشعار."),
            ("cashback_percent", 2.0),
            ("referral_commission_percent", 5.0),
            ("auto_post_proofs_enabled", True),
            ("min_order_value", 1.0),
        ]
        for key, val in default_configs:
            cfg_check = await session.execute(select(BotConfig).where(BotConfig.key == key))
            if not cfg_check.scalar_one_or_none():
                session.add(BotConfig(key=key, value=val, updated_by="system_seeder"))

        # 8. Top-Up Bonus Tiers (Phase 8B)
        tier_check = await session.execute(select(TopUpBonusTier))
        if not tier_check.scalars().first():
            tier1 = TopUpBonusTier(min_deposit_usd=Decimal("25.00"), bonus_usd=Decimal("1.00"))
            tier2 = TopUpBonusTier(min_deposit_usd=Decimal("50.00"), bonus_usd=Decimal("3.00"))
            tier3 = TopUpBonusTier(min_deposit_usd=Decimal("100.00"), bonus_usd=Decimal("7.00"))
            session.add_all([tier1, tier2, tier3])
            logger.info("Created default Top-Up bonus tiers ($25->+$1, $50->+$3, $100->+$7).")

        # 9. Sample Active Coupons (Phase 8B)
        coup_check = await session.execute(select(Coupon).where(Coupon.code == "WELCOME10"))
        if not coup_check.scalar_one_or_none():
            c1 = Coupon(
                code="WELCOME10",
                discount_type=DiscountType.PERCENT,
                value=Decimal("10.0"),
                max_uses=500,
                used_count=0,
                is_active=True,
                expires_at=datetime.now(timezone.utc) + timedelta(days=365)
            )
            c2 = Coupon(
                code="VIP20",
                discount_type=DiscountType.FIXED,
                value=Decimal("2.00"),
                max_uses=200,
                used_count=0,
                is_active=True,
                expires_at=datetime.now(timezone.utc) + timedelta(days=365)
            )
            session.add_all([c1, c2])
            logger.info("Created sample coupons: WELCOME10 (10%) and VIP20 ($2.00 off).")

        # 10. Sample Test Customer & Reseller (Phase 9)
        test_tg_id = 987654321
        cust_check = await session.execute(select(Customer).where(Customer.telegram_id == test_tg_id))
        cust = cust_check.scalar_one_or_none()
        sample_api_key = "sk_live_test_reseller_key_12345"
        key_hash = hashlib.sha256(sample_api_key.encode("utf-8")).hexdigest()

        if not cust:
            cust = Customer(
                telegram_id=test_tg_id,
                telegram_username="test_reseller_dev",
                first_name="Test Dev",
                last_name="Reseller",
                language_code="ar",
                wallet_balance=Decimal("100.00"),
                total_spent=Decimal("250.00"),
                total_deposited=Decimal("350.00"),
                total_orders=5,
                vip_tier=VipTier.GOLD,
                api_key_hash=key_hash,
                api_key_prefix="sk_live_test...",
                api_enabled=True,
                account_status=CustomerStatus.ACTIVE,
            )
            session.add(cust)
            logger.info(f"Created dev test customer & reseller (TG ID: {test_tg_id}, API Key: {sample_api_key}).")

        await session.commit()
        logger.info("Database seeding successfully completed with all Phase 8 & 9 entities!")


if __name__ == "__main__":
    asyncio.run(seed())
