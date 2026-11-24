"""Q100 final pre-production polish regression tests."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
import fakeredis.aioredis
from fastapi import HTTPException
from sqlalchemy import text

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_q100_final.db"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST_Q100")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-q100-final-polish-32chars")
os.environ.setdefault("ENCRYPTION_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
os.environ.setdefault("ADMIN_TELEGRAM_IDS", "111001")
os.environ.setdefault("HOT_WALLET_ADDRESS", "0xTestHotWalletBscAddress00000000000001")

from src.core.database import AsyncSessionLocal, engine, Base
from src.core.models.entities import BotConfig, AdminUser, PaymentInvoice, Order, ProductSupplierLink
from src.core.models.enums import PaymentMethod
from src.core.redis import event_bus
from src.core.security import get_password_hash, verify_password
from src.api.routes.miniapp import _resolve_sham_cash_account, _resolve_deposit
from src.api.routes import auth as auth_routes
from src.config import settings


ROOT = Path(__file__).resolve().parents[1]


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


@pytest.mark.asyncio
async def test_sham_cash_deposit_reads_botconfig_not_hardcoded(db_redis):
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as ei:
            await _resolve_sham_cash_account(session)
        assert ei.value.status_code == 503

        session.add(BotConfig(key="sham_cash_account_number", value="0991234567"))
        await session.commit()
        with pytest.raises(HTTPException):
            await _resolve_sham_cash_account(session)

        row = (
            await session.execute(
                __import__("sqlalchemy").select(BotConfig).where(
                    BotConfig.key == "sham_cash_account_number"
                )
            )
        ).scalar_one()
        row.value = "0944123456"
        await session.commit()
        assert await _resolve_sham_cash_account(session) == "0944123456"

    assert _resolve_deposit(PaymentMethod.SHAM_CASH) == ""


@pytest.mark.asyncio
async def test_admin_bootstrap_requires_env_credentials(db_redis):
    """Empty DB + username=admin must NOT invent an admin from arbitrary password."""
    with patch.object(settings, "ADMIN_USERNAME", ""), patch.object(settings, "ADMIN_PASSWORD", ""):
        async with AsyncSessionLocal() as session:
            with pytest.raises(HTTPException) as ei:
                await auth_routes._authenticate(session, "admin", "attacker-chosen-password")
            assert ei.value.status_code == 401
            count = (
                await session.execute(
                    __import__("sqlalchemy").select(__import__("sqlalchemy").func.count()).select_from(AdminUser)
                )
            ).scalar() or 0
            assert count == 0

    with patch.object(settings, "ADMIN_USERNAME", "ops_admin"), patch.object(
        settings, "ADMIN_PASSWORD", "env-only-strong-pass"
    ):
        async with AsyncSessionLocal() as session:
            admin = await auth_routes._authenticate(session, "ops_admin", "env-only-strong-pass")
            assert admin.username == "ops_admin"
            assert verify_password("env-only-strong-pass", admin.password_hash)


def test_docker_compose_no_public_app_ports():
    text_yml = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "8000:8000" not in text_yml
    assert "3000:3000" not in text_yml or "127.0.0.1:3001:3000" in text_yml
    # backend/dashboard must not publish open host ports
    assert 'ports:\n      - "8000:8000"' not in text_yml.replace("\r\n", "\n")
    assert 'ports:\n      - "3000:3000"' not in text_yml.replace("\r\n", "\n")
    assert "${MB_ENCRYPTION_SECRET_KEY" in text_yml


def test_ws_source_cookie_only_no_query_token():
    src = (ROOT / "src/api/main.py").read_text(encoding="utf-8")
    assert "query_params.get(\"token\")" not in src
    assert "query_params.get('token')" not in src
    assert "websocket.cookies.get" in src


def test_indexes_and_legacy_pages_and_guards():
    ent = (ROOT / "src/core/models/entities.py").read_text(encoding="utf-8")
    assert "tx_hash = Column(String(100), nullable=True, unique=True" in ent
    assert "created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)" in ent
    assert "nullable=False, index=True" in ent  # supplier / master product link indexes

    mig1 = (ROOT / "migrations/001_security_perf_indexes.sql").read_text(encoding="utf-8")
    assert "uq_payment_invoices_tx_hash" in mig1
    assert "idx_orders_created_at" in mig1

    for rel, dest in (
        ("dashboard/app/marketing/page.tsx", "bot-config"),
        ("dashboard/app/pricing/page.tsx", "products"),
        ("dashboard/app/sham-cash/page.tsx", "orders"),
        ("dashboard/app/support/page.tsx", "customers"),
        ("dashboard/app/treasury/page.tsx", "replace(\"/\")"),
    ):
        page = (ROOT / rel).read_text(encoding="utf-8")
        assert "useRouter" in page
        assert dest.split("(")[0] in page or dest in page

    mini = (ROOT / "dashboard/app/miniapp/page.tsx").read_text(encoding="utf-8")
    assert "amount: 10, network: \"sham_cash\"" not in mini
    assert "topupAmount" in mini and "topupNetwork" in mini

    shop = (ROOT / "src/bot/handlers/shop.py").read_text(encoding="utf-8")
    assert "already = Decimal(str(order.wallet_portion_reserved or 0))" in shop
    assert "if already > 0:" in shop
    assert "handle_receipt_non_photo" in shop
    assert "admin_sham_cash_review_keyboard" not in shop
    assert "adm_appr:" not in shop
