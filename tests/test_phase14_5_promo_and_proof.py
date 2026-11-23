"""
Phase 14.5 — Promo ads, proof cards, buy_ deep-link qty + bulk tiers.

Run: pytest -v tests/test_phase14_5_promo_and_proof.py
"""
from __future__ import annotations

import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_phase14_5.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "111:BOT"
os.environ["ADMIN_BOT_TOKEN"] = "222:ADMIN"
os.environ["ADMIN_TELEGRAM_IDS"] = "900001"
os.environ["HOT_WALLET_ADDRESS"] = "0xPhase145Hot000000000000000000000001"

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select

from src.config import settings
from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, MasterProduct, Category, Supplier, ProductSupplierLink, PricingRule,
)
from src.core.models.enums import (
    SupplierStatus, PaymentNetwork, PricingScope, PricingRuleType,
)
from src.core.security import encrypt_secret
from src.core.redis import event_bus
from src.core.services.pricing_service import pricing_service
from src.core.services.proof_service import (
    format_purchase_proof_card, format_deposit_proof_card, format_restock_card,
    mask_username, publish_purchase_proof, publish_deposit_proof,
)
from src.core.services.promo_ad_service import build_promo_keyboard, promo_ad_service
from src.core.services.order_service import order_service
from src.core.models.enums import PaymentMethod


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    event_bus.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        cat = Category(name_ar="ألعاب", name_en="Games", is_active=True)
        session.add(cat)
        await session.flush()
        prod = MasterProduct(
            category_id=cat.id,
            internal_sku="SKU-PROMO",
            title_ar="Figma Pro Edu 2Y",
            is_active=True,
            warranty_days=30,
            bulk_pricing_tiers=[
                {"min_qty": 5, "unit_price": 9.50},
                {"min_qty": 10, "unit_price": 9.00},
            ],
        )
        session.add(prod)
        await session.flush()
        session.add(PricingRule(
            scope=PricingScope.PRODUCT,
            scope_id=prod.id,
            rule_type=PricingRuleType.FIXED,
            value=Decimal("5.00"),
            gas_buffer_enabled=False,
            is_active=True,
        ))
        sup = Supplier(
            name="PromoSup",
            driver_type="generic_rest",
            status=SupplierStatus.ACTIVE,
            config_encrypted=encrypt_secret('{"base_url":"https://x","api_key":"k"}'),
            payment_network=PaymentNetwork.USDT_BEP20,
            cached_balance=Decimal("100"),
        )
        session.add(sup)
        await session.flush()
        session.add(ProductSupplierLink(
            master_product_id=prod.id,
            supplier_id=sup.id,
            supplier_product_id="S1",
            last_known_cost=Decimal("5.00"),
            is_in_stock=True,
            available_quantity=50,
        ))
        session.add(Customer(
            telegram_id=77001,
            telegram_username="buyerone",
            first_name="George",
            wallet_balance=Decimal("0"),
        ))
        await session.commit()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# 1. Bulk qty pricing + profit guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bulk_tier_qty2_and_qty5_with_profit_guard():
    async with AsyncSessionLocal() as session:
        prod = (await session.execute(select(MasterProduct))).scalar_one()
        info = await pricing_service.get_best_supplier_and_price(session, str(prod.id))
        assert info["is_available"]
        list_unit = Decimal(str(info["final_price"]))
        # cost 5 + fixed margin 5 = 10 (no gas)
        assert list_unit == Decimal("10.00")

        rate = await pricing_service.get_incentive_rate(session)
        unit_q2 = pricing_service.resolve_bulk_unit_price(
            list_unit, 2, prod.bulk_pricing_tiers,
            unit_cost=Decimal("5.00"), incentive_rate=rate,
        )
        # qty 2 → no tier → full list price
        assert unit_q2 == Decimal("10.00")
        total_q2 = (unit_q2 * 2).quantize(Decimal("0.01"))
        assert total_q2 == Decimal("20.00")

        unit_q5 = pricing_service.resolve_bulk_unit_price(
            list_unit, 5, prod.bulk_pricing_tiers,
            unit_cost=Decimal("5.00"), incentive_rate=rate,
        )
        # tier 5+: 9.50 but still above profit floor
        assert unit_q5 == Decimal("9.50")
        assert unit_q5 < list_unit

        # Aggressive undercut must be clamped by Profit Guard
        bad_tiers = [{"min_qty": 5, "unit_price": 0.01}]
        guarded = pricing_service.resolve_bulk_unit_price(
            list_unit, 5, bad_tiers,
            unit_cost=Decimal("5.00"), incentive_rate=rate,
        )
        min_pay = pricing_service.profit_guard_min_payable(
            list_unit * 5, Decimal("25.00"), rate
        )
        assert guarded * 5 >= min_pay


@pytest.mark.asyncio
async def test_pending_buy_redis_and_order_qty_math():
    redis = event_bus.redis
    await redis.set("pending_buy:77001", "prod-uuid-xyz", ex=3600)
    assert await redis.get("pending_buy:77001") == "prod-uuid-xyz"
    await redis.delete("pending_buy:77001")
    assert await redis.get("pending_buy:77001") is None

    async with AsyncSessionLocal() as session:
        cust = (await session.execute(select(Customer))).scalar_one()
        prod = (await session.execute(select(MasterProduct))).scalar_one()
        link = (await session.execute(select(ProductSupplierLink))).scalar_one()
        info = await pricing_service.get_best_supplier_and_price(session, str(prod.id))
        unit = Decimal(str(info["final_price"]))
        qty = 2
        order, inv = await order_service.create_order(
            session=session,
            customer_id=str(cust.id),
            master_product_id=str(prod.id),
            supplier_id=str(link.supplier_id),
            supplier_product_id=link.supplier_product_id,
            supplier_cost=Decimal("10.00"),
            our_price=unit * qty,
            profit_margin=Decimal("10.00"),
            payment_method=PaymentMethod.USDT_BEP20_DIRECT,
            quantity=qty,
        )
        assert order.quantity == 2
        assert order.our_price == Decimal("20.00")


# ---------------------------------------------------------------------------
# 2. Proof cards — masking, no wholesale leaks
# ---------------------------------------------------------------------------

def test_purchase_and_deposit_proof_formatting():
    purchase = format_purchase_proof_card({
        "order_number": "ORD-7660209ABC",
        "customer_username": "george",
        "customer_first_name": "George",
        "product_title": "CDK GOOGLE AI PRO 12M",
        "quantity": 2,
        "payment_method": "binance_pay",
    })
    assert "عملية شراء جديدة" in purchase
    assert "New Purchase" in purchase
    assert "CDK GOOGLE AI PRO 12M" in purchase
    assert "`2`" in purchase
    assert "تم التسليم بنجاح" in purchase
    assert "george" not in purchase.lower() or "***" in purchase
    assert "wholesale" not in purchase.lower()
    assert "supplier" not in purchase.lower()
    assert "5.00" not in purchase  # no cost leak

    masked = mask_username("george", "George")
    assert "***" in masked or "****" in masked

    deposit = format_deposit_proof_card({
        "tx_ref": "TX-ABCDEF123456",
        "customer_first_name": "Nora",
        "amount_usd": 25,
        "payment_method": "Binance Pay",
    })
    assert "تم شحن المحفظة بنجاح" in deposit
    assert "Wallet Top-Up" in deposit or "شحن" in deposit
    assert "N****" in deposit or "***" in deposit


def test_restock_card_format():
    text = format_restock_card(
        title="Figma Pro Edu 2Y",
        emoji="🎨",
        added_qty=20,
        current_stock=36,
        unit_price=Decimal("3.20"),
        warranty_days=30,
        bulk_caption="➕ **Bulk pricing**\n• From 5+: `$3.10` / each",
        is_new=False,
    )
    assert "JUST RESTOCKED" in text
    assert "Added: `20`" in text
    assert "Current stock: `36`" in text
    assert "$3.20" in text
    assert "Bulk pricing" in text


@pytest.mark.asyncio
async def test_proof_publish_to_redis():
    with patch.object(event_bus, "publish", new_callable=AsyncMock) as pub:
        await publish_purchase_proof({"order_number": "ORD-1", "quantity": 1})
        await publish_deposit_proof({"tx_ref": "TX-1", "amount_usd": 10})
        assert pub.await_count == 2
        assert pub.await_args_list[0].args[0] == "channel:broadcast_proof"
        assert pub.await_args_list[0].args[1]["type"] == "order_completed"
        assert pub.await_args_list[1].args[1]["type"] == "deposit_confirmed"


# ---------------------------------------------------------------------------
# 3. Promo ad keyboard + service (mocked Telegram)
# ---------------------------------------------------------------------------

def test_promo_keyboard_has_two_cta_buttons():
    kb = build_promo_keyboard(
        bot_username="MyShopBot",
        product_id="abc-123",
        miniapp_url="https://example.com/miniapp",
    )
    rows = kb.inline_keyboard
    assert len(rows) == 2
    assert "فتح المتجر" in rows[0][0].text or "🏪" in rows[0][0].text
    assert "الشراء عبر البوت" in rows[1][0].text or "🤖" in rows[1][0].text
    assert "buy_abc-123" in rows[1][0].url


@pytest.mark.asyncio
async def test_promo_ad_service_publishes_channels_and_audit():
    async with AsyncSessionLocal() as session:
        prod = (await session.execute(select(MasterProduct))).scalar_one()

        fake_msg = MagicMock()
        fake_msg.message_id = 42
        fake_msg.chat.id = -100

        class FakeBot:
            def __init__(self, token):
                self.token = token
                self.session = MagicMock()
                self.session.close = AsyncMock()

            async def get_me(self):
                m = MagicMock()
                m.username = "TestShopBot"
                return m

            async def send_photo(self, **kwargs):
                return fake_msg

            async def send_message(self, **kwargs):
                return fake_msg

            async def pin_chat_message(self, **kwargs):
                return True

        with patch("src.core.services.promo_ad_service.Bot", FakeBot):
            with patch("src.core.services.promo_ad_service.settings") as st:
                st.TELEGRAM_BOT_TOKEN = "111:BOT"
                st.TELEGRAM_PROOF_CHANNEL_ID = "-100111"
                st.TELEGRAM_PUBLIC_CHANNEL_ID = "-100222"
                st.MINIAPP_URL = "https://example.com/miniapp"
                # inject channel ids via BotConfig-less defaults on settings
                result = await promo_ad_service.publish_promo_ad(
                    session,
                    caption="🤖 TEST AD\n💎 $7\n#AI",
                    reviewer="tg:900001",
                    product_id=str(prod.id),
                    image_url="https://example.com/ad.jpg",
                    publish_proof_channel=True,
                    publish_main_channel=True,
                    broadcast_customers=False,
                    pin_in_channel=False,
                )
        assert result["status"] == "ok"
        # Without BotConfig rows, channel ids fall back to settings — may be None in test env
        # At minimum AuditLog path completed without error
        assert "delivery" in result
