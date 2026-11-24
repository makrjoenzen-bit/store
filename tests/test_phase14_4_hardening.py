"""
Phase 14.4 — Production hardening: webhook HMAC, boot guard, gas oracle, resilience.

Run: pytest -v tests/test_phase14_4_hardening.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_phase14_4.db"
os.environ["TELEGRAM_BOT_TOKEN"] = "111:BOT"
os.environ["ADMIN_BOT_TOKEN"] = "222:ADMIN"
os.environ["ADMIN_TELEGRAM_IDS"] = "900001"
os.environ["HOT_WALLET_ADDRESS"] = "0xPhase144Hot000000000000000000000001"
os.environ["OXAPAY_MERCHANT_API_KEY"] = "oxa-test-merchant-key-phase14"
os.environ["CRYPTOMUS_PAYMENT_KEY"] = "cryptomus-test-payment-key"

import pytest
import pytest_asyncio
import fakeredis.aioredis
from cryptography.fernet import Fernet

from src.config import Settings, validate_production_secrets, settings
from src.core.redis import event_bus
from src.core.services.gas_oracle import GasFXOracle, FALLBACK_BNB_USD, FALLBACK_SAFETY_MULT
from src.core.services.audit_helpers import merge_audit_new_values
from src.core.resilience import compute_backoff_seconds, extract_flood_retry_after
from src.api.routes.webhooks import verify_oxapay_hmac, verify_cryptomus_sign


OXA_KEY = "oxa-test-merchant-key-phase14"
CRYPTO_KEY = "cryptomus-test-payment-key"


def _oxa_sign(body: bytes) -> str:
    return hmac.new(OXA_KEY.encode(), body, hashlib.sha512).hexdigest()


def _crypto_sign(payload: dict) -> str:
    data = {k: v for k, v in payload.items() if k != "sign"}
    encoded = base64.b64encode(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    ).decode()
    return hashlib.md5((encoded + CRYPTO_KEY).encode()).hexdigest()


@pytest_asyncio.fixture(autouse=True)
async def _redis():
    event_bus.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings.OXAPAY_MERCHANT_API_KEY = OXA_KEY
    settings.CRYPTOMUS_PAYMENT_KEY = CRYPTO_KEY
    yield


# ---------------------------------------------------------------------------
# 1. Webhook HMAC + replay
# ---------------------------------------------------------------------------

def test_oxapay_hmac_helpers():
    body = b'{"status":"paid","order_id":"x","track_id":"t1"}'
    sig = _oxa_sign(body)
    assert verify_oxapay_hmac(body, sig, OXA_KEY) is True
    assert verify_oxapay_hmac(body, "deadbeef", OXA_KEY) is False
    assert verify_oxapay_hmac(body, None, OXA_KEY) is False


def test_cryptomus_sign_helpers():
    payload = {"status": "paid", "order_id": "x", "uuid": "u1"}
    payload["sign"] = _crypto_sign(payload)
    assert verify_cryptomus_sign(payload, CRYPTO_KEY) is True
    bad = dict(payload)
    bad["sign"] = "0" * 32
    assert verify_cryptomus_sign(bad, CRYPTO_KEY) is False


def _mock_request(raw: bytes, headers: dict | None = None):
    req = MagicMock()
    req.headers = headers or {}
    req.client = MagicMock(host="127.0.0.1")

    async def body():
        return raw

    req.body = body
    return req


@pytest.mark.asyncio
async def test_unsigned_and_tampered_webhooks_return_401():
    from fastapi import HTTPException
    from src.api.routes import webhooks as wh

    session = AsyncMock()

    with pytest.raises(HTTPException) as e1:
        await wh.oxapay_webhook(
            _mock_request(b'{"status":"paid","order_id":"1"}'),
            session,
        )
    assert e1.value.status_code == 401

    body = b'{"status":"paid","order_id":"1","track_id":"tx-a"}'
    with pytest.raises(HTTPException) as e2:
        await wh.oxapay_webhook(
            _mock_request(body, {"HMAC": "00" * 64}),
            session,
        )
    assert e2.value.status_code == 401

    bad = json.dumps(
        {"status": "paid", "order_id": "1", "uuid": "u-bad", "sign": "nope"}
    ).encode()
    with pytest.raises(HTTPException) as e3:
        await wh.cryptomus_webhook(_mock_request(bad), session)
    assert e3.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_webhook_once_then_replay_ignored():
    from src.api.routes import webhooks as wh

    calls = []

    async def fake_confirm(order_id, provider, session):
        calls.append((order_id, provider))

    body_obj = {"status": "paid", "order_id": "ord-replay-1", "track_id": "trk-unique-99"}
    raw = json.dumps(body_obj, separators=(",", ":")).encode()
    sig = _oxa_sign(raw)
    session = AsyncMock()

    with patch("src.api.routes.webhooks._confirm_paid_webhook", new=fake_confirm):
        r1 = await wh.oxapay_webhook(_mock_request(raw, {"HMAC": sig}), session)
        r2 = await wh.oxapay_webhook(_mock_request(raw, {"HMAC": sig}), session)

    assert r1 == "ok"
    assert r2 == "ok"
    assert len(calls) == 1
    assert calls[0] == ("ord-replay-1", "oxapay")


@pytest.mark.asyncio
async def test_valid_cryptomus_webhook_once():
    from src.api.routes import webhooks as wh

    calls = []

    async def fake_confirm(order_id, provider, session):
        calls.append((order_id, provider))

    payload = {
        "status": "paid",
        "order_id": "ord-c-1",
        "uuid": "crypto-uuid-77",
    }
    payload["sign"] = _crypto_sign(payload)
    raw = json.dumps(payload).encode()
    session = AsyncMock()

    with patch("src.api.routes.webhooks._confirm_paid_webhook", new=fake_confirm):
        r1 = await wh.cryptomus_webhook(_mock_request(raw), session)
        r2 = await wh.cryptomus_webhook(_mock_request(raw), session)

    assert r1 == "ok" and r2 == "ok"
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 2. Production boot guard
# ---------------------------------------------------------------------------

def _settings_isolated(**kwargs) -> Settings:
    """Build Settings without inheriting host .env / process secrets into asserts."""
    base = dict(
        ENVIRONMENT="development",
        JWT_SECRET_KEY="dev-jwt-secret-key-at-least-32-chars!!",
        ENCRYPTION_MASTER_KEY=Fernet.generate_key().decode(),
        TELEGRAM_BOT_TOKEN="1:dev",
        ADMIN_BOT_TOKEN="2:dev",
        ADMIN_TELEGRAM_IDS="1",
        ADMIN_TELEGRAM_ID=None,
        OWNER_TELEGRAM_IDS="",
        STAFF_TELEGRAM_IDS="",
        HOT_WALLET_ADDRESS="",
        HOT_WALLET_PRIVATE_KEY="",
        TRON_HOT_WALLET_ADDRESS="",
        TRON_HOT_WALLET_PRIVATE_KEY="",
        BINANCE_PAY_MERCHANT_ID="",
        BINANCE_SPOT_API_KEY=None,
        BINANCE_SPOT_API_SECRET=None,
        BINANCE_PAY_API_KEY=None,
        BINANCE_PAY_SECRET_KEY=None,
    )
    base.update(kwargs)
    return Settings(_env_file=None, **base)


def test_validate_production_blocks_weak_secrets():
    weak = _settings_isolated(
        ENVIRONMENT="production",
        JWT_SECRET_KEY="short",
        ENCRYPTION_MASTER_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        TELEGRAM_BOT_TOKEN="",
        ADMIN_BOT_TOKEN="",
        ADMIN_TELEGRAM_IDS="",
        ADMIN_TELEGRAM_ID=None,
    )
    with pytest.raises(RuntimeError) as ei:
        validate_production_secrets(weak)
    msg = str(ei.value)
    assert "JWT_SECRET_KEY" in msg
    assert "ENCRYPTION_MASTER_KEY" in msg
    assert "TELEGRAM_BOT_TOKEN" in msg
    assert "ADMIN_BOT_TOKEN" in msg
    assert "ADMIN_TELEGRAM_IDS" in msg


def test_validate_production_blocks_fake_binance_and_zero_wallet():
    strong_jwt = "a" * 40
    fernet = Fernet.generate_key().decode()
    bad = _settings_isolated(
        ENVIRONMENT="production",
        JWT_SECRET_KEY=strong_jwt,
        ENCRYPTION_MASTER_KEY=fernet,
        TELEGRAM_BOT_TOKEN="1:tok",
        ADMIN_BOT_TOKEN="2:tok",
        ADMIN_TELEGRAM_IDS="100",
        HOT_WALLET_ADDRESS="0x1111111111111111111111111111111111111111",
        HOT_WALLET_PRIVATE_KEY="0x" + ("0" * 64),
        BINANCE_PAY_MERCHANT_ID="48192841",
        BINANCE_SPOT_API_KEY="spotkey",
    )
    with pytest.raises(RuntimeError) as ei:
        validate_production_secrets(bad)
    msg = str(ei.value)
    assert "HOT_WALLET_PRIVATE_KEY" in msg
    assert "BINANCE_PAY_MERCHANT_ID" in msg


def test_validate_production_passes_strong_and_dev_noop():
    strong_jwt = "production-grade-jwt-secret-key-32+"
    fernet = Fernet.generate_key().decode()
    good = _settings_isolated(
        ENVIRONMENT="production",
        JWT_SECRET_KEY=strong_jwt,
        ENCRYPTION_MASTER_KEY=fernet,
        TELEGRAM_BOT_TOKEN="1:live-bot",
        ADMIN_BOT_TOKEN="2:admin-bot",
        ADMIN_TELEGRAM_IDS="900001,900002",
    )
    validate_production_secrets(good)  # must not raise

    dev = _settings_isolated(ENVIRONMENT="development")
    validate_production_secrets(dev)  # no-op


# ---------------------------------------------------------------------------
# 3. Gas FX oracle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gas_oracle_caches_and_fallback():
    oracle = GasFXOracle(ttl=300)
    redis = event_bus.redis

    class FakeResp:
        status_code = 200

        def json(self):
            return {"price": "612.50"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return FakeResp()

    with patch("src.core.services.gas_oracle.httpx.AsyncClient", return_value=FakeClient()):
        p1 = await oracle.get_bnb_usd()
        assert p1 == Decimal("612.50")
        cached = await redis.get("fx:bnb_usdt")
        assert cached == "612.50"
        # Second call hits cache (no HTTP)
        with patch("src.core.services.gas_oracle.httpx.AsyncClient") as boom:
            boom.side_effect = AssertionError("should not call HTTP")
            p2 = await oracle.get_bnb_usd()
            assert p2 == Decimal("612.50")

    # Fallback on HTTP failure after clearing cache
    await redis.delete("fx:bnb_usdt")

    class FailClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            raise RuntimeError("network down")

    with patch("src.core.services.gas_oracle.httpx.AsyncClient", return_value=FailClient()):
        fb = await oracle.get_bnb_usd()
        assert fb == (FALLBACK_BNB_USD * FALLBACK_SAFETY_MULT).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# 4. Resilience + audit tags
# ---------------------------------------------------------------------------

def test_audit_actor_tags_admin_telegram_id():
    meta = merge_audit_new_values("tg:900001", {"action": "refund"})
    assert meta["admin_telegram_id"] == 900001
    assert meta["reviewer"] == "tg:900001"
    assert meta["action"] == "refund"


def test_backoff_grows_and_flood_wait_extract():
    d0 = compute_backoff_seconds(0, base=1.0, cap=60.0, jitter=0)
    d3 = compute_backoff_seconds(3, base=1.0, cap=60.0, jitter=0)
    assert d0 == 1.0
    assert d3 == 8.0

    class Flood(Exception):
        def __init__(self):
            self.retry_after = 12

    assert extract_flood_retry_after(Flood()) == 12.0


@pytest.mark.asyncio
async def test_listeners_and_admin_feed_survive_burst():
    from src.workers.tron_listener import TronListenerWorker
    from src.workers.binance_pay_listener import BinancePayListenerWorker
    from src.admin_bot.handlers.live_feed import broadcast_to_admins
    from src.core.resilience import sleep_backoff

    tron = TronListenerWorker()
    assert hasattr(tron, "_fail_streak")
    bp = BinancePayListenerWorker()
    assert hasattr(bp, "_fail_streak")

    with patch("src.core.resilience.asyncio.sleep", new_callable=AsyncMock) as sleeper:
        delay = await sleep_backoff(0, base=0.01, cap=1.0)
        assert delay >= 0.01
        sleeper.assert_awaited()

    class RetryAfter(Exception):
        def __init__(self):
            self.retry_after = 0.01

    bot = MagicMock()
    n = {"i": 0}

    async def send_message(**kwargs):
        n["i"] += 1
        if n["i"] == 1:
            raise RetryAfter()
        return True

    bot.send_message = send_message
    with patch("src.admin_bot.handlers.live_feed.settings") as s:
        s.admin_telegram_id_set = {900001}
        with patch("src.admin_bot.handlers.live_feed.asyncio.sleep", new_callable=AsyncMock) as sleeper:
            sent = await broadcast_to_admins(bot, "test alert")
            assert sent == 1
            sleeper.assert_awaited()
