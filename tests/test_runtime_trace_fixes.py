"""Regression tests for Runtime Trace Audit fixes."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_runtime_trace.db"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_RT")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-runtime-trace-fix-32c")
os.environ.setdefault("ENCRYPTION_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "111001")
os.environ.setdefault("HOT_WALLET_ADDRESS", "0xTestHotWalletBscAddress00000000000001")

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Category, MasterProduct, Order, PaymentInvoice,
    ProductSupplierLink, Supplier, WalletTransaction,
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod, SupplierStatus, PaymentNetwork,
)
from src.core.redis import event_bus
from src.core.security import encrypt_secret
from src.core.services.order_expiry_service import order_expiry_service
from src.admin_bot.keyboards.main_menu import sham_cash_claim_keyboard, sham_cash_review_keyboard
from src.bot.middlewares.force_sub import force_sub_keyboard
from src.bot.handlers.shop import ShamCashUploadState, handle_receipt_non_photo
from src.bot.fsm_storage import build_fsm_storage
from aiogram.fsm.storage.memory import MemoryStorage


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


async def _seed_unpaid_order(*, wallet_reserved: Decimal = Decimal("0"), stock_qty: int = 5):
    async with AsyncSessionLocal() as session:
        cust = Customer(
            telegram_id=555001,
            first_name="Expire",
            language_code="ar",
            wallet_balance=Decimal("100.00"),
        )
        session.add(cust)
        await session.flush()

        cat = Category(name_ar="c", name_en="c", is_active=True)
        session.add(cat)
        await session.flush()
        prod = MasterProduct(
            category_id=cat.id,
            internal_sku=f"RT-{uuid.uuid4().hex[:6].upper()}",
            title_ar="Prod",
            title_en="Prod",
            is_active=True,
        )
        session.add(prod)
        await session.flush()

        sup = Supplier(
            name="SupRT",
            driver_type="generic_rest",
            config_encrypted=encrypt_secret('{"api_key":"rt"}'),
            payment_network=PaymentNetwork.USDT_BEP20,
            status=SupplierStatus.ACTIVE,
        )
        session.add(sup)
        await session.flush()

        link = ProductSupplierLink(
            master_product_id=prod.id,
            supplier_id=sup.id,
            supplier_product_id="sku-rt",
            last_known_cost=Decimal("5"),
            is_in_stock=True,
            available_quantity=stock_qty,
        )
        session.add(link)
        await session.flush()

        # Simulate prior stock reservation
        link.available_quantity = stock_qty - 1

        order = Order(
            order_number=f"ORD-RT-{uuid.uuid4().hex[:6]}",
            customer_id=cust.id,
            master_product_id=prod.id,
            supplier_id=sup.id,
            supplier_product_id="sku-rt",
            supplier_cost=Decimal("5"),
            our_price=Decimal("12"),
            profit_margin=Decimal("7"),
            quantity=1,
            wallet_portion_reserved=wallet_reserved,
            idempotency_key=f"rt-{uuid.uuid4().hex}",
            state=OrderState.AWAITING_PAYMENT,
        )
        session.add(order)
        await session.flush()

        inv = PaymentInvoice(
            customer_id=cust.id,
            order_id=order.id,
            payment_method=PaymentMethod.USDT_BEP20_DIRECT,
            amount_expected=Decimal("12") - wallet_reserved,
            unique_amount=Decimal("12.0123"),
            status=InvoiceStatus.PENDING,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        session.add(inv)
        await session.flush()
        order.invoice_id = inv.id

        if wallet_reserved > 0:
            cust.wallet_balance = Decimal("100.00") - wallet_reserved

        await session.commit()
        return {
            "order_id": str(order.id),
            "invoice_id": str(inv.id),
            "customer_id": str(cust.id),
            "link_id": str(link.id),
            "wallet_reserved": wallet_reserved,
            "stock_before": stock_qty - 1,
        }


# ---------------------------------------------------------------------------
# 1) Unpaid order expiry sweeper
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expiry_sweeper_cancels_refunds_releases_stock(db_redis):
    published = []

    async def capture(channel, message):
        published.append({"channel": channel, "message": message})

    event_bus.publish = capture  # type: ignore

    seed = await _seed_unpaid_order(wallet_reserved=Decimal("4.50"), stock_qty=10)

    async with AsyncSessionLocal() as session:
        result = await order_expiry_service.sweep_expired(session)
        assert result["expired_count"] >= 1

    async with AsyncSessionLocal() as session:
        order = (
            await session.execute(
                select(Order)
                .where(Order.id == uuid.UUID(seed["order_id"]))
                .options(selectinload(Order.invoice), selectinload(Order.customer))
            )
        ).scalar_one()
        assert order.state == OrderState.CANCELLED
        assert order.invoice.status == InvoiceStatus.EXPIRED
        assert Decimal(str(order.wallet_portion_reserved)) == Decimal("0")
        assert Decimal(str(order.customer.wallet_balance)) == Decimal("100.00")

        link = (
            await session.execute(
                select(ProductSupplierLink).where(
                    ProductSupplierLink.id == uuid.UUID(seed["link_id"])
                )
            )
        ).scalar_one()
        assert link.available_quantity == seed["stock_before"] + 1

    assert any(p["channel"] == "bot:send_message" for p in published)


# ---------------------------------------------------------------------------
# 2) Sham Cash non-photo stays in FSM (no AI concierge)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sham_receipt_non_photo_stays_in_fsm(db_redis):
    async with AsyncSessionLocal() as session:
        session.add(Customer(telegram_id=777001, first_name="FSM", language_code="en"))
        await session.commit()

    state = MagicMock()
    state.get_state = AsyncMock(return_value=ShamCashUploadState.waiting_for_receipt.state)
    state.get_data = AsyncMock(return_value={"order_id": "00000000-0000-0000-0000-000000000001"})
    state.clear = AsyncMock()

    msg = MagicMock()
    msg.text = "hello I paid"
    msg.photo = None
    msg.document = None
    u = MagicMock()
    u.id = 777001
    u.first_name = "FSM"
    u.last_name = None
    u.username = "fsm"
    u.language_code = "en"
    msg.from_user = u
    msg.answer = AsyncMock()

    await handle_receipt_non_photo(msg, state)

    msg.answer.assert_awaited()
    text = msg.answer.await_args.args[0]
    assert "pdf" in text.lower() or "photo" in text.lower() or "Cancel" in text or "إيصال" in text
    state.clear.assert_not_awaited()


# ---------------------------------------------------------------------------
# 3) Force-Join English for en users
# ---------------------------------------------------------------------------

def test_force_join_keyboard_english():
    kb = force_sub_keyboard(
        main_url="https://t.me/main",
        proof_url=None,
        need_main=True,
        need_proof=False,
        language="en",
    )
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    joined = " ".join(labels).lower()
    assert "join" in joined or "verify" in joined
    assert "انضم" not in joined


@pytest.mark.asyncio
async def test_force_join_resolves_stored_english(db_redis):
    from src.bot.middlewares.force_sub import _resolve_user_lang
    from src.bot.i18n import t

    async with AsyncSessionLocal() as session:
        session.add(Customer(telegram_id=888001, first_name="Eng", language_code="en"))
        await session.commit()

    lang = await _resolve_user_lang(888001, telegram_lang="ar")
    assert lang == "en"
    title = t(lang, "force_sub_title")
    assert "join" in title.lower() or "channel" in title.lower()
    assert "للاستخدام يجب الاشتراك" not in title

# ---------------------------------------------------------------------------
# 4) Admin Sham Cash keyboard matches Admin Bot handlers
# ---------------------------------------------------------------------------

def test_admin_sham_cash_keyboard_matches_handlers():
    oid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    claim = sham_cash_claim_keyboard(oid)
    claim_data = [b.callback_data for row in claim.inline_keyboard for b in row]
    assert any(c.startswith("adm:claim:") for c in claim_data)

    review = sham_cash_review_keyboard(oid, claimed=True, for_claimer=True)
    data = [b.callback_data for row in review.inline_keyboard for b in row]
    assert any(c.startswith("adm_sc_ok:") for c in data)
    assert any(c.startswith("adm_sc_no:") for c in data)

    # Dead customer-bot keyboard must be gone
    from src.bot import keyboards
    import src.bot.keyboards.inline as inline_mod
    assert not hasattr(inline_mod, "admin_sham_cash_review_keyboard")

    orders_src = open("src/admin_bot/handlers/orders.py", encoding="utf-8").read()
    assert 'F.data.startswith("adm:claim:")' in orders_src or 'adm:claim:' in orders_src
    assert 'adm_sc_ok:' in orders_src
    assert 'adm_sc_no:' in orders_src

    shop_src = open("src/bot/handlers/shop.py", encoding="utf-8").read()
    assert "adm_appr:" not in shop_src
    assert "adm_rej:" not in shop_src


def test_fsm_storage_uses_memory_under_pytest():
    storage = build_fsm_storage()
    assert isinstance(storage, MemoryStorage)


@pytest.mark.asyncio
async def test_redis_dedup_persists(db_redis):
    from src.core.dedup import TxDeduper
    d = TxDeduper("test_ns", ttl_seconds=60)
    assert await d.seen_or_mark("tx-1") is False
    assert await d.seen_or_mark("tx-1") is True
    assert await db_redis.get("dedup:test_ns:tx-1") == "1"
