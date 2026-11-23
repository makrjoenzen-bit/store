"""Tiger Store adapter — cents-based pricing + async 202 pending delivery."""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from src.core.adapters.base import (
    SupplierAdapter, SupplierProductDTO, QuoteDTO,
    SupplierOrderResultDTO, DepositInfoDTO, HealthStatusDTO,
    STATUS_COMPLETED, STATUS_PENDING_DELIVERY, STATUS_FAILED,
    format_delivery_payload,
)


def cents_to_usd(cents: Any) -> Decimal:
    """Convert integer cents to exact Decimal USD (120 → 1.20)."""
    if cents is None:
        return Decimal("0")
    return (Decimal(str(int(cents))) / Decimal("100")).quantize(Decimal("0.01"))


class TigerAdapter(SupplierAdapter):
    driver_name = "tiger_v1"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_url = (
            config.get("base_url")
            or "https://bot-production-36e9d811.up.railway.app/api/v1"
        ).rstrip("/")
        self.api_key = config.get("api_key") or ""
        self.timeout = float(config.get("timeout_seconds", 20.0))

    def _headers(self, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
        retries: int = 2,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        last_exc = None
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    res = await client.request(
                        method, url,
                        headers=self._headers(idempotency_key),
                        json=json_body,
                        params=params,
                    )
                if res.status_code == 429:
                    wait = int(res.headers.get("Retry-After", "2"))
                    await asyncio.sleep(max(wait, 1))
                    continue
                if res.status_code == 503 and attempt < retries:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                return res
            except Exception as e:
                last_exc = e
                if attempt < retries:
                    await asyncio.sleep(0.8 * (attempt + 1))
        raise last_exc or RuntimeError("Tiger request failed")

    async def check_health(self) -> HealthStatusDTO:
        start = time.perf_counter()
        try:
            res = await self._request("GET", "/products", params={"limit": 1}, retries=1)
            latency = int((time.perf_counter() - start) * 1000)
            balance = None
            # Prefer dedicated balance if present
            try:
                bal_res = await self._request("GET", "/balance", retries=0)
                if bal_res.status_code == 200:
                    data = bal_res.json()
                    if "balance_cents" in data:
                        balance = cents_to_usd(data["balance_cents"])
                    elif "balance" in data:
                        balance = Decimal(str(data["balance"]))
            except Exception:
                pass
            alive = res.status_code in (200, 201)
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
        products: List[SupplierProductDTO] = []
        cursor = None
        for _ in range(100):  # safety cap
            params: Dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            res = await self._request("GET", "/products", params=params)
            res.raise_for_status()
            data = res.json()
            items = data.get("data") or data.get("products") or data.get("items") or []
            if isinstance(data, list):
                items = data
            for item in items:
                price_cents = item.get("price_cents")
                available = item.get("available", True)
                if price_cents is None or available is False:
                    continue
                stock = item.get("stock")
                if stock is None:
                    avail_qty = 9999
                    in_stock = True
                else:
                    avail_qty = int(stock)
                    in_stock = avail_qty > 0
                raw_name = str(item.get("name") or item.get("title") or item.get("id"))
                products.append(SupplierProductDTO(
                    supplier_product_id=str(item.get("id")),
                    name=raw_name,
                    raw_name=raw_name,
                    cost=cents_to_usd(price_cents),
                    is_in_stock=in_stock,
                    available_quantity=avail_qty,
                    metadata={
                        "raw": item,
                        "delivery_type": item.get("delivery_type") or "instant",
                    },
                ))
            cursor = data.get("next_cursor") if isinstance(data, dict) else None
            if not cursor:
                break
        return products

    async def get_quote(self, supplier_product_id: str, quantity: int = 1) -> QuoteDTO:
        products = await self.fetch_products()
        for p in products:
            if p.supplier_product_id == supplier_product_id:
                return QuoteDTO(
                    cost=p.cost * quantity,
                    is_available=p.is_in_stock and (p.available_quantity or 0) >= quantity,
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

    def _parse_order(self, res: httpx.Response) -> SupplierOrderResultDTO:
        try:
            data = res.json()
        except Exception:
            data = {"raw": res.text}
        order = data.get("data") if isinstance(data.get("data"), dict) else data
        oid = str(order.get("id") or order.get("order_id") or "")
        status_raw = str(order.get("status") or "").lower()

        if res.status_code == 202 or status_raw in ("processing", "pending", "pending_delivery"):
            return SupplierOrderResultDTO(
                supplier_order_id=oid or None,
                is_success=True,
                status=STATUS_PENDING_DELIVERY,
                cost_charged=cents_to_usd(order.get("total_cents") or order.get("price_cents")),
                raw_response=data,
            )

        if res.status_code in (200, 201) and status_raw in ("delivered", "completed", "success", ""):
            payload = format_delivery_payload(
                order.get("delivery") or order.get("items") or order.get("codes")
            )
            if payload or status_raw in ("delivered", "completed", "success"):
                return SupplierOrderResultDTO(
                    supplier_order_id=oid or None,
                    is_success=True,
                    status=STATUS_COMPLETED,
                    delivered_payload=payload,
                    cost_charged=cents_to_usd(order.get("total_cents") or order.get("price_cents")),
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
        try:
            res = await self._request(
                "POST",
                "/orders",
                json_body={"product_id": supplier_product_id, "quantity": quantity},
                idempotency_key=idempotency_key,
            )
            return self._parse_order(res)
        except Exception as e:
            logger.error(f"Tiger place_order error: {e}")
            return SupplierOrderResultDTO(
                is_success=False, status="pending", error_message=str(e)
            )

    async def get_order_status(self, supplier_order_id: str) -> SupplierOrderResultDTO:
        await self.throttle_or_wait()
        try:
            res = await self._request("GET", f"/orders/{supplier_order_id}")
            return self._parse_order(res)
        except Exception as e:
            return SupplierOrderResultDTO(
                supplier_order_id=supplier_order_id,
                is_success=False,
                status="pending",
                error_message=str(e),
            )

    async def lookup_order(self, idempotency_key: str) -> SupplierOrderResultDTO:
        # Tiger recovers via Idempotency-Key replay on POST; try lookup endpoint if exists
        try:
            res = await self._request(
                "GET", "/orders", params={"idempotency_key": idempotency_key}
            )
            if res.status_code == 200:
                return self._parse_order(res)
        except Exception:
            pass
        return SupplierOrderResultDTO(
            is_success=False, status="not_found", error_message="Order not found"
        )
