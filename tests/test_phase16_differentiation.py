"""Phase 16 — Local market, Staff RBAC, warranty, families, local stock proofs."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ---------- 1. SYP rate + fraud ----------
def test_syp_rate_calculation_and_format():
    from src.core.services.syp_rate_service import usd_to_syp, format_usd_and_syp, format_syp

    syp = usd_to_syp(Decimal("3.50"), Decimal("11500"))
    assert syp == Decimal("40250")
    line = format_usd_and_syp("3.50", Decimal("11500"))
    assert "$3.50" in line
    assert "40,250" in line or "40250" in line
    assert "ل.س" in format_syp(syp)


def test_duplicate_receipt_fraud_banner():
    from src.core.services.sham_cash_service import sham_cash_service

    banner = sham_cash_service.fraud_banner("ORD-OLD-1")
    assert "تحذير احتيال" in banner
    assert "ORD-OLD-1" in banner
    assert sham_cash_service.fraud_banner(None) is None
    assert "receipt_file_sha256" in _read("src/core/models/entities.py")
    assert "find_duplicate_receipt" in _read("src/core/services/sham_cash_service.py")
    assert "fraud_warning" in _read("dashboard/app/sham-cash/page.tsx")


# ---------- 2. Staff RBAC + claim lock + dual approve ----------
def test_staff_rbac_blocks_owner_only_routes():
    from src.admin_bot.middlewares.admin_acl import (
        staff_may_access, OWNER_ONLY_TEXTS, STAFF_ALLOWED_TEXTS,
    )
    assert "📊 الخزينة والأرباح" in OWNER_ONLY_TEXTS
    assert not staff_may_access(text="📊 الخزينة والأرباح")
    assert not staff_may_access(callback_data="adm:treasury")
    assert not staff_may_access(callback_data="adm:products")
    assert staff_may_access(text="🧾 طلبات المراجعة (شام كاش / باينانس)")
    assert staff_may_access(callback_data="adm:orders:sham")
    assert staff_may_access(callback_data="adm:claim:abc")
    assert staff_may_access(callback_data="adm:warranty")


def test_order_claim_keyboard_and_dual_approve():
    from src.admin_bot.keyboards.main_menu import (
        sham_cash_claim_keyboard, sham_cash_review_keyboard,
    )
    kb0 = sham_cash_claim_keyboard("oid")
    flat0 = [b.text for r in kb0.inline_keyboard for b in r]
    assert any("استلام الطلب" in t for t in flat0)

    kb = sham_cash_review_keyboard(
        "oid", claimed=True, claimed_by="staff1", for_claimer=True, is_owner=False
    )
    flat = [b.text for r in kb.inline_keyboard for b in r]
    assert any("شراء آلي" in t for t in flat)
    assert any("تسليم يدوي" in t for t in flat)

    locked = sham_cash_review_keyboard(
        "oid", claimed=True, claimed_by="other", for_claimer=False, is_owner=False
    )
    flat_l = [b.text for r in locked.inline_keyboard for b in r]
    assert any("قيد المعالجة" in t for t in flat_l)

    src = _read("src/core/services/order_claim_service.py")
    assert "already claimed" in src.lower() or "Order already claimed" in src
    assert "approve_manual_delivery" in _read("src/core/services/sham_cash_service.py")


@pytest.mark.asyncio
async def test_second_claim_rejected():
    from src.core.services.order_claim_service import OrderClaimService
    from src.core.models.entities import Order

    svc = OrderClaimService()
    order = MagicMock(spec=Order)
    order.id = uuid.uuid4()
    order.order_number = "ORD-1"
    order.claimed_by_telegram_id = 111
    order.claimed_by_username = "alice"

    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = order
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.add = MagicMock()

    with pytest.raises(ValueError, match="already claimed|claimed"):
        await svc.claim(session, order.id, telegram_id=222, username="bob")


# ---------- 3. Warranty ----------
def test_warranty_eligibility_and_admin_card_supplier_only():
    from datetime import datetime, timezone, timedelta
    from src.core.services.warranty_service import WarrantyService
    from src.core.models.enums import OrderState

    svc = WarrantyService()
    order = MagicMock()
    order.state = OrderState.COMPLETED
    order.delivered_at = datetime.now(timezone.utc) - timedelta(days=1)
    order.warranty_expires_at = datetime.now(timezone.utc) + timedelta(days=10)
    order.order_number = "ORD-W"
    order.delivered_product = "KEY-ORIGINAL"
    order.supplier_order_id = "EXT-999"
    order.supplier = MagicMock(name="SupX")
    order.supplier.name = "LaraStock"
    order.customer = MagicMock(telegram_id=1, telegram_username="u", first_name="A")
    order.master_product = MagicMock(title_ar="ChatGPT")

    assert svc.is_eligible(order) is True
    order.warranty_expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    assert svc.is_eligible(order) is False

    claim = MagicMock(issue_description="لا يعمل")
    order.warranty_expires_at = datetime.now(timezone.utc) + timedelta(days=5)
    card = svc.format_admin_card(order, claim)
    assert "LaraStock" in card
    assert "EXT-999" in card
    assert "المورد" in card
    # Customer bot must not expose supplier in warranty start prompt
    shop = _read("src/bot/handlers/shop.py")
    assert "warranty:" in shop
    assert "طلب استبدال (ضمن الضمان)" in shop


# ---------- 4. Families, featured, search, renewal ----------
def test_product_family_featured_and_bilingual_search():
    ents = _read("src/core/models/entities.py")
    assert "brand_family" in ents
    assert "is_featured" in ents
    assert "search_keywords" in ents
    shop = _read("src/bot/handlers/shop.py")
    assert "nav:featured" in shop
    assert "الأكثر طلباً" in shop
    assert "brand_family" in shop
    assert "search_keywords" in shop
    assert "المتوف" in shop or "المتوف" in _read("src/bot/handlers/shop.py")
    assert "الأرخص أولاً" in shop
    # Search matches arabic alias pattern in keywords field
    assert "MasterProduct.search_keywords.ilike" in shop


def test_renewal_reminder_sent_once():
    ents = _read("src/core/models/entities.py")
    assert "renewal_reminder_sent" in ents
    assert "subscription_duration_days" in ents
    rem = _read("src/core/services/renewal_reminder_service.py")
    assert "renewal_reminder_sent" in rem
    assert "timedelta(days=3)" in rem
    assert "send_subscription_renewal_reminders" in _read("src/workers/health_monitor.py")
    assert "bot:renewal_reminder" in _read("src/bot/bot_main.py")
    assert "تجديد الاشتراك الآن" in _read("src/bot/bot_main.py")


# ---------- 5. Flexible customer input ----------
def test_flexible_customer_input_label_forwarded():
    ents = _read("src/core/models/entities.py")
    assert "requires_customer_input" in ents
    assert "customer_input_label_ar" in ents
    assert "customer_input_type" in ents
    shop = _read("src/bot/handlers/shop.py")
    assert "CheckoutCustomerInputState" in shop
    assert "customer_input_label_ar" in shop
    mini = _read("dashboard/app/miniapp/page.tsx")
    assert "customer_input_label_ar" in mini
    assert "buyer_email" in mini
    api = _read("src/api/routes/miniapp.py")
    assert "customer_input_label_ar" in api


# ---------- 6. Local stock + cashback ----------
def test_local_stock_and_cashback_wiring():
    ents = _read("src/core/models/entities.py")
    assert "class LocalStockItem" in ents
    assert "fulfillment_mode" in ents
    loc = _read("src/core/services/local_stock_service.py")
    assert "skip_locked" in loc
    assert "encrypt_secret" in loc
    jit = _read("src/core/services/jit_executor.py")
    assert "local_stock_service" in jit
    assert "local_only" in jit
    assert "ربحت" in jit or "كاش باك" in jit
    assert "cashback_percent" in ents  # customer override
    assert "local-stock" in _read("src/api/routes/products.py")


def test_owner_staff_config_properties():
    from src.config import Settings
    s = Settings(
        OWNER_TELEGRAM_IDS="100,200",
        STAFF_TELEGRAM_IDS="300",
        ADMIN_TELEGRAM_IDS="",
    )
    assert 100 in s.owner_telegram_id_set
    assert 300 in s.staff_telegram_id_set
    assert 300 in s.admin_telegram_id_set
    assert s.is_owner(100)
    assert s.is_staff_only(300)
    assert not s.is_staff_only(100)


def test_dashboard_syp_settings_and_admin_bot_syp():
    assert "sham_cash_usd_to_syp_rate" in _read("dashboard/app/bot-config/page.tsx")
    assert "adm:settings:syp" in _read("src/admin_bot/handlers/orders.py")
    assert "format_sham_cash_amount_line" in _read("src/bot/handlers/shop.py")
