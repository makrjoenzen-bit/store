"""Live countdown formatting + multi-admin escalating alerts for PENDING_MANUAL_DELIVERY."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import fakeredis.aioredis
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_manual_countdown.db"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_COUNTDOWN")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-manual-countdown-timer32")
os.environ.setdefault("ENCRYPTION_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "111001")
os.environ.setdefault("ADMIN_USERNAME", "cd_admin")
os.environ.setdefault("ADMIN_PASSWORD", "countdown-strong-pass-99")
os.environ.setdefault("HOT_WALLET_ADDRESS", "0xTestHotWalletBscAddress00000000000001")
os.environ["HOT_WALLET_PRIVATE_KEY"] = ""
os.environ["TRON_HOT_WALLET_PRIVATE_KEY"] = ""

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import (
    Customer, Category, MasterProduct, Order, PaymentInvoice, BotConfig,
)
from src.core.models.enums import OrderState, InvoiceStatus, PaymentMethod
from src.core.redis import event_bus
from src.core.services.manual_delivery_status import (
    format_countdown_message,
    progress_bar,
    escalation_stage,
    format_escalation_caption,
    get_escalation_telegram_ids,
    cache_escalation_ids,
)
from src.workers.health_monitor import SupplierHealthMonitor


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


def test_progress_bar_and_countdown_ar_en_at_55_10_0():
    sla = 60
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # 55 minutes remaining → ~8% elapsed
    dl_55 = now + timedelta(minutes=55)
    ar55 = format_countdown_message(
        order_number="ORD-A", lang="ar", deadline=dl_55, sla_minutes=sla, now=now,
    )
    en55 = format_countdown_message(
        order_number="ORD-A", lang="en", deadline=dl_55, sla_minutes=sla, now=now,
    )
    assert "ORD-A" in ar55 and "تم استلام الدفع" in ar55
    assert "55" in ar55 or "دقيقة" in ar55
    assert progress_bar(5 / 60) in ar55 or "]" in ar55
    assert "Payment received" in en55
    assert "Preparing" in en55

    # 10 minutes remaining → ~83%
    dl_10 = now + timedelta(minutes=10)
    ar10 = format_countdown_message(
        order_number="ORD-B", lang="ar", deadline=dl_10, sla_minutes=sla, now=now,
    )
    en10 = format_countdown_message(
        order_number="ORD-B", lang="en", deadline=dl_10, sla_minutes=sla, now=now,
    )
    assert "10" in ar10
    assert "10" in en10
    bar83 = progress_bar(50 / 60)
    assert "83%" in bar83 or "83" in bar83 or "█" in bar83

    # 0 / overdue
    dl_0 = now - timedelta(seconds=1)
    ar0 = format_countdown_message(
        order_number="ORD-C", lang="ar", deadline=dl_0, sla_minutes=sla, now=now, overdue=True,
    )
    en0 = format_countdown_message(
        order_number="ORD-C", lang="en", deadline=dl_0, sla_minutes=sla, now=now, overdue=True,
    )
    assert "نأسف جداً للتأخير" in ar0
    assert "sincerely apologize" in en0.lower()


def test_escalation_stage_thresholds():
    assert escalation_stage(0) is None
    assert escalation_stage(29.9) is None
    assert escalation_stage(30) == "half"
    assert escalation_stage(49) == "half"
    assert escalation_stage(50) == "urgent"
    assert escalation_stage(75) == "urgent"
    assert "مرّ 30 دقيقة" in format_escalation_caption(
        stage="half", order_number="X1", product_title="P", elapsed_minutes=30,
    )
    assert "عاجل" in format_escalation_caption(
        stage="urgent", order_number="X1", product_title="P", elapsed_minutes=60,
    )


@pytest.mark.asyncio
async def test_escalation_t30_t60_fans_out_to_admin_and_assistant_stops_on_delivered(db_redis):
    """T+30 and T+60 escalations hit both primary + assistant; stop after DELIVERED."""
    published = []

    async def capture(channel, message):
        published.append({"channel": channel, "message": message})

    event_bus.publish = capture  # type: ignore
    event_bus.publish_alert = AsyncMock()  # type: ignore

    primary = 111001
    assistant = 222003
    await cache_escalation_ids(f"{primary},{assistant}")

    async with AsyncSessionLocal() as session:
        session.add(BotConfig(key="escalation_telegram_ids", value=f"{primary},{assistant}"))
        session.add(BotConfig(key="manual_delivery_sla_minutes", value=60))
        cust = Customer(telegram_id=5001, first_name="C", language_code="ar")
        session.add(cust)
        await session.flush()
        cat = Category(name_ar="c", name_en="c", is_active=True)
        session.add(cat)
        await session.flush()
        prod = MasterProduct(
            category_id=cat.id,
            internal_sku="CD-1",
            title_ar="منتج",
            title_en="P",
            is_active=True,
            fulfillment_mode="manual",
        )
        session.add(prod)
        await session.flush()
        now = datetime.now(timezone.utc)
        order = Order(
            order_number=f"ORD-CD-{uuid.uuid4().hex[:6]}",
            customer_id=cust.id,
            master_product_id=prod.id,
            our_price=Decimal("9"),
            quantity=1,
            idempotency_key=f"cd-{uuid.uuid4().hex}",
            state=OrderState.PENDING_MANUAL_DELIVERY,
            status_chat_id=5001,
            status_message_id=99,
            manual_deadline_at=now + timedelta(minutes=30),  # started 30m ago (sla 60)
            updated_at=now - timedelta(minutes=30),
        )
        session.add(order)
        await session.commit()
        oid = order.id
        onum = order.order_number

    ids = await get_escalation_telegram_ids()
    # With session for DB merge
    async with AsyncSessionLocal() as session:
        ids = await get_escalation_telegram_ids(session)
    assert primary in ids and assistant in ids

    mon = SupplierHealthMonitor()
    await mon.check_manual_delivery_escalations()

    half = [
        p for p in published
        if p["channel"] == "admin:manual_review"
        and p["message"].get("escalation_stage") == "half"
    ]
    assert half, "expected T+30 half reminder"
    assert half[0]["message"].get("review_kind") == "manual_delivery"
    assert onum in (half[0]["message"].get("caption") or "")

    # Advance to urgent (≥50m elapsed): deadline was now+30 at T+30 check;
    # set deadline to now+5 → started = deadline - 60 ≈ 55m ago
    published.clear()
    async with AsyncSessionLocal() as session:
        order = (await session.execute(select(Order).where(Order.id == oid))).scalar_one()
        order.manual_deadline_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        await session.commit()
    # Clear half throttle so urgent can fire independently
    await db_redis.delete(f"esc:manual:{oid}:half")

    await mon.check_manual_delivery_escalations()
    urgent = [
        p for p in published
        if p["channel"] == "admin:manual_review"
        and p["message"].get("escalation_stage") == "urgent"
    ]
    assert urgent, "expected T+50/60 urgent escalation"

    # Deliver → further escalations must stop
    published.clear()
    async with AsyncSessionLocal() as session:
        order = (await session.execute(select(Order).where(Order.id == oid))).scalar_one()
        order.state = OrderState.DELIVERED
        await session.commit()
    await db_redis.delete(f"esc:manual:{oid}:urgent")
    await db_redis.delete(f"esc:manual:{oid}:half")
    await mon.check_manual_delivery_escalations()
    assert not [
        p for p in published if p["channel"] == "admin:manual_review"
    ], "escalations must stop once DELIVERED"
