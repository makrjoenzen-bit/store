"""Lara Stock / Shop Bot adapter — conn_ decode, ChatGPT seat invites, HMAC webhooks."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from src.core.adapters.base import (
    SupplierAdapter, SupplierProductDTO, QuoteDTO,
    SupplierOrderResultDTO, DepositInfoDTO, HealthStatusDTO,
    STATUS_COMPLETED, STATUS_PENDING_DELIVERY, STATUS_FAILED,
    format_delivery_payload,
)


def decode_conn_code(raw: str) -> Dict[str, str]:
    """
    Decode Lara Stock connection paste: `conn_<base64url>` → {k: api_key, u: base_url}.
    Also accepts bare base64 JSON.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("Empty connection code")
    if text.startswith("conn_"):
        text = text[5:]
    # Fix URL-safe padding
    pad = "=" * (-len(text) % 4)
    try:
        decoded = base64.urlsafe_b64decode(text + pad)
    except Exception:
        decoded = base64.b64decode(text + pad)
    data = json.loads(decoded.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid connection payload")
    api_key = data.get("k") or data.get("api_key") or data.get("key")
    base_url = data.get("u") or data.get("base_url") or data.get("url")
    if not api_key or not base_url:
        raise ValueError("Connection code missing k/u")
    return {"api_key": str(api_key), "base_url": str(base_url).rstrip("/")}


def verify_lara_webhook_signature(
    body: bytes,
    timestamp: str,
    signature_header: str,
    secret: str,
) -> bool:
    """
    Verify X-Webhook-Signature: sha256=<hex> over f\"{timestamp}.\".encode() + body
    """
    if not signature_header or not secret:
        return False
    provided = signature_header.strip()
    if provided.startswith("sha256="):
        provided = provided[7:]
    message = f"{timestamp}.".encode("utf-8") + body
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


class LaraStockAdapter(SupplierAdapter):
    driver_name = "larastock_v1"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        # Auto-decode conn_ paste if provided
        conn = config.get("connection_code") or config.get("conn_code")
        if conn and str(conn).strip().startswith("conn_"):
            try:
                decoded = decode_conn_code(str(conn))
                config = {**config, **decoded}
                self.config = config
            except Exception as e:
                logger.warning(f"LaraStock conn_ decode failed: {e}")

        self.base_url = (config.get("base_url") or "").rstrip("/")
        self.api_key = config.get("api_key") or ""
        self.webhook_secret = config.get("webhook_secret") or self.api_key
        self.timeout = float(config.get("timeout_seconds", 20.0))
        # 30 req/min default
        if "rate_limit_rpm" not in self.config:
            self.config["rate_limit_rpm"] = 30

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    @staticmethod
    def _is_chatgpt_product(product_id: str, meta: Optional[dict] = None) -> bool:
        if product_id.startswith("cgpt_"):
            return True
        source = (meta or {}).get("source") or ""
        return source == "chatgpt_business"

    async def check_health(self) -> HealthStatusDTO:
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(f"{self.base_url}/products", headers=self._headers())
            latency = int((time.perf_counter() - start) * 1000)
            balance = None
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    bres = await client.get(f"{self.base_url}/balance", headers=self._headers())
                if bres.status_code == 200:
                    bd = bres.json()
                    balance = Decimal(str(bd.get("balance") or bd.get("data", {}).get("balance") or 0))
            except Exception:
                pass
            alive = res.status_code == 200
            return HealthStatusDTO(
                is_alive=alive,
                latency_ms=latency,
                http_status=res.status_code,
                balance_usd=balance,
                error_message=None if alive else f"HTTP {res.status_code}",
            )
        except Exception as e:
            return HealthStatusDTO(
                is_alive=False,
                latency_ms=int((time.perf_counter() - start) * 1000),
                error_message=str(e),
            )

    async def fetch_products(self) -> List[SupplierProductDTO]:
        await self.throttle_or_wait()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(f"{self.base_url}/products", headers=self._headers())
            res.raise_for_status()
            payload = res.json()
        items = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            items = payload.get("products") or []
        out: List[SupplierProductDTO] = []
        for item in items:
            pid = str(item.get("id") or item.get("_id") or "")
            if not pid:
                continue
            raw_name = str(
                item.get("name_ar")
                or item.get("name_en")
                or item.get("name")
                or pid
            )
            stock = item.get("stock")
            if stock == "unlimited" or stock is None:
                avail_qty = 9999
                in_stock = True
            else:
                try:
                    avail_qty = int(stock)
                except Exception:
                    avail_qty = 0
                in_stock = avail_qty > 0
            source = item.get("source")
            is_manual = bool(item.get("is_manual"))
            requires_email = self._is_chatgpt_product(pid, {"source": source})
            out.append(SupplierProductDTO(
                supplier_product_id=pid,
                name=raw_name,
                raw_name=raw_name,
                cost=Decimal(str(item.get("your_price") or item.get("price") or "0")),
                is_in_stock=in_stock,
                available_quantity=avail_qty,
                metadata={
                    "raw": item,
                    "source": source,
                    "is_manual": is_manual,
                    "delivery_type": "manual" if is_manual else "instant",
                    "requires_customer_email": requires_email,
                    "desc_ar": item.get("desc_ar"),
                    "desc_en": item.get("desc_en"),
                    "name_ar": item.get("name_ar"),
                    "name_en": item.get("name_en"),
                },
            ))
        return out

    async def get_quote(self, supplier_product_id: str, quantity: int = 1) -> QuoteDTO:
        products = await self.fetch_products()
        for p in products:
            if p.supplier_product_id == supplier_product_id:
                return QuoteDTO(
                    cost=p.cost * quantity,
                    is_available=p.is_in_stock,
                    metadata=p.metadata,
                )
        return QuoteDTO(cost=Decimal("0"), is_available=False)

    async def get_deposit_info(self, network: str = "usdt_bep20", amount: Optional[Decimal] = None) -> DepositInfoDTO:
        return DepositInfoDTO(
            network=network,
            deposit_address=self.config.get("deposit_address") or "",
            min_deposit=Decimal(str(self.config.get("min_deposit", "1.0"))),
            exact_amount=amount,
            window_seconds=int(self.config.get("deposit_window_seconds", 3600)),
        )

    def _parse_order(self, res: httpx.Response, data: Optional[dict] = None) -> SupplierOrderResultDTO:
        if data is None:
            try:
                data = res.json()
            except Exception:
                data = {"raw": res.text}
        order = data.get("data") if isinstance(data.get("data"), dict) else data
        oid = str(order.get("order_id") or order.get("id") or "")
        status_raw = str(order.get("status") or "").lower()

        if res.status_code == 202 or status_raw in (
            "pending_manual", "pending_verification", "pending", "processing"
        ):
            return SupplierOrderResultDTO(
                supplier_order_id=oid or None,
                is_success=True,
                status=STATUS_PENDING_DELIVERY,
                cost_charged=Decimal(str(order.get("total") or order.get("price") or 0)) or None,
                raw_response=data,
            )

        if res.status_code in (200, 201) and status_raw in ("completed", "success", "delivered", ""):
            # ChatGPT confirmation message path
            msg = order.get("message")
            expires = order.get("expires_at")
            codes = order.get("codes") or order.get("delivery") or order.get("items")
            if msg and (expires or self._is_chatgpt_product(str(order.get("product_id") or ""), order)):
                payload = f"{msg}" + (f"\nexpires_at: {expires}" if expires else "")
            else:
                payload = format_delivery_payload(codes) or (str(msg) if msg else None)
            return SupplierOrderResultDTO(
                supplier_order_id=oid or None,
                is_success=True,
                status=STATUS_COMPLETED,
                delivered_payload=payload,
                cost_charged=Decimal(str(order.get("total") or order.get("price") or 0)) or None,
                raw_response=data,
            )

        return SupplierOrderResultDTO(
            supplier_order_id=oid or None,
            is_success=False,
            status=STATUS_FAILED,
            error_message=data.get("message") or f"HTTP {res.status_code}",
            raw_response=data,
        )

    async def place_order(
        self,
        supplier_product_id: str,
        quantity: int,
        idempotency_key: str,
        **kwargs: Any,
    ) -> SupplierOrderResultDTO:
        await self.throttle_or_wait()
        buyer_email = kwargs.get("buyer_email")
        buyer_info = kwargs.get("buyer_info") or {}
        meta = kwargs.get("product_metadata") or {}

        if self._is_chatgpt_product(supplier_product_id, meta):
            if not buyer_email:
                return SupplierOrderResultDTO(
                    is_success=False,
                    status=STATUS_FAILED,
                    error_message="buyer_email required for ChatGPT Business product",
                )
            body = {"product_id": supplier_product_id, "email": buyer_email}
        else:
            body = {
                "product_id": supplier_product_id,
                "qty": quantity,
                "buyer_info": buyer_info or ({"email": buyer_email} if buyer_email else {}),
            }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(
                    f"{self.base_url}/purchase",
                    json=body,
                    headers=self._headers(idempotency_key),
                )
            return self._parse_order(res)
        except Exception as e:
            logger.error(f"LaraStock place_order error: {e}")
            return SupplierOrderResultDTO(
                is_success=False, status="pending", error_message=str(e)
            )

    async def get_order_status(self, supplier_order_id: str) -> SupplierOrderResultDTO:
        await self.throttle_or_wait()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(
                    f"{self.base_url}/order/{supplier_order_id}",
                    headers=self._headers(),
                )
            return self._parse_order(res)
        except Exception as e:
            return SupplierOrderResultDTO(
                supplier_order_id=supplier_order_id,
                is_success=False,
                status="pending",
                error_message=str(e),
            )

    async def lookup_order(self, idempotency_key: str) -> SupplierOrderResultDTO:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(
                    f"{self.base_url}/order/lookup",
                    params={"idempotency_key": idempotency_key},
                    headers=self._headers(),
                )
            if res.status_code == 404:
                return SupplierOrderResultDTO(
                    is_success=False, status="not_found", error_message="not found"
                )
            return self._parse_order(res)
        except Exception as e:
            return SupplierOrderResultDTO(
                is_success=False, status="lookup_error", error_message=str(e)
            )
