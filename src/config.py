from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List

# Known-weak defaults that must never ship in production
_DEFAULT_JWT = "super_secret_jwt_key_must_be_changed_in_prod_32chars"
_DEFAULT_FERNET = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
_ZERO_ETH_KEY = "0x" + ("0" * 64)
_FAKE_BINANCE_MERCHANT = "48192841"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Database & Redis
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/aggregator_db"
    DATABASE_URL_SYNC: str = "postgresql://postgres:postgres@localhost:5432/aggregator_db"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Security
    JWT_SECRET_KEY: str = _DEFAULT_JWT
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    ENCRYPTION_MASTER_KEY: str = _DEFAULT_FERNET

    # Telegram — Customer Store Bot
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_PUBLIC_CHANNEL_ID: Optional[str] = None
    TELEGRAM_MAIN_CHANNEL_URL: Optional[str] = None
    TELEGRAM_PROOF_CHANNEL_ID: Optional[str] = None
    TELEGRAM_PROOF_CHANNEL_URL: Optional[str] = None
    ADMIN_TELEGRAM_ID: Optional[int] = None  # legacy single-admin (still honored)
    MINIAPP_URL: str = "http://localhost:3000/miniapp"

    # Telegram — Dedicated Admin Control Bot (Phase 14.1)
    ADMIN_BOT_TOKEN: str = ""
    # Comma-separated Telegram user IDs, e.g. "111,222,333"
    ADMIN_TELEGRAM_IDS: str = ""
    # Phase 16 — Owner (full) vs Staff (orders/warranty/support only)
    OWNER_TELEGRAM_IDS: str = ""  # if empty, ADMIN_TELEGRAM_IDS are treated as owners
    STAFF_TELEGRAM_IDS: str = ""

    @staticmethod
    def _parse_id_csv(raw: str) -> set:
        ids = set()
        for part in (raw or "").split(","):
            part = part.strip()
            if part:
                try:
                    ids.add(int(part))
                except ValueError:
                    continue
        return ids

    @property
    def owner_telegram_id_set(self) -> set:
        owners = self._parse_id_csv(self.OWNER_TELEGRAM_IDS)
        if owners:
            return owners
        # Back-compat: all ADMIN_* are owners when OWNER_* unset
        return self.admin_telegram_id_set

    @property
    def staff_telegram_id_set(self) -> set:
        return self._parse_id_csv(self.STAFF_TELEGRAM_IDS)

    @property
    def admin_telegram_id_set(self) -> set:
        """Owners ∪ Staff ∪ legacy ADMIN_TELEGRAM_IDS / ADMIN_TELEGRAM_ID."""
        ids = set()
        ids |= self._parse_id_csv(self.ADMIN_TELEGRAM_IDS)
        ids |= self._parse_id_csv(self.OWNER_TELEGRAM_IDS)
        ids |= self._parse_id_csv(self.STAFF_TELEGRAM_IDS)
        if self.ADMIN_TELEGRAM_ID is not None:
            ids.add(int(self.ADMIN_TELEGRAM_ID))
        return ids

    def is_owner(self, telegram_id: int) -> bool:
        return int(telegram_id) in self.owner_telegram_id_set

    def is_staff_only(self, telegram_id: int) -> bool:
        tid = int(telegram_id)
        return tid in self.staff_telegram_id_set and tid not in self.owner_telegram_id_set

    # BSC Web3
    BSC_RPC_URL: str = "https://bsc-dataseed.binance.org/"
    BSC_FALLBACK_RPC_URL: str = "https://rpc.ankr.com/bsc"
    BSC_CHAIN_ID: int = 56
    USDT_BEP20_CONTRACT_ADDRESS: str = "0x55d398326f99059fF775485246999027B3197955"

    # TRON TRC-20
    TRON_RPC_URL: str = "https://api.trongrid.io"
    TRON_API_KEY: Optional[str] = None
    TRON_HOT_WALLET_ADDRESS: str = ""
    TRON_HOT_WALLET_PRIVATE_KEY: str = ""
    USDT_TRC20_CONTRACT_ADDRESS: str = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    
    # Hot Wallet
    HOT_WALLET_ADDRESS: str = ""
    HOT_WALLET_PRIVATE_KEY: str = ""
    MIN_BNB_GAS_RESERVE: float = 0.005
    MIN_USDT_RESERVE: float = 5.00

    # Binance Pay — inbound via Spot API GET /sapi/v1/pay/transactions (read-only)
    # Prefer BINANCE_SPOT_* ; fall back to BINANCE_PAY_* for legacy .env
    BINANCE_SPOT_API_KEY: Optional[str] = None
    BINANCE_SPOT_API_SECRET: Optional[str] = None
    BINANCE_PAY_API_KEY: Optional[str] = None
    BINANCE_PAY_SECRET_KEY: Optional[str] = None
    # Personal Binance Pay ID shown to customers (NOT a fake merchant id)
    BINANCE_PAY_MERCHANT_ID: Optional[str] = None
    BINANCE_PAY_POLL_SECONDS: float = 5.0

    # Gateways
    OXAPAY_MERCHANT_API_KEY: Optional[str] = None
    OXAPAY_PAYOUT_API_KEY: Optional[str] = None
    CRYPTOMUS_MERCHANT_UUID: Optional[str] = None
    CRYPTOMUS_PAYMENT_KEY: Optional[str] = None

    # Constants
    DEFAULT_GAS_BUFFER_USD: float = 0.08
    MIN_ORDER_VALUE_USD: float = 1.00
    INVOICE_TIMEOUT_MINUTES: int = 30
    # Halfway reminder for unpaid invoices (minutes after creation)
    CHECKOUT_REMINDER_MINUTES: int = 15
    VENTEBOT_DEPOSIT_TIMEOUT_SECONDS: int = 300
    # Alert Admin Bot when supplier API balance falls below this USD threshold
    SUPPLIER_BALANCE_ALERT_USD: float = 15.0

    # Phase 17 — AI Concierge (OpenAI-compatible: Groq / OpenRouter / etc.)
    AI_CONCIERGE_BASE_URL: str = "https://api.groq.com/openai/v1"
    AI_CONCIERGE_API_KEY: Optional[str] = None
    AI_CONCIERGE_MODEL: str = "llama-3.3-70b-versatile"
    AI_CONCIERGE_MAX_PER_HOUR: int = 15

    # Competitor radar (optional Telethon)
    TELETHON_API_ID: Optional[int] = None
    TELETHON_API_HASH: Optional[str] = None

    # Supplier quality auto-demotion thresholds
    SUPPLIER_WARRANTY_CLAIM_RATE_MAX: float = 10.0  # percent
    SUPPLIER_API_FAILURE_RATE_MAX: float = 15.0
    SUPPLIER_QUALITY_MIN_ORDERS: int = 10

    @property
    def is_production(self) -> bool:
        return (self.ENVIRONMENT or "").strip().lower() == "production"

    @property
    def crypto_rails_enabled(self) -> bool:
        """True when a hot-wallet address is configured for on-chain JIT / inbound."""
        hw = (self.HOT_WALLET_ADDRESS or "").strip().lower()
        tron = (self.TRON_HOT_WALLET_ADDRESS or "").strip()
        zero = "0x" + ("0" * 40)
        return bool((hw and hw != zero) or tron)

    @property
    def binance_pay_enabled(self) -> bool:
        mid = (self.BINANCE_PAY_MERCHANT_ID or "").strip()
        keys = (
            (self.BINANCE_SPOT_API_KEY or self.BINANCE_PAY_API_KEY or "").strip()
        )
        return bool(mid or keys)


def validate_production_secrets(cfg: Optional[Settings] = None) -> None:
    """
    Fail-fast boot guard for ENVIRONMENT=production.
    Raises RuntimeError listing every weak/missing secret. No-op outside production.
    """
    s = cfg or settings
    if (s.ENVIRONMENT or "").strip().lower() != "production":
        return

    errors: List[str] = []

    jwt = (s.JWT_SECRET_KEY or "").strip()
    if not jwt or jwt == _DEFAULT_JWT or len(jwt) < 32:
        errors.append("JWT_SECRET_KEY must be a unique secret with length >= 32")

    enc = (s.ENCRYPTION_MASTER_KEY or "").strip()
    if not enc or enc == _DEFAULT_FERNET:
        errors.append("ENCRYPTION_MASTER_KEY must be a non-placeholder Fernet key")
    else:
        try:
            from cryptography.fernet import Fernet
            Fernet(enc.encode() if isinstance(enc, str) else enc)
        except Exception:
            errors.append("ENCRYPTION_MASTER_KEY is not a valid Fernet key")

    if not (s.TELEGRAM_BOT_TOKEN or "").strip():
        errors.append("TELEGRAM_BOT_TOKEN is required")
    if not (s.ADMIN_BOT_TOKEN or "").strip():
        errors.append("ADMIN_BOT_TOKEN is required")
    if not s.admin_telegram_id_set:
        errors.append("ADMIN_TELEGRAM_IDS (or ADMIN_TELEGRAM_ID) must list at least one admin")

    if s.crypto_rails_enabled:
        pk = (s.HOT_WALLET_PRIVATE_KEY or "").strip().lower()
        if not pk or pk in (_ZERO_ETH_KEY.lower(), "0x" + ("0" * 64)):
            # Also reject empty / all-zero without 0x
            stripped = pk[2:] if pk.startswith("0x") else pk
            if not stripped or set(stripped) <= {"0"}:
                errors.append(
                    "HOT_WALLET_PRIVATE_KEY must be a real key when crypto rails are enabled"
                )

    if s.binance_pay_enabled:
        mid = (s.BINANCE_PAY_MERCHANT_ID or "").strip()
        if not mid or mid == _FAKE_BINANCE_MERCHANT:
            errors.append(
                "BINANCE_PAY_MERCHANT_ID must be a real Pay ID (not empty or placeholder 48192841)"
            )

    if errors:
        joined = "; ".join(errors)
        raise RuntimeError(f"Production boot refused — insecure configuration: {joined}")


settings = Settings()
