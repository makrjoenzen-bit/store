"""
Binance Spot Read-Only Pay transactions reader.
Primary verifier: GET /sapi/v1/pay/transactions (HMAC-SHA256 Spot API key/secret).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from loguru import logger

from src.config import settings


class BinancePayReader:
    """Polls personal Binance Pay transaction history via Spot API."""

    BASE = "https://api.binance.com"

    def __init__(self):
        self.api_key = (
            getattr(settings, "BINANCE_SPOT_API_KEY", None)
            or settings.BINANCE_PAY_API_KEY
        )
        self.api_secret = (
            getattr(settings, "BINANCE_SPOT_API_SECRET", None)
            or settings.BINANCE_PAY_SECRET_KEY
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _sign(self, params: Dict[str, Any]) -> str:
        query = urlencode(params)
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def list_transactions(
        self,
        *,
        start_ms: Optional[int] = None,
        end_ms: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        GET /sapi/v1/pay/transactions
        Returns normalized list: [{transaction_id, amount, currency, note, timestamp, raw}, ...]
        """
        if not self.is_configured:
            raise ValueError("Binance Spot API key/secret not configured")

        params: Dict[str, Any] = {
            "timestamp": int(time.time() * 1000),
            "recvWindow": 60000,
            "limit": min(limit, 100),
        }
        if start_ms:
            params["startTime"] = int(start_ms)
        if end_ms:
            params["endTime"] = int(end_ms)
        params["signature"] = self._sign(params)

        headers = {"X-MBX-APIKEY": self.api_key}
        url = f"{self.BASE}/sapi/v1/pay/transactions"

        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(url, params=params, headers=headers)
            data = res.json()
            if res.status_code != 200:
                logger.error(f"Binance pay transactions error: {data}")
                raise RuntimeError(data.get("msg") or res.text)

        # Response shape: { code, message, data: [ ... ], success }
        rows = data.get("data") if isinstance(data, dict) else data
        if rows is None:
            rows = []
        if isinstance(rows, dict):
            rows = rows.get("list") or rows.get("transactions") or []

        normalized = []
        for row in rows or []:
            normalized.append(self._normalize(row))
        return normalized

    def _normalize(self, row: Dict[str, Any]) -> Dict[str, Any]:
        tx_id = str(
            row.get("transactionId")
            or row.get("tranId")
            or row.get("orderId")
            or row.get("uid")
            or ""
        )
        amount_raw = row.get("amount") or row.get("totalAmount") or row.get("orderAmount") or "0"
        # Binance may return signed amounts (negative for send)
        amount = abs(Decimal(str(amount_raw)))
        note = ""
        for key in ("note", "remark", "description"):
            if row.get(key):
                note = str(row.get(key))
                break
        payer = row.get("payerInfo")
        if not note and isinstance(payer, dict) and payer.get("name"):
            note = str(payer.get("name"))
        currency = str(row.get("currency") or row.get("asset") or "USDT")
        ts = row.get("transactionTime") or row.get("createTime") or row.get("timestamp") or 0
        return {
            "transaction_id": tx_id,
            "amount": amount,
            "currency": currency.upper(),
            "note": note,
            "timestamp": int(ts) if ts else 0,
            "raw": row,
        }

    async def find_by_pay_order_id(self, pay_order_id: str) -> Optional[Dict[str, Any]]:
        """Lookup a single Pay Order / transaction id in recent history (last 24h)."""
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - 24 * 3600 * 1000
        rows = await self.list_transactions(start_ms=start_ms, end_ms=end_ms, limit=100)
        target = str(pay_order_id).strip()
        for row in rows:
            if row["transaction_id"] == target:
                return row
            raw = row.get("raw") or {}
            for key in ("orderId", "transactionId", "tranId", "uid"):
                if str(raw.get(key) or "") == target:
                    return row
        return None


binance_pay_reader = BinancePayReader()
