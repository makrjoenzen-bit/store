"""Tesla Tools adapter — X-API-Key + selling_price price-protection."""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from src.core.adapters.base import (
    SupplierAdapter, SupplierProductDTO, QuoteDTO,
    SupplierOrderResultDTO, DepositInfoDTO, HealthStatusDTO,
    STATUS_COMPLETED, STATUS_FAILED, format_delivery_payload,
)


class TeslaToolsAdapter(SupplierAdapter):
    driver_name = "teslatools_v1"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_url = (
            config.get("base_url") or "https://bunnytoolsbot.tech/api/v1"
        ).rstrip("/")
        self.api_key = config.get("api_key") or ""
        self.timeout = float(config.get("timeout_seconds", 20.0))

    def _headers(self) -> Dict[str, str]:
        return {
            "X-API-Key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def check_health(self) -> HealthStatusDTO:
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(f"{self.base_url}/profile", headers=self._headers())
            latency = int((time.perf_counter() - start) * 1000)
            if res.status_code == 200:
                data = res.json()
                merchant = data.get("merchant") or data.get("data", {}).get("merchant") or data
                balance = Decimal(str(merchant.get("balance", 0)))
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
            pid = str(item.get("id"))
            raw_name = str(item.get("name") or pid)
            stock = item.get("stock_count")
            avail = int(stock) if stock is not None else None
            out.append(SupplierProductDTO(
                supplier_product_id=pid,
                name=raw_name,
                raw_name=raw_name,
                cost=Decimal(str(item.get("price") or 0)),
                is_in_stock=avail is None or avail > 0,
                available_quantity=avail,
                metadata={
                    "raw": item,
                    "warranty_info": item.get("warranty_info"),
                    "delivery_type": "instant",
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

    async def place_order(
        self,
        supplier_product_id: str,
        quantity: int,
        idempotency_key: str,
        **kwargs: Any,
    ) -> SupplierOrderResultDTO:
        await self.throttle_or_wait()
        # CRITICAL: selling_price must be our retail so Tesla Price Protection passes
        selling_price = kwargs.get("selling_price")
        if selling_price is None:
            selling_price = kwargs.get("retail_price")
        body = {
            "product_id": int(supplier_product_id) if str(supplier_product_id).isdigit() else supplier_product_id,
            "quantity": quantity,
            "selling_price": float(selling_price) if selling_price is not None else None,
            "idempotency_key": idempotency_key,
        }
        # Drop None selling_price only if truly missing — still send when 0
        if body["selling_price"] is None:
            del body["selling_price"]

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(
                    f"{self.base_url}/order/create",
                    json=body,
                    headers=self._headers(),
                )
            data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
            order = data.get("order") or data.get("data") or data
            if res.status_code in (200, 201):
                delivered = (
                    order.get("delivered_item")
                    or order.get("delivery")
                    or order.get("content")
                )
                return SupplierOrderResultDTO(
                    supplier_order_id=str(order.get("id") or order.get("order_id") or ""),
                    is_success=True,
                    status=STATUS_COMPLETED,
                    delivered_payload=format_delivery_payload(delivered),
                    cost_charged=Decimal(str(order.get("price") or order.get("total") or 0)) or None,
                    raw_response=data,
                )
            return SupplierOrderResultDTO(
                is_success=False,
                status=STATUS_FAILED,
                error_message=data.get("message") or f"HTTP {res.status_code}: {res.text}",
                raw_response=data,
            )
        except Exception as e:
            logger.error(f"TeslaTools place_order error: {e}")
            return SupplierOrderResultDTO(
                is_success=False, status="pending", error_message=str(e)
            )

    async def get_order_status(self, supplier_order_id: str) -> SupplierOrderResultDTO:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(
                    f"{self.base_url}/order/{supplier_order_id}",
                    headers=self._headers(),
                )
            if res.status_code != 200:
                return SupplierOrderResultDTO(
                    supplier_order_id=supplier_order_id,
                    is_success=False,
                    status="not_found",
                    error_message=f"HTTP {res.status_code}",
                )
            data = res.json()
            order = data.get("order") or data.get("data") or data
            return SupplierOrderResultDTO(
                supplier_order_id=supplier_order_id,
                is_success=True,
                status=STATUS_COMPLETED,
                delivered_payload=format_delivery_payload(
                    order.get("delivered_item") or order.get("delivery")
                ),
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
                    f"{self.base_url}/order/lookup",
                    params={"idempotency_key": idempotency_key},
                    headers=self._headers(),
                )
            if res.status_code == 404:
                return SupplierOrderResultDTO(
                    is_success=False, status="not_found", error_message="not found"
                )
            data = res.json()
            order = data.get("order") or data.get("data") or data
            return SupplierOrderResultDTO(
                supplier_order_id=str(order.get("id") or ""),
                is_success=True,
                status=STATUS_COMPLETED,
                delivered_payload=format_delivery_payload(order.get("delivered_item")),
                raw_response=data,
            )
        except Exception as e:
            return SupplierOrderResultDTO(
                is_success=False, status="lookup_error", error_message=str(e)
            )
