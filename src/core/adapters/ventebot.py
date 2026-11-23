"""VenteBot API v1.2.0 — X-Reseller-Key, lookup recovery, JIT BEP-20 deposits."""
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


class VenteBotAdapter(SupplierAdapter):
    driver_name = "ventebot_v1"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.base_url = (
            config.get("base_url")
            or "https://web-production-1c7b1.up.railway.app/api/reseller"
        ).rstrip("/")
        self.reseller_key = config.get("api_key") or config.get("reseller_key", "")
        self.timeout = float(config.get("timeout_seconds", 15.0))

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Reseller-Key": self.reseller_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def check_health(self) -> HealthStatusDTO:
        start_time = time.perf_counter()
        url = f"{self.base_url}/me"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(url, headers=self._headers())
                latency_ms = int((time.perf_counter() - start_time) * 1000)

                if res.status_code == 200:
                    data = res.json().get("data", res.json())
                    wallet = data.get("wallet") or {}
                    balance = Decimal(str(wallet.get("balance", data.get("balance", "0.0"))))
                    return HealthStatusDTO(
                        is_alive=True,
                        latency_ms=latency_ms,
                        http_status=res.status_code,
                        balance_usd=balance,
                    )
                return HealthStatusDTO(
                    is_alive=False,
                    latency_ms=latency_ms,
                    http_status=res.status_code,
                    error_message=f"HTTP {res.status_code}: {res.text}",
                )
        except Exception as e:
            latency_ms = int((time.perf_counter() - start_time) * 1000)
            return HealthStatusDTO(
                is_alive=False,
                latency_ms=latency_ms,
                error_message=str(e),
            )

    async def fetch_products(self) -> List[SupplierProductDTO]:
        await self.throttle_or_wait()
        url = f"{self.base_url}/products"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(url, headers=self._headers())
            res.raise_for_status()
            payload = res.json()
            raw_list = payload.get("data", []) if isinstance(payload, dict) else payload

            products = []
            for item in raw_list:
                raw_name = str(item.get("name", ""))
                stock_val = item.get("stock")
                avail_qty = int(stock_val) if isinstance(stock_val, (int, float)) and stock_val is not None else None
                requires_activation = bool(
                    item.get("requires_activation_identifier")
                    or item.get("activation_required")
                )
                products.append(
                    SupplierProductDTO(
                        supplier_product_id=str(item.get("id")),
                        name=raw_name,
                        raw_name=raw_name,
                        cost=Decimal(str(item.get("price", "0.0"))),
                        is_in_stock=bool(item.get("in_stock", True) and (stock_val is None or stock_val > 0)),
                        available_quantity=avail_qty,
                        currency=str(item.get("currency", "USD")),
                        supported_deposit_networks=["usdt_bep20"],
                        metadata={
                            "raw": item,
                            "requires_customer_email": requires_activation,
                            "delivery_type": "instant",
                        },
                    )
                )
            return products

    async def get_quote(self, supplier_product_id: str, quantity: int = 1) -> QuoteDTO:
        await self.throttle_or_wait()
        url = f"{self.base_url}/quote"
        payload = {
            "product_id": int(supplier_product_id) if str(supplier_product_id).isdigit() else supplier_product_id,
            "quantity": quantity,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(url, json=payload, headers=self._headers())
            res.raise_for_status()
            data = res.json().get("data", res.json())
            return QuoteDTO(
                quote_id=str(data.get("quote_id", "")),
                cost=Decimal(str(data.get("total_cost", data.get("price", "0.0")))),
                is_available=bool(data.get("is_available", True)),
                metadata=data,
            )

    async def list_deposit_methods(self) -> List[Dict[str, Any]]:
        await self.throttle_or_wait()
        url = f"{self.base_url}/wallet/deposit-methods"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(url, headers=self._headers())
            res.raise_for_status()
            data = res.json()
            return data.get("data", data) if isinstance(data, dict) else data

    async def get_deposit_info(self, network: str = "usdt_bep20", amount: Optional[Decimal] = None) -> DepositInfoDTO:
        await self.throttle_or_wait()
        url = f"{self.base_url}/wallet/deposits"
        payload = {
            "method": "USDTBSC",
            "amount": float(amount) if amount else 0.10,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.post(url, json=payload, headers=self._headers())
            res.raise_for_status()
            data = res.json().get("data", res.json())

            return DepositInfoDTO(
                network="usdt_bep20",
                deposit_address=data.get("address") or data.get("deposit_address"),
                min_deposit=Decimal(str(data.get("min_amount", "0.0713"))),
                exact_amount=Decimal(str(data.get("amount", amount))) if amount is not None else None,
                fee_usd=Decimal(str(data.get("fee", "0.0"))),
                window_seconds=int(data.get("expires_in_seconds", 300)),
                deposit_id=str(data.get("deposit_id", "")),
            )

    async def get_deposit_status(self, deposit_id: str) -> Dict[str, Any]:
        await self.throttle_or_wait()
        url = f"{self.base_url}/wallet/deposits/{deposit_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(url, headers=self._headers())
            res.raise_for_status()
            return res.json().get("data", res.json())

    def _map_order_payload(self, order_data: dict, http_ok: bool = True) -> SupplierOrderResultDTO:
        status_raw = str(order_data.get("status") or "").upper()
        oid = str(order_data.get("id") or order_data.get("order_id") or "")
        items = order_data.get("items") or order_data.get("content") or order_data.get("key")
        cost = order_data.get("price") or order_data.get("total_cost") or order_data.get("total")

        if status_raw in ("COMPLETED", "DELIVERED", "SUCCESS"):
            return SupplierOrderResultDTO(
                supplier_order_id=oid or None,
                is_success=True,
                status=STATUS_COMPLETED,
                delivered_payload=format_delivery_payload(items),
                cost_charged=Decimal(str(cost or 0)) if cost is not None else None,
                raw_response=order_data,
            )
        if status_raw in ("PAID_PENDING_DELIVERY", "PENDING_DELIVERY", "PROCESSING", "PENDING"):
            return SupplierOrderResultDTO(
                supplier_order_id=oid or None,
                is_success=True,
                status=STATUS_PENDING_DELIVERY,
                cost_charged=Decimal(str(cost or 0)) if cost is not None else None,
                raw_response=order_data,
            )
        if status_raw in ("FAILED", "REFUNDED", "CANCELLED"):
            return SupplierOrderResultDTO(
                supplier_order_id=oid or None,
                is_success=False,
                status=STATUS_FAILED,
                error_message=order_data.get("message") or status_raw,
                raw_response=order_data,
            )
        # HTTP success without explicit status — try deliver items
        if http_ok and items:
            return SupplierOrderResultDTO(
                supplier_order_id=oid or None,
                is_success=True,
                status=STATUS_COMPLETED,
                delivered_payload=format_delivery_payload(items),
                cost_charged=Decimal(str(cost or 0)) if cost is not None else None,
                raw_response=order_data,
            )
        return SupplierOrderResultDTO(
            supplier_order_id=oid or None,
            is_success=False,
            status=STATUS_FAILED,
            error_message=order_data.get("message") or f"Unknown status {status_raw}",
            raw_response=order_data,
        )

    async def place_order(
        self,
        supplier_product_id: str,
        quantity: int,
        idempotency_key: str,
        **kwargs: Any,
    ) -> SupplierOrderResultDTO:
        await self.throttle_or_wait()
        url = f"{self.base_url}/orders"
        payload: Dict[str, Any] = {
            "product_id": int(supplier_product_id) if str(supplier_product_id).isdigit() else supplier_product_id,
            "quantity": quantity,
            "idempotency_key": idempotency_key,
        }
        activation = kwargs.get("activation_identifier") or kwargs.get("buyer_email")
        if activation:
            payload["activation_identifier"] = activation

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(url, json=payload, headers=self._headers())
                res_json = res.json()
                if res.status_code in (200, 201, 202):
                    order_data = res_json.get("data", res_json)
                    return self._map_order_payload(order_data, http_ok=True)
                return SupplierOrderResultDTO(
                    is_success=False,
                    status=STATUS_FAILED,
                    error_message=res_json.get("message", f"HTTP {res.status_code}: {res.text}"),
                    raw_response=res_json,
                )
        except Exception as e:
            logger.error(f"VenteBot place_order error for {idempotency_key}: {e}")
            return SupplierOrderResultDTO(
                is_success=False,
                status="pending",
                error_message=str(e),
            )

    async def get_order_status(self, supplier_order_id: str) -> SupplierOrderResultDTO:
        await self.throttle_or_wait()
        url = f"{self.base_url}/orders/{supplier_order_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(url, headers=self._headers())
            if res.status_code != 200:
                return SupplierOrderResultDTO(
                    supplier_order_id=supplier_order_id,
                    is_success=False,
                    status="not_found",
                    error_message=f"HTTP {res.status_code}",
                )
            order_data = res.json().get("data", res.json())
            return self._map_order_payload(order_data, http_ok=True)
        except Exception as e:
            return SupplierOrderResultDTO(
                supplier_order_id=supplier_order_id,
                is_success=False,
                status="pending",
                error_message=str(e),
            )

    async def lookup_order(self, idempotency_key: str) -> SupplierOrderResultDTO:
        url = f"{self.base_url}/orders/lookup"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(
                    url, params={"idempotency_key": idempotency_key}, headers=self._headers()
                )
                if res.status_code == 200:
                    order_data = res.json().get("data", res.json())
                    return self._map_order_payload(order_data, http_ok=True)
                elif res.status_code == 404:
                    return SupplierOrderResultDTO(
                        is_success=False,
                        status="not_found",
                        error_message="Order not found for idempotency key",
                    )
                else:
                    return SupplierOrderResultDTO(
                        is_success=False,
                        status=STATUS_FAILED,
                        error_message=f"HTTP {res.status_code}: {res.text}",
                    )
        except Exception as e:
            return SupplierOrderResultDTO(
                is_success=False,
                status="lookup_error",
                error_message=str(e),
            )
