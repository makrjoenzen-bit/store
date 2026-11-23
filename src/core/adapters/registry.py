import json
from typing import Dict, Type, Any
from decimal import Decimal

from src.core.adapters.base import SupplierAdapter
from src.core.adapters.ventebot import VenteBotAdapter
from src.core.adapters.generic_rest import GenericRESTAdapter
from src.core.adapters.tiger import TigerAdapter
from src.core.adapters.larastock import LaraStockAdapter, decode_conn_code
from src.core.adapters.teslatools import TeslaToolsAdapter
from src.core.adapters.hangout import HangoutAdapter
from src.core.security import decrypt_secret
from src.core.models.entities import Supplier


# Official base URLs for dashboard presets
SUPPLIER_PRESETS: Dict[str, Dict[str, Any]] = {
    "tiger_v1": {
        "label": "Tiger Store",
        "base_url": "https://bot-production-36e9d811.up.railway.app/api/v1",
        "auth_hint": "Bearer API Key",
    },
    "ventebot_v1": {
        "label": "VenteBot",
        "base_url": "https://web-production-1c7b1.up.railway.app/api/reseller",
        "auth_hint": "X-Reseller-Key",
    },
    "larastock_v1": {
        "label": "Lara Stock",
        "base_url": "",
        "auth_hint": "Bearer API Key أو لصق conn_...",
        "supports_conn_code": True,
    },
    "teslatools_v1": {
        "label": "Tesla Tools",
        "base_url": "https://bunnytoolsbot.tech/api/v1",
        "auth_hint": "X-API-Key",
    },
    "hangout_v1": {
        "label": "Hangout",
        "base_url": "https://hangoutapi.tg-tools.shop/api/v1",
        "auth_hint": "Bearer API Key",
    },
    "generic_rest": {
        "label": "Generic REST",
        "base_url": "https://api.example.com/v1",
        "auth_hint": "Bearer / custom header",
    },
}


class AdapterRegistry:
    """Factory registry for creating and managing supplier adapter instances."""

    _drivers: Dict[str, Type[SupplierAdapter]] = {
        "tiger_v1": TigerAdapter,
        "ventebot_v1": VenteBotAdapter,
        "larastock_v1": LaraStockAdapter,
        "teslatools_v1": TeslaToolsAdapter,
        "hangout_v1": HangoutAdapter,
        "generic_rest": GenericRESTAdapter,
    }

    @classmethod
    def register(cls, driver_name: str, adapter_cls: Type[SupplierAdapter]):
        cls._drivers[driver_name] = adapter_cls

    @classmethod
    def list_drivers(cls) -> list[str]:
        return list(cls._drivers.keys())

    @classmethod
    def presets(cls) -> Dict[str, Dict[str, Any]]:
        return SUPPLIER_PRESETS

    @classmethod
    def normalize_config(cls, driver_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply presets + auto-decode Lara conn_ codes."""
        cfg = dict(config or {})
        preset = SUPPLIER_PRESETS.get(driver_type) or {}
        if not cfg.get("base_url") and preset.get("base_url"):
            cfg["base_url"] = preset["base_url"]

        # Lara Stock: paste conn_... into api_key or connection_code field
        raw_key = str(cfg.get("api_key") or cfg.get("connection_code") or "")
        if driver_type == "larastock_v1" and raw_key.startswith("conn_"):
            decoded = decode_conn_code(raw_key)
            cfg["api_key"] = decoded["api_key"]
            cfg["base_url"] = decoded["base_url"]
            cfg.pop("connection_code", None)
        elif cfg.get("connection_code") and str(cfg["connection_code"]).startswith("conn_"):
            decoded = decode_conn_code(str(cfg["connection_code"]))
            cfg["api_key"] = decoded["api_key"]
            cfg["base_url"] = decoded["base_url"]
            cfg.pop("connection_code", None)
        return cfg

    @classmethod
    def create_from_config(cls, driver_type: str, config: Dict[str, Any]) -> SupplierAdapter:
        adapter_cls = cls._drivers.get(driver_type)
        if not adapter_cls:
            adapter_cls = GenericRESTAdapter
        return adapter_cls(cls.normalize_config(driver_type, config))

    @classmethod
    def create_from_supplier(cls, supplier: Supplier) -> SupplierAdapter:
        """Decrypts config from database model and instantiates driver."""
        raw_config_str = decrypt_secret(supplier.config_encrypted)
        config_dict = json.loads(raw_config_str)

        config_dict["supplier_id"] = str(supplier.id)
        config_dict["rate_limit_rpm"] = supplier.rate_limit_rpm or 60
        config_dict["payment_network"] = (
            supplier.payment_network.value
            if hasattr(supplier.payment_network, "value")
            else str(supplier.payment_network)
        )
        config_dict["deposit_address"] = supplier.deposit_address
        config_dict["binance_id"] = supplier.binance_id
        config_dict["min_deposit"] = float(supplier.min_deposit or Decimal("0.0"))
        config_dict["deposit_window_seconds"] = supplier.deposit_window_seconds

        return cls.create_from_config(supplier.driver_type, config_dict)
