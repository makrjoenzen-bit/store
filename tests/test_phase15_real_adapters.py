"""
Phase 15 — Real multi-supplier adapters, pending delivery poller, Lara conn_/HMAC.

Run: pytest -v tests/test_phase15_real_adapters.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_phase15.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "111:BOT"
os.environ["ADMIN_BOT_TOKEN"] = "222:ADMIN"
os.environ["ADMIN_TELEGRAM_IDS"] = "900001"
os.environ["HOT_WALLET_ADDRESS"] = "0xPhase15Hot00000000000000000000000001"

import pytest
import pytest_asyncio
import httpx
import fakeredis.aioredis

from src.core.adapters.base import STATUS_COMPLETED, STATUS_PENDING_DELIVERY
from src.core.adapters.tiger import TigerAdapter, cents_to_usd
from src.core.adapters.ventebot import VenteBotAdapter
from src.core.adapters.larastock import LaraStockAdapter, decode_conn_code, verify_lara_webhook_signature
from src.core.adapters.teslatools import TeslaToolsAdapter
from src.core.adapters.hangout import HangoutAdapter
from src.core.adapters.registry import AdapterRegistry
from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, MasterProduct, Order, Supplier, ProductSupplierLink,
)
from src.core.models.enums import OrderState, PaymentNetwork, SupplierStatus
from src.core.redis import event_bus
from src.core.security import encrypt_secret
from src.core.services.jit_executor import jit_executor
from src.workers.delivery_poller import delivery_poller


class FakeResponse:
    def __init__(self, status_code: int, payload, headers=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {"content-type": "application/json"}
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=MagicMock(), response=self)


class FakeAsyncClient:
    """Minimal httpx.AsyncClient stand-in driven by a handler(method, url, **kwargs)."""

    def __init__(self, handler, *args, **kwargs):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        return await self._handler("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return await self._handler("POST", url, **kwargs)

    async def request(self, method, url, **kwargs):
        return await self._handler(method, url, **kwargs)


@pytest_asyncio.fixture(autouse=True)
async def setup_env():
    event_bus.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# 1. Tiger — cents→USD + HTTP 202 pending
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tiger_cents_and_202_pending():
    assert cents_to_usd(120) == Decimal("1.20")
    assert cents_to_usd(0) == Decimal("0.00")

    adapter = TigerAdapter({"api_key": "tk", "base_url": "https://tiger.test/api/v1"})

    async def handler(method, url, **kwargs):
        if method == "GET" and url.endswith("/products"):
            return FakeResponse(200, {
                "data": [
                    {"id": "p1", "name": "Gemini", "price_cents": 120, "stock": None, "available": True},
                    {"id": "p2", "name": "Bad", "price_cents": None, "available": False},
                ],
                "next_cursor": None,
            })
        if method == "POST" and url.endswith("/orders"):
            assert kwargs.get("headers", {}).get("Idempotency-Key") == "idem-1"
            return FakeResponse(202, {"id": "ord-t1", "status": "processing", "total_cents": 120})
        if method == "GET" and "/orders/ord-t1" in url:
            return FakeResponse(200, {
                "id": "ord-t1",
                "status": "delivered",
                "delivery": ["KEY-AAA", "KEY-BBB"],
                "total_cents": 120,
            })
        if method == "GET" and url.endswith("/balance"):
            return FakeResponse(200, {"balance_cents": 5000})
        return FakeResponse(404, {})

    with patch("httpx.AsyncClient", lambda *a, **k: FakeAsyncClient(handler)):
        products = await adapter.fetch_products()
        assert len(products) == 1
        assert products[0].cost == Decimal("1.20")
        assert products[0].available_quantity == 9999

        placed = await adapter.place_order("p1", 1, "idem-1")
        assert placed.status == STATUS_PENDING_DELIVERY
        assert placed.is_success is True
        assert placed.supplier_order_id == "ord-t1"

        status = await adapter.get_order_status("ord-t1")
        assert status.status == STATUS_COMPLETED
        assert "KEY-AAA" in status.delivered_payload


# ---------------------------------------------------------------------------
# 2. VenteBot v1.2.0 — X-Reseller-Key + lookup + deposit methods
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ventebot_key_lookup_and_deposits():
    adapter = VenteBotAdapter({
        "api_key": "vb-secret",
        "base_url": "https://ventebot.test/api/reseller",
    })
    assert adapter._headers()["X-Reseller-Key"] == "vb-secret"

    async def handler(method, url, **kwargs):
        if url.endswith("/me"):
            return FakeResponse(200, {"data": {"wallet": {"balance": "42.50"}}})
        if url.endswith("/wallet/deposit-methods"):
            return FakeResponse(200, {"data": [{"method": "USDTBSC", "network": "BSC"}]})
        if url.endswith("/wallet/deposits") and method == "POST":
            return FakeResponse(200, {"data": {
                "address": "0xDEP", "amount": 1.5, "deposit_id": "dep1",
                "min_amount": 0.0713, "expires_in_seconds": 300,
            }})
        if url.endswith("/orders") and method == "POST":
            body = kwargs.get("json") or {}
            assert body.get("idempotency_key") == "vb-idem"
            assert body.get("activation_identifier") == "user@x.com"
            return FakeResponse(200, {"data": {
                "id": "vb-99", "status": "PAID_PENDING_DELIVERY", "price": "3.00",
            }})
        if "/orders/lookup" in url:
            return FakeResponse(200, {"data": {
                "id": "vb-99", "status": "COMPLETED",
                "items": [{"code": "VB-KEY-1"}], "price": "3.00",
            }})
        if url.endswith("/orders/vb-99"):
            return FakeResponse(200, {"data": {
                "id": "vb-99", "status": "COMPLETED",
                "items": [{"code": "VB-KEY-1"}], "price": "3.00",
            }})
        return FakeResponse(404, {})

    with patch("httpx.AsyncClient", lambda *a, **k: FakeAsyncClient(handler)):
        health = await adapter.check_health()
        assert health.is_alive and health.balance_usd == Decimal("42.50")

        methods = await adapter.list_deposit_methods()
        assert methods[0]["method"] == "USDTBSC"

        dep = await adapter.get_deposit_info(amount=Decimal("1.5"))
        assert dep.deposit_address == "0xDEP"

        placed = await adapter.place_order(
            "10", 1, "vb-idem", activation_identifier="user@x.com"
        )
        assert placed.status == STATUS_PENDING_DELIVERY

        recovered = await adapter.lookup_order("vb-idem")
        assert recovered.status == STATUS_COMPLETED
        assert "VB-KEY-1" in recovered.delivered_payload


# ---------------------------------------------------------------------------
# 3. Lara Stock — conn_ decode + unlimited + chatgpt email + HMAC
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_larastock_conn_unlimited_chatgpt_hmac():
    payload = {"k": "lara-key", "u": "https://lara.test/api"}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    conn = f"conn_{encoded}"
    decoded = decode_conn_code(conn)
    assert decoded["api_key"] == "lara-key"
    assert decoded["base_url"] == "https://lara.test/api"

    # Registry normalize
    cfg = AdapterRegistry.normalize_config("larastock_v1", {"api_key": conn})
    assert cfg["api_key"] == "lara-key"
    assert cfg["base_url"] == "https://lara.test/api"

    adapter = LaraStockAdapter({"api_key": "lara-key", "base_url": "https://lara.test/api"})

    async def handler(method, url, **kwargs):
        if url.endswith("/products"):
            return FakeResponse(200, {"data": [
                {
                    "id": "cgpt_seat_1", "name_ar": "ChatGPT Seat",
                    "your_price": "8.00", "stock": "unlimited",
                    "source": "chatgpt_business", "is_manual": False,
                },
                {
                    "id": "regular_1", "name_en": "Regular",
                    "your_price": "2.00", "stock": 5, "is_manual": True,
                },
            ]})
        if url.endswith("/purchase"):
            body = kwargs.get("json") or {}
            if body.get("product_id") == "cgpt_seat_1":
                assert body.get("email") == "buyer@mail.com"
                return FakeResponse(200, {
                    "order_id": "L-1", "status": "completed",
                    "message": "Invite sent", "expires_at": "2026-12-01T00:00:00Z",
                })
            return FakeResponse(202, {"order_id": "L-2", "status": "pending_manual"})
        if "/order/L-2" in url:
            return FakeResponse(200, {"order_id": "L-2", "status": "completed", "codes": ["CODE-9"]})
        if url.endswith("/balance"):
            return FakeResponse(200, {"balance": "10"})
        return FakeResponse(404, {})

    with patch("httpx.AsyncClient", lambda *a, **k: FakeAsyncClient(handler)):
        products = await adapter.fetch_products()
        by_id = {p.supplier_product_id: p for p in products}
        assert by_id["cgpt_seat_1"].available_quantity == 9999
        assert by_id["cgpt_seat_1"].metadata["requires_customer_email"] is True
        assert by_id["regular_1"].metadata["delivery_type"] == "manual"

        cgpt = await adapter.place_order(
            "cgpt_seat_1", 1, "idem-c", buyer_email="buyer@mail.com",
            product_metadata={"source": "chatgpt_business"},
        )
        assert cgpt.status == STATUS_COMPLETED
        assert "Invite sent" in cgpt.delivered_payload
        assert "expires_at" in cgpt.delivered_payload

        pending = await adapter.place_order("regular_1", 1, "idem-r")
        assert pending.status == STATUS_PENDING_DELIVERY

    # HMAC webhook signature
    body = b'{"event":"stock.added","data":{"product_id":"x","stock":3}}'
    ts = "1710000000"
    secret = "whsec"
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert verify_lara_webhook_signature(body, ts, f"sha256={sig}", secret) is True
    assert verify_lara_webhook_signature(body, ts, f"sha256=deadbeef", secret) is False


# ---------------------------------------------------------------------------
# 4. Tesla Tools — X-API-Key + selling_price + delivered_item
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_teslatools_selling_price_and_delivery():
    adapter = TeslaToolsAdapter({"api_key": "tesla-k", "base_url": "https://tesla.test/api/v1"})
    assert adapter._headers()["X-API-Key"] == "tesla-k"

    async def handler(method, url, **kwargs):
        if url.endswith("/profile"):
            return FakeResponse(200, {"merchant": {"balance": 99.5}})
        if url.endswith("/products"):
            return FakeResponse(200, {"data": [
                {"id": 7, "name": "Netflix", "price": 4.5, "stock_count": 12, "warranty_info": "7d"},
            ]})
        if url.endswith("/order/create"):
            body = kwargs.get("json") or {}
            assert body.get("selling_price") == 6.99
            assert body.get("product_id") == 7
            return FakeResponse(200, {"order": {
                "id": "T-1", "delivered_item": "user:pass", "price": 4.5,
            }})
        return FakeResponse(404, {})

    with patch("httpx.AsyncClient", lambda *a, **k: FakeAsyncClient(handler)):
        health = await adapter.check_health()
        assert health.balance_usd == Decimal("99.5")
        products = await adapter.fetch_products()
        assert products[0].cost == Decimal("4.5")
        result = await adapter.place_order("7", 1, "idem-t", selling_price=6.99)
        assert result.status == STATUS_COMPLETED
        assert result.delivered_payload == "user:pass"


# ---------------------------------------------------------------------------
# 5. Hangout max_total + PENDING_DELIVERY → delivery_poller completes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hangout_pending_then_poller_delivers():
    # Seed DB: customer, product, supplier (hangout), order pending
    async with AsyncSessionLocal() as session:
        customer = Customer(telegram_id=15001, first_name="Buyer")
        session.add(customer)
        await session.flush()
        product = MasterProduct(
            internal_sku="P15-H1",
            title_ar="منتج هانغ اوت",
            title_en="Hangout Product",
            requires_customer_email=False,
        )
        session.add(product)
        await session.flush()
        supplier = Supplier(
            name="Hangout",
            driver_type="hangout_v1",
            config_encrypted=encrypt_secret(json.dumps({
                "api_key": "hg-key",
                "base_url": "https://hangout.test/api/v1",
            })),
            payment_network=PaymentNetwork.USDT_BEP20,
            status=SupplierStatus.ACTIVE,
            deposit_address="0xHG",
        )
        session.add(supplier)
        await session.flush()
        session.add(ProductSupplierLink(
            master_product_id=product.id,
            supplier_id=supplier.id,
            supplier_product_id="hg-prod-1",
            last_known_cost=Decimal("2.00"),
            is_in_stock=True,
        ))
        order = Order(
            order_number="ORD-P15-HG-1",
            customer_id=customer.id,
            master_product_id=product.id,
            supplier_id=supplier.id,
            supplier_product_id="hg-prod-1",
            supplier_cost=Decimal("2.00"),
            our_price=Decimal("3.50"),
            profit_margin=Decimal("1.50"),
            quantity=1,
            idempotency_key=f"idem-{uuid.uuid4().hex}",
            supplier_order_id="HG-ORDER-9",
            state=OrderState.PENDING_SUPPLIER_DELIVERY,
        )
        session.add(order)
        await session.commit()
        order_id = str(order.id)

    # Adapter place_order with max_total
    adapter = HangoutAdapter({"api_key": "hg-key", "base_url": "https://hangout.test/api/v1"})

    async def place_handler(method, url, **kwargs):
        if url.endswith("/purchase"):
            body = kwargs.get("json") or {}
            assert body.get("max_total") == 2.5
            return FakeResponse(200, {"id": "HG-ORDER-9", "status": "pending"})
        return FakeResponse(404, {})

    with patch("httpx.AsyncClient", lambda *a, **k: FakeAsyncClient(place_handler)):
        placed = await adapter.place_order("hg-prod-1", 1, "idem-h", max_total=2.5)
        assert placed.status == STATUS_PENDING_DELIVERY
        assert placed.is_success is True

    # Poller: first still pending, then completed
    poll_state = {"n": 0}

    async def status_handler(method, url, **kwargs):
        if "/orders/HG-ORDER-9" in url:
            poll_state["n"] += 1
            if poll_state["n"] == 1:
                return FakeResponse(200, {"id": "HG-ORDER-9", "status": "pending"})
            return FakeResponse(200, {
                "id": "HG-ORDER-9",
                "status": "completed",
                "delivery": ["HG-SECRET-KEY"],
            })
        return FakeResponse(404, {})

    with patch("httpx.AsyncClient", lambda *a, **k: FakeAsyncClient(status_handler)):
        with patch.object(event_bus, "publish", new_callable=AsyncMock):
            with patch.object(event_bus, "publish_alert", new_callable=AsyncMock):
                with patch.object(jit_executor, "_dispatch_telegram_fulfillment", new_callable=AsyncMock):
                    with patch(
                        "src.core.services.order_service.order_service.generate_formal_invoice",
                        new_callable=AsyncMock,
                        return_value=MagicMock(invoice_number="INV-HG"),
                    ):
                        n1 = await delivery_poller.poll_once()
                        assert n1 == 0
                        n2 = await delivery_poller.poll_once()
                        assert n2 == 1

    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        order = (await session.execute(
            select(Order).where(Order.id == uuid.UUID(order_id))
        )).scalar_one()
        assert order.state == OrderState.COMPLETED
        assert "HG-SECRET-KEY" in (order.delivered_product or "")


@pytest.mark.asyncio
async def test_hangout_poller_marks_completed():
    """Standalone assertion that poller flipped state (rebuilds seed for isolation)."""
    async with AsyncSessionLocal() as session:
        customer = Customer(telegram_id=15002, first_name="Buyer2")
        session.add(customer)
        await session.flush()
        product = MasterProduct(
            internal_sku="P15-H2", title_ar="منتج 2", title_en="P2",
        )
        session.add(product)
        await session.flush()
        supplier = Supplier(
            name="Hangout2",
            driver_type="hangout_v1",
            config_encrypted=encrypt_secret(json.dumps({
                "api_key": "hg2", "base_url": "https://hangout2.test/api/v1",
            })),
            payment_network=PaymentNetwork.USDT_BEP20,
            status=SupplierStatus.ACTIVE,
        )
        session.add(supplier)
        await session.flush()
        order = Order(
            order_number="ORD-P15-HG-2",
            customer_id=customer.id,
            master_product_id=product.id,
            supplier_id=supplier.id,
            supplier_product_id="p",
            supplier_cost=Decimal("1.00"),
            our_price=Decimal("2.00"),
            quantity=1,
            idempotency_key=f"idem-{uuid.uuid4().hex}",
            supplier_order_id="HG-DONE",
            state=OrderState.PENDING_SUPPLIER_DELIVERY,
        )
        session.add(order)
        await session.commit()
        oid = order.id

    async def handler(method, url, **kwargs):
        return FakeResponse(200, {
            "id": "HG-DONE", "status": "completed", "codes": ["DONE-KEY"],
        })

    with patch("httpx.AsyncClient", lambda *a, **k: FakeAsyncClient(handler)):
        with patch.object(event_bus, "publish", new_callable=AsyncMock):
            with patch.object(event_bus, "publish_alert", new_callable=AsyncMock):
                with patch.object(jit_executor, "_dispatch_telegram_fulfillment", new_callable=AsyncMock):
                    with patch(
                        "src.core.services.order_service.order_service.generate_formal_invoice",
                        new_callable=AsyncMock,
                        return_value=MagicMock(invoice_number="INV-1"),
                    ):
                        n = await delivery_poller.poll_once()
                        assert n == 1

    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        order = (await session.execute(select(Order).where(Order.id == oid))).scalar_one()
        assert order.state == OrderState.COMPLETED
        assert order.delivered_product == "DONE-KEY"


@pytest.mark.asyncio
async def test_registry_lists_all_five_drivers():
    drivers = AdapterRegistry.list_drivers()
    for d in ("tiger_v1", "ventebot_v1", "larastock_v1", "teslatools_v1", "hangout_v1"):
        assert d in drivers
    presets = AdapterRegistry.presets()
    assert "tiger_v1" in presets
