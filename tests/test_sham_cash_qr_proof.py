"""Sham Cash QR + sham_paid receipt FSM + proof channel publishing regressions."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select, text

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_sham_qr_proof.db"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_SHAM_QR")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-sham-cash-qr-proof-32ch")
os.environ.setdefault("ENCRYPTION_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "111001")
os.environ.setdefault("ADMIN_USERNAME", "sham_admin")
os.environ.setdefault("ADMIN_PASSWORD", "sham-strong-pass-99xx")
os.environ.setdefault("HOT_WALLET_ADDRESS", "0xTestHotWalletBscAddress00000000000001")
os.environ["HOT_WALLET_PRIVATE_KEY"] = ""
os.environ["TRON_HOT_WALLET_PRIVATE_KEY"] = ""

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Category, MasterProduct, Order, PaymentInvoice, BotConfig,
)
from src.core.models.enums import OrderState, InvoiceStatus, PaymentMethod
from src.core.redis import event_bus
from src.bot.keyboards.inline import sham_cash_payment_keyboard
from src.core.services.proof_service import (
    format_trust_channel_card,
    publish_to_proof_channel,
)
from src.core.services.sham_cash_qr import build_sham_cash_caption


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


async def _seed_order(*, tg_id: int = 424242, lang: str = "ar"):
    async with AsyncSessionLocal() as session:
        cust = Customer(
            telegram_id=tg_id,
            first_name="Ali",
            language_code=lang,
            wallet_balance=Decimal("0"),
        )
        session.add(cust)
        await session.flush()
        cat = Category(name_ar="c", name_en="c", is_active=True)
        session.add(cat)
        await session.flush()
        prod = MasterProduct(
            category_id=cat.id,
            internal_sku=f"SHAM-{uuid.uuid4().hex[:6]}",
            title_ar="منتج شام",
            title_en="Sham Prod",
            is_active=True,
            fulfillment_mode="manual",
        )
        session.add(prod)
        await session.flush()
        order = Order(
            order_number=f"ORD-SHAM-{uuid.uuid4().hex[:6]}",
            customer_id=cust.id,
            master_product_id=prod.id,
            our_price=Decimal("15.00"),
            quantity=1,
            idempotency_key=f"sham-{uuid.uuid4().hex}",
            state=OrderState.AWAITING_PAYMENT,
            delivered_product="SECRET-PASSWORD-DO-NOT-LEAK",
        )
        session.add(order)
        await session.flush()
        inv = PaymentInvoice(
            customer_id=cust.id,
            order_id=order.id,
            payment_method=PaymentMethod.SHAM_CASH,
            amount_expected=Decimal("15"),
            status=InvoiceStatus.PENDING,
        )
        session.add(inv)
        await session.flush()
        order.invoice_id = inv.id
        session.add(BotConfig(key="sham_cash_account_number", value="0999888777"))
        session.add(BotConfig(key="sham_cash_qr_url", value="/uploads/sham_cash/qr.jpg"))
        session.add(BotConfig(key="proof_channel_id", value="-100999"))
        session.add(BotConfig(key="proof_channel_auto_post", value=False))
        await session.commit()
        return str(order.id), order.order_number, str(cust.id), tg_id


def test_sham_cash_keyboard_has_paid_and_cancel():
    kb = sham_cash_payment_keyboard(order_id="oid-1", account_code="0999", lang="ar")
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "sham_paid:oid-1" in flat
    assert "cancel_order:oid-1" in flat


@pytest.mark.asyncio
async def test_caption_includes_account_and_amount(db_redis):
    async with AsyncSessionLocal() as session:
        session.add(BotConfig(key="sham_cash_account_number", value="0911122233"))
        session.add(BotConfig(key="sham_cash_recipient_name", value="Store"))
        await session.commit()
        cap = await build_sham_cash_caption(session, amount_usd=12.5, order_number="ORD-1", lang="ar")
    assert "12.50" in cap
    assert "0911122233" in cap
    assert "تم الدفع" in cap or "شام كاش" in cap


@pytest.mark.asyncio
async def test_sham_paid_idor_and_receipt_fsm(db_redis):
    """Ownership check on sham_paid + receipt handlers accept PDF / reject plain text."""
    from src.bot.handlers import shop as shop_mod
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.fsm.storage.base import StorageKey

    oid, onum, cust_id, owner_tg = await _seed_order(tg_id=777001)

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=777001, user_id=777001)
    state = FSMContext(storage=storage, key=key)

    # Intruder callback
    def _user(uid, username="u", first="F"):
        u = MagicMock()
        u.id = uid
        u.username = username
        u.first_name = first
        u.last_name = None
        u.language_code = "ar"
        return u

    bad_cb = MagicMock()
    bad_cb.data = f"sham_paid:{oid}"
    bad_cb.from_user = _user(999999, "hacker", "H")
    bad_cb.answer = AsyncMock()
    bad_cb.message = MagicMock()
    bad_cb.message.answer = AsyncMock()
    await shop_mod.sham_paid_prompt_receipt(bad_cb, state)
    bad_cb.answer.assert_awaited()
    assert "لا يخص" in str(bad_cb.answer.await_args)
    bad_cb.message.answer.assert_not_awaited()

    # Owner OK
    good_cb = MagicMock()
    good_cb.data = f"sham_paid:{oid}"
    good_cb.from_user = _user(owner_tg, "ali", "Ali")
    good_cb.answer = AsyncMock()
    good_cb.message = MagicMock()
    good_cb.message.answer = AsyncMock()
    await shop_mod.sham_paid_prompt_receipt(good_cb, state)
    cur = await state.get_state()
    assert cur and "waiting_for_receipt" in cur
    good_cb.message.answer.assert_awaited()
    prompt = good_cb.message.answer.await_args.args[0]
    assert "PDF" in prompt or "إيصال" in prompt

    # Plain text rejected — stays in FSM, no AI
    msg = MagicMock()
    msg.from_user = _user(owner_tg, "ali", "Ali")
    msg.text = "hello concierge please"
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    await shop_mod.handle_receipt_non_photo(msg, state)
    msg.answer.assert_awaited()
    assert await state.get_state()  # still in FSM
    assert "PDF" in msg.answer.await_args.args[0] or "إيصال" in msg.answer.await_args.args[0]

    # PDF document accepted path (mocked finalize)
    with patch.object(shop_mod, "_finalize_sham_receipt", new=AsyncMock()) as fin:
        doc_msg = MagicMock()
        doc_msg.from_user = _user(owner_tg)
        doc_msg.document = MagicMock(
            mime_type="application/pdf",
            file_name="receipt.pdf",
            file_id="file-pdf-1",
            file_unique_id="uniq-pdf-1",
            file_size=1200,
        )
        doc_msg.bot = MagicMock()
        doc_msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path="x.pdf"))
        stream = MagicMock()
        stream.read = MagicMock(return_value=b"%PDF-1.4 fake")
        doc_msg.bot.download_file = AsyncMock(return_value=stream)
        doc_msg.answer = AsyncMock()
        await shop_mod.handle_receipt_pdf(doc_msg, state)
        fin.assert_awaited()
        assert fin.await_args.kwargs.get("receipt_type") == "pdf"


@pytest.mark.asyncio
async def test_publish_to_proof_channel_masked_idempotent(db_redis):
    published = []

    async def capture(channel, message):
        published.append({"channel": channel, "message": message})

    event_bus.publish = capture  # type: ignore

    oid, onum, _, _ = await _seed_order(tg_id=888001)

    # force=False + auto_post false → skipped
    r0 = await publish_to_proof_channel(oid, stage="delivered", force=False)
    assert r0["status"] == "skipped"
    assert r0["reason"] == "auto_post_disabled"

    r1 = await publish_to_proof_channel(oid, stage="delivered", force=True)
    assert r1["status"] == "ok"
    text = r1["text"]
    assert "SECRET-PASSWORD" not in text
    assert onum in text or "#" in text
    assert "تم توثيق" in text or "تم تأكيد" in text
    assert any(p["channel"] == "channel:broadcast_proof" for p in published)
    payload = [p for p in published if p["channel"] == "channel:broadcast_proof"][0]["message"]
    assert payload.get("delivered_product") in (None, "")
    assert payload.get("receipt_file_id") in (None, "")

    # Idempotent second call
    published.clear()
    r2 = await publish_to_proof_channel(oid, stage="delivered", force=True)
    assert r2["status"] == "skipped"
    assert r2["reason"] == "already_posted"
    assert not published


def test_trust_card_never_embeds_password():
    card = format_trust_channel_card({
        "order_number": "ORD-X",
        "product_title": "Grok",
        "quantity": 1,
        "payment_method": "sham_cash",
        "customer_first_name": "أحمد",
        "customer_username": "ahmad99",
        "delivered_product": "pass=LEAKME",
    }, stage="delivered")
    assert "LEAKME" not in card
    assert "أ" in card or "a***" in card.lower() or "أحمد"[0] in card or "@a" in card


@pytest.mark.asyncio
async def test_adm_proof_callback_handler(db_redis):
    from src.admin_bot.handlers import orders as admin_orders

    oid, onum, _, _ = await _seed_order(tg_id=555001)
    async with AsyncSessionLocal() as session:
        # ensure channel configured
        pass

    published = []

    async def capture(channel, message):
        published.append({"channel": channel, "message": message})

    event_bus.publish = capture  # type: ignore

    cb = MagicMock()
    cb.data = f"adm:proof:{oid}"
    cb.from_user = MagicMock(id=111001, username="admin")
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    await admin_orders.cb_publish_proof(cb)
    cb.answer.assert_awaited()
    assert any(p["channel"] == "channel:broadcast_proof" for p in published)
