"""Q100 final micro-fixes: HTML admin alerts, ACL warm, ban gate, empty-key boot."""
from __future__ import annotations

import os
from html import escape

import pytest

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_q100_micro.db")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_Q100_MICRO")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-q100-micro-fixes-32ch!")
os.environ.setdefault("ENCRYPTION_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "111001")
os.environ["HOT_WALLET_PRIVATE_KEY"] = ""
os.environ["TRON_HOT_WALLET_PRIVATE_KEY"] = ""
os.environ["BSC_HOT_WALLET_PRIVATE_KEY"] = ""


def test_admin_review_caption_html_escapes_special_chars():
    from src.core.services.admin_review_notify import format_manual_review_caption

    text = format_manual_review_caption({
        "review_kind": "sham_cash",
        "order_number": "ORD_1",
        "customer_username": "bad_user*name[x]",
        "customer_name": "Ali_Hassan*",
        "customer_telegram_id": 99,
        "product_title": "Chat_GPT*[Pro]",
        "quantity": 1,
        "amount": 10,
        "supplier_name": "Supplier_X*[1]",
        "tx_ref": "TX_1*",
    })
    assert "<b>" in text or "<code>" in text
    assert "**" not in text
    assert escape("bad_user*name[x]", quote=False) in text
    assert escape("Supplier_X*[1]", quote=False) in text
    assert "المورد" in text


def test_live_feed_uses_html_parse_mode():
    src = open("src/admin_bot/handlers/live_feed.py", encoding="utf-8").read()
    assert 'parse_mode="HTML"' in src
    assert 'parse_mode="Markdown"' not in src


def test_nginx_api_sets_x_real_ip():
    src = open("docker/nginx.conf", encoding="utf-8").read()
    api_block = src.split("location /api/")[1].split("location ")[0]
    assert "proxy_set_header X-Real-IP $remote_addr;" in api_block


def test_miniapp_auth_rejects_banned_property():
    src = open("src/api/routes/miniapp.py", encoding="utf-8").read()
    assert "is_banned" in src
    assert "403" in src
    ent = open("src/core/models/entities.py", encoding="utf-8").read()
    assert "def is_banned" in ent


def test_receipt_confirmation_30_60():
    src = open("src/bot/handlers/shop.py", encoding="utf-8").read()
    assert "30–60" in src or "30-60" in src
    assert "تم استلام إيصال الدفع بنجاح" in src
    assert "مضمون" in src or "secured" in src.lower()


def test_no_stale_adm_appr_refs():
    shop = open("src/bot/handlers/shop.py", encoding="utf-8").read()
    assert "adm_appr:" not in shop


def test_empty_hot_wallet_keys_import_safe():
    from src.outgest.bsc_sender import bsc_sender, BscSender
    from src.outgest.tron_sender import tron_sender, TronSender
    # Re-init must not raise with empty keys
    BscSender()
    TronSender()
    assert bsc_sender is not None
    assert tron_sender is not None


def test_proof_card_masks_and_channel_has_order_button():
    from src.core.services.proof_service import format_trust_channel_card
    card = format_trust_channel_card({
        "order_number": "ORD-99",
        "product_title": "Grok",
        "customer_first_name": "أحمد",
        "customer_username": "ahmad_full",
        "payment_method": "sham_cash",
        "quantity": 1,
        "delivered_product": "SECRET",
    })
    assert "SECRET" not in card
    assert "ahmad_full" not in card
    sync = open("src/workers/channel_sync.py", encoding="utf-8").read()
    assert "اطلب الآن" in sync


def test_miniapp_timer_clamps_non_negative():
    src = open("dashboard/app/miniapp/page.tsx", encoding="utf-8").read()
    assert "Math.max(0" in src
    assert "overdue" in src or "تأخير" in src


@pytest.mark.asyncio
async def test_warm_escalation_acl_cache():
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_q100_micro_acl.db"
    from sqlalchemy import text
    from src.core.database import AsyncSessionLocal, engine, Base
    from src.core.models.entities import BotConfig
    from src.core.services.manual_delivery_status import warm_escalation_acl_cache
    import src.core.services.manual_delivery_status as mds

    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    mds._ACL_ESCALATION_CACHE.clear()
    async with AsyncSessionLocal() as session:
        session.add(BotConfig(key="escalation_telegram_ids", value="222003,333004"))
        await session.commit()
    ids = await warm_escalation_acl_cache(force=True)
    assert 222003 in ids and 333004 in ids
    assert 222003 in mds._ACL_ESCALATION_CACHE
    assert 333004 in mds._ACL_ESCALATION_CACHE
