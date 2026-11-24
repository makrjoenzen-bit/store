import time
from typing import List, Optional, Dict, Any
from decimal import Decimal
import httpx
from loguru import logger

from src.core.adapters.base import (
    SupplierAdapter, SupplierProductDTO, QuoteDTO,
    SupplierOrderResultDTO, DepositInfoDTO, HealthStatusDTO
)


class GenericRESTAdapter(SupplierAdapter):
    """
    Highly flexible adapter for standard JSON REST suppliers.
    Configurable paths, headers, and mapping parameters.
    """

    driver_name = "generic_rest"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        from src.core.ssrf import validate_outbound_url

        raw_base = (config.get("base_url") or "").rstrip("/")
        self.base_url = validate_outbound_url(raw_base) if raw_base else ""
        self.headers = config.get("headers", {})
        self.api_key = config.get("api_key")
        self.api_key_header = config.get("api_key_header", "Authorization")
        self.api_key_prefix = config.get("api_key_prefix", "Bearer ")
        self.timeout = float(config.get("timeout_seconds", 15.0))

        # Endpoint mappings
        self.products_endpoint = config.get("products_endpoint", "/products")
        self.orders_endpoint = config.get("orders_endpoint", "/orders")
        self.balance_endpoint = config.get("balance_endpoint", "/balance")

    def _get_headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        h.update(self.headers)
        if self.api_key:
            h[self.api_key_header] = f"{self.api_key_prefix}{self.api_key}".strip()
        return h

    def _safe_url(self, path: str) -> str:
        from src.core.ssrf import validate_outbound_url
        return validate_outbound_url(f"{self.base_url}{path}")

    async def check_health(self) -> HealthStatusDTO:
        start_time = time.perf_counter()
        url = self._safe_url(self.balance_endpoint or self.products_endpoint)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(url, headers=self._get_headers())
                latency = int((time.perf_counter() - start_time) * 1000)
                is_alive = res.status_code in (200, 201)
                balance = None
                if is_alive and "balance" in res.text.lower():
                    try:
                        data = res.json()
                        val = data.get("balance") or data.get("data", {}).get("balance")
                        if val is not None:
                            balance = Decimal(str(val))
                    except Exception:
                        pass

                return HealthStatusDTO(
                    is_alive=is_alive,
                    latency_ms=latency,
                    http_status=res.status_code,
                    balance_usd=balance,
                    error_message=None if is_alive else f"HTTP {res.status_code}",
                )
        except Exception as e:
            return HealthStatusDTO(
                is_alive=False,
                latency_ms=int((time.perf_counter() - start_time) * 1000),
                error_message=str(e),
            )

    async def fetch_products(self) -> List[SupplierProductDTO]:
        url = self._safe_url(self.products_endpoint)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            res = await client.get(url, headers=self._get_headers())
            res.raise_for_status()
            data = res.json()
            items = data.get("data") if isinstance(data, dict) and "data" in data else data
            if not isinstance(items, list):
                items = []

            results = []
            supported_nets = self.config.get("supported_deposit_networks", ["usdt_bep20", "usdt_trc20"])
            for item in items:
                p_id = str(item.get("id") or item.get("sku") or item.get("product_id"))
                raw_name = str(item.get("name") or item.get("title") or p_id)
                cost = Decimal(str(item.get("price") or item.get("cost") or "0.0"))
                stock_val = item.get("stock")
                avail_qty = int(stock_val) if isinstance(stock_val, (int, float)) and stock_val is not None else None
                stock = bool(item.get("in_stock", True) and (stock_val is None or stock_val > 0))
                results.append(
                    SupplierProductDTO(
                        supplier_product_id=p_id,
                        name=raw_name,
                        raw_name=raw_name,
                        cost=cost,
                        is_in_stock=stock,
                        available_quantity=avail_qty,
                        currency=str(item.get("currency", "USD")),
                        supported_deposit_networks=supported_nets,
                        metadata={"raw": item},
                    )
                )
            return results

    async def get_quote(self, supplier_product_id: str, quantity: int = 1) -> QuoteDTO:
        # If no dedicated quote endpoint, fallback to current fetched cost
        products = await self.fetch_products()
        for p in products:
            if p.supplier_product_id == supplier_product_id:
                return QuoteDTO(
                    cost=p.cost * quantity,
                    is_available=p.is_in_stock,
                )
        return QuoteDTO(cost=Decimal("0.0"), is_available=False)

    async def get_deposit_info(self, network: str = "usdt_bep20", amount: Optional[Decimal] = None) -> DepositInfoDTO:
        # Generic deposit info from stored supplier config
        deposit_addr = self.config.get("deposit_address", "")
        min_dep = Decimal(str(self.config.get("min_deposit", "1.0")))
        return DepositInfoDTO(
            network=network,
            deposit_address=deposit_addr,
            min_deposit=min_dep,
            exact_amount=amount,
            fee_usd=Decimal("0.0"),
            window_seconds=int(self.config.get("deposit_window_seconds", 3600)),
        )

    async def place_order(
        self,
        supplier_product_id: str,
        quantity: int,
        idempotency_key: str,
        **kwargs,
    ) -> SupplierOrderResultDTO:
        url = self._safe_url(self.orders_endpoint)
        payload = {
            "product_id": supplier_product_id,
            "quantity": quantity,
            "idempotency_key": idempotency_key,
        }
        if kwargs.get("buyer_email"):
            payload["email"] = kwargs["buyer_email"]
        if kwargs.get("activation_identifier"):
            payload["activation_identifier"] = kwargs["activation_identifier"]
        if kwargs.get("max_total") is not None:
            payload["max_total"] = float(kwargs["max_total"])
        if kwargs.get("selling_price") is not None:
            payload["selling_price"] = float(kwargs["selling_price"])
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.post(url, json=payload, headers=self._get_headers())
                data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {"raw": res.text}
                
                if res.status_code in (200, 201):
                    key_val = (
                        data.get("key") or data.get("license") or 
                        data.get("credentials") or data.get("delivered") or
                        str(data.get("data", ""))
                    )
                    return SupplierOrderResultDTO(
                        supplier_order_id=str(data.get("order_id") or data.get("id")),
                        is_success=True,
                        status="completed",
                        delivered_payload=str(key_val),
                        cost_charged=Decimal(str(data.get("cost") or data.get("price") or "0.0")),
                        raw_response=data,
                    )
                return SupplierOrderResultDTO(
                    is_success=False,
                    status="failed",
                    error_message=f"HTTP {res.status_code}: {res.text}",
                    raw_response=data,
                )
        except Exception as e:
            return SupplierOrderResultDTO(
                is_success=False,
                status="pending",
                error_message=str(e),
            )

    async def lookup_order(self, idempotency_key: str) -> SupplierOrderResultDTO:
        url = self._safe_url(self.orders_endpoint)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                res = await client.get(url, params={"idempotency_key": idempotency_key}, headers=self._get_headers())
                if res.status_code == 200:
                    data = res.json()
                    return SupplierOrderResultDTO(
                        is_success=True,
                        status="completed",
                        delivered_payload=str(data.get("delivered") or data.get("key")),
                        raw_response=data,
                    )
                return SupplierOrderResultDTO(is_success=False, status="not_found")
        except Exception as e:
            return SupplierOrderResultDTO(is_success=False, status="error", error_message=str(e))
