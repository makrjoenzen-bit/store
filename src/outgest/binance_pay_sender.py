import time
import hmac
import hashlib
import uuid
from decimal import Decimal
from typing import Optional, Dict, Any
import httpx
from loguru import logger

from src.config import settings


class BinancePaySender:
    """Binance Pay Merchant Payout API connector for instant off-chain supplier top-ups."""

    def __init__(self):
        self.api_key = settings.BINANCE_PAY_API_KEY
        self.secret_key = settings.BINANCE_PAY_SECRET_KEY
        self.base_url = "https://bpay.binanceapi.com"

    def _generate_signature(self, timestamp: int, nonce: str, payload_str: str) -> str:
        data_to_sign = f"{timestamp}\n{nonce}\n{payload_str}\n"
        sig = hmac.new(
            self.secret_key.encode("utf-8"),
            data_to_sign.encode("utf-8"),
            hashlib.sha512
        ).hexdigest().upper()
        return sig

    async def transfer_to_binance_id(
        self,
        recipient_binance_id: str,
        amount_usd: Decimal,
        currency: str = "USDT",
        order_ref: Optional[str] = None
    ) -> Dict[str, Any]:
        """Transfers funds directly to a supplier's Binance Pay ID or Email."""
        if not self.api_key or not self.secret_key:
            raise ValueError("Binance Pay API keys not configured.")

        url = f"{self.base_url}/binancepay/openapi/payout/transfer"
        req_id = str(uuid.uuid4()).replace("-", "")[:32]
        timestamp = int(time.time() * 1000)
        nonce = str(uuid.uuid4())[:32]

        payload = {
            "requestId": req_id,
            "batchName": f"JIT-Payout-{order_ref or req_id[:8]}",
            "currency": currency,
            "totalAmount": float(amount_usd),
            "totalNumber": 1,
            "transferDetailList": [
                {
                    "transferDetailId": f"TD-{req_id[:16]}",
                    "receiver": recipient_binance_id,
                    "transferAmount": float(amount_usd),
                    "transferType": "BINANCE_ID",
                    "remark": f"JIT Funding {order_ref}",
                }
            ],
        }

        import json
        payload_str = json.dumps(payload)
        signature = self._generate_signature(timestamp, nonce, payload_str)

        headers = {
            "Content-Type": "application/json",
            "BinancePay-Timestamp": str(timestamp),
            "BinancePay-Nonce": nonce,
            "BinancePay-Certificate-SN": self.api_key,
            "BinancePay-Signature": signature,
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(url, json=payload, headers=headers)
            res_json = res.json()
            if res.status_code == 200 and res_json.get("status") == "SUCCESS":
                return {
                    "is_success": True,
                    "payout_id": req_id,
                    "data": res_json.get("data"),
                }
            return {
                "is_success": False,
                "error_message": res_json.get("errorMessage", res.text),
                "raw": res_json,
            }


binance_pay_sender = BinancePaySender()
