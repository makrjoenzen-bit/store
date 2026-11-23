import re
import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, model_validator
from decimal import Decimal
from datetime import datetime


# Canonical adapter status values
STATUS_COMPLETED = "completed"
STATUS_PENDING_DELIVERY = "PENDING_DELIVERY"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"  # network timeout / recovery path


class SupplierProductDTO(BaseModel):
    supplier_product_id: str
    name: str
    cost: Decimal
    is_in_stock: bool
    currency: str = "USD"
    metadata: Dict[str, Any] = {}
    supported_deposit_networks: List[str] = []
    available_quantity: Optional[int] = None
    raw_name: str = ""

    @model_validator(mode='before')
    @classmethod
    def strip_emoji_and_normalize_name(cls, values: Any) -> Any:
        if isinstance(values, dict):
            name = values.get("name")
            if name and isinstance(name, str):
                # Store original name before normalization
                if not values.get("raw_name"):
                    values["raw_name"] = name
                # Strip supplementary-plane emoji (U+10000–U+10FFFF)
                name = re.sub(r'[\U00010000-\U0010ffff]', '', name)
                # Strip common symbols, dingbats, and misc emoji in BMP
                name = re.sub(
                    r'[\u2600-\u27BF\u2B50\u2764\u231A-\u23FF'
                    r'\uFE00-\uFEFF\u200D\u20E3\u2702-\u27B0\u2600-\u26FF]',
                    '', name,
                )
                # Collapse whitespace and strip
                name = re.sub(r'\s+', ' ', name).strip()
                values["name"] = name
        return values


class QuoteDTO(BaseModel):
    quote_id: Optional[str] = None
    cost: Decimal
    currency: str = "USD"
    expires_at: Optional[datetime] = None
    is_available: bool = True
    metadata: Dict[str, Any] = {}


class SupplierOrderResultDTO(BaseModel):
    supplier_order_id: Optional[str] = None
    is_success: bool
    status: str  # completed | PENDING_DELIVERY | failed | pending | not_found
    delivered_payload: Optional[str] = None  # License key, credentials, or file link
    cost_charged: Optional[Decimal] = None
    error_message: Optional[str] = None
    raw_response: Dict[str, Any] = {}


class DepositInfoDTO(BaseModel):
    network: str
    deposit_address: str
    min_deposit: Decimal
    fee_usd: Decimal = Decimal("0.0")
    window_seconds: int = 300  # Default 5 minutes
    memo_or_tag: Optional[str] = None
    deposit_id: Optional[str] = None
    exact_amount: Optional[Decimal] = None


class HealthStatusDTO(BaseModel):
    is_alive: bool
    latency_ms: int
    http_status: Optional[int] = None
    balance_usd: Optional[Decimal] = None
    error_message: Optional[str] = None


def format_delivery_payload(value: Any) -> Optional[str]:
    """Normalize supplier delivery arrays/dicts into a single plaintext payload."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(
                    item.get("code")
                    or item.get("key")
                    or item.get("content")
                    or item.get("credentials")
                    or item.get("account")
                    or str(item)
                )
            else:
                parts.append(str(item))
        joined = "\n".join(p for p in parts if p)
        return joined or None
    if isinstance(value, dict):
        return (
            value.get("code")
            or value.get("key")
            or value.get("content")
            or value.get("message")
            or str(value)
        )
    return str(value)


class SupplierAdapter(ABC):
    """Abstract base class for all upstream supplier integrations.

    Adapters MUST strip all upstream UI elements (emojis, button labels,
    decorative text) from returned data. Only raw normalized product
    identifiers, wholesale costs, and stock statuses should be returned.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    async def throttle_or_wait(self):
        """Enforces per-supplier Token Bucket rate limiting via Redis before outbound calls."""
        from src.core.redis import event_bus
        supplier_id = str(self.config.get("supplier_id") or self.driver_name)
        rpm = int(self.config.get("rate_limit_rpm", 60))
        allowed = await event_bus.check_and_consume_supplier_quota(supplier_id, rate_limit_rpm=rpm)
        if not allowed:
            await asyncio.sleep(1.2)

    @property
    @abstractmethod
    def driver_name(self) -> str:
        """Returns the unique driver code (e.g. 'ventebot_v1')."""
        pass

    # ---- Phase 15 convenience aliases (dashboard / docs naming) ----
    async def test_connection(self) -> HealthStatusDTO:
        return await self.check_health()

    async def get_balance(self) -> Optional[Decimal]:
        health = await self.check_health()
        return health.balance_usd

    async def fetch_catalog(self) -> List[SupplierProductDTO]:
        return await self.fetch_products()

    @abstractmethod
    async def check_health(self) -> HealthStatusDTO:
        """Pings the supplier API, checks latency and wallet balance."""
        pass

    @abstractmethod
    async def fetch_products(self) -> List[SupplierProductDTO]:
        """Pulls the latest catalog of products with their current wholesale costs."""
        pass

    @abstractmethod
    async def get_quote(self, supplier_product_id: str, quantity: int = 1) -> QuoteDTO:
        """Requests a real-time price quote before locking an order."""
        pass

    @abstractmethod
    async def get_deposit_info(self, network: str = "usdt_bep20", amount: Optional[Decimal] = None) -> DepositInfoDTO:
        """Generates or retrieves a live JIT deposit address to fund the supplier."""
        pass

    @abstractmethod
    async def place_order(
        self,
        supplier_product_id: str,
        quantity: int,
        idempotency_key: str,
        **kwargs: Any,
    ) -> SupplierOrderResultDTO:
        """
        Executes the purchase on the supplier API.
        Optional kwargs (adapter-specific):
          buyer_email, activation_identifier, selling_price, max_total, buyer_info
        """
        pass

    @abstractmethod
    async def lookup_order(self, idempotency_key: str) -> SupplierOrderResultDTO:
        """Recovers order state after network timeouts without double-charging."""
        pass

    async def get_order_status(self, supplier_order_id: str) -> SupplierOrderResultDTO:
        """
        Polls an existing supplier order by its external id.
        Default: not implemented — adapters with async fulfillment MUST override.
        """
        return SupplierOrderResultDTO(
            supplier_order_id=supplier_order_id,
            is_success=False,
            status="not_found",
            error_message="get_order_status not implemented for this adapter",
        )
