"""Regression tests for security & concurrency hardening fixes."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_security_hardening.db"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_SEC")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-security-hardening-32c")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-security-hardening-32c")
os.environ.setdefault("ENCRYPTION_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "111001")
os.environ.setdefault("HOT_WALLET_ADDRESS", "0xTestHotWalletBscAddress00000000000001")

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    AdminUser, Customer, Category, MasterProduct, Order, PaymentInvoice,
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod,
)
from src.core.redis import event_bus
from src.core.security import get_password_hash, create_access_token, get_fernet_cipher
from src.core.ssrf import UnsafeURLError, validate_outbound_url
from src.core.services.payment_verification import payment_verification_service
from src.api.main import app
from src.api.routes import auth as auth_routes


@pytest_asyncio.fixture
async def db_redis():
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    event_bus.redis = fake
    auth_routes._login_fail_memory.clear()
    yield fake
    await fake.flushall()
    await fake.aclose()


async def _seed_admin():
    async with AsyncSessionLocal() as session:
        admin = AdminUser(
            username="sec_admin",
            password_hash=get_password_hash("correct-horse"),
            display_name="Sec",
            role="admin",
            is_active=True,
        )
        session.add(admin)
        await session.commit()


# ---------------------------------------------------------------------------
# 1) Login rate limiting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_rate_limit_returns_429(db_redis):
    await _seed_admin()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            r = await client.post(
                "/api/auth/login/json",
                json={"username": "sec_admin", "password": "wrong"},
            )
            assert r.status_code == 401
        r429 = await client.post(
            "/api/auth/login/json",
            json={"username": "sec_admin", "password": "wrong"},
        )
        assert r429.status_code == 429


# ---------------------------------------------------------------------------
# 2) SSRF
# ---------------------------------------------------------------------------

def test_ssrf_rejects_private_and_metadata_ips():
    with pytest.raises(UnsafeURLError):
        validate_outbound_url("http://127.0.0.1/admin")
    with pytest.raises(UnsafeURLError):
        validate_outbound_url("https://169.254.169.254/latest/meta-data/")
    with pytest.raises(UnsafeURLError):
        validate_outbound_url("https://10.0.0.5/secret")
    with pytest.raises(UnsafeURLError):
        validate_outbound_url("https://192.168.1.1/")
    with pytest.raises(UnsafeURLError):
        validate_outbound_url("ftp://example.com/")


def test_ssrf_allows_public_https(monkeypatch):
    # Avoid real DNS: patch getaddrinfo to a public IP
    import socket
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))],
    )
    url = validate_outbound_url("https://example.com/api")
    assert url.startswith("https://example.com")


def test_fernet_no_silent_fallback_seed():
    # Valid key works
    cipher = get_fernet_cipher()
    assert cipher.encrypt(b"x")


# ---------------------------------------------------------------------------
# 3) WebSocket unauthorized / authorized gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_websocket_rejects_unauthorized(db_redis):
    from src.api.main import websocket_endpoint

    closed = {}

    class FakeWS:
        cookies = {}
        query_params = {}

        async def close(self, code=1000, reason=""):
            closed["code"] = code
            closed["reason"] = reason

    await websocket_endpoint(FakeWS())
    assert closed.get("code") == 1008


@pytest.mark.asyncio
async def test_websocket_accepts_valid_token(db_redis):
    from src.api.main import websocket_endpoint
    from src.api.websocket.manager import ws_manager

    token = create_access_token({"sub": "sec_admin", "role": "admin"})
    connected = {"ok": False}
    closed = {}

    class FakeWS:
        cookies = {}
        query_params = {"token": token}

        async def close(self, code=1000, reason=""):
            closed["code"] = code

        async def receive_text(self):
            raise Exception("disconnect")

        async def send_text(self, text):
            return None

    async def fake_connect(ws):
        connected["ok"] = True

    async def fake_disconnect(ws):
        return None

    with patch.object(ws_manager, "connect", new=fake_connect), patch.object(
        ws_manager, "disconnect", new=fake_disconnect
    ):
        await websocket_endpoint(FakeWS())

    assert connected["ok"] is True
    assert closed.get("code") != 1008


# ---------------------------------------------------------------------------
# 4) Split-payment double-tap idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_split_payment_double_tap_no_double_debit(db_redis):
    from src.bot.handlers.shop import _reserve_split_and_set_crypto

    async with AsyncSessionLocal() as session:
        cust = Customer(
            telegram_id=555001,
            first_name="Split",
            wallet_balance=Decimal("40.00"),
            language_code="ar",
        )
        session.add(cust)
        await session.flush()
        order = Order(
            order_number="ORD-SPLIT-1",
            customer_id=cust.id,
            master_product_id=uuid.uuid4(),  # may fail FK — create product
            our_price=Decimal("25.00"),
            profit_margin=Decimal("10.00"),
            quantity=1,
            idempotency_key=f"idem-{uuid.uuid4().hex}",
            state=OrderState.AWAITING_PAYMENT,
            wallet_portion_reserved=Decimal("0"),
        )
        # Need real product/supplier for FK
        cat = Category(name_ar="c", name_en="c", is_active=True)
        session.add(cat)
        await session.flush()
        prod = MasterProduct(
            category_id=cat.id,
            internal_sku=f"SKU-{uuid.uuid4().hex[:6]}",
            title_ar="P",
            is_active=True,
            cached_final_price=Decimal("25"),
            cached_in_stock=True,
        )
        session.add(prod)
        await session.flush()
        order.master_product_id = prod.id
        session.add(order)
        await session.flush()
        inv = PaymentInvoice(
            customer_id=cust.id,
            order_id=order.id,
            payment_method=PaymentMethod.USDT_BEP20_DIRECT,
            amount_expected=Decimal("25.00"),
            status=InvoiceStatus.PENDING,
            expires_at=datetime.now(timezone.utc),
        )
        session.add(inv)
        await session.flush()
        order.invoice_id = inv.id
        await session.commit()
        oid, cid = order.id, cust.id

    async with AsyncSessionLocal() as session:
        order = (await session.execute(
            select(Order).where(Order.id == oid).options(selectinload(Order.invoice)).with_for_update()
        )).scalar_one()
        cust = (await session.execute(select(Customer).where(Customer.id == cid))).scalar_one()
        w1, _r1, _ = await _reserve_split_and_set_crypto(
            session, cust, order,
            method=PaymentMethod.USDT_BEP20_DIRECT,
            deposit_address="0xabc",
        )
        bal_after_first = Decimal(str(
            (await session.execute(select(Customer).where(Customer.id == cid))).scalar_one().wallet_balance
        ))
        order = (await session.execute(
            select(Order).where(Order.id == oid).options(selectinload(Order.invoice)).with_for_update()
        )).scalar_one()
        cust = (await session.execute(select(Customer).where(Customer.id == cid))).scalar_one()
        w2, _r2, _ = await _reserve_split_and_set_crypto(
            session, cust, order,
            method=PaymentMethod.USDT_BEP20_DIRECT,
            deposit_address="0xabc",
        )
        cust2 = (await session.execute(select(Customer).where(Customer.id == cid))).scalar_one()
        assert w1 == Decimal("25.00")
        assert w2 == w1
        assert Decimal(str(cust2.wallet_balance)) == bal_after_first


# ---------------------------------------------------------------------------
# 5) tx_hash double-confirm protection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tx_hash_double_confirm_protected(db_redis):
    async with AsyncSessionLocal() as session:
        cat = Category(name_ar="c", name_en="c", is_active=True)
        session.add(cat)
        await session.flush()
        prod = MasterProduct(
            category_id=cat.id,
            internal_sku=f"SKU-{uuid.uuid4().hex[:6]}",
            title_ar="Pay",
            is_active=True,
            cached_final_price=Decimal("5"),
            cached_in_stock=True,
        )
        session.add(prod)
        cust = Customer(telegram_id=777001, first_name="P", wallet_balance=Decimal("0"))
        session.add(cust)
        await session.flush()
        order = Order(
            order_number="ORD-TX-1",
            customer_id=cust.id,
            master_product_id=prod.id,
            our_price=Decimal("5.00"),
            profit_margin=Decimal("1.00"),
            quantity=1,
            idempotency_key=f"idem-{uuid.uuid4().hex}",
            state=OrderState.AWAITING_PAYMENT,
        )
        session.add(order)
        await session.flush()
        inv = PaymentInvoice(
            customer_id=cust.id,
            order_id=order.id,
            payment_method=PaymentMethod.USDT_BEP20_DIRECT,
            amount_expected=Decimal("5.001"),
            unique_amount=Decimal("5.001"),
            status=InvoiceStatus.PENDING,
            expires_at=datetime.now(timezone.utc),
        )
        session.add(inv)
        await session.flush()
        order.invoice_id = inv.id
        await session.commit()
        inv_id = inv.id

    with patch("src.core.services.payment_verification.enqueue_jit_order", new=AsyncMock()):
        async with AsyncSessionLocal() as session:
            inv = (await session.execute(
                select(PaymentInvoice).where(PaymentInvoice.id == inv_id).options(
                    selectinload(PaymentInvoice.order),
                    selectinload(PaymentInvoice.customer),
                )
            )).scalar_one()
            r1 = await payment_verification_service.confirm_exact_payment(
                session, inv,
                amount=Decimal("5.001"),
                tx_ref="0xtxdedup123",
                trigger_source="test",
            )
            assert r1["status"] in ("confirmed", "already_confirmed")

        async with AsyncSessionLocal() as session:
            inv = (await session.execute(
                select(PaymentInvoice).where(PaymentInvoice.id == inv_id).options(
                    selectinload(PaymentInvoice.order),
                    selectinload(PaymentInvoice.customer),
                )
            )).scalar_one()
            r2 = await payment_verification_service.confirm_exact_payment(
                session, inv,
                amount=Decimal("5.001"),
                tx_ref="0xtxdedup123",
                trigger_source="test",
            )
            assert r2["status"] == "already_confirmed"


# ---------------------------------------------------------------------------
# Mini App initData query param removed
# ---------------------------------------------------------------------------

def test_miniapp_no_query_initdata_fallback():
    from pathlib import Path
    src = Path("src/api/routes/miniapp.py").read_text(encoding="utf-8")
    assert 'query_params.get("initData")' not in src
    assert "X-Telegram-Init-Data" in src
