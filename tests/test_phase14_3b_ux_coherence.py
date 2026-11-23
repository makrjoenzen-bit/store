"""
Phase 14.3B — UX coherence: cache invalidation, checkout reminder, low-balance meta,
import pricing fields.

Run: pytest -v tests/test_phase14_3b_ux_coherence.py
"""
from __future__ import annotations

import os
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_phase14_3b_ux.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "111:BOT"
os.environ["ADMIN_BOT_TOKEN"] = "222:ADMIN"
os.environ["ADMIN_TELEGRAM_IDS"] = "900001"
os.environ["HOT_WALLET_ADDRESS"] = "0xPhase143BHot0000000000000000000001"
os.environ["SUPPLIER_BALANCE_ALERT_USD"] = "15"
os.environ["CHECKOUT_REMINDER_MINUTES"] = "15"

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select

from src.config import settings
from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Order, MasterProduct, Category, Supplier, ProductSupplierLink,
    PaymentInvoice, PricingRule,
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod, PaymentNetwork, SupplierStatus,
    PricingScope, PricingRuleType,
)
from src.core.security import encrypt_secret
from src.core.redis import event_bus
from src.core.services.order_service import order_service
from src.workers.health_monitor import SupplierHealthMonitor
from src.api.routes.products import SmartImportDTO

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    settings.SUPPLIER_BALANCE_ALERT_USD = 15.0
    settings.CHECKOUT_REMINDER_MINUTES = 15
    event_bus.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        cat = Category(name_ar="ألعاب", name_en="Games", is_active=True)
        session.add(cat)
        await session.flush()
        prod = MasterProduct(
            category_id=cat.id, internal_sku="SKU-UX", title_ar="منتج UX",
            is_active=True, warranty_days=7,
        )
        session.add(prod)
        await session.flush()
        sup = Supplier(
            name="UXSup",
            driver_type="generic_rest",
            status=SupplierStatus.ACTIVE,
            config_encrypted=encrypt_secret('{"base_url":"https://x","api_key":"k"}'),
            payment_network=PaymentNetwork.USDT_BEP20,
            cached_balance=Decimal("10"),
        )
        session.add(sup)
        await session.flush()
        session.add(ProductSupplierLink(
            master_product_id=prod.id, supplier_id=sup.id,
            supplier_product_id="S1", last_known_cost=Decimal("5"),
            is_in_stock=True, available_quantity=5,
        ))
        session.add(Customer(
            telegram_id=88001, telegram_username="uxbuyer",
            first_name="UX", wallet_balance=Decimal("0"),
        ))
        await session.commit()

    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_publish_config_change_invalidates_quote_cache():
    redis = event_bus.redis
    await redis.set("cache:quote:abc:def", '{"cost":"1"}')
    await redis.set("cache:catalog:x", "1")
    await event_bus.publish_config_change("products", "pid-1")
    assert await redis.get("cache:quote:abc:def") is None
    assert await redis.get("cache:catalog:x") is None


@pytest.mark.asyncio
async def test_abandoned_checkout_reminder_once():
    monitor = SupplierHealthMonitor()
    async with AsyncSessionLocal() as session:
        cust = (await session.execute(select(Customer))).scalar_one()
        prod = (await session.execute(select(MasterProduct))).scalar_one()
        link = (await session.execute(select(ProductSupplierLink))).scalar_one()
        order, inv = await order_service.create_order(
            session=session,
            customer_id=str(cust.id),
            master_product_id=str(prod.id),
            supplier_id=str(link.supplier_id),
            supplier_product_id=link.supplier_product_id,
            supplier_cost=Decimal("5"),
            our_price=Decimal("10"),
            profit_margin=Decimal("5"),
            payment_method=PaymentMethod.USDT_BEP20_DIRECT,
        )
        # Backdate created_at to 16 minutes ago
        past = datetime.now(timezone.utc) - timedelta(minutes=16)
        inv.created_at = past
        inv.reminder_sent = False
        inv.expires_at = datetime.now(timezone.utc) + timedelta(minutes=14)
        await session.commit()
        iid = inv.id

    with patch("src.workers.health_monitor.event_bus.publish", new_callable=AsyncMock) as pub:
        await monitor.send_abandoned_checkout_reminders()
        pub.assert_awaited()
        assert pub.await_args.args[0] == "bot:checkout_reminder"

    async with AsyncSessionLocal() as session:
        inv = (await session.execute(
            select(PaymentInvoice).where(PaymentInvoice.id == iid)
        )).scalar_one()
        assert inv.reminder_sent is True

    # Second pass must not re-send
    with patch("src.workers.health_monitor.event_bus.publish", new_callable=AsyncMock) as pub2:
        await monitor.send_abandoned_checkout_reminders()
        pub2.assert_not_awaited()


@pytest.mark.asyncio
async def test_low_balance_alert_debounced():
    monitor = SupplierHealthMonitor()
    redis = event_bus.redis
    async with AsyncSessionLocal() as session:
        sup = (await session.execute(select(Supplier))).scalar_one()

    with patch("src.workers.health_monitor.event_bus.publish_alert", new_callable=AsyncMock) as alert:
        await monitor._maybe_alert_low_supplier_balance(
            redis, MagicSupplier(sup), Decimal("10"), Decimal("15")
        )
        alert.assert_awaited_once()
        meta = alert.await_args.kwargs.get("meta") or {}
        assert meta.get("alert_type") == "low_balance"
        assert meta.get("kind") == "supplier"

        # Debounced
        await monitor._maybe_alert_low_supplier_balance(
            redis, MagicSupplier(sup), Decimal("8"), Decimal("15")
        )
        assert alert.await_count == 1


class MagicSupplier:
    def __init__(self, row):
        self.id = row.id
        self.name = row.name


def test_smart_import_dto_has_pricing_fields() -> None:
    dto = SmartImportDTO(
        supplier_id="x",
        supplier_product_id="y",
        title_ar="عنوان",
        pricing_mode="percentage",
        pricing_value=20,
    )
    assert dto.pricing_mode == "percentage"
    assert dto.pricing_value == 20


def test_config_settings_thresholds() -> None:
    assert settings.SUPPLIER_BALANCE_ALERT_USD == 15.0
    assert settings.CHECKOUT_REMINDER_MINUTES == 15
