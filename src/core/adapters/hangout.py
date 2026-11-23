"""Hangout adapter — max_total protection + manual PENDING_DELIVERY fulfillment."""
from __future__ import annotations

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


class HangoutAdapter(SupplierAdapter):
    driver_name = "hangout_v1"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_url = (
            config.get("base_url") or "https://hangoutapi.tg-tools.shop/api/v1"
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

    async def check_health(self) -> HealthStatusDTO:
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(f"{self.base_url}/balance", headers=self._headers())
            latency = int((time.perf_counter() - start) * 1000)
            if res.status_code == 200:
                data = res.json()
                balance = Decimal(str(
                    data.get("balance")
                    or data.get("data", {}).get("balance")
                    or 0
                ))
                return HealthStatusDTO(
                    is_alive=True,
                    latency_ms=latency,
                    http_status=200,
                    balance_usd=balance,
                )
            return HealthStatusDTO(
                is_alive=False,
                latency_ms=latency,
                http_status=res.status_code,
                error_message=f"HTTP {res.status_code}",
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
        items = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
        if not isinstance(items, list):
            items = payload.get("products") or []
        out = []
        for item in items:
            pid = str(item.get("id") or item.get("product_id"))
            raw_name = str(item.get("name") or item.get("title") or pid)
            stock = item.get("stock")
            is_manual = bool(item.get("is_manual") or item.get("manual_fulfillment"))
            avail = None
            if stock == "unlimited" or stock is None:
                avail = 9999 if not is_manual else None
                in_stock = True
            else:
                avail = int(stock)
                in_stock = avail > 0
            out.append(SupplierProductDTO(
                supplier_product_id=pid,
                name=raw_name,
                raw_name=raw_name,
                cost=Decimal(str(item.get("price") or item.get("cost") or 0)),
                is_in_stock=in_stock,
                available_quantity=avail,
                metadata={
                    "raw": item,
                    "is_manual": is_manual,
                    "delivery_type": "manual" if is_manual else "instant",
                },
            ))
        return out

    async def get_quote(self, supplier_product_id: str, quantity: int = 1) -> QuoteDTO:
        products = await self.fetch_products()
        for p in products:
            if p.supplier_product_id == supplier_product_id:
                return QuoteDTO(cost=p.cost * quantity, is_available=p.is_in_stock)
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

        # Accepted / manual fulfillment — NEVER treat as failure
        if res.status_code in (200, 201, 202) and status_raw in (
            "pending", "pending_delivery", "manual", "processing", "accepted", ""
        ):
            payload = format_delivery_payload(
                order.get("delivery") or order.get("codes") or order.get("items")
            )
            if payload and status_raw in ("completed", "delivered", "success"):
                return SupplierOrderResultDTO(
                    supplier_order_id=oid or None,
                    is_success=True,
                    status=STATUS_COMPLETED,
                    delivered_payload=payload,
                    cost_charged=Decimal(str(order.get("total") or order.get("price") or 0)) or None,
                    raw_response=data,
                )
            # Default: pending delivery for Hangout accepted purchases
            if status_raw in ("completed", "delivered", "success") and payload:
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
                is_success=True,
                status=STATUS_PENDING_DELIVERY,
                cost_charged=Decimal(str(order.get("total") or order.get("price") or 0)) or None,
                raw_response=data,
            )

        if status_raw in ("completed", "delivered", "success"):
            return SupplierOrderResultDTO(
                supplier_order_id=oid or None,
                is_success=True,
                status=STATUS_COMPLETED,
                delivered_payload=format_delivery_payload(
                    order.get("delivery") or order.get("codes") or order.get("items")
                ),
                cost_charged=Decimal(str(order.get("total") or order.get("price") or 0)) or None,
                raw_response=data,
            )

        if res.status_code in (200, 201) and format_delivery_payload(
            order.get("delivery") or order.get("codes")
        ):
            return SupplierOrderResultDTO(
                supplier_order_id=oid or None,
                is_success=True,
                status=STATUS_COMPLETED,
                delivered_payload=format_delivery_payload(
                    order.get("delivery") or order.get("codes")
                ),
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
        max_total = kwargs.get("max_total")
        body: Dict[str, Any] = {
            "product_id": supplier_product_id,
            "quantity": quantity,
        }
        if max_total is not None:
            body["max_total"] = float(max_total)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(
                    f"{self.base_url}/purchase",
                    json=body,
                    headers=self._headers(idempotency_key),
                )
            return self._parse_order(res)
        except Exception as e:
            logger.error(f"Hangout place_order error: {e}")
            return SupplierOrderResultDTO(
                is_success=False, status="pending", error_message=str(e)
            )

    async def get_order_status(self, supplier_order_id: str) -> SupplierOrderResultDTO:
        await self.throttle_or_wait()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(
                    f"{self.base_url}/orders/{supplier_order_id}",
                    headers=self._headers(),
                )
            # Completed path for poller
            try:
                data = res.json()
            except Exception:
                data = {}
            order = data.get("data") if isinstance(data.get("data"), dict) else data
            status_raw = str(order.get("status") or "").lower()
            if status_raw in ("completed", "delivered", "success"):
                return SupplierOrderResultDTO(
                    supplier_order_id=supplier_order_id,
                    is_success=True,
                    status=STATUS_COMPLETED,
                    delivered_payload=format_delivery_payload(
                        order.get("delivery") or order.get("codes") or order.get("items")
                    ),
                    raw_response=data,
                )
            if status_raw in ("failed", "refunded", "cancelled"):
                return SupplierOrderResultDTO(
                    supplier_order_id=supplier_order_id,
                    is_success=False,
                    status=STATUS_FAILED,
                    error_message=order.get("message") or status_raw,
                    raw_response=data,
                )
            return SupplierOrderResultDTO(
                supplier_order_id=supplier_order_id,
                is_success=True,
                status=STATUS_PENDING_DELIVERY,
                raw_response=data,
            )
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
                    f"{self.base_url}/orders",
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
