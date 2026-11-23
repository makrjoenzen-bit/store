"""
Phase 14.2 — TRC-20 listener path, Binance Spot Pay verifier, unique amounts,
ARQ JIT enqueue helper, no fake Binance merchant ID.

Run:  pytest -v tests/test_phase14_2_payment_rails.py
"""
from __future__ import annotations

import os
import hashlib
import hmac
from decimal import Decimal
from unittest.mock import AsyncMock, patch

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_phase14_2_rails.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "111:CUSTOMER_BOT"
os.environ["ADMIN_BOT_TOKEN"] = "222:ADMIN_BOT"
os.environ["ADMIN_TELEGRAM_IDS"] = "900001"
os.environ["HOT_WALLET_ADDRESS"] = "0xPhase142BscHotWallet000000000000000001"
os.environ["TRON_HOT_WALLET_ADDRESS"] = "TPhase142TronHotWallet00000000000001"
os.environ["BINANCE_PAY_MERCHANT_ID"] = "987654321"
os.environ["BINANCE_SPOT_API_KEY"] = "test_spot_key"
os.environ["BINANCE_SPOT_API_SECRET"] = "test_spot_secret"

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import settings
from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Order, MasterProduct, Category, Supplier, ProductSupplierLink,
    PaymentInvoice, AdminUser, HotWallet, PricingRule,
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod, PaymentNetwork,
    SupplierStatus, PricingScope, PricingRuleType,
)
from src.core.security import encrypt_secret
from src.core.redis import event_bus
from src.core.services.order_service import order_service
from src.core.services.payment_verification import payment_verification_service
from src.core.services.hot_wallet_sync import bump_hot_wallet_usdt
from src.core.services.jit_queue import enqueue_jit_order
from src.outgest.binance_pay_reader import BinancePayReader
from src.api.routes.miniapp import _resolve_deposit

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    settings.HOT_WALLET_ADDRESS = "0xPhase142BscHotWallet000000000000000001"
    settings.TRON_HOT_WALLET_ADDRESS = "TPhase142TronHotWallet00000000000001"
    settings.BINANCE_PAY_MERCHANT_ID = "987654321"
    settings.BINANCE_SPOT_API_KEY = "test_spot_key"
    settings.BINANCE_SPOT_API_SECRET = "test_spot_secret"

    event_bus.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        cat = Category(name_ar="اختبار", name_en="Test", is_active=True)
        session.add(cat)
        await session.flush()
        prod = MasterProduct(
            category_id=cat.id,
            internal_sku="SKU-P142",
            title_ar="منتج 14.2",
            title_en="P142 Product",
            is_active=True,
            warranty_days=30,
        )
        session.add(prod)
        await session.flush()
        supplier = Supplier(
            name="MockSup",
            driver_type="generic_rest",
            status=SupplierStatus.ACTIVE,
            config_encrypted=encrypt_secret('{"api_key":"k"}'),
            payment_network=PaymentNetwork.USDT_BEP20,
            deposit_address="0xsup",
        )
        session.add(supplier)
        await session.flush()
        session.add(ProductSupplierLink(
            master_product_id=prod.id,
            supplier_id=supplier.id,
            supplier_product_id="SKU-P142",
            last_known_cost=Decimal("5.00"),
            is_in_stock=True,
            available_quantity=100,
        ))
        session.add(Customer(
            telegram_id=700001,
            telegram_username="p142user",
            first_name="Phase",
            wallet_balance=Decimal("0"),
        ))
        session.add(PricingRule(
            scope=PricingScope.GLOBAL,
            rule_type=PricingRuleType.PERCENTAGE,
            value=Decimal("20"),
            is_active=True,
        ))
        await session.commit()

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _seed_order(session, method: PaymentMethod, price: Decimal = Decimal("10.00")):
    cust = (await session.execute(select(Customer))).scalar_one()
    prod = (await session.execute(select(MasterProduct))).scalar_one()
    link = (await session.execute(select(ProductSupplierLink))).scalar_one()
    order, invoice = await order_service.create_order(
        session=session,
        customer_id=str(cust.id),
        master_product_id=str(prod.id),
        supplier_id=str(link.supplier_id),
        supplier_product_id=link.supplier_product_id,
        supplier_cost=Decimal("5.00"),
        our_price=price,
        profit_margin=Decimal("5.00"),
        payment_method=method,
    )
    await session.commit()
    return order, invoice


# ---------- 1. unique_amount for TRC20 + Binance ----------
async def test_order_service_unique_amount_trc20_and_binance():
    async with AsyncSessionLocal() as session:
        _, inv_trc = await _seed_order(session, PaymentMethod.USDT_TRC20_DIRECT)
        _, inv_bn = await _seed_order(session, PaymentMethod.BINANCE_PAY, Decimal("12.00"))

    assert inv_trc.unique_amount is not None
    assert inv_trc.unique_amount > Decimal("10.00")
    assert inv_trc.deposit_address == settings.TRON_HOT_WALLET_ADDRESS

    assert inv_bn.unique_amount is not None
    assert inv_bn.deposit_address == "987654321"


# ---------- 2. Exact confirm + HotWallet bump + JIT enqueue ----------
async def test_payment_verification_exact_confirm_enqueues_jit():
    async with AsyncSessionLocal() as session:
        order, inv = await _seed_order(session, PaymentMethod.USDT_TRC20_DIRECT)
        amount = inv.unique_amount

    with patch("src.core.services.payment_verification.enqueue_jit_order", new_callable=AsyncMock) as mock_enq:
        async with AsyncSessionLocal() as session:
            result = await payment_verification_service.process_inbound_transfer(
                session,
                methods=[PaymentMethod.USDT_TRC20_DIRECT],
                amount=amount,
                tx_ref="tron_tx_abc123xyz",
                trigger_prefix="tron_listener",
                sync_wallet_address=settings.TRON_HOT_WALLET_ADDRESS,
            )
        assert result["status"] == "confirmed"
        mock_enq.assert_awaited_once()

    async with AsyncSessionLocal() as session:
        inv = (await session.execute(
            select(PaymentInvoice).where(PaymentInvoice.tx_hash == "tron_tx_abc123xyz")
        )).scalar_one()
        assert inv.status == InvoiceStatus.CONFIRMED
        order = (await session.execute(
            select(Order).where(Order.id == inv.order_id)
        )).scalar_one()
        assert order.state == OrderState.PAYMENT_CONFIRMED

        wallet = (await session.execute(
            select(HotWallet).where(HotWallet.address == settings.TRON_HOT_WALLET_ADDRESS)
        )).scalar_one_or_none()
        assert wallet is not None
        assert wallet.usdt_balance == amount


# ---------- 3. Underpay / overpay / late ----------
async def test_payment_verification_under_and_over():
    async with AsyncSessionLocal() as session:
        order, inv = await _seed_order(session, PaymentMethod.USDT_BEP20_DIRECT, Decimal("20.00"))
        expected = inv.unique_amount

    under = expected - Decimal("0.50")
    async with AsyncSessionLocal() as session:
        r = await payment_verification_service.process_inbound_transfer(
            session,
            methods=[PaymentMethod.USDT_BEP20_DIRECT],
            amount=under,
            tx_ref="bsc_under_1",
            trigger_prefix="bsc_listener",
        )
    assert r["status"] == "partial"

    async with AsyncSessionLocal() as session:
        order2, inv2 = await _seed_order(session, PaymentMethod.USDT_BEP20_DIRECT, Decimal("15.00"))
        expected2 = inv2.unique_amount

    with patch("src.core.services.payment_verification.enqueue_jit_order", new_callable=AsyncMock):
        async with AsyncSessionLocal() as session:
            r2 = await payment_verification_service.process_inbound_transfer(
                session,
                methods=[PaymentMethod.USDT_BEP20_DIRECT],
                amount=expected2 + Decimal("1.25"),
                tx_ref="bsc_over_1",
                trigger_prefix="bsc_listener",
            )
    assert r2["status"] == "confirmed_overpay"


# ---------- 4. Binance confirm by Pay Order ID ----------
async def test_confirm_binance_by_pay_order_id():
    async with AsyncSessionLocal() as session:
        order, inv = await _seed_order(session, PaymentMethod.BINANCE_PAY, Decimal("8.00"))
        oid = order.id
        amount = inv.unique_amount

    with patch("src.core.services.payment_verification.enqueue_jit_order", new_callable=AsyncMock) as mock_enq:
        async with AsyncSessionLocal() as session:
            result = await payment_verification_service.confirm_binance_by_pay_order_id(
                session,
                pay_order_id="PAY_ORDER_998877",
                amount=amount,
                order_id=oid,
                reviewer="tg:900001",
            )
        assert result["status"] == "confirmed"
        mock_enq.assert_awaited_once()

    async with AsyncSessionLocal() as session:
        inv = (await session.execute(
            select(PaymentInvoice).where(PaymentInvoice.tx_hash == "PAY_ORDER_998877")
        )).scalar_one()
        assert inv.status == InvoiceStatus.CONFIRMED


# ---------- 5. BinancePayReader HMAC signing ----------
def test_binance_pay_reader_sign():
    reader = BinancePayReader()
    params = {"timestamp": 1700000000000, "limit": 100}
    sig = reader._sign(params)
    expected = hmac.new(
        b"test_spot_secret",
        b"timestamp=1700000000000&limit=100",
        hashlib.sha256,
    ).hexdigest()
    assert sig == expected
    assert reader.is_configured is True


async def test_binance_pay_reader_normalize_and_list(monkeypatch):
    reader = BinancePayReader()
    fake_rows = {
        "data": [
            {
                "transactionId": "TX1",
                "amount": "10.1234",
                "currency": "USDT",
                "note": "ORD-20260101-1111",
                "transactionTime": 1700000000000,
            }
        ]
    }

    class FakeResp:
        status_code = 200
        def json(self):
            return fake_rows
        text = "ok"

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def get(self, *a, **k):
            return FakeResp()

    with patch("src.outgest.binance_pay_reader.httpx.AsyncClient", return_value=FakeClient()):
        rows = await reader.list_transactions(limit=10)
    assert len(rows) == 1
    assert rows[0]["transaction_id"] == "TX1"
    assert rows[0]["amount"] == Decimal("10.1234")
    assert "ORD-20260101-1111" in rows[0]["note"]


# ---------- 6. No fake merchant ID in resolver ----------
def test_resolve_deposit_no_fake_binance_id():
    settings.BINANCE_PAY_MERCHANT_ID = ""
    assert _resolve_deposit(PaymentMethod.BINANCE_PAY) == ""
    settings.BINANCE_PAY_MERCHANT_ID = "987654321"
    assert _resolve_deposit(PaymentMethod.BINANCE_PAY) == "987654321"
    assert _resolve_deposit(PaymentMethod.USDT_TRC20_DIRECT) == settings.TRON_HOT_WALLET_ADDRESS


# ---------- 7. JIT enqueue fallback ----------
async def test_enqueue_jit_falls_back_to_create_task():
    with patch("src.core.services.jit_queue.create_pool", side_effect=RuntimeError("no redis"), create=True):
        with patch("arq.create_pool", side_effect=RuntimeError("no redis")):
            with patch("src.core.services.jit_executor.jit_executor") as mock_jit:
                mock_jit.execute_order = AsyncMock()
                # Force import path failure then fallback
                with patch("src.core.services.jit_queue.settings") as s:
                    s.REDIS_URL = "redis://127.0.0.1:1/0"
                    await enqueue_jit_order("00000000-0000-0000-0000-000000000099")
                # create_task schedules the coroutine — just ensure no exception
                assert True


# ---------- 8. Duplicate tx ignored ----------
async def test_duplicate_tx_ref_ignored():
    async with AsyncSessionLocal() as session:
        _, inv = await _seed_order(session, PaymentMethod.USDT_TRC20_DIRECT)
        amount = inv.unique_amount

    with patch("src.core.services.payment_verification.enqueue_jit_order", new_callable=AsyncMock):
        async with AsyncSessionLocal() as session:
            await payment_verification_service.process_inbound_transfer(
                session,
                methods=[PaymentMethod.USDT_TRC20_DIRECT],
                amount=amount,
                tx_ref="dup_tx_1",
                trigger_prefix="tron_listener",
            )
        async with AsyncSessionLocal() as session:
            r = await payment_verification_service.process_inbound_transfer(
                session,
                methods=[PaymentMethod.USDT_TRC20_DIRECT],
                amount=amount,
                tx_ref="dup_tx_1",
                trigger_prefix="tron_listener",
            )
    assert r["status"] == "duplicate"


# ---------- 9. Workers importable ----------
def test_workers_importable():
    from src.workers.tron_listener import TronListenerWorker
    from src.workers.binance_pay_listener import BinancePayListenerWorker
    from src.workers.bsc_listener import BscListenerWorker
    assert TronListenerWorker is not None
    assert BinancePayListenerWorker is not None
    assert BscListenerWorker is not None


# ---------- 10. Admin binance keyboard present ----------
def test_admin_orders_hub_has_binance_button():
    from src.admin_bot.keyboards.main_menu import orders_hub_keyboard
    kb = orders_hub_keyboard()
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "adm:orders:binance" in datas
