"""
Phase 14.1 — Dedicated Admin Control Bot: ACL, Treasury, Sham Cash, Live Feed.

Run:  pytest -v tests/test_phase14_1_admin_bot.py
"""
from __future__ import annotations

import os
import json
import secrets
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_phase14_1_admin.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "111:CUSTOMER_BOT"
os.environ["ADMIN_BOT_TOKEN"] = "222:ADMIN_BOT_TOKEN"
os.environ["ADMIN_TELEGRAM_IDS"] = "900001,900002"
os.environ["ADMIN_TELEGRAM_ID"] = "900001"
os.environ["HOT_WALLET_ADDRESS"] = "0xAdminBotHotWallet00000000000000000001"
os.environ["TRON_HOT_WALLET_ADDRESS"] = "TAdminBotTronWallet0000000000000001"

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import settings
from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Order, MasterProduct, Category, Supplier, ProductSupplierLink,
    PaymentInvoice, AdminUser, HotWallet, AuditLog, BotConfig, PricingRule,
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod, PaymentNetwork,
    SupplierStatus, PricingScope, PricingRuleType,
)
from src.core.security import get_password_hash, encrypt_secret
from src.core.redis import event_bus
from src.core.services.treasury_service import treasury_service
from src.core.services.sham_cash_service import sham_cash_service
from src.core.services.order_service import order_service
from src.admin_bot.middlewares.admin_acl import AdminAclMiddleware, is_whitelisted_admin
from src.admin_bot.formatters.alerts import format_treasury_card
from src.admin_bot.handlers.live_feed import render_live_event, broadcast_to_admins
from src.admin_bot.keyboards.main_menu import sham_cash_review_keyboard

pytestmark = pytest.mark.asyncio

ADMIN_TG = 900001
STRANGER_TG = 555555


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # Reload whitelist from env into settings object
    settings.ADMIN_TELEGRAM_IDS = "900001,900002"
    settings.ADMIN_TELEGRAM_ID = 900001
    settings.ADMIN_BOT_TOKEN = "222:ADMIN_BOT_TOKEN"

    event_bus.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        session.add(AdminUser(
            username="admin",
            password_hash=get_password_hash("admin123456"),
            display_name="Phase14 Admin",
            role="super_admin",
            telegram_id=ADMIN_TG,
            is_active=True,
        ))
        session.add(HotWallet(
            address=os.environ["HOT_WALLET_ADDRESS"],
            usdt_balance=Decimal("1250.00"),
            bnb_balance=Decimal("2.00"),
        ))
        session.add(BotConfig(key="cashback_percent", value="0"))
        session.add(BotConfig(key="referral_commission_percent", value="0"))

        cat = Category(name_ar="ألعاب", name_en="Games", is_active=True)
        session.add(cat)
        await session.flush()

        prod = MasterProduct(
            category_id=cat.id,
            internal_sku="SKU-P14-1",
            title_ar="منتج اختبار الإدارة",
            title_en="Admin Test Product",
            warranty_days=30,
            is_active=True,
            custom_emoji="🎮",
        )
        session.add(prod)
        await session.flush()

        enc = encrypt_secret(json.dumps({"api_key": "x"}))
        sup = Supplier(
            name="AdminTest Supplier",
            driver_type="generic_rest",
            payment_network=PaymentNetwork.USDT_BEP20,
            status=SupplierStatus.ACTIVE,
            config_encrypted=enc,
            deposit_address="0xSupplierDepositAdminTest",
        )
        session.add(sup)
        await session.flush()
        session.add(ProductSupplierLink(
            master_product_id=prod.id,
            supplier_id=sup.id,
            supplier_product_id="sku1",
            last_known_cost=Decimal("5.00"),
            is_in_stock=True,
            available_quantity=10,
        ))
        session.add(PricingRule(
            scope=PricingScope.GLOBAL,
            rule_type=PricingRuleType.FIXED,
            value=Decimal("3.00"),
            gas_buffer_enabled=False,
            gas_buffer_fixed=Decimal("0"),
            is_active=True,
        ))

        cust = Customer(
            telegram_id=700001,
            telegram_username="buyer14",
            first_name="Buyer",
            wallet_balance=Decimal("100.00"),
        )
        session.add(cust)
        await session.commit()

        # Expose IDs
        setup_db.product_id = str(prod.id)
        setup_db.supplier_id = str(sup.id)
        setup_db.customer_id = str(cust.id)

    yield


# ---------------------------------------------------------------------------
# 1. ACL hard-deny
# ---------------------------------------------------------------------------

async def test_01_acl_denies_non_whitelisted_user():
    assert is_whitelisted_admin(ADMIN_TG) is True
    assert is_whitelisted_admin(900002) is True
    assert is_whitelisted_admin(STRANGER_TG) is False

    mw = AdminAclMiddleware()
    denied = []

    class FakeMsg:
        def __init__(self):
            self.from_user = MagicMock(id=STRANGER_TG)
            self.answers = []

        async def answer(self, text, **kwargs):
            self.answers.append(text)
            denied.append(text)

    msg = FakeMsg()
    called = {"handler": False}

    async def handler(event, data):
        called["handler"] = True
        return "OK"

    result = await mw(handler, msg, {"event_from_user": msg.from_user})
    assert result is None
    assert called["handler"] is False
    assert any("غير مصرح" in a for a in msg.answers)
    print("✓ ACL hard-denies stranger")


async def test_01b_acl_allows_whitelisted_admin():
    mw = AdminAclMiddleware()
    msg = MagicMock()
    msg.from_user = MagicMock(id=ADMIN_TG)

    async def handler(event, data):
        return "ALLOWED"

    result = await mw(handler, msg, {"event_from_user": msg.from_user})
    assert result == "ALLOWED"
    print("✓ ACL allows whitelisted admin")


# ---------------------------------------------------------------------------
# 2. Treasury matches DB via shared service
# ---------------------------------------------------------------------------

async def test_02_treasury_summary_matches_db_and_formatter():
    async with AsyncSessionLocal() as session:
        summary = await treasury_service.get_summary(session)

    assert summary["hot_wallet_usdt"] == 1250.0
    assert summary["total_liabilities"] == 100.0  # customer wallet
    assert abs(summary["net_withdrawable"] - (1250.0 - 100.0)) < 0.01
    assert "profit_today" in summary
    assert "profit_7d" in summary
    assert "profit_30d" in summary

    card = format_treasury_card(summary)
    assert "الخزينة والأرباح" in card
    assert "1250.00" in card or "1250" in card
    assert "100.00" in card or "100" in card
    print("✓ Treasury summary matches DB; Arabic card renders")


# ---------------------------------------------------------------------------
# 3. Sham Cash approve / reject via shared service
# ---------------------------------------------------------------------------

async def _seed_sham_order(session):
    cust = (await session.execute(
        select(Customer).where(Customer.telegram_id == 700001)
    )).scalar_one()
    prod_id = setup_db.product_id
    sup_id = setup_db.supplier_id
    order, inv = await order_service.create_order(
        session,
        customer_id=str(cust.id),
        master_product_id=prod_id,
        supplier_id=sup_id,
        supplier_product_id="sku1",
        supplier_cost=Decimal("5.00"),
        our_price=Decimal("8.00"),
        profit_margin=Decimal("3.00"),
        payment_method=PaymentMethod.SHAM_CASH,
    )
    inv.sham_cash_receipt_photo_file_id = "receipt_file_p14"
    inv.sham_cash_tx_ref = f"SC-{secrets.token_hex(3)}"
    await order_service.transition_order_state(
        session, order, OrderState.SHAM_CASH_REVIEW, "customer_uploaded_receipt"
    )
    await session.commit()
    return str(order.id), order.order_number


async def test_03_sham_cash_approve_updates_db_jit_notify_audit():
    published = []

    async def capture_publish(channel, message):
        published.append({"channel": channel, "message": message})
        if event_bus.redis:
            await event_bus.redis.publish(channel, json.dumps(message, default=str))

    event_bus.publish = capture_publish  # type: ignore

    async with AsyncSessionLocal() as session:
        oid, onum = await _seed_sham_order(session)

    with patch(
        "src.core.services.sham_cash_service.enqueue_jit_order",
        new_callable=AsyncMock,
    ) as jit_mock:
        async with AsyncSessionLocal() as session:
            result = await sham_cash_service.approve(
                session,
                order_id=oid,
                reviewer="tg:900001/@cto",
                admin_user_id=None,
                trigger_jit=True,
            )
        assert result["state"] == "payment_confirmed"
        jit_mock.assert_awaited_once_with(oid)

    async with AsyncSessionLocal() as session:
        import uuid
        order = (await session.execute(
            select(Order).where(Order.id == uuid.UUID(oid)).options(selectinload(Order.invoice))
        )).scalar_one()
        assert order.state == OrderState.PAYMENT_CONFIRMED
        assert order.invoice.status == InvoiceStatus.CONFIRMED

        audits = (await session.execute(
            select(AuditLog).where(AuditLog.action == "sham_cash_approve")
        )).scalars().all()
        assert len(audits) >= 1
        assert audits[-1].new_values.get("state") == "payment_confirmed"

    notify = [
        p for p in published
        if p["channel"] == "bot:send_message"
        and "تم تأكيد الدفع" in (p["message"].get("text") or "")
    ]
    assert notify, "Customer Arabic confirm notice missing"
    print("✓ Sham approve → PAYMENT_CONFIRMED + JIT + customer notify + audit")


async def test_04_sham_cash_reject_updates_db_notify_audit():
    published = []

    async def capture_publish(channel, message):
        published.append({"channel": channel, "message": message})

    event_bus.publish = capture_publish  # type: ignore

    async with AsyncSessionLocal() as session:
        oid, onum = await _seed_sham_order(session)

    async with AsyncSessionLocal() as session:
        result = await sham_cash_service.reject(
            session,
            order_id=oid,
            reviewer="tg:900001",
            reason="إيصال مزور",
        )
    assert result["state"] == "rejected"

    async with AsyncSessionLocal() as session:
        import uuid
        order = (await session.execute(
            select(Order).where(Order.id == uuid.UUID(oid)).options(selectinload(Order.invoice))
        )).scalar_one()
        assert order.state == OrderState.REJECTED
        assert order.invoice.status == InvoiceStatus.REJECTED
        audits = (await session.execute(
            select(AuditLog).where(AuditLog.action == "sham_cash_reject")
        )).scalars().all()
        assert len(audits) >= 1
        assert "مزور" in (audits[-1].new_values.get("reason") or "")

    reject_msgs = [
        p for p in published
        if p["channel"] == "bot:send_message"
        and "تم رفض العملية" in (p["message"].get("text") or "")
    ]
    assert reject_msgs
    assert "مزور" in reject_msgs[-1]["message"]["text"]
    print("✓ Sham reject → REJECTED + customer notify + audit")


# ---------------------------------------------------------------------------
# 4. Live feed formatter + broadcast
# ---------------------------------------------------------------------------

async def test_05_live_feed_formats_sham_review_and_broadcasts():
    payload = {
        "type": "order_state_change",
        "order_id": "11111111-1111-1111-1111-111111111111",
        "new_state": "sham_cash_review",
        "details": {"order_number": "ORD-TEST-0001", "trigger": "customer_uploaded_receipt"},
    }
    text, kb = render_live_event("orders:live", payload)
    assert text and "شام كاش" in text
    assert kb is not None
    # Keyboard has approve + reject
    rows = kb.inline_keyboard
    flat = [btn.callback_data for row in rows for btn in row]
    assert any(c.startswith("adm_sc_ok:") for c in flat)
    assert any(c.startswith("adm_sc_no:") for c in flat)

    alert_text, _ = render_live_event("alerts:stream", {
        "title": "Hot Wallet Low",
        "message": "USDT below reserve",
        "level": "danger",
    })
    assert "Hot Wallet Low" in alert_text or "تنبيه" in alert_text or "🚨" in alert_text

    # Broadcast to both whitelisted admins
    bot = MagicMock()
    bot.send_message = AsyncMock()
    n = await broadcast_to_admins(bot, "اختبار تنبيه إداري")
    assert n == 2  # 900001 and 900002
    assert bot.send_message.await_count == 2
    print("✓ Live feed formats Sham card + broadcasts to all ADMIN_TELEGRAM_IDS")


async def test_06_pending_list_via_shared_service():
    async with AsyncSessionLocal() as session:
        await _seed_sham_order(session)
        pending = await sham_cash_service.list_pending(session)
    assert len(pending) >= 1
    assert pending[0]["receipt_file_id"]
    assert pending[0]["order_number"]
    print("✓ Shared list_pending returns Sham Cash queue")
