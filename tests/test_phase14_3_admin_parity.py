"""
Phase 14.3 — Admin Bot full control parity + fulfillment report.

Run:  pytest -v tests/test_phase14_3_admin_parity.py
"""
from __future__ import annotations

import os
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_phase14_3_parity.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "111:CUSTOMER"
os.environ["ADMIN_BOT_TOKEN"] = "222:ADMIN"
os.environ["ADMIN_TELEGRAM_IDS"] = "900001"
os.environ["HOT_WALLET_ADDRESS"] = "0xPhase143HotWallet00000000000000000001"
os.environ["TRON_HOT_WALLET_ADDRESS"] = "TPhase143TronWallet000000000000001"
os.environ["BINANCE_PAY_MERCHANT_ID"] = "143000111"

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select

from src.config import settings
from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Order, MasterProduct, Category, Supplier, ProductSupplierLink,
    PaymentInvoice, AuditLog, PricingRule, Coupon, SupportTicket, HotWallet,
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod, PaymentNetwork,
    SupplierStatus, PricingScope, PricingRuleType, DiscountType,
    TicketStatus, WalletTxType,
)
from src.core.security import encrypt_secret
from src.core.redis import event_bus
from src.core.services.order_service import order_service
from src.core.services.supplier_admin_service import supplier_admin_service
from src.core.services.product_admin_service import product_admin_service
from src.core.services.order_admin_service import order_admin_service
from src.core.services.crm_service import crm_service
from src.core.services.marketing_admin_service import marketing_admin_service
from src.core.services.fulfillment_report import fulfillment_report_service
from src.core.services.pricing_service import pricing_service
from src.admin_bot.keyboards.main_menu import orders_hub_keyboard, order_actions_keyboard

pytestmark = pytest.mark.asyncio


class FakeHealth:
    def __init__(self, alive=True, balance=Decimal("42.50")):
        self.is_alive = alive
        self.latency_ms = 12
        self.http_status = 200
        self.balance_usd = balance
        self.error_message = None


class FakeProduct:
    def __init__(self, pid="SKU-1", name="Game Key", cost="5.00", qty=10):
        self.supplier_product_id = pid
        self.name = name
        self.raw_name = name
        self.cost = Decimal(cost)
        self.is_in_stock = True
        self.available_quantity = qty
        self.currency = "USD"
        self.supported_deposit_networks = []


class FakeAdapter:
    async def check_health(self):
        return FakeHealth()

    async def fetch_products(self):
        return [FakeProduct(), FakeProduct("SKU-2", "Other", "7.00", 3)]


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    settings.ADMIN_TELEGRAM_IDS = "900001"
    settings.HOT_WALLET_ADDRESS = os.environ["HOT_WALLET_ADDRESS"]
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
            internal_sku="SKU-P143",
            title_ar="منتج أساسي",
            title_en="Base",
            is_active=True,
            warranty_days=30,
        )
        session.add(prod)
        await session.flush()
        sup = Supplier(
            name="ParitySup",
            driver_type="generic_rest",
            status=SupplierStatus.ACTIVE,
            config_encrypted=encrypt_secret('{"base_url":"https://x.test","api_key":"k"}'),
            payment_network=PaymentNetwork.USDT_BEP20,
            deposit_address="0xsup",
            cached_balance=Decimal("10"),
        )
        session.add(sup)
        await session.flush()
        session.add(ProductSupplierLink(
            master_product_id=prod.id,
            supplier_id=sup.id,
            supplier_product_id="SKU-1",
            last_known_cost=Decimal("5.00"),
            is_in_stock=True,
            available_quantity=10,
        ))
        session.add(PricingRule(
            scope=PricingScope.PRODUCT,
            scope_id=prod.id,
            rule_type=PricingRuleType.PERCENTAGE,
            value=Decimal("20"),
            gas_buffer_enabled=False,
            is_active=True,
            priority=100,
        ))
        session.add(Customer(
            telegram_id=800001,
            telegram_username="buyer143",
            first_name="Buyer",
            wallet_balance=Decimal("50.00"),
        ))
        session.add(HotWallet(
            address=os.environ["HOT_WALLET_ADDRESS"],
            usdt_balance=Decimal("100"),
            bnb_balance=Decimal("1"),
        ))
        await session.commit()
        setup_db.product_id = str(prod.id)
        setup_db.supplier_id = str(sup.id)
        setup_db.customer_id = str((await session.execute(select(Customer))).scalar_one().id)

    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _seed_order(session, *, state=OrderState.FAILED_MANUAL_REVIEW, price=Decimal("12.00")):
    cust = (await session.execute(select(Customer))).scalar_one()
    prod = (await session.execute(select(MasterProduct))).scalar_one()
    link = (await session.execute(select(ProductSupplierLink))).scalar_one()
    order, inv = await order_service.create_order(
        session=session,
        customer_id=str(cust.id),
        master_product_id=str(prod.id),
        supplier_id=str(link.supplier_id),
        supplier_product_id=link.supplier_product_id,
        supplier_cost=Decimal("5.00"),
        our_price=price,
        profit_margin=Decimal("7.00"),
        payment_method=PaymentMethod.USDT_BEP20_DIRECT,
    )
    if state != OrderState.AWAITING_PAYMENT:
        await order_service.transition_order_state(
            session, order, OrderState.PAYMENT_CONFIRMED, "test_seed"
        )
        if state != OrderState.PAYMENT_CONFIRMED:
            await order_service.transition_order_state(
                session, order, state, "test_seed_fail"
            )
    await session.commit()
    return order, inv


# ---------- 1. Supplier add + test + cherry-pick + disable ----------
async def test_supplier_add_test_cherrypick_disable():
    with patch(
        "src.core.services.supplier_admin_service.AdapterRegistry.create_from_supplier",
        return_value=FakeAdapter(),
    ):
        async with AsyncSessionLocal() as session:
            created = await supplier_admin_service.create_supplier(
                session,
                name="NewAPI",
                driver_type="generic_rest",
                config={"base_url": "https://api.test", "api_key": "secret"},
                reviewer="tg:900001",
            )
            assert created["name"] == "NewAPI"
            sid = created["id"]

            health = await supplier_admin_service.test_connection(session, sid)
            assert health["is_alive"] is True
            assert health["status_emoji"] == "🟢"
            assert health["balance_usd"] == 42.5

            result = await supplier_admin_service.cherry_pick_create(
                session,
                supplier_id=sid,
                supplier_product_id="SKU-1",
                title_ar="مفتاح مستورد",
                pricing_rule_type=PricingRuleType.PERCENTAGE,
                pricing_value=Decimal("25"),
                reviewer="tg:900001",
            )
            assert result["title_ar"] == "مفتاح مستورد"
            new_pid = result["product_id"]

            # Price visible while supplier still active
            pricing = await pricing_service.get_best_supplier_and_price(session, new_pid)
            assert pricing["final_price"] is not None
            assert pricing["final_price"] > Decimal("5")

            disabled = await supplier_admin_service.set_status(
                session, sid, active=False, reviewer="tg:900001"
            )
            assert disabled["status"] == "inactive"

            audits = (await session.execute(select(AuditLog))).scalars().all()
            actions = {a.action for a in audits}
            assert "supplier_create" in actions
            assert "product_cherry_pick_create" in actions
            assert "supplier_toggle" in actions


# ---------- 2. Product title + pricing edit updates retail price ----------
async def test_product_title_and_pricing_edit_updates_price():
    pid = setup_db.product_id
    async with AsyncSessionLocal() as session:
        before = await product_admin_service.get_product(session, pid)
        old_price = before["final_price"]

        updated = await product_admin_service.update_product(
            session, pid, title_ar="عنوان جديد من الأدمن", reviewer="tg:900001"
        )
        assert updated["title_ar"] == "عنوان جديد من الأدمن"

        priced = await product_admin_service.upsert_product_pricing_rule(
            session, pid,
            rule_type=PricingRuleType.FIXED,
            value=Decimal("4.00"),
            reviewer="tg:900001",
        )
        # cost 5 + fixed 4 = 9
        assert priced["final_price"] == 9.0
        assert priced["final_price"] != old_price

        audits = (await session.execute(
            select(AuditLog).where(AuditLog.action.in_(["product_update", "pricing_rule_upsert"]))
        )).scalars().all()
        assert len(audits) >= 2


# ---------- 3. Retry JIT + refund + double-refund ----------
async def test_order_retry_jit_and_refund_double_reject():
    async with AsyncSessionLocal() as session:
        order, _ = await _seed_order(session, state=OrderState.FAILED_MANUAL_REVIEW)
        oid = str(order.id)
        onum = order.order_number

    with patch("src.core.services.order_admin_service.enqueue_jit_order", new_callable=AsyncMock) as enq:
        async with AsyncSessionLocal() as session:
            result = await order_admin_service.retry_jit(
                session, oid, reviewer="tg:900001"
            )
        assert result["status"] == "ok"
        enq.assert_awaited_once()

    async with AsyncSessionLocal() as session:
        found = await order_admin_service.find_by_number(session, onum)
        assert found["can_retry_jit"] is True or found["state"] == "payment_confirmed"

    with patch(
        "src.core.services.fulfillment_report.fulfillment_report_service.dispatch_for_order",
        new_callable=AsyncMock,
    ):
        async with AsyncSessionLocal() as session:
            refunded = await order_admin_service.refund_to_wallet(
                session, oid, reviewer="tg:900001", reason="test refund"
            )
        assert refunded["state"] == "failed_refunded"
        assert refunded["refund_amount"] == 12.0

        async with AsyncSessionLocal() as session:
            cust = (await session.execute(select(Customer))).scalar_one()
            assert cust.wallet_balance == Decimal("62.00")  # 50 + 12

        with pytest.raises(ValueError, match="already refunded"):
            async with AsyncSessionLocal() as session:
                await order_admin_service.refund_to_wallet(
                    session, oid, reviewer="tg:900001"
                )

    kb = order_actions_keyboard(oid, can_retry=True, can_refund=True)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"adm:ord:retry:{oid}" in datas
    assert f"adm:ord:refund:{oid}" in datas
    assert "adm:orders:search" in [
        b.callback_data for row in orders_hub_keyboard().inline_keyboard for b in row
    ]


# ---------- 4. CRM credit/debit/ban + ticket reply ----------
async def test_crm_credit_debit_ban_and_ticket_reply():
    cid = setup_db.customer_id
    async with AsyncSessionLocal() as session:
        found = await crm_service.search_customer(session, "buyer143")
        assert found["telegram_id"] == 800001

        credit = await crm_service.adjust_balance(
            session, cid, amount=Decimal("5"), direction="credit",
            reason="بونص اختبار", reviewer="tg:900001",
        )
        assert credit["new_balance"] == 55.0

        debit = await crm_service.adjust_balance(
            session, cid, amount=Decimal("2"), direction="debit",
            reason="خصم اختبار", reviewer="tg:900001",
        )
        assert debit["new_balance"] == 53.0

        banned = await crm_service.set_ban(
            session, cid, ban=True, reason="abuse", reviewer="tg:900001"
        )
        assert banned["is_blocked"] is True
        unbanned = await crm_service.set_ban(
            session, cid, ban=False, reviewer="tg:900001"
        )
        assert unbanned["is_blocked"] is False

        # Ticket reply tunnel
        order, _ = await _seed_order(session, state=OrderState.COMPLETED, price=Decimal("8"))
        ticket = SupportTicket(
            customer_id=cid if not isinstance(cid, str) else __import__("uuid").UUID(cid),
            order_id=order.id,
            subject="مشكلة تسليم",
            status=TicketStatus.OPEN,
        )
        # fix customer_id type
        from uuid import UUID
        ticket.customer_id = UUID(cid)
        session.add(ticket)
        await session.commit()
        await session.refresh(ticket)
        tid = str(ticket.id)

    with patch("src.core.services.crm_service.event_bus.publish", new_callable=AsyncMock) as pub:
        async with AsyncSessionLocal() as session:
            await crm_service.reply_ticket(
                session, tid, message_text="تم التحقق من المفتاح",
                reviewer="tg:900001",
            )
        pub.assert_awaited()
        assert pub.await_args.args[0] == "bot:ticket_reply"

    async with AsyncSessionLocal() as session:
        audits = (await session.execute(
            select(AuditLog).where(AuditLog.action.in_([
                "balance_credit", "balance_debit", "customer_ban", "customer_unban", "ticket_reply"
            ]))
        )).scalars().all()
        assert len(audits) >= 5


# ---------- 5. Coupon + fulfillment report ----------
async def test_coupon_and_fulfillment_report_card():
    async with AsyncSessionLocal() as session:
        coupon = await marketing_admin_service.create_coupon(
            session,
            code="VIP20",
            discount_type=DiscountType.PERCENT,
            value=Decimal("20"),
            max_uses=50,
            reviewer="tg:900001",
        )
        assert coupon["code"] == "VIP20"
        toggled = await marketing_admin_service.toggle_coupon(
            session, coupon["coupon_id"], reviewer="tg:900001"
        )
        assert toggled["is_active"] is False

        order, _ = await _seed_order(session, state=OrderState.COMPLETED, price=Decimal("20"))
        order.gas_fee_actual = Decimal("0.08")
        order.profit_margin = Decimal("14.92")
        order.completed_at = datetime.now(timezone.utc)
        await session.commit()
        oid = order.id

    with patch("src.core.services.fulfillment_report.event_bus.publish", new_callable=AsyncMock) as pub:
        async with AsyncSessionLocal() as session:
            payload = await fulfillment_report_service.dispatch_for_order(
                session, order_id=oid, outcome="COMPLETED"
            )
        assert payload["net_profit"] == 14.92
        assert payload["sale_price"] == 20.0
        card = fulfillment_report_service.format_card(payload)
        assert "صافي الربح" in card
        assert "تقرير تنفيذ ناجح" in card
        assert pub.await_count >= 1


# ---------- API endpoints exist ----------
def test_orders_api_has_retry_and_refund_routes():
    from src.api.routes import orders as orders_routes
    paths = {getattr(r, "path", None) for r in orders_routes.router.routes}
    assert any(p and p.endswith("/retry-jit") for p in paths)
    assert any(p and p.endswith("/refund") for p in paths)


def test_admin_bot_routers_registered():
    from src.admin_bot.handlers import suppliers, products_pricing, crm_support, marketing
    assert suppliers.router is not None
    assert products_pricing.router is not None
    assert crm_support.router is not None
    assert marketing.router is not None
