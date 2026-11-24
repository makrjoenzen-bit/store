"""Safe Hybrid / Manual Fulfillment + surviving critical-blocker regressions."""
from __future__ import annotations

import os
import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_hybrid_manual.db"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_HYBRID")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-hybrid-manual-fulfill-32c")
os.environ.setdefault("ENCRYPTION_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "111001")
os.environ.setdefault("ADMIN_USERNAME", "hybrid_admin")
os.environ.setdefault("ADMIN_PASSWORD", "hybrid-strong-pass-99")
os.environ.setdefault("HOT_WALLET_ADDRESS", "0xTestHotWalletBscAddress00000000000001")
os.environ["HOT_WALLET_PRIVATE_KEY"] = ""
os.environ["TRON_HOT_WALLET_PRIVATE_KEY"] = ""

import fakeredis.aioredis as _fr
from httpx import ASGITransport, AsyncClient

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Category, MasterProduct, Order, PaymentInvoice,
    ProductSupplierLink, Supplier, AdminUser, Coupon,
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod, SupplierStatus, PaymentNetwork,
    DiscountType,
)
from src.core.redis import event_bus
from src.core.security import get_password_hash, create_access_token, encrypt_secret
from src.core.services.jit_executor import jit_executor, JitExecutor
from src.core.services.order_service import order_service
from src.core.services.sham_cash_service import sham_cash_service
from src.api.main import app, websocket_endpoint
from src.config import settings


@pytest_asyncio.fixture
async def db_redis():
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    event_bus.redis = fake
    yield fake
    await fake.flushall()
    await fake.aclose()


async def _seed_paid_order(*, mode: str = "manual", with_supplier: bool = False):
    async with AsyncSessionLocal() as session:
        cust = Customer(
            telegram_id=424201,
            first_name="Hybrid",
            language_code="ar",
            wallet_balance=Decimal("0"),
        )
        session.add(cust)
        await session.flush()
        cat = Category(name_ar="c", name_en="c", is_active=True)
        session.add(cat)
        await session.flush()
        prod = MasterProduct(
            category_id=cat.id,
            internal_sku=f"HYB-{uuid.uuid4().hex[:6].upper()}",
            title_ar="منتج يدوي",
            title_en="Manual Product",
            is_active=True,
            fulfillment_mode=mode,
        )
        session.add(prod)
        await session.flush()
        supplier_id = None
        supplier_product_id = None
        if with_supplier:
            sup = Supplier(
                name="HybSup",
                driver_type="generic_rest",
                config_encrypted=encrypt_secret('{"api_key":"x"}'),
                payment_network=PaymentNetwork.USDT_BEP20,
                status=SupplierStatus.ACTIVE,
            )
            session.add(sup)
            await session.flush()
            session.add(ProductSupplierLink(
                master_product_id=prod.id,
                supplier_id=sup.id,
                supplier_product_id="sku-hyb",
                last_known_cost=Decimal("5"),
                is_in_stock=True,
                available_quantity=None,
            ))
            supplier_id = sup.id
            supplier_product_id = "sku-hyb"

        order = Order(
            order_number=f"ORD-HYB-{uuid.uuid4().hex[:6]}",
            customer_id=cust.id,
            master_product_id=prod.id,
            supplier_id=supplier_id,
            supplier_product_id=supplier_product_id,
            supplier_cost=Decimal("5") if with_supplier else Decimal("0"),
            our_price=Decimal("12"),
            profit_margin=Decimal("7"),
            quantity=1,
            idempotency_key=f"hyb-{uuid.uuid4().hex}",
            state=OrderState.PAYMENT_CONFIRMED,
        )
        session.add(order)
        await session.flush()
        inv = PaymentInvoice(
            customer_id=cust.id,
            order_id=order.id,
            payment_method=PaymentMethod.INTERNAL_BALANCE,
            amount_expected=Decimal("12"),
            status=InvoiceStatus.CONFIRMED,
        )
        session.add(inv)
        await session.flush()
        order.invoice_id = inv.id
        await session.commit()
        return str(order.id), order.order_number


@pytest.mark.asyncio
async def test_manual_mode_routes_to_pending_manual_no_refund(db_redis):
    published = []

    async def capture(channel, message):
        published.append({"channel": channel, "message": message})

    event_bus.publish = capture  # type: ignore
    event_bus.publish_alert = AsyncMock()  # type: ignore

    oid, onum = await _seed_paid_order(mode="manual", with_supplier=False)

    with patch.object(event_bus, "acquire_lock_with_token", new=AsyncMock(return_value="tok")), \
         patch.object(event_bus, "release_lock", new=AsyncMock()):
        await jit_executor.execute_order(oid)

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(select(Order).where(Order.id == uuid.UUID(oid)))
        ).scalar_one()
        assert order.state == OrderState.PENDING_MANUAL_DELIVERY
        assert order.state != OrderState.FAILED_REFUNDED

    cust_msgs = [
        p for p in published
        if p["channel"] == "bot:manual_status"
        and (p["message"].get("text") or "")
    ]
    assert cust_msgs
    assert "تم استلام الدفع" in (cust_msgs[0]["message"].get("text") or "")
    admin_alerts = [p for p in published if p["channel"] == "admin:manual_review"]
    assert admin_alerts
    assert admin_alerts[0]["message"].get("review_kind") == "manual_delivery"


@pytest.mark.asyncio
async def test_no_supplier_fallback_to_manual(db_redis):
    published = []

    async def capture(channel, message):
        published.append({"channel": channel, "message": message})

    event_bus.publish = capture  # type: ignore
    event_bus.publish_alert = AsyncMock()  # type: ignore
    oid, _ = await _seed_paid_order(mode="api_first", with_supplier=False)

    with patch.object(event_bus, "acquire_lock_with_token", new=AsyncMock(return_value="tok")), \
         patch.object(event_bus, "release_lock", new=AsyncMock()):
        await jit_executor.execute_order(oid)

    async with AsyncSessionLocal() as session:
        order = (await session.execute(select(Order).where(Order.id == uuid.UUID(oid)))).scalar_one()
        assert order.state == OrderState.PENDING_MANUAL_DELIVERY


@pytest.mark.asyncio
async def test_manual_deliver_api_saves_payload(db_redis):
    async with AsyncSessionLocal() as session:
        session.add(AdminUser(
            username="hybrid_admin",
            password_hash=get_password_hash("hybrid-strong-pass-99"),
            display_name="H",
            role="admin",
            is_active=True,
        ))
        await session.commit()

    oid, onum = await _seed_paid_order(mode="manual")
    # Put into pending manual
    async with AsyncSessionLocal() as session:
        order = (await session.execute(select(Order).where(Order.id == uuid.UUID(oid)))).scalar_one()
        order.state = OrderState.PENDING_MANUAL_DELIVERY
        await session.commit()

    with patch.object(jit_executor, "complete_order_fulfillment", new=AsyncMock()) as ful:
        async def _ful(session, order, payload):
            order.delivered_product = payload
            order.state = OrderState.DELIVERED

        ful.side_effect = _ful

        # Use service directly (same path as API)
        async with AsyncSessionLocal() as session:
            result = await sham_cash_service.approve_manual_delivery(
                session,
                oid,
                delivered_payload="user:pass@example",
                reviewer="dashboard:hybrid_admin",
            )
        assert result["status"] == "ok"
        assert result["order_number"] == onum

    async with AsyncSessionLocal() as session:
        order = (await session.execute(select(Order).where(Order.id == uuid.UUID(oid)))).scalar_one()
        assert order.delivered_product == "user:pass@example"


def test_safe_import_jit_and_senders_without_keys():
    # Module-level singletons must not crash when keys empty
    from src.outgest.bsc_sender import bsc_sender
    from src.outgest.tron_sender import tron_sender
    from src.core.services.jit_executor import jit_executor as je
    assert bsc_sender is not None
    assert tron_sender is not None
    assert je is not None
    assert JitExecutor._outbound_keys_missing(
        MagicMock(payment_network=PaymentNetwork.USDT_BEP20)
    ) is True


@pytest.mark.asyncio
async def test_bsc_last_scanned_block_redis_persist(db_redis):
    from src.workers.bsc_listener import BscListenerWorker

    class _FakeEth:
        @property
        def block_number(self):
            async def _coro():
                return 1_000_000
            return _coro()

    class _FakeW3:
        eth = _FakeEth()

    w = BscListenerWorker()
    w.w3 = _FakeW3()
    await w._persist_last_scanned_block(999_990)
    assert await db_redis.get("bsc:last_scanned_block") == "999990"
    resumed = await w._resume_last_scanned_block()
    assert resumed == 999_990


@pytest.mark.asyncio
async def test_unique_amount_avoids_pending_collision(db_redis):
    async with AsyncSessionLocal() as session:
        cust = Customer(telegram_id=999001, first_name="U", language_code="ar")
        session.add(cust)
        await session.flush()
        base = Decimal("10.00")
        first = await order_service.generate_unique_invoice_amount_safe(
            session, base, PaymentMethod.USDT_BEP20_DIRECT
        )
        session.add(PaymentInvoice(
            customer_id=cust.id,
            payment_method=PaymentMethod.USDT_BEP20_DIRECT,
            amount_expected=base,
            unique_amount=first,
            status=InvoiceStatus.PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        ))
        await session.commit()

        # Force generator to collide first then succeed
        seq = [first, first, base + Decimal("0.0077")]
        with patch.object(
            order_service,
            "generate_unique_invoice_amount",
            side_effect=lambda b: seq.pop(0) if seq else (b + Decimal("0.0088")),
        ):
            second = await order_service.generate_unique_invoice_amount_safe(
                session, base, PaymentMethod.USDT_BEP20_DIRECT
            )
        assert second != first


@pytest.mark.asyncio
async def test_coupon_for_update_in_miniapp_source():
    src = open("src/api/routes/miniapp.py", encoding="utf-8").read()
    assert "with_for_update()" in src
    shop = open("src/bot/handlers/shop.py", encoding="utf-8").read()
    assert "select(Coupon)" in shop and "with_for_update()" in shop


@pytest.mark.asyncio
async def test_ws_origin_reject_and_jti_logout(db_redis):
    closed = {}

    class FakeWS:
        headers = {"origin": "https://evil.example"}
        cookies = {}
        query_params = {}

        async def close(self, code=1000, reason=""):
            closed["code"] = code
            closed["reason"] = reason

    # Patch cors list
    with patch.object(type(settings), "cors_origins_list", new_callable=lambda: property(lambda self: ["https://bigishere.tech"])):
        # settings is instance — patch attribute
        pass

    settings_cors = settings.cors_origins_list
    object.__setattr__(settings, "CORS_ORIGINS", "https://bigishere.tech") if False else None

    # Directly test with patched property via monkeypatch on settings module
    from src import config as cfg
    with patch.object(cfg.settings, "CORS_ORIGINS", "https://bigishere.tech"):
        # Re-read property
        allowed = cfg.settings.cors_origins_list
        assert "https://bigishere.tech" in allowed
        await websocket_endpoint(FakeWS())
    assert closed.get("code") == 1008

    # JWT revoke on logout path
    from src.api.routes import auth as auth_routes
    token = create_access_token({"sub": "hybrid_admin", "role": "admin"})
    payload = __import__("src.core.security", fromlist=["decode_access_token"]).decode_access_token(token)
    assert payload and payload.get("jti")
    await auth_routes._revoke_jti(payload["jti"], 3600)
    assert await auth_routes._is_jti_revoked(payload["jti"]) is True


def test_manual_keyboard_has_adm_manual():
    from src.admin_bot.keyboards.main_menu import manual_delivery_keyboard
    kb = manual_delivery_keyboard("oid-1")
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(c.startswith("adm:manual:") for c in flat)


def test_infra_compose_and_pool():
    yml = open("docker-compose.yml", encoding="utf-8").read()
    assert "noeviction" in yml
    assert 'max-size: "10m"' in yml
    assert 'profiles: ["extras"]' in yml
    assert "worker-radar" in yml
    from src.core import database as db
    assert int(getattr(settings, "DB_POOL_SIZE", 5)) <= 5
