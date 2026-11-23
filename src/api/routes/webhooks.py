"""
Payment gateway webhooks — HMAC-verified, replay-protected, idempotent confirmations.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from src.config import settings
from src.core.database import get_db
from src.core.models.entities import Order
from src.core.models.enums import OrderState
from src.core.services.order_service import order_service
from src.core.services.jit_queue import enqueue_jit_order

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Replay TTL: 7 days — same gateway tx never double-credits
_REPLAY_TTL_SECONDS = 7 * 24 * 3600


def verify_oxapay_hmac(raw_body: bytes, header_hmac: Optional[str], api_key: str) -> bool:
    """OxaPay: hex HMAC-SHA512 of raw body with MERCHANT_API_KEY."""
    if not header_hmac or not api_key:
        return False
    expected = hmac.new(
        api_key.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected.lower(), header_hmac.strip().lower())


def verify_cryptomus_sign(payload: Dict[str, Any], api_key: str) -> bool:
    """
    Cryptomus: MD5( base64(json_body_without_sign) + PAYMENT_KEY ).
    Sign is removed from body before hashing; JSON uses compact separators.
    """
    if not api_key:
        return False
    received = payload.get("sign")
    if not received or not isinstance(received, str):
        return False
    data = {k: v for k, v in payload.items() if k != "sign"}
    encoded = base64.b64encode(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("utf-8")
    expected = hashlib.md5((encoded + api_key).encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected.lower(), received.strip().lower())


async def _claim_webhook_tx(provider: str, tx_id: str) -> bool:
    """
    Strict replay protection. Returns True if this tx_id is new (claimed),
    False if already processed (idempotent ignore).
    """
    if not tx_id:
        return True  # no external id — rely on order-state lock only
    from src.core.redis import event_bus

    key = f"webhook:seen:{provider}:{tx_id}"
    redis = event_bus.redis
    if redis is None:
        from src.core.redis import get_redis_client
        redis = get_redis_client()
        event_bus.redis = redis
    # SET NX — first writer wins
    ok = await redis.set(key, "1", nx=True, ex=_REPLAY_TTL_SECONDS)
    return bool(ok)


async def _confirm_paid_webhook(order_id: str, provider: str, session: AsyncSession) -> None:
    """
    Idempotent webhook confirmation. A per-order Redis lock spans the check and the
    state transition, so a replayed/concurrent callback can never confirm twice.
    """
    from src.core.redis import event_bus

    lock_key = f"lock:webhook:{order_id}"
    token = await event_bus.acquire_lock_with_token(lock_key, ttl_seconds=30)
    if not token:
        logger.warning(f"Webhook for order {order_id} is already being processed; ignoring replay.")
        return
    try:
        fresh = (await session.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )).scalar_one_or_none()
        if not fresh or fresh.state != OrderState.AWAITING_PAYMENT:
            return
        await order_service.transition_order_state(
            session, fresh, OrderState.PAYMENT_CONFIRMED, f"webhook.{provider}_paid"
        )
        await session.commit()
        await enqueue_jit_order(str(fresh.id))
    finally:
        await event_bus.release_lock(lock_key, token=token)


@router.post("/oxapay")
async def oxapay_webhook(request: Request, session: AsyncSession = Depends(get_db)):
    """Receives instant payment notifications from OxaPay (HMAC-SHA512 required)."""
    raw = await request.body()
    header_sig = request.headers.get("HMAC") or request.headers.get("hmac")
    api_key = (settings.OXAPAY_MERCHANT_API_KEY or "").strip()

    if not api_key:
        logger.error("OxaPay webhook rejected: OXAPAY_MERCHANT_API_KEY not configured")
        raise HTTPException(status_code=503, detail="OxaPay gateway not configured")

    if not verify_oxapay_hmac(raw, header_sig, api_key):
        logger.warning(
            f"OxaPay webhook SPOOF attempt — invalid/missing HMAC "
            f"(ip={request.client.host if request.client else '?'})"
        )
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.info(f"OxaPay webhook verified: status={payload.get('status')} order={payload.get('order_id')}")

    tx_id = str(
        payload.get("track_id")
        or payload.get("txID")
        or payload.get("transaction_id")
        or payload.get("trackId")
        or ""
    )
    if tx_id and not await _claim_webhook_tx("oxapay", tx_id):
        logger.info(f"OxaPay webhook replay ignored for track_id={tx_id}")
        return "ok"

    if payload.get("status") == "paid" and payload.get("order_id"):
        await _confirm_paid_webhook(str(payload["order_id"]), "oxapay", session)
    return "ok"


@router.post("/cryptomus")
async def cryptomus_webhook(request: Request, session: AsyncSession = Depends(get_db)):
    """Receives instant payment notifications from Cryptomus (MD5 sign required)."""
    raw = await request.body()
    api_key = (settings.CRYPTOMUS_PAYMENT_KEY or "").strip()

    if not api_key:
        logger.error("Cryptomus webhook rejected: CRYPTOMUS_PAYMENT_KEY not configured")
        raise HTTPException(status_code=503, detail="Cryptomus gateway not configured")

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not verify_cryptomus_sign(payload, api_key):
        logger.warning(
            f"Cryptomus webhook SPOOF attempt — invalid/missing sign "
            f"(ip={request.client.host if request.client else '?'})"
        )
        raise HTTPException(status_code=401, detail="Invalid signature")

    logger.info(
        f"Cryptomus webhook verified: status={payload.get('status')} order={payload.get('order_id')}"
    )

    tx_id = str(payload.get("uuid") or payload.get("txid") or payload.get("tx_id") or "")
    if tx_id and not await _claim_webhook_tx("cryptomus", tx_id):
        logger.info(f"Cryptomus webhook replay ignored for uuid={tx_id}")
        return "ok"

    status_str = payload.get("status")
    order_id = payload.get("order_id")
    if status_str in ("paid", "paid_over") and order_id:
        await _confirm_paid_webhook(str(order_id), "cryptomus", session)

    return "ok"


@router.post("/larastock/{supplier_id}")
async def larastock_webhook(
    supplier_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """
    Lara Stock HMAC webhook: X-Webhook-Signature sha256=... over f\"{timestamp}.\" + body.
    Events: stock.sold / stock.added / order.completed
    """
    from src.core.models.entities import Supplier, ProductSupplierLink, MasterProduct
    from src.core.adapters.larastock import verify_lara_webhook_signature
    from src.core.adapters.registry import AdapterRegistry
    from src.core.security import decrypt_secret
    from src.workers.delivery_poller import delivery_poller

    raw = await request.body()
    timestamp = request.headers.get("X-Webhook-Timestamp") or request.headers.get("X-Timestamp") or ""
    signature = request.headers.get("X-Webhook-Signature") or ""

    supplier = (
        await session.execute(select(Supplier).where(Supplier.id == supplier_id))
    ).scalar_one_or_none()
    if not supplier or supplier.driver_type != "larastock_v1":
        raise HTTPException(status_code=404, detail="Supplier not found")

    try:
        cfg = json.loads(decrypt_secret(supplier.config_encrypted))
    except Exception:
        cfg = {}
    secret = cfg.get("webhook_secret") or cfg.get("api_key") or ""
    if not verify_lara_webhook_signature(raw, timestamp, signature, secret):
        logger.warning(f"LaraStock webhook invalid signature for supplier={supplier_id}")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = str(payload.get("event") or payload.get("type") or "")
    data = payload.get("data") or payload

    if event in ("stock.sold", "stock.added"):
        spid = str(data.get("product_id") or data.get("id") or "")
        stock = data.get("stock")
        if spid:
            links = (
                await session.execute(
                    select(ProductSupplierLink).where(
                        ProductSupplierLink.supplier_id == supplier.id,
                        ProductSupplierLink.supplier_product_id == spid,
                    )
                )
            ).scalars().all()
            for link in links:
                if stock == "unlimited":
                    link.available_quantity = 9999
                    link.is_in_stock = True
                elif stock is not None:
                    try:
                        qty = int(stock)
                    except Exception:
                        qty = 0
                    link.available_quantity = qty
                    link.is_in_stock = qty > 0
            await session.commit()
        return {"status": "ok", "event": event}

    if event == "order.completed":
        # Kick a delivery poll cycle so pending orders resolve immediately
        try:
            await delivery_poller.poll_once()
        except Exception as e:
            logger.warning(f"LaraStock order.completed poll kick failed: {e}")
        return {"status": "ok", "event": event}

    return {"status": "ignored", "event": event}
