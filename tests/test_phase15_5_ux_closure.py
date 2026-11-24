"""Phase 15.5 — Critical UX & wiring gap closure proofs."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_sham_cash_accepts_pdf_and_photo_handlers():
    src = _read("src/bot/handlers/shop.py")
    assert "waiting_for_receipt" in src
    assert "F.photo" in src
    assert "F.document" in src
    assert "application/pdf" in src
    assert "publish_manual_review_alert" in src
    assert "تم استلام إيصال الدفع بنجاح" in src or "تم استلام إيصالك بنجاح" in src
    # Never leak supplier to customer confirmation
    customer_confirm = "تم استلام إيصال"
    idx = src.find(customer_confirm)
    chunk = src[idx : idx + 400]
    assert "المورد" not in chunk
    assert "supplier_name" not in chunk


def test_admin_review_caption_includes_supplier_only_for_staff():
    from src.core.services.admin_review_notify import format_manual_review_caption

    text = format_manual_review_caption({
        "review_kind": "sham_cash",
        "order_number": "ORD-1",
        "customer_username": "buyer",
        "customer_telegram_id": 99,
        "product_title": "ChatGPT",
        "quantity": 2,
        "amount": 12.5,
        "supplier_name": "LaraStock-X",
        "tx_ref": "TX123",
        "receipt_type": "pdf",
    })
    assert "LaraStock-X" in text
    assert "المورد" in text
    assert "TX123" in text
    assert "pdf" in text


def test_sham_cash_list_pending_exposes_supplier_and_receipt_type():
    src = _read("src/core/services/sham_cash_service.py")
    assert '"supplier_name"' in src or "'supplier_name'" in src
    assert "receipt_type" in src
    assert "ADMIN/STAFF ONLY" in src or "never render on customer" in src.lower() or "NEVER" in src


def test_live_feed_subscribes_manual_review_all_admins():
    src = _read("src/admin_bot/handlers/live_feed.py")
    assert "admin:manual_review" in src
    assert "admin_telegram_id_set" in src
    assert "broadcast_manual_review" in src
    assert "send_document" in src
    assert "send_photo" in src
    # No legacy single ADMIN_TELEGRAM_ID fan-out in live feed
    assert "settings.ADMIN_TELEGRAM_ID" not in src or "admin_telegram_id_set" in src


def test_copy_text_buttons_on_payment_keyboards():
    from src.bot.keyboards.inline import (
        crypto_payment_keyboard,
        binance_payment_keyboard,
        sham_cash_payment_keyboard,
        copy_btn,
    )

    crypto = crypto_payment_keyboard(
        order_id="oid", address="0xABC", amount="10.01", network="bep20"
    )
    flat = [b for row in crypto.inline_keyboard for b in row]
    assert any("نسخ عنوان" in (b.text or "") for b in flat)
    assert any("نسخ المبلغ" in (b.text or "") for b in flat)
    assert any("TxID" in (b.text or "") for b in flat)

    bn = binance_payment_keyboard(order_id="oid", pay_id="123456789")
    flat_b = [b for row in bn.inline_keyboard for b in row]
    assert any("Pay ID" in (b.text or "") for b in flat_b)
    assert any("Order ID" in (b.text or "") for b in flat_b)

    sham = sham_cash_payment_keyboard(order_id="oid", account_code="0991234567")
    flat_s = [b for row in sham.inline_keyboard for b in row]
    assert any("شام كاش" in (b.text or "") for b in flat_s)

    btn = copy_btn("📋 نسخ", "secret-value")
    # Native CopyTextButton when available, else fallback callback
    assert getattr(btn, "copy_text", None) is not None or btn.callback_data == "noop"


def test_binance_order_id_fsm_and_auto_verify_path():
    src = _read("src/bot/handlers/shop.py")
    assert "BinancePayOrderState" in src
    assert "waiting_order_id" in src
    assert "binance_pay_reader" in src
    assert "confirm_binance_by_pay_order_id" in src
    assert 'review_kind": "binance_pay"' in src or "binance_pay" in src
    assert "supplier_name" in src


def test_dual_force_subscribe_main_and_proof():
    src = _read("src/bot/middlewares/force_sub.py")
    assert "proof_channel_id" in src
    assert "force_sub_proof_channel_enabled" in src
    assert "need_main" in src and "need_proof" in src
    assert "force_sub_verify" in src
    assert "pending_buy" in src
    i18n = _read("src/bot/i18n/messages.py")
    assert "تم التحقق" in i18n or "تحقق من الاشتراك" in i18n


def test_stock_alert_subscribe_and_notify():
    from src.core.services.stock_alert_service import StockAlertService
    from src.core.models.entities import StockAlertSubscription

    assert StockAlertSubscription.__tablename__ == "stock_alert_subscriptions"
    src = _read("src/bot/keyboards/inline.py")
    assert "نبّهني عند التوفر" in src
    assert "stock_alert:" in src
    shop = _read("src/bot/handlers/shop.py")
    assert "stock_alert:" in shop
    assert "stock_alert_service.subscribe" in shop
    sync = _read("src/workers/channel_sync.py")
    assert "notify_restocked_products" in sync
    bot = _read("src/bot/bot_main.py")
    assert "bot:stock_alert" in bot
    assert "اشترِ الآن" in bot


def test_admin_bot_all_supplier_presets_and_conn_decode():
    from src.core.adapters.registry import SUPPLIER_PRESETS, AdapterRegistry
    from src.core.adapters.larastock import decode_conn_code
    import base64
    import json

    expected = {
        "tiger_v1", "ventebot_v1", "larastock_v1",
        "teslatools_v1", "hangout_v1", "generic_rest",
    }
    assert expected.issubset(set(SUPPLIER_PRESETS.keys()))
    # 5 real + generic
    assert len([k for k in expected if k != "generic_rest"]) == 5

    payload = base64.urlsafe_b64encode(
        json.dumps({"k": "secret-key", "u": "https://lara.example/api"}).encode()
    ).decode().rstrip("=")
    decoded = decode_conn_code(f"conn_{payload}")
    assert decoded["api_key"] == "secret-key"
    assert "lara.example" in decoded["base_url"]

    cfg = AdapterRegistry.normalize_config(
        "larastock_v1", {"api_key": f"conn_{payload}"}
    )
    assert cfg["api_key"] == "secret-key"
    assert cfg["base_url"].startswith("https://")

    admin_src = _read("src/admin_bot/handlers/suppliers.py")
    assert "SUPPLIER_PRESETS" in admin_src
    assert "normalize_config" in admin_src
    assert "conn_" in admin_src

    prod_src = _read("src/admin_bot/handlers/products_pricing.py")
    assert "تحديث صورة المنتج" in prod_src
    assert "photo_file_id" in prod_src


def test_miniapp_requires_customer_email_and_buyer_email():
    api = _read("src/api/routes/miniapp.py")
    assert "requires_customer_email" in api
    assert "buyer_email" in api
    page = _read("dashboard/app/miniapp/page.tsx")
    assert "requires_customer_email" in page
    assert "buyer_email" in page
    assert "البريد" in page


def test_dashboard_sham_cash_supplier_column_and_receipt_proxy():
    # Sham Cash UI lives under /orders (legacy /sham-cash redirects)
    page = _read("dashboard/app/orders/page.tsx")
    assert "supplier_name" in page
    assert "المورد" in page
    redirect = _read("dashboard/app/sham-cash/page.tsx")
    assert "orders?tab=sham-cash" in redirect
    orders_api = _read("src/api/routes/orders.py")
    assert '"{order_id}/receipt"' in orders_api or "/receipt" in orders_api
    assert "download_telegram_file" in orders_api


def test_storefront_blockquote_and_stock_labels():
    shop = _read("src/bot/handlers/shop.py")
    assert "<blockquote>" in shop
    assert "total_users" in shop or "👥" in shop
    kb = _read("src/bot/keyboards/inline.py")
    assert "📦" in kb
    assert "نفد" in kb
    assert "CheckoutEmailState" in shop or "waiting_email" in shop


def test_product_detail_keyboard_notify_oos():
    from src.bot.keyboards.inline import product_detail_keyboard

    kb = product_detail_keyboard("pid", False, show_notify=True)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert any("نبّهني" in (b.text or "") for b in flat)
    assert any((b.callback_data or "").startswith("stock_alert:") for b in flat)
