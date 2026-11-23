import os
import sys
import uuid
import json
import hmac
import hashlib
import asyncio
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_sec_audit.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST_BOT_TOKEN_SEC_AUDIT"
os.environ["TELEGRAM_PUBLIC_CHANNEL_ID"] = "-1001234567890"
os.environ["HOT_WALLET_ADDRESS"] = "0xTestHotWalletBscAddress00000000000001"
os.environ["TRON_HOT_WALLET_ADDRESS"] = "TTestTronHotWalletAddress00000000001"

import fakeredis.aioredis
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func, and_
from sqlalchemy.orm import selectinload

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Order, MasterProduct, Category, Supplier, ProductSupplierLink,
    PaymentInvoice, FormalInvoice, CustomerReview, Coupon, TopUpBonusTier,
    AdminUser, HotWallet, SupplierCredit, SupportTicket, TicketMessage,
    WalletTransaction, BotConfig, PricingRule
)
from src.core.models.enums import (
    OrderState, InvoiceStatus, PaymentMethod, PaymentNetwork,
    WalletTxType, VipTier, DiscountType, TicketStatus, SupplierStatus,
    PricingScope, PricingRuleType
)
from src.core.services.order_service import order_service
from src.core.services.pricing_service import pricing_service
from src.api.routes.auth import create_access_token, get_password_hash
from src.api.main import app
from src.core.redis import event_bus
from src.config import settings
from src.core.security import encrypt_secret

# Global test identifiers
ADMIN_TOKEN = ""
USER_A_TG_ID = 888001
USER_B_TG_ID = 888002
USER_A_ID = None
USER_B_ID = None
INVOICE_A_ID = None
ORDER_A_ID = None
TICKET_A_ID = None
PRODUCT_ID = None


async def setup_db_and_fixtures():
    global ADMIN_TOKEN, USER_A_ID, USER_B_ID, INVOICE_A_ID, ORDER_A_ID, TICKET_A_ID, PRODUCT_ID

    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    event_bus.redis = fake_redis

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Admin
        admin = AdminUser(
            username="admin",
            password_hash=get_password_hash("admin123456"),
            display_name="Auditor Admin",
            role="super_admin",
            is_active=True
        )
        session.add(admin)

        # Bot configs
        configs = [
            ("force_subscribe_enabled", "true"),
            ("main_channel_id", "-1001234567890"),
            ("cashback_percent", "2.0"),
            ("referral_commission_percent", "5.0"),
        ]
        for k, v in configs:
            session.add(BotConfig(key=k, value=v))

        # Category & Products
        cat = Category(name_ar="أدوات", name_en="Tools", is_active=True, sort_order=1)
        session.add(cat)
        await session.flush()

        prod = MasterProduct(
            category_id=cat.id,
            internal_sku="SKU-SEC-AUDIT",
            title_ar="منتج الاختبار الأمني",
            title_en="Security Audit SKU",
            description_ar="اختبار أمني",
            warranty_days=30,
            is_active=True
        )
        session.add(prod)
        await session.flush()
        PRODUCT_ID = prod.id

        # Supplier
        sup = Supplier(
            name="Secure Upstream Supplier",
            driver_type="mock_audit_driver",
            payment_network=PaymentNetwork.USDT_BEP20,
            status=SupplierStatus.ACTIVE,
            config_encrypted=encrypt_secret('{"api_key": "test_audit_key"}'),
            deposit_address="0xSupplierPrivateDepositAddress",
            cached_balance=Decimal("500.00")
        )
        session.add(sup)
        await session.flush()

        link = ProductSupplierLink(
            master_product_id=prod.id,
            supplier_id=sup.id,
            supplier_product_id="sup_sec_1",
            supplier_product_name="Raw Supplier Good",
            last_known_cost=Decimal("10.00"),
            is_in_stock=True,
            available_quantity=50
        )
        session.add(link)

        # Global pricing rule: +$2.00 fixed margin
        rule = PricingRule(
            scope=PricingScope.GLOBAL,
            rule_type=PricingRuleType.FIXED,
            value=Decimal("2.00"),
            gas_buffer_enabled=True,
            gas_buffer_fixed=Decimal("0.08"),
            priority=0,
            is_active=True
        )
        session.add(rule)

        # Hot Wallet
        hw = HotWallet(
            address=settings.HOT_WALLET_ADDRESS,
            usdt_balance=Decimal("2000.00"),
            bnb_balance=Decimal("2.00")
        )
        session.add(hw)

        # Coupons
        coupon = Coupon(
            code="WELCOME10",
            discount_type=DiscountType.PERCENT,
            value=Decimal("10.0"),
            max_uses=100,
            used_count=0,
            is_active=True
        )
        session.add(coupon)

        # User A
        user_a = Customer(
            telegram_id=USER_A_TG_ID,
            telegram_username="user_a_target",
            first_name="Alice",
            wallet_balance=Decimal("50.00"),
            total_spent=Decimal("2500.00"), # WHOLESALE Tier
            vip_tier=VipTier.WHOLESALE,
            api_enabled=True,
            api_key_hash="hash_of_alice_private_api_key_secret",
            api_key_prefix="sk_live_alice..."
        )
        session.add(user_a)
        await session.flush()
        USER_A_ID = user_a.id

        # User B (Attacker)
        user_b = Customer(
            telegram_id=USER_B_TG_ID,
            telegram_username="user_b_attacker",
            first_name="Bob",
            wallet_balance=Decimal("20.00"),
            total_spent=Decimal("0.00"),
            vip_tier=VipTier.STANDARD
        )
        session.add(user_b)
        await session.flush()
        USER_B_ID = user_b.id

        # User A's Order & Invoice
        order_a = Order(
            order_number="ORD-ALICE-SEC-001",
            customer_id=user_a.id,
            master_product_id=prod.id,
            supplier_id=sup.id,
            supplier_product_id="sup_sec_1",
            supplier_cost=Decimal("10.00"),
            our_price=Decimal("12.08"),
            profit_margin=Decimal("2.00"),
            idempotency_key="alice_idemp_key_001",
            state=OrderState.AWAITING_PAYMENT,
            delivered_product="ALICE_SECRET_LICENSE_KEY_12345"
        )
        session.add(order_a)
        await session.flush()
        ORDER_A_ID = order_a.id

        inv_a = PaymentInvoice(
            customer_id=user_a.id,
            order_id=order_a.id,
            payment_method=PaymentMethod.USDT_BEP20_DIRECT,
            amount_expected=Decimal("12.08"),
            unique_amount=Decimal("12.080001"),
            deposit_address=settings.HOT_WALLET_ADDRESS,
            status=InvoiceStatus.PENDING,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30)
        )
        session.add(inv_a)
        await session.flush()
        INVOICE_A_ID = inv_a.id
        order_a.invoice_id = inv_a.id

        # User A's Ticket
        ticket_a = SupportTicket(
            customer_id=user_a.id,
            order_id=order_a.id,
            subject="Alice Private Dispute",
            status=TicketStatus.OPEN,
            priority=1
        )
        session.add(ticket_a)
        await session.flush()
        TICKET_A_ID = ticket_a.id

        msg_a = TicketMessage(
            ticket_id=ticket_a.id,
            sender_type="customer",
            sender_name="Alice",
            message_text="Alice confidential private support details"
        )
        session.add(msg_a)

        await session.commit()

    ADMIN_TOKEN = create_access_token({"sub": "admin"})


# =====================================================================
# SUITE 4: SECURITY, ADMIN AUTH, IDOR & DATA LEAK AUDIT
# =====================================================================

ADMIN_PREFIXES = tuple("/api" + p for p in (
    "/treasury", "/customers", "/sham-cash", "/support", "/marketing",
    "/suppliers", "/products", "/pricing", "/bot-config", "/orders", "/dashboard",
))


def _discover_admin_routes():
    """Introspect the live FastAPI app so EVERY admin route is tested, including future ones."""
    from fastapi.routing import APIRoute

    routes = []

    def collect(router, prefix=""):
        for r in getattr(router, "routes", []):
            if isinstance(r, APIRoute):
                routes.append((sorted(r.methods)[0], prefix + r.path))

    for entry in app.routes:
        if type(entry).__name__ == "_IncludedRouter":
            collect(entry.original_router, getattr(entry.include_context, "prefix", "") or "")
        else:
            collect(entry)
    return [(m, p) for m, p in routes if p.startswith(ADMIN_PREFIXES)]


async def test_4_1_admin_route_lockdown():
    """Verify EVERY admin route returns 401 Unauthorized when called without JWT token."""
    print("\n--- [Suite 4.1: Admin Route Lockdown Audit] ---")
    transport = ASGITransport(app=app)

    admin_endpoints = _discover_admin_routes()
    # Make sure the required route families are all present in the discovered set.
    for prefix in ADMIN_PREFIXES:
        assert any(p.startswith(prefix) for _, p in admin_endpoints), (
            f"Admin route family {prefix} was not discovered from the app!"
        )

    placeholder = "00000000-0000-0000-0000-000000000000"
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        for method, endpoint in admin_endpoints:
            path = endpoint
            for param in ("order_id", "ticket_id", "customer_id", "supplier_id", "product_id",
                          "rule_id", "coupon_id", "tier_id", "campaign_id", "link_id"):
                path = path.replace("{" + param + "}", placeholder)
            assert "{" not in path, f"Unresolved path parameter in {path}"

            kwargs = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
            resp = await client.request(method, path, **kwargs)

            assert resp.status_code == 401, (
                f"SECURITY VULNERABILITY: Endpoint {method} {path} returned {resp.status_code} "
                f"instead of 401 Unauthorized without admin token!"
            )

    print(f"✓ All {len(admin_endpoints)} discovered Admin Endpoints strictly return 401 Unauthorized without Admin JWT.")


async def test_4_2_idor_cross_customer_protection():
    """User B (Attacker) attempts to view/cancel User A's invoice, orders, and tickets."""
    print("\n--- [Suite 4.2: IDOR Protection & Authorization Isolation] ---")
    transport = ASGITransport(app=app)
    attacker_headers = {"X-Dev-Telegram-Id": str(USER_B_TG_ID)}

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Attacker attempts to view Alice's Payment Invoice status
        res1 = await client.get(f"/api/miniapp/payments/{INVOICE_A_ID}/status", headers=attacker_headers)
        assert res1.status_code in (403, 404), f"IDOR Vulnerability: Attacker viewed Alice invoice: {res1.text}"

        # 2. Attacker attempts to cancel Alice's Payment Invoice
        res2 = await client.post(f"/api/miniapp/payments/{INVOICE_A_ID}/cancel", headers=attacker_headers)
        assert res2.status_code in (403, 404), f"IDOR Vulnerability: Attacker cancelled Alice invoice: {res2.text}"

        # 3. Attacker attempts to view Alice's Order details
        res3 = await client.get(f"/api/miniapp/orders/{ORDER_A_ID}", headers=attacker_headers)
        assert res3.status_code in (403, 404), f"IDOR Vulnerability: Attacker viewed Alice order: {res3.text}"

        # 4. Attacker attempts to view Alice's Support Ticket details
        res4 = await client.get(f"/api/miniapp/tickets/{TICKET_A_ID}", headers=attacker_headers)
        assert res4.status_code in (403, 404), f"IDOR Vulnerability: Attacker viewed Alice ticket: {res4.text}"

        # 5. Attacker attempts to open a ticket claiming Alice's Order ID
        res5 = await client.post("/api/miniapp/tickets", json={
            "order_id": str(ORDER_A_ID),
            "subject": "Attacker Hijack Attempt",
            "message": "Give me Alice's keys"
        }, headers=attacker_headers)
        assert res5.status_code in (403, 404), f"IDOR Vulnerability: Attacker created ticket for Alice's order: {res5.text}"

    print("✓ IDOR Protection verified 100%: Cross-tenant access strictly blocked (403/404) across invoices, orders, and tickets.")


async def test_4_3_tampered_auth_and_prod_environment_bypass():
    """Verify forged initData signatures and X-Dev-Telegram-Id in production are rejected."""
    print("\n--- [Suite 4.3: Tampered Auth & Production Bypass Audit] ---")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Tampered initData (invalid hash)
        forged_init_data = (
            "query_id=AAHdF6IQAAAAAN0XohD34T3l&"
            "user=%7B%22id%22%3A999999%2C%22first_name%22%3A%22Hacker%22%7D&"
            "auth_date=1620000000&"
            "hash=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        )
        res_tampered = await client.get(
            "/api/miniapp/profile",
            headers={"X-Telegram-Init-Data": forged_init_data}
        )
        assert res_tampered.status_code == 401, f"Expected 401 on forged signature, got {res_tampered.status_code}: {res_tampered.text}"

        # 2. Production mode check: X-Dev-Telegram-Id must be rejected when ENVIRONMENT=production
        old_env = settings.ENVIRONMENT
        try:
            settings.ENVIRONMENT = "production"
            res_prod = await client.get(
                "/api/miniapp/profile",
                headers={"X-Dev-Telegram-Id": str(USER_A_TG_ID)}
            )
            assert res_prod.status_code == 401, f"Expected 401 in production for dev header, got {res_prod.status_code}: {res_prod.text}"
        finally:
            settings.ENVIRONMENT = old_env

    print("✓ Auth Security verified: Forged HMAC signatures and dev header in production strictly rejected with 401.")


async def test_4_4_wholesale_and_secret_leak_scan():
    """Verify client responses never leak supplier IDs, costs, api_key_hash, or private secrets."""
    print("\n--- [Suite 4.4: Wholesale & Secret Leak Scan] ---")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Scan Mini App Catalog
        res_cat = await client.get("/api/miniapp/catalog")
        assert res_cat.status_code == 200
        cat_str = json.dumps(res_cat.json()).lower()
        forbidden_terms = [
            "wholesale_cost", "last_known_cost", "encrypted_secret", "secure upstream supplier",
            "0xsupplierprivatedepositaddress", "config_encrypted"
        ]
        for term in forbidden_terms:
            assert term not in cat_str, f"DATA LEAK DETECTED: Term '{term}' leaked in /api/miniapp/catalog!"

        # Scan Mini App Profile
        res_prof = await client.get("/api/miniapp/profile", headers={"X-Dev-Telegram-Id": str(USER_A_TG_ID)})
        assert res_prof.status_code == 200
        prof_str = json.dumps(res_prof.json()).lower()
        assert "hash_of_alice_private_api_key_secret" not in prof_str, "CRITICAL: api_key_hash leaked in profile!"
        assert "private_key" not in prof_str

        # Scan Reseller Products
        # Alice is WHOLESALE tier, generate her a reseller key
        alice_raw_key = "sk_live_alice_test_secret_key_12345"
        alice_hash = hashlib.sha256(alice_raw_key.encode("utf-8")).hexdigest()
        async with AsyncSessionLocal() as session:
            cust_stmt = select(Customer).where(Customer.id == USER_A_ID)
            alice = (await session.execute(cust_stmt)).scalar_one()
            alice.api_key_hash = alice_hash
            alice.api_enabled = True
            await session.commit()

        res_reseller = await client.get("/api/v1/reseller/products", headers={"Authorization": f"Bearer {alice_raw_key}"})
        assert res_reseller.status_code == 200
        reseller_str = json.dumps(res_reseller.json()).lower()
        for term in ["supplier_id", "supplier_cost", "secure upstream supplier"]:
            assert term not in reseller_str, f"DATA LEAK DETECTED: Term '{term}' leaked in reseller products!"

    print("✓ Zero Data Leaks: Supplier identities, wholesale costs, and private secrets are 100% hidden from client APIs.")


# =====================================================================
# SUITE 4.5: DISCOUNT-STACKING PROFIT PROTECTION TEST
# =====================================================================

async def test_4_5_discount_stacking_profit_protection():
    """Stack WHOLESALE VIP (8%) + Promo WELCOME10 (10%) + Cashback (2%) + Referral (5%) + Gas ($0.08)."""
    print("\n--- [Suite 4.5: Discount-Stacking Profit Protection Test] ---")
    transport = ASGITransport(app=app)

    # Alice has WHOLESALE VIP (8%); gross unit = supplier $10.00 + fixed markup $2.00
    # + gas buffer $0.08 = $12.08. WELCOME10 adds 10%. Cashback 2% + referral 5%
    # are credited by the JIT executor on the GROSS value (order.our_price).
    payload = {
        "items": [{"product_id": str(PRODUCT_ID), "quantity": 1}],
        "pay_via_wallet": True,
        "coupon_code": "WELCOME10"
    }

    gross_total = Decimal("12.08")
    supplier_cost = Decimal("10.00")
    incentive_rate = Decimal("0.07")  # 2% cashback + 5% referral
    min_payable = pricing_service.profit_guard_min_payable(gross_total, supplier_cost, incentive_rate)

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(
            "/api/miniapp/cart/checkout",
            json=payload,
            headers={"X-Dev-Telegram-Id": str(USER_A_TG_ID)}
        )
        assert res.status_code == 200, f"Checkout failed: {res.text}"
        body = res.json()
        assert body["status"] == "paid"

        paid_amount = Decimal(str(body["total_paid"]))
        discount_applied = Decimal(str(body["discount_applied"]))

        # Exact guard arithmetic: the circuit breaker must clamp the full stack to
        # gross - min_payable; it must not leave a single cent below the floor.
        assert paid_amount == min_payable, (
            f"Profit guard did not clamp correctly: paid ${paid_amount}, expected ${min_payable}"
        )
        assert discount_applied == gross_total - min_payable, (
            f"Expected discount ${gross_total - min_payable}, got ${discount_applied}"
        )

        # Mirror the JIT executor's payouts: cashback/referral on the GROSS order value.
        cashback = (gross_total * Decimal("0.02")).quantize(Decimal("0.01"))
        referral = (gross_total * Decimal("0.05")).quantize(Decimal("0.01"))
        incentives = cashback + referral
        net_margin = paid_amount - supplier_cost - incentives

        print(f"• Gross Order Value:    ${gross_total:.2f}")
        print(f"• VIP 8% + Coupon 10%:  ${(gross_total * Decimal('0.18')).quantize(Decimal('0.01')):.2f} requested -> ${discount_applied:.2f} allowed")
        print(f"• Final Paid Amount:    ${paid_amount:.2f} (floor ${min_payable:.2f})")
        print(f"• Supplier Cost:        ${supplier_cost:.2f}")
        print(f"• Cashback 2% + Ref 5%: ${incentives:.2f} (on gross)")
        print(f"• Net Margin:           +${net_margin:.2f}")

        assert net_margin >= pricing_service.DEFAULT_PROFIT_SAFETY_FLOOR, (
            f"PROFIT GUARD BREACH: Net margin dropped below safe floor! Margin: ${net_margin}"
        )
        assert paid_amount > supplier_cost, "Paid amount must never be below supplier wholesale cost!"

    # Pure-function matrix: the shared guard must hold for every stacked scenario,
    # including low-margin products where a naive guard would go negative.
    for cost, markup, discount_pct in [
        (Decimal("10.00"), Decimal("2.00"), Decimal("18")),   # this order
        (Decimal("10.00"), Decimal("0.50"), Decimal("8")),    # thin margin reseller case
        (Decimal("100.00"), Decimal("1.00"), Decimal("10")),  # near-wholesale VIP
        (Decimal("2.00"), Decimal("3.00"), Decimal("18")),    # cheap digital good
    ]:
        gross = cost + markup
        requested = (gross * (discount_pct / Decimal("100"))).quantize(Decimal("0.01"))
        allowed = pricing_service.clamp_total_discount(gross, requested, cost, incentive_rate)
        paid = pricing_service.enforce_profit_floor(gross, gross - allowed, cost, incentive_rate)
        net = paid - cost - (gross * incentive_rate).quantize(Decimal("0.01"))
        assert net >= Decimal("0.00"), (
            f"PROFIT GUARD BREACH in matrix cost=${cost} markup=${markup} disc={discount_pct}%: net ${net}"
        )

    # Allow any background JIT executor tasks from checkout to finish
    await asyncio.sleep(0.8)

    print("✓ Profit Guard Circuit Breaker verified: gross-based incentive reserve keeps net margin >= floor across every stacked scenario.")


# =====================================================================
# SUITE 5: PERFORMANCE, N+1 QUERY AUDIT & COMPOSITE DB INDEXES
# =====================================================================

async def test_5_1_composite_db_indexes_verification():
    """Verify composite DB indexes exist on orders, payment_invoices, and wallet_transactions."""
    print("\n--- [Suite 5.1: Critical Composite DB Indexes Audit] ---")

    order_indexes = {idx.name: [c.name for c in idx.columns] for idx in Order.__table__.indexes}
    invoice_indexes = {idx.name: [c.name for c in idx.columns] for idx in PaymentInvoice.__table__.indexes}
    wallet_indexes = {idx.name: [c.name for c in idx.columns] for idx in WalletTransaction.__table__.indexes}

    # 1. orders(customer_id, state)
    assert "idx_orders_customer_state" in order_indexes, "Missing index idx_orders_customer_state on orders!"
    assert order_indexes["idx_orders_customer_state"] == ["customer_id", "state"]

    # 2. payment_invoices(status, expires_at)
    assert "idx_payment_invoices_status_expires" in invoice_indexes, "Missing index idx_payment_invoices_status_expires on payment_invoices!"
    assert invoice_indexes["idx_payment_invoices_status_expires"] == ["status", "expires_at"]

    # 3. wallet_transactions(customer_id, created_at)
    assert "idx_wallet_ledger_customer_created" in wallet_indexes, "Missing index idx_wallet_ledger_customer_created on wallet_transactions!"
    assert wallet_indexes["idx_wallet_ledger_customer_created"] == ["customer_id", "created_at"]

    print("✓ All 3 Critical Composite DB Indexes verified in metadata schema:")
    print("  • orders(customer_id, state)")
    print("  • payment_invoices(status, expires_at)")
    print("  • wallet_transactions(customer_id, created_at)")


async def test_5_2_n_plus_one_sql_query_audit():
    """Verify /api/miniapp/catalog executes in O(1) query count (no N+1 per product)."""
    print("\n--- [Suite 5.2: Performance & N+1 Query Audit] ---")

    # Add 10 additional products to test database
    async with AsyncSessionLocal() as session:
        cat_stmt = select(Category).limit(1)
        cat = (await session.execute(cat_stmt)).scalar_one()

        sup_stmt = select(Supplier).limit(1)
        sup = (await session.execute(sup_stmt)).scalar_one()

        for i in range(10):
            p = MasterProduct(
                category_id=cat.id,
                internal_sku=f"SKU-BULK-{i+1}",
                title_ar=f"منتج مجمع {i+1}",
                title_en=f"Bulk Product {i+1}",
                is_active=True
            )
            session.add(p)
            await session.flush()

            l = ProductSupplierLink(
                master_product_id=p.id,
                supplier_id=sup.id,
                supplier_product_id=f"sup_bulk_{i+1}",
                last_known_cost=Decimal("5.00"),
                is_in_stock=True
            )
            session.add(l)
        await session.commit()

    await asyncio.sleep(0.3)

    # Track queries executed during /api/miniapp/catalog
    query_count = 0
    from sqlalchemy import event

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/api/miniapp/catalog")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_products"] >= 11, f"Expected at least 11 products, got {data['total_products']}"

        print(f"• Total Products in Catalog: {data['total_products']}")
        print(f"• SQL Queries Executed:     {query_count}")

        # In N+1 architecture, 11 products would trigger 11 * 2 + 2 = 24+ queries.
        # With eager loading and single batch rules query, it must be <= 5 queries!
        assert query_count <= 5, f"N+1 QUERY LEAK DETECTED: {query_count} queries executed for 11 products (expected <= 5)!"
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", before_cursor_execute)

    print("✓ Performance Audit Passed: /api/miniapp/catalog executes in O(1) batch queries (0 N+1 loops).")


def _count_selects_during(coro_factory):
    """Runs a coroutine factory while counting only SELECT statements."""
    from sqlalchemy import event

    state = {"selects": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            state["selects"] += 1

    async def run():
        event.listen(engine.sync_engine, "before_cursor_execute", before_cursor_execute)
        try:
            return await coro_factory()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", before_cursor_execute)

    return run(), state


async def test_5_2b_miniapp_orders_n_plus_one_audit():
    """Verify /api/miniapp/orders batch-loads formal invoice numbers (no per-order query)."""
    print("\n--- [Suite 5.2b: /api/miniapp/orders N+1 Audit] ---")

    async with AsyncSessionLocal() as session:
        prod = (await session.execute(select(MasterProduct).where(MasterProduct.id == PRODUCT_ID))).scalar_one()
        for i in range(10):
            o = Order(
                order_number=f"ORD-PERF-{i+1:03d}",
                customer_id=USER_A_ID,
                master_product_id=prod.id,
                supplier_cost=Decimal("1.00"),
                our_price=Decimal("2.08"),
                profit_margin=Decimal("1.08"),
                idempotency_key=f"perf_orders_{i+1}",
                state=OrderState.COMPLETED,
            )
            session.add(o)
            await session.flush()
            if i < 3:
                session.add(FormalInvoice(
                    invoice_number=f"INV-PERF-{i+1:05d}",
                    customer_id=USER_A_ID,
                    order_id=o.id,
                    item_title="Perf SKU",
                    quantity=1,
                    unit_price=Decimal("2.08"),
                    net_total=Decimal("2.08"),
                    payment_method=PaymentMethod.INTERNAL_BALANCE,
                ))
        await session.commit()

    transport = ASGITransport(app=app)

    async def call():
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            r = await client.get("/api/miniapp/orders", headers={"X-Dev-Telegram-Id": str(USER_A_TG_ID)})
            assert r.status_code == 200, r.text
            return r.json()

    coro, state = _count_selects_during(call)
    data = await coro
    orders_returned = len(data["orders"])
    print(f"• Orders Returned:          {orders_returned}")
    print(f"• SELECT Statements:        {state['selects']}")
    # auth(1) + orders(1) + eager products(1) + batched formal invoices(1) = 4.
    assert orders_returned >= 12, f"Expected >= 12 orders, got {orders_returned}"
    assert state["selects"] <= 5, (
        f"N+1 QUERY LEAK: /api/miniapp/orders issued {state['selects']} SELECTs for "
        f"{orders_returned} orders (expected <= 5)!"
    )
    print("✓ /api/miniapp/orders verified O(1): formal invoices batch-loaded, zero per-order queries.")


async def test_5_2c_channel_sync_n_plus_one_audit():
    """Verify channel_sync eager-loads supplier links instead of querying per supplier."""
    print("\n--- [Suite 5.2c: channel_sync.py N+1 Audit] ---")

    from src.workers.channel_sync import ChannelSyncWorker

    async with AsyncSessionLocal() as session:
        for i in range(3):
            session.add(Supplier(
                name=f"Perf Supplier {i+1}",
                driver_type="mock_audit_driver",
                payment_network=PaymentNetwork.USDT_BEP20,
                status=SupplierStatus.ACTIVE,
                config_encrypted=encrypt_secret('{"api_key": "perf"}'),
            ))
        await session.commit()

    class _EmptyAdapter:
        async def fetch_products(self):
            return []

    coro, state = _count_selects_during(lambda: ChannelSyncWorker().sync_upstream_supplier_stocks())
    with patch("src.workers.channel_sync.AdapterRegistry.create_from_supplier",
               return_value=_EmptyAdapter()):
        await coro

    print(f"• Active Suppliers Synced:  4")
    print(f"• SELECT Statements:        {state['selects']}")
    # Without selectinload this would be 1 (suppliers) + 4 (links in loop) = 5.
    assert state["selects"] <= 3, (
        f"N+1 QUERY LEAK: channel_sync issued {state['selects']} SELECTs for 4 suppliers "
        f"(expected <= 3)!"
    )
    print("✓ channel_sync.py verified O(1): supplier links eager-loaded in a single batch query.")


async def test_5_3b_wallet_lock_fail_closed():
    """Verify a contended wallet lock fails closed on BOTH debit and credit (no unlocked fallback)."""
    print("\n--- [Suite 5.3b: Wallet Lock Fail-Closed Audit] ---")

    async with AsyncSessionLocal() as session:
        target = Customer(
            telegram_id=777100,
            telegram_username="lock_contention_target",
            wallet_balance=Decimal("10.00"),
        )
        session.add(target)
        await session.commit()
        await session.refresh(target)
        target_id = target.id

    lock_key = f"lock:wallet:{str(target_id)}"
    token = await event_bus.acquire_lock_with_token(lock_key, ttl_seconds=30)
    assert token, "Could not acquire the test wallet lock"

    try:
        for op, label in (
            (order_service.debit_customer_wallet, "debit"),
            (order_service.credit_customer_wallet, "credit"),
        ):
            async with AsyncSessionLocal() as session:
                try:
                    await op(
                        session=session,
                        customer_id=target_id,
                        amount=Decimal("1.00"),
                        tx_type=WalletTxType.PURCHASE_DEBIT,
                        description=f"contended {label}",
                    )
                    raise AssertionError(f"{label} proceeded while the wallet lock was held!")
                except ValueError as err:
                    assert "locked" in str(err).lower(), f"Unexpected error for {label}: {err}"
    finally:
        await event_bus.release_lock(lock_key, token=token)

    # After release the wallet must be usable again, with no phantom ledger rows.
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Customer).where(Customer.id == target_id))).scalar_one()
        tx_count = (await session.execute(
            select(func.count(WalletTransaction.id)).where(WalletTransaction.customer_id == target_id)
        )).scalar_one()
        assert row.wallet_balance == Decimal("10.00"), f"Balance drifted: {row.wallet_balance}"
        assert tx_count == 0, f"Contended operations wrote {tx_count} ledger rows!"
    print("✓ Wallet lock fails closed on debit AND credit, is token-safely released, and leaves the ledger untouched.")


async def test_5_3c_atomic_debit_survives_uncommitted_overlap():
    """
    Regression for the cross-channel race: the Redis lock is released when the service
    returns, but the caller commits later. A second request must still see the first
    debit. This test forces that overlap with a deliberate pre-commit delay.
    """
    print("\n--- [Suite 5.3c: Atomic Debit vs. Pre-Commit Overlap] ---")

    async with AsyncSessionLocal() as session:
        target = Customer(
            telegram_id=777101,
            telegram_username="precommit_overlap_target",
            wallet_balance=Decimal("20.00"),
        )
        session.add(target)
        await session.commit()
        await session.refresh(target)
        target_id = target.id

    results = []

    async def slow_commit_debit(idx):
        async with AsyncSessionLocal() as s:
            try:
                await order_service.debit_customer_wallet(
                    session=s,
                    customer_id=target_id,
                    amount=Decimal("10.00"),
                    tx_type=WalletTxType.PURCHASE_DEBIT,
                    description=f"overlapping channel debit #{idx}",
                )
                # The service has released the Redis lock; simulate order creation
                # happening in the caller's still-open transaction.
                await asyncio.sleep(0.2)
                await s.commit()
                results.append("ok")
            except ValueError:
                await s.rollback()
                results.append("rejected")

    await asyncio.gather(*(slow_commit_debit(i) for i in range(6)))

    assert results.count("ok") == 2, (
        f"Double-spend under pre-commit overlap: {results.count('ok')} debits succeeded, expected 2"
    )
    assert results.count("rejected") == 4

    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Customer).where(Customer.id == target_id))).scalar_one()
        tx_rows = (await session.execute(
            select(WalletTransaction.balance_after)
            .where(WalletTransaction.customer_id == target_id)
            .order_by(WalletTransaction.created_at.asc())
        )).scalars().all()
        assert row.wallet_balance == Decimal("0.00"), f"Balance should be $0.00, got {row.wallet_balance}"
        assert sorted(tx_rows) == [Decimal("0.0000"), Decimal("10.0000")], (
            f"Ledger balance_after values are inconsistent: {tx_rows}"
        )
    print("✓ Atomic guarded debit survives lock-release/commit overlap: no stale-snapshot double spend.")


async def test_5_3_cross_channel_central_wallet_concurrency():
    """Verify central order_service.debit_customer_wallet serializes concurrent cross-channel calls."""
    print("\n--- [Suite 5.3: Central Cross-Channel Wallet Serialization] ---")

    async with AsyncSessionLocal() as session:
        target = Customer(
            telegram_id=777099,
            telegram_username="cross_channel_target",
            wallet_balance=Decimal("25.00")
        )
        session.add(target)
        await session.commit()
        await session.refresh(target)
        target_id = target.id

    # Simulate 5 concurrent requests from different channels (Bot, Mini App, Reseller API)
    # Attempting to debit $10 each on a $25 balance.
    # Invariants: Exactly 2 must succeed, 3 must fail with insufficient funds, balance never negative ($5 remaining).
    success_count = 0
    failure_count = 0

    async def attempt_debit(channel_name: str, idx: int):
        nonlocal success_count, failure_count
        async with AsyncSessionLocal() as s:
            try:
                await order_service.debit_customer_wallet(
                    session=s,
                    customer_id=target_id,
                    amount=Decimal("10.00"),
                    tx_type=WalletTxType.PURCHASE_DEBIT,
                    description=f"Concurrent debit from {channel_name} #{idx}"
                )
                await s.commit()
                success_count += 1
            except ValueError:
                await s.rollback()
                failure_count += 1

    tasks = [
        attempt_debit("MiniApp", 1),
        attempt_debit("TelegramBot", 2),
        attempt_debit("ResellerAPI", 3),
        attempt_debit("MiniApp", 4),
        attempt_debit("TelegramBot", 5),
    ]
    await asyncio.gather(*tasks)

    print(f"Results of 5 Cross-Channel Concurrent Debits: {success_count} SUCCEEDED, {failure_count} REJECTED")

    async with AsyncSessionLocal() as session:
        t_row = (await session.execute(select(Customer).where(Customer.id == target_id))).scalar_one()
        assert success_count == 2, f"Expected 2 successful debits (2 * $10 = $20 from $25), got {success_count}"
        assert failure_count == 3, f"Expected 3 rejected debits, got {failure_count}"
        assert t_row.wallet_balance == Decimal("5.00"), f"Expected balance $5.00, got {t_row.wallet_balance}"

    print("✓ Central Cross-Channel Wallet Lock verified: All entry points serialized with zero double-spend.")


# =====================================================================
# MASTER RUNNER
# =====================================================================

async def main():
    print("=" * 70)
    print("  PHASE 11B: SECURITY AUDIT, CENTRAL WALLET LOCK & PERFORMANCE SUITE")
    print("=" * 70)

    start_time = datetime.now()
    await setup_db_and_fixtures()

    # Suite 4
    await test_4_1_admin_route_lockdown()
    await test_4_2_idor_cross_customer_protection()
    await test_4_3_tampered_auth_and_prod_environment_bypass()
    await test_4_4_wholesale_and_secret_leak_scan()
    await test_4_5_discount_stacking_profit_protection()

    # Suite 5
    await test_5_1_composite_db_indexes_verification()
    await test_5_2_n_plus_one_sql_query_audit()
    await test_5_2b_miniapp_orders_n_plus_one_audit()
    await test_5_2c_channel_sync_n_plus_one_audit()
    await test_5_3b_wallet_lock_fail_closed()
    await test_5_3c_atomic_debit_survives_uncommitted_overlap()
    await test_5_3_cross_channel_central_wallet_concurrency()

    elapsed = (datetime.now() - start_time).total_seconds()
    print("\n" + "=" * 70)
    print(f"  ALL SECURITY, IDOR, PROFIT GUARD & PERFORMANCE TESTS PASSED ({elapsed:.2f}s)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
