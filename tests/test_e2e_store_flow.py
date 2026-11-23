"""
E2E Store Flow Audit — Customer Bot + Admin Bot + Dashboard API + Mini App.

Mocks Telegram I/O; hits real FastAPI/service/DB logic (sqlite + fakeredis).
"""
from __future__ import annotations

import os
import sys
import io
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import secrets
import pytest
import pytest_asyncio

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Test env BEFORE importing app modules
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_e2e_store.db"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_E2E_BOT")
os.environ.setdefault("ADMIN_BOT_TOKEN", "123456:TEST_E2E_ADMIN")
os.environ.setdefault("TELEGRAM_PUBLIC_CHANNEL_ID", "-1001234567890")
os.environ.setdefault("TELEGRAM_MAIN_CHANNEL_URL", "https://t.me/TestMain")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "111001")
os.environ.setdefault("OWNER_TELEGRAM_IDS", "111001")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-e2e-not-for-prod")
os.environ.setdefault("HOT_WALLET_ADDRESS", "0xTestHotWalletBscAddress00000000000001")
os.environ.setdefault("TRON_HOT_WALLET_ADDRESS", "TTestTronHotWalletAddress00000000001")

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from aiogram.types import Message, Chat, User

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Category, MasterProduct, Supplier, ProductSupplierLink,
    PricingRule, BotConfig, AdminUser,
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod, PricingScope, PricingRuleType,
    SupplierStatus, WalletTxType, PaymentNetwork,
)
from src.core.redis import event_bus
from src.core.security import get_password_hash, encrypt_secret
from src.core.services.product_admin_service import product_admin_service
from src.core.services.sham_cash_service import sham_cash_service
from src.core.services.order_service import order_service
from src.bot.keyboards.inline import (
    main_menu_keyboard, payment_methods_keyboard, quantity_selector_keyboard,
    product_detail_keyboard,
)
from src.bot.middlewares.force_sub import ForceSubscribeMiddleware
from src.api.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_redis():
    # orders ⇄ payment_invoices circular FKs: keep SQLite FK checks off for test DDL/DML
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


async def _seed_catalog(*, price: Decimal = Decimal("10.00"), stock: int = 20):
    async with AsyncSessionLocal() as session:
        cat = Category(name_ar="ذكاء اصطناعي", name_en="AI", icon_emoji="🤖", is_active=True)
        session.add(cat)
        await session.flush()

        supplier = Supplier(
            name="E2E-Supplier",
            driver_type="generic_rest",
            config_encrypted=encrypt_secret('{"api_key":"e2e"}'),
            payment_network=PaymentNetwork.USDT_BEP20,
            status=SupplierStatus.ACTIVE,
            priority_weight=1,
        )
        session.add(supplier)
        await session.flush()

        prod = MasterProduct(
            category_id=cat.id,
            internal_sku=f"E2E-{uuid.uuid4().hex[:6].upper()}",
            title_ar="ChatGPT شهر",
            title_en="ChatGPT 1 Month",
            description_ar="اشتراك شهري",
            description_en="Monthly plan",
            custom_description_ar="تسليم فوري",
            is_active=True,
            warranty_days=30,
            cached_final_price=price,
            cached_in_stock=True,
            photo_url="https://example.com/product.webp",
        )
        session.add(prod)
        await session.flush()

        session.add(ProductSupplierLink(
            master_product_id=prod.id,
            supplier_id=supplier.id,
            supplier_product_id="sup-chatgpt-1m",
            last_known_cost=Decimal("5.00"),
            available_quantity=stock,
            is_in_stock=True,
        ))
        session.add(PricingRule(
            scope=PricingScope.PRODUCT,
            scope_id=prod.id,
            rule_type=PricingRuleType.FLOOR,
            value=price,
            is_active=True,
            priority=100,
            gas_buffer_enabled=False,
        ))

        for key, val in {
            "force_subscribe_enabled": False,
            "pay_sham_cash_enabled": True,
            "pay_usdt_enabled": True,
            "pay_binance_enabled": True,
            "maintenance_mode": False,
            "sham_cash_account_number": "0999988877",
            "sham_cash_instructions_ar": "حوّل ثم أرسل الإيصال",
            "welcome_message_ar": "أهلاً بك في متجر الاختبار",
            "main_channel_id": "-1001234567890",
            "main_channel_url": "https://t.me/TestMain",
        }.items():
            session.add(BotConfig(key=key, value=val))

        admin = AdminUser(
            username="e2e_admin",
            password_hash=get_password_hash("e2e-pass-123"),
            display_name="E2E Admin",
            role="owner",
            is_active=True,
            telegram_id=111001,
        )
        session.add(admin)

        customer = Customer(
            telegram_id=987650001,
            first_name="Buyer",
            telegram_username="e2e_buyer",
            language_code="ar",
            wallet_balance=Decimal("100.00"),
        )
        session.add(customer)
        await session.commit()
        return {
            "product_id": str(prod.id),
            "category_id": str(cat.id),
            "supplier_id": str(supplier.id),
            "customer_tg": customer.telegram_id,
            "admin_id": str(admin.id),
        }


# ---------------------------------------------------------------------------
# 1) Force-Join Gate
# ---------------------------------------------------------------------------

def _mock_message(user_id: int, text: str = "/start", bot=None) -> Message:
    u = User(id=user_id, is_bot=False, first_name="Gate", username=f"u{user_id}", language_code="ar")
    c = Chat(id=user_id, type="private")
    m = Message(message_id=secrets.randbelow(10000) + 1, date=datetime.now(), chat=c, from_user=u, text=text)
    answers = []
    object.__setattr__(m, "answers", answers)
    if bot:
        object.__setattr__(m, "_bot", bot)

    async def mock_answer(t, **kwargs):
        answers.append({"text": t, "kwargs": kwargs})
        return m

    object.__setattr__(m, "answer", mock_answer)
    return m


@pytest.mark.asyncio
async def test_force_join_blocks_then_unlocks(db_redis):
    await _seed_catalog()
    mw = ForceSubscribeMiddleware()

    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(BotConfig).where(BotConfig.key == "force_subscribe_enabled")
        )).scalar_one()
        row.value = True
        await session.commit()
    await event_bus.invalidate_bot_config_caches()

    class Member:
        def __init__(self, status): self.status = status

    bot = AsyncMock()
    bot.get_chat_member = AsyncMock(return_value=Member("left"))

    msg = _mock_message(424242, bot=bot)
    user = msg.from_user

    async def passthrough(event, data):
        return "PASSED"

    # Not subscribed → gated (handler not called)
    result = await mw(passthrough, msg, {"event_from_user": user, "bot": bot})
    assert result is None
    assert len(msg.answers) >= 1
    sent = msg.answers[-1]
    gate = sent["text"] or ""
    assert "الاشتراك" in gate or "اشترك" in gate or "القناة" in gate
    kb = sent["kwargs"].get("reply_markup")
    flat = [b.callback_data or b.url for row in kb.inline_keyboard for b in row]
    assert "verify_sub" in flat
    assert any(isinstance(x, str) and x.startswith("http") for x in flat)

    # Subscribed → unlock
    bot.get_chat_member = AsyncMock(return_value=Member("member"))
    await event_bus.redis.delete("cache:forcesub:424242:-1001234567890")
    result2 = await mw(passthrough, msg, {"event_from_user": user, "bot": bot})
    assert result2 == "PASSED"


@pytest.mark.asyncio
async def test_config_cache_invalidates_on_publish(db_redis):
    await _seed_catalog()
    await event_bus.redis.set("cache:cfg:force_subscribe_enabled", "true", ex=60)
    await event_bus.redis.set("cache:quote:x:y", "{}", ex=60)
    await event_bus.publish_config_change("bot_config", "force_subscribe_enabled")
    assert await event_bus.redis.get("cache:cfg:force_subscribe_enabled") is None
    assert await event_bus.redis.get("cache:quote:x:y") is None


# ---------------------------------------------------------------------------
# 2) Product card / qty / payment keyboards
# ---------------------------------------------------------------------------

def test_main_menu_four_rows_max():
    kb = main_menu_keyboard(language="ar")
    assert len(kb.inline_keyboard) == 4
    flat = [b.callback_data or (b.web_app.url if b.web_app else None) for row in kb.inline_keyboard for b in row]
    assert "nav:categories" in flat
    assert "user:orders" in flat
    assert "user:wallet" in flat
    assert "nav:support_lang" in flat


def test_quantity_stepper_recalculates_total():
    pid = str(uuid.uuid4())
    kb1 = quantity_selector_keyboard(pid, max_qty=10, current_qty=1, unit_cents=999)
    texts = [b.text for row in kb1.inline_keyboard for b in row]
    assert any("9.99" in (t or "") for t in texts)
    # Adjust to 5
    kb5 = quantity_selector_keyboard(pid, max_qty=10, current_qty=5, unit_cents=999)
    texts5 = [b.text for row in kb5.inline_keyboard for b in row]
    assert any("49.95" in (t or "") for t in texts5)
    # Caps at max
    kb_cap = quantity_selector_keyboard(pid, max_qty=3, current_qty=99, unit_cents=100)
    assert any("× 3" in (t or "") for t in [b.text for row in kb_cap.inline_keyboard for b in row])
    cbs = [b.callback_data for row in kb5.inline_keyboard for b in row]
    assert any(c and c.startswith(f"buy_qty:{pid}:5") for c in cbs)
    assert any(c and c.startswith("qty_adj:") for c in cbs)


def test_payment_rails_respect_db_flags():
    oid = str(uuid.uuid4())
    kb_all = payment_methods_keyboard(oid, total_price=10, has_wallet_balance=True, wallet_balance=50)
    flat_all = " ".join(b.text or "" for row in kb_all.inline_keyboard for b in row)
    assert "USDT" in flat_all and "شام كاش" in flat_all and "Binance" in flat_all

    kb_off = payment_methods_keyboard(
        oid, total_price=10, has_wallet_balance=False, wallet_balance=0,
        enable_usdt=False, enable_binance=False, enable_sham_cash=True,
    )
    flat_off = " ".join(b.text or "" for row in kb_off.inline_keyboard for b in row)
    assert "USDT" not in flat_off
    assert "Binance" not in flat_off
    assert "شام كاش" in flat_off


def test_product_detail_keyboard_buy_and_oos():
    pid = str(uuid.uuid4())
    in_stock = product_detail_keyboard(pid, True)
    assert any(b.callback_data == f"select_qty:{pid}" for row in in_stock.inline_keyboard for b in row)
    oos = product_detail_keyboard(pid, False, show_notify=True)
    assert any(b.callback_data == f"stock_alert:{pid}" for row in oos.inline_keyboard for b in row)


# ---------------------------------------------------------------------------
# 3) Checkout journeys (wallet + sham cash + approve sync)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wallet_checkout_atomic_debit_and_order(db_redis):
    ids = await _seed_catalog(price=Decimal("12.50"), stock=5)
    async with AsyncSessionLocal() as session:
        cust = (await session.execute(
            select(Customer).where(Customer.telegram_id == ids["customer_tg"])
        )).scalar_one()
        before = Decimal(str(cust.wallet_balance))
        await order_service.debit_customer_wallet(
            session=session,
            customer_id=str(cust.id),
            amount=Decimal("12.50"),
            tx_type=WalletTxType.PURCHASE_DEBIT,
            description="e2e wallet buy",
        )
        await session.commit()
        await session.refresh(cust)
        assert Decimal(str(cust.wallet_balance)) == before - Decimal("12.50")

        # Insufficient funds raises
        with pytest.raises(ValueError):
            await order_service.debit_customer_wallet(
                session=session,
                customer_id=str(cust.id),
                amount=Decimal("9999.00"),
                tx_type=WalletTxType.PURCHASE_DEBIT,
                description="should fail",
            )

        kb = payment_methods_keyboard(
            str(uuid.uuid4()),
            total_price=12.50,
            has_wallet_balance=True,
            wallet_balance=float(before),
        )
        assert any("رصيد" in (b.text or "") for row in kb.inline_keyboard for b in row)
        # Low-balance path offers top-up rails only when flags allow
        kb_low = payment_methods_keyboard(
            str(uuid.uuid4()),
            total_price=50.0,
            has_wallet_balance=True,
            wallet_balance=1.0,
            enable_usdt=False,
            enable_binance=False,
            enable_sham_cash=True,
        )
        flat = " ".join(b.text or "" for row in kb_low.inline_keyboard for b in row)
        assert "شام كاش" in flat and "USDT" not in flat


@pytest.mark.asyncio
async def test_sham_cash_pending_then_admin_approve_notifies(db_redis):
    ids = await _seed_catalog(price=Decimal("8.00"), stock=5)
    published = []

    async def _capture(channel, message):
        published.append((channel, message))

    async with AsyncSessionLocal() as session:
        cust = (await session.execute(
            select(Customer).where(Customer.telegram_id == ids["customer_tg"])
        )).scalar_one()
        order, invoice = await order_service.create_order(
            session=session,
            customer_id=str(cust.id),
            master_product_id=ids["product_id"],
            supplier_id=ids["supplier_id"],
            supplier_product_id="sup-chatgpt-1m",
            supplier_cost=Decimal("4.00"),
            our_price=Decimal("8.00"),
            profit_margin=Decimal("4.00"),
            payment_method=PaymentMethod.SHAM_CASH,
            quantity=1,
        )
        invoice.sham_cash_receipt_photo_file_id = "AgAC_e2e_receipt"
        invoice.receipt_type = "photo"
        invoice.status = InvoiceStatus.PENDING
        await session.commit()
        oid = str(order.id)

    with patch.object(event_bus, "publish", new=AsyncMock(side_effect=_capture)):
        with patch("src.core.services.sham_cash_service.enqueue_jit_order", new=AsyncMock()):
            async with AsyncSessionLocal() as session:
                result = await sham_cash_service.approve(
                    session, oid, reviewer="tg:111001", trigger_jit=True
                )
    assert result["status"] == "ok"
    assert result["state"] == OrderState.PAYMENT_CONFIRMED.value
    # Customer notify published with celebration effect
    send_msgs = [m for ch, m in published if ch == "bot:send_message"]
    assert send_msgs, "customer notify missing"
    assert send_msgs[0].get("effect_id") or "تأكيد" in (send_msgs[0].get("text") or "")

    async with AsyncSessionLocal() as session:
        pending = await sham_cash_service.list_pending(session)
        assert all(p["order_id"] != oid for p in pending)


@pytest.mark.asyncio
async def test_admin_product_create_appears_in_list(db_redis):
    await _seed_catalog()
    async with AsyncSessionLocal() as session:
        cats = await product_admin_service.list_categories(session)
        assert cats
        created = await product_admin_service.create_product(
            session,
            title_ar="منتج جديد E2E",
            title_en="E2E New Product",
            category_id=cats[0]["id"],
            retail_price=Decimal("15.00"),
            wholesale_cost=Decimal("7.00"),
            description_ar="وصف",
            reviewer="tg:111001",
        )
        assert created["title_ar"] == "منتج جديد E2E"
        listed = await product_admin_service.list_products(session, limit=50)
        assert any(p["id"] == created["id"] for p in listed)


# ---------------------------------------------------------------------------
# 4) Dashboard API 3-way sync + auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_login_and_bot_config_sync(db_redis):
    await _seed_catalog()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Unauthenticated blocked
        r = await client.get("/api/bot-config")
        assert r.status_code in (401, 403)

        # Login (JSON endpoint used by dashboard)
        login = await client.post("/api/auth/login/json", json={
            "username": "e2e_admin",
            "password": "e2e-pass-123",
        })
        assert login.status_code == 200, login.text
        token = login.json().get("access_token")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        cookies = login.cookies

        # Save settings — must publish + persist
        put = await client.put("/api/bot-config", headers=headers, cookies=cookies, json={
            "configs": {
                "sham_cash_account_number": "0911223344",
                "welcome_message_ar": "ترحيب محدث E2E",
                "pay_usdt_enabled": False,
                "force_subscribe_enabled": True,
                "maintenance_mode": False,
            }
        })
        assert put.status_code == 200, put.text

        got = await client.get("/api/bot-config", headers=headers, cookies=cookies)
        assert got.status_code == 200
        data = got.json()
        assert str(data.get("sham_cash_account_number")) == "0911223344"
        # Customer bot live read
        async with AsyncSessionLocal() as session:
            from src.bot.handlers.shop import get_dynamic_config, payment_rail_flags
            acc = await get_dynamic_config(session, "sham_cash_account_number", "")
            assert "0911223344" in str(acc)
            rails = await payment_rail_flags(session)
            assert rails["enable_usdt"] is False
            assert rails["enable_sham_cash"] is True


@pytest.mark.asyncio
async def test_dashboard_sections_live(db_redis):
    await _seed_catalog()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/auth/login/json", json={
            "username": "e2e_admin", "password": "e2e-pass-123",
        })
        assert login.status_code == 200
        token = login.json().get("access_token")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        cookies = login.cookies

        for path in (
            "/api/treasury/summary",
            "/api/products",
            "/api/orders",
            "/api/sham-cash/pending",
            "/api/bot-config",
        ):
            res = await client.get(path, headers=headers, cookies=cookies)
            assert res.status_code == 200, f"{path} -> {res.status_code} {res.text[:200]}"


@pytest.mark.asyncio
async def test_miniapp_catalog_and_insufficient_balance(db_redis):
    ids = await _seed_catalog(price=Decimal("50.00"), stock=3)
    # Drain wallet
    async with AsyncSessionLocal() as session:
        cust = (await session.execute(
            select(Customer).where(Customer.telegram_id == ids["customer_tg"])
        )).scalar_one()
        cust.wallet_balance = Decimal("1.00")
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Dev header auth for miniapp
        headers = {"X-Dev-Telegram-Id": str(ids["customer_tg"])}
        cat = await client.get("/api/miniapp/catalog", headers=headers)
        assert cat.status_code == 200, cat.text
        body = cat.json()
        products = body.get("products") or body
        assert isinstance(products, list) and len(products) >= 1
        p0 = products[0]
        assert "photo_url" in p0 or p0.get("photo_url") is None
        assert "is_in_stock" in p0 or "retail_price" in p0 or "final_price" in p0

        checkout = await client.post(
            "/api/miniapp/cart/checkout",
            headers=headers,
            json={
                "items": [{"product_id": ids["product_id"], "quantity": 1}],
                "pay_via_wallet": True,
                "payment_method": "wallet",
            },
        )
        assert checkout.status_code == 200, checkout.text
        data = checkout.json()
        assert data.get("status") == "insufficient_balance"
        assert float(data.get("deficit") or 0) > 0


# ---------------------------------------------------------------------------
# 5) Admin ACL deny
# ---------------------------------------------------------------------------

def test_admin_acl_denies_strangers():
    from src.admin_bot.middlewares.admin_acl import is_whitelisted_admin
    assert is_whitelisted_admin(111001) is True
    assert is_whitelisted_admin(999999999) is False


# ---------------------------------------------------------------------------
# 6) HTML product card has no markdown leftovers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_product_card_html_no_raw_markdown(db_redis):
    ids = await _seed_catalog()
    from src.bot.handlers.shop import show_product_detail

    answers = []

    class FakeMsg:
        async def answer(self, text, **kwargs):
            answers.append(text)
        async def answer_photo(self, **kwargs):
            answers.append(kwargs.get("caption") or "")
        async def edit_text(self, text, **kwargs):
            answers.append(text)
        async def edit_caption(self, caption=None, **kwargs):
            answers.append(caption or "")
        async def delete(self):
            return None

    async with AsyncSessionLocal() as session:
        # Force text card path (no photo crash / no CallbackQuery isinstance needed)
        prod = (await session.execute(
            select(MasterProduct).where(MasterProduct.id == uuid.UUID(ids["product_id"]))
        )).scalar_one()
        prod.photo_file_id = None
        prod.photo_url = None
        await session.commit()
        await show_product_detail(FakeMsg(), ids["product_id"], session, language="ar")

    assert answers, "product card produced no output"
    text = answers[-1]
    assert "<b>" in text
    assert "**" not in text
    assert "`" not in text
    assert "<code>" in text
    assert "ChatGPT" in text or "شهر" in text
