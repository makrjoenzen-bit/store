import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, List, Any

from sqlalchemy import (
    Column, String, Text, Boolean, Integer, Numeric, DateTime, ForeignKey,
    LargeBinary, BigInteger, Enum as SQLEnum, Index, UniqueConstraint, TypeDecorator,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from src.core.database import Base


class FlexibleUUID(TypeDecorator):
    """
    UUID column that also accepts canonical string values on bind.

    Callback data, webhook payloads and path parameters arrive as strings; previously
    each such value raised "'str' object has no attribute 'hex'". Coercing at the type
    boundary hardens every entry point (Bot callbacks, webhooks, admin APIs).
    """
    impl = UUID
    cache_ok = True

    def __init__(self):
        super().__init__(as_uuid=True)

    def process_bind_param(self, value, dialect):
        if isinstance(value, str):
            try:
                return uuid.UUID(value)
            except (ValueError, AttributeError):
                return value
        return value

    def process_literal_param(self, value, dialect):
        """Cache-safe literal binding (used by ORM identity comparisons)."""
        return self.process_bind_param(value, dialect)
from src.core.models.enums import (
    SupplierStatus, PaymentNetwork, PricingRuleType, PricingScope,
    PaymentMethod, InvoiceStatus, OrderState, JitTxState,
    WalletTxType, TicketStatus, CustomerStatus, DiscountType, VipTier
)


def get_utc_now():
    return datetime.now(timezone.utc)


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    driver_type = Column(String(50), nullable=False)  # 'ventebot_v1', 'generic_rest', 'binance_pay_supplier'
    config_encrypted = Column(LargeBinary, nullable=False)
    payment_network = Column(SQLEnum(PaymentNetwork), nullable=False, default=PaymentNetwork.USDT_BEP20)
    deposit_address = Column(String(100), nullable=True)
    binance_id = Column(String(50), nullable=True)

    status = Column(SQLEnum(SupplierStatus), nullable=False, default=SupplierStatus.INACTIVE)
    priority_weight = Column(Integer, nullable=False, default=100)
    # Phase 17 — reliability scoring (higher = worse; subtracted from routing priority)
    quality_penalty_score = Column(Integer, nullable=False, default=0)
    warranty_claim_rate_30d = Column(Numeric(8, 4), nullable=True)
    api_failure_rate_30d = Column(Numeric(8, 4), nullable=True)
    rate_limit_rpm = Column(Integer, nullable=False, default=60)
    min_deposit = Column(Numeric(18, 4), nullable=False, default=Decimal("0.0"))
    deposit_window_seconds = Column(Integer, nullable=True)

    cb_failure_threshold = Column(Integer, nullable=False, default=3)
    cb_cooldown_seconds = Column(Integer, nullable=False, default=60)
    cb_consecutive_failures = Column(Integer, nullable=False, default=0)
    cb_last_failure_at = Column(DateTime(timezone=True), nullable=True)

    cached_balance = Column(Numeric(18, 4), nullable=True)
    cached_balance_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    product_links = relationship("ProductSupplierLink", back_populates="supplier", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="supplier")
    health_logs = relationship("SupplierHealthLog", back_populates="supplier", cascade="all, delete-orphan")
    credits = relationship("SupplierCredit", back_populates="supplier")


class Category(Base):
    __tablename__ = "categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name_ar = Column(String(200), nullable=False)
    name_en = Column(String(200), nullable=False)
    icon_emoji = Column(String(10), default="📦", nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    products = relationship("MasterProduct", back_populates="category")


class MasterProduct(Base):
    __tablename__ = "master_products"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    internal_sku = Column(String(50), unique=True, nullable=False)
    title_ar = Column(String(300), nullable=False)
    title_en = Column(String(300), nullable=True)
    description_ar = Column(Text, nullable=True)
    description_en = Column(Text, nullable=True)
    photo_file_id = Column(String(200), nullable=True)
    photo_url = Column(String(500), nullable=True)

    # Headless CMS & Custom Display Overrides (Independent of supplier naming)
    custom_emoji = Column(String(10), default="✨", nullable=False)
    custom_button_label_ar = Column(String(100), default="🛒 شراء الآن", nullable=False)
    custom_description_ar = Column(Text, nullable=True)
    custom_description_en = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    is_published_to_channel = Column(Boolean, default=False, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    warranty_days = Column(Integer, default=30, nullable=False)
    # Optional bulk retail tiers: [{"min_qty": 5, "unit_price": 3.10}, ...]
    # Always clamped by Profit Guard so unit_price never undercuts cost + incentives.
    bulk_pricing_tiers = Column(JSONB, nullable=True)
    # Buyer must provide email/activation id before checkout (ChatGPT seats, etc.)
    requires_customer_email = Column(Boolean, default=False, nullable=False)
    # Phase 16 — flexible activation input (generalizes requires_customer_email)
    requires_customer_input = Column(Boolean, default=False, nullable=False)
    customer_input_type = Column(String(20), nullable=True)  # email | link | username | text
    customer_input_label_ar = Column(String(200), nullable=True)
    # Storefront grouping / merchandising
    brand_family = Column(String(100), nullable=True, index=True)
    delivery_type_label = Column(String(40), nullable=True)  # ⚡ فوري / 🕒 يدوي
    is_featured = Column(Boolean, default=False, nullable=False, index=True)
    subscription_duration_days = Column(Integer, nullable=True)
    search_keywords = Column(Text, nullable=True)  # comma-separated aliases
    # local_first | local_only | api_first (default) | manual
    fulfillment_mode = Column(String(20), default="api_first", nullable=False)
    # Phase 17 — competitor radar auto-match (clamped by Profit Guard)
    auto_match_competitor = Column(Boolean, default=False, nullable=False)

    cached_best_price = Column(Numeric(18, 4), nullable=True)
    cached_final_price = Column(Numeric(18, 4), nullable=True)
    cached_best_supplier_id = Column(UUID(as_uuid=True), nullable=True)
    cached_in_stock = Column(Boolean, default=False, nullable=False)
    cache_refreshed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    category = relationship("Category", back_populates="products")
    supplier_links = relationship("ProductSupplierLink", back_populates="master_product", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="master_product")
    channel_post = relationship("ChannelPost", back_populates="master_product", uselist=False, cascade="all, delete-orphan")


class ProductSupplierLink(Base):
    __tablename__ = "product_supplier_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    master_product_id = Column(
        UUID(as_uuid=True), ForeignKey("master_products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    supplier_id = Column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    supplier_product_id = Column(String(100), nullable=False)
    supplier_product_name = Column(String(300), nullable=True)

    last_known_cost = Column(Numeric(18, 4), nullable=True)
    is_in_stock = Column(Boolean, default=True, nullable=False)
    available_quantity = Column(Integer, nullable=True)  # Live stock quantity passthrough
    priority_override = Column(Integer, nullable=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("master_product_id", "supplier_id", "supplier_product_id", name="uq_product_supplier_link"),
    )

    # Relationships
    master_product = relationship("MasterProduct", back_populates="supplier_links")
    supplier = relationship("Supplier", back_populates="product_links")


class PricingRule(Base):
    __tablename__ = "pricing_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope = Column(SQLEnum(PricingScope), nullable=False)
    scope_id = Column(UUID(as_uuid=True), nullable=True)
    rule_type = Column(SQLEnum(PricingRuleType), nullable=False)
    value = Column(Numeric(18, 4), nullable=False)

    gas_buffer_enabled = Column(Boolean, default=True, nullable=False)
    gas_buffer_fixed = Column(Numeric(18, 4), default=Decimal("0.08"), nullable=True)
    priority = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    telegram_username = Column(String(100), nullable=True)
    first_name = Column(String(200), nullable=True)
    last_name = Column(String(200), nullable=True)
    language_code = Column(String(10), default="ar", nullable=False)

    wallet_balance = Column(Numeric(18, 4), nullable=False, default=Decimal("0.0"))
    total_orders = Column(Integer, nullable=False, default=0)
    total_spent = Column(Numeric(18, 4), nullable=False, default=Decimal("0.0"))
    total_deposited = Column(Numeric(18, 4), nullable=False, default=Decimal("0.0"))

    vip_tier = Column(SQLEnum(VipTier), default=VipTier.STANDARD, nullable=False)
    # Phase 16 — optional per-customer overrides (Owner CRM)
    vip_discount_percent = Column(Numeric(8, 4), nullable=True)
    cashback_percent = Column(Numeric(8, 4), nullable=True)
    api_key_hash = Column(String(128), nullable=True, index=True)
    api_key_prefix = Column(String(20), nullable=True)
    api_enabled = Column(Boolean, default=False, nullable=False)

    is_blocked = Column(Boolean, default=False, nullable=False)
    blocked_reason = Column(Text, nullable=True)
    account_status = Column(SQLEnum(CustomerStatus), default=CustomerStatus.ACTIVE, nullable=False, index=True)

    referred_by = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=True)
    referral_code = Column(String(20), unique=True, nullable=True)
    referral_earnings = Column(Numeric(18, 4), default=Decimal("0.0"), nullable=False)
    referral_count = Column(Integer, default=0, nullable=False)

    first_seen_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    last_active_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    orders = relationship("Order", back_populates="customer")
    wallet_transactions = relationship("WalletTransaction", back_populates="customer")
    invoices = relationship("PaymentInvoice", back_populates="customer")
    tickets = relationship("SupportTicket", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_customer_state", "customer_id", "state"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_number = Column(String(30), unique=True, nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    master_product_id = Column(UUID(as_uuid=True), ForeignKey("master_products.id"), nullable=False)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("payment_invoices.id"), nullable=True)

    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=True)
    supplier_product_id = Column(String(100), nullable=True)

    supplier_cost = Column(Numeric(18, 4), nullable=True)
    our_price = Column(Numeric(18, 4), nullable=False)
    profit_margin = Column(Numeric(18, 4), nullable=True)
    gas_fee_actual = Column(Numeric(18, 4), nullable=True)
    quantity = Column(Integer, default=1, nullable=False)
    wallet_portion_reserved = Column(Numeric(18, 4), default=Decimal("0.0"), nullable=False)
    coupon_id = Column(UUID(as_uuid=True), ForeignKey("coupons.id"), nullable=True)
    discount_amount = Column(Numeric(18, 4), default=Decimal("0.0"), nullable=False)

    supplier_quote_id = Column(String(100), nullable=True)
    supplier_quote_expires_at = Column(DateTime(timezone=True), nullable=True)

    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    supplier_order_id = Column(String(100), nullable=True)

    # Buyer-provided fields for seat invites / activation products
    buyer_email = Column(String(255), nullable=True)
    buyer_activation_id = Column(String(255), nullable=True)

    delivered_product = Column(Text, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    warranty_expires_at = Column(DateTime(timezone=True), nullable=True)
    renewal_reminder_sent = Column(Boolean, default=False, nullable=False)
    # Trust / proof channel ("قناة التوثيق") — idempotent publish stamp
    proof_posted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Phase 16 — staff claim lock (manual Sham Cash / Binance review)
    claimed_by_telegram_id = Column(BigInteger, nullable=True, index=True)
    claimed_by_username = Column(String(100), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)

    # Live manual-fulfillment countdown card (customer Telegram message)
    status_chat_id = Column(BigInteger, nullable=True)
    status_message_id = Column(BigInteger, nullable=True)
    manual_deadline_at = Column(DateTime(timezone=True), nullable=True, index=True)
    status_countdown_closed = Column(Boolean, default=False, nullable=False)
    status_overdue_notified = Column(Boolean, default=False, nullable=False)

    state = Column(SQLEnum(OrderState), nullable=False, default=OrderState.CREATED, index=True)
    failure_reason = Column(Text, nullable=True)
    fallback_attempts = Column(Integer, default=0, nullable=False)
    max_fallback_attempts = Column(Integer, default=2, nullable=False)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    customer = relationship("Customer", back_populates="orders")
    master_product = relationship("MasterProduct", back_populates="orders")
    supplier = relationship("Supplier", back_populates="orders")
    invoice = relationship("PaymentInvoice", foreign_keys=[invoice_id])
    state_logs = relationship("OrderStateLog", back_populates="order", cascade="all, delete-orphan")
    jit_transactions = relationship("JitTransaction", back_populates="order")
    tickets = relationship("SupportTicket", back_populates="order")


class PaymentInvoice(Base):
    __tablename__ = "payment_invoices"
    __table_args__ = (
        Index("idx_payment_invoices_status_expires", "status", "expires_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    payment_method = Column(SQLEnum(PaymentMethod), nullable=False)

    amount_expected = Column(Numeric(18, 4), nullable=False)
    amount_received = Column(Numeric(18, 4), nullable=True)
    currency = Column(String(10), default="USDT", nullable=False)

    deposit_address = Column(String(100), nullable=True)
    unique_amount = Column(Numeric(18, 8), nullable=True, index=True)
    # Nullable unique: PostgreSQL/SQLite allow multiple NULLs; non-null tx hashes are unique
    tx_hash = Column(String(100), nullable=True, unique=True, index=True)
    block_number = Column(BigInteger, nullable=True)
    confirmations = Column(Integer, default=0, nullable=False)

    gateway_provider = Column(String(30), nullable=True)
    gateway_invoice_id = Column(String(100), nullable=True)
    gateway_pay_url = Column(String(500), nullable=True)

    sham_cash_receipt_photo_file_id = Column(String(200), nullable=True)
    receipt_type = Column(String(20), nullable=True)  # photo | pdf
    receipt_sha256 = Column(String(64), nullable=True, index=True)
    # Alias / Phase 16 naming (kept in sync with receipt_sha256)
    receipt_file_sha256 = Column(String(64), nullable=True, index=True)
    receipt_file_unique_id = Column(String(100), nullable=True, index=True)
    sham_cash_tx_ref = Column(String(50), nullable=True, index=True)
    # Fraud: prior order that shared hash or tx_ref (admin warning only)
    duplicate_of_order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    duplicate_of_order_number = Column(String(30), nullable=True)
    # Phase 17 — PDF forensics summary (admin/staff only)
    pdf_forensics_json = Column(JSONB, nullable=True)
    sham_cash_reviewed_by = Column(String(100), nullable=True)
    sham_cash_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    binance_pay_order_id = Column(String(100), nullable=True, index=True)

    status = Column(SQLEnum(InvoiceStatus), nullable=False, default=InvoiceStatus.PENDING, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    # Abandoned-checkout gentle reminder (halfway through invoice window)
    reminder_sent = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    customer = relationship("Customer", back_populates="invoices")
    order = relationship("Order", foreign_keys="[PaymentInvoice.order_id]")


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"
    __table_args__ = (
        Index("idx_wallet_ledger_customer_created", "customer_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    tx_type = Column(SQLEnum(WalletTxType), nullable=False)
    amount = Column(Numeric(18, 4), nullable=False)
    balance_after = Column(Numeric(18, 4), nullable=False)

    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("payment_invoices.id"), nullable=True)

    description = Column(Text, nullable=False)
    metadata_json = Column(JSONB, default={}, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    # Relationships
    customer = relationship("Customer", back_populates="wallet_transactions")


class OrderStateLog(Base):
    __tablename__ = "order_state_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    from_state = Column(SQLEnum(OrderState), nullable=True)
    to_state = Column(SQLEnum(OrderState), nullable=False)
    trigger_source = Column(String(50), nullable=False)
    details = Column(JSONB, default={}, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    # Relationships
    order = relationship("Order", back_populates="state_logs")


class JitTransaction(Base):
    __tablename__ = "jit_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    payment_network = Column(SQLEnum(PaymentNetwork), nullable=False)

    from_address = Column(String(100), nullable=True)
    to_address = Column(String(100), nullable=True)
    amount = Column(Numeric(18, 4), nullable=False)
    gas_fee = Column(Numeric(18, 6), nullable=True)
    tx_hash = Column(String(100), nullable=True, index=True)
    block_number = Column(BigInteger, nullable=True)
    nonce = Column(Integer, nullable=True)

    binance_payout_id = Column(String(100), nullable=True)
    supplier_deposit_id = Column(String(100), nullable=True)
    supplier_deposit_address = Column(String(100), nullable=True)
    deposit_expires_at = Column(DateTime(timezone=True), nullable=True)

    state = Column(SQLEnum(JitTxState), default=JitTxState.PENDING, nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    order = relationship("Order", back_populates="jit_transactions")


class SupplierCredit(Base):
    __tablename__ = "supplier_credits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    jit_tx_id = Column(UUID(as_uuid=True), ForeignKey("jit_transactions.id"), nullable=True)
    amount = Column(Numeric(18, 4), nullable=False)

    is_consumed = Column(Boolean, default=False, nullable=False)
    consumed_by_order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    # Relationships
    supplier = relationship("Supplier", back_populates="credits")


class HotWallet(Base):
    __tablename__ = "hot_wallet"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    address = Column(String(100), nullable=False)
    usdt_balance = Column(Numeric(18, 4), default=Decimal("0.0"), nullable=False)
    bnb_balance = Column(Numeric(18, 8), default=Decimal("0.0"), nullable=False)
    usdt_committed = Column(Numeric(18, 4), default=Decimal("0.0"), nullable=False)

    min_bnb_reserve = Column(Numeric(18, 8), default=Decimal("0.005"), nullable=False)
    min_usdt_reserve = Column(Numeric(18, 4), default=Decimal("5.00"), nullable=False)
    current_nonce = Column(Integer, default=0, nullable=False)

    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    subject = Column(String(300), nullable=False)
    status = Column(SQLEnum(TicketStatus), default=TicketStatus.OPEN, nullable=False, index=True)
    assigned_to = Column(String(100), nullable=True)
    priority = Column(Integer, default=0, nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    # Relationships
    customer = relationship("Customer", back_populates="tickets")
    order = relationship("Order", back_populates="tickets")
    messages = relationship("TicketMessage", back_populates="ticket", cascade="all, delete-orphan")


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False)
    sender_type = Column(String(20), nullable=False)  # 'customer', 'admin', 'system'
    sender_name = Column(String(100), nullable=True)
    message_text = Column(Text, nullable=False)
    attachment_file_id = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    # Relationships
    ticket = relationship("SupportTicket", back_populates="messages")


class SupplierHealthLog(Base):
    __tablename__ = "supplier_health_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False)
    check_type = Column(String(30), nullable=False)  # 'ping', 'products', 'balance', 'order_test'
    is_success = Column(Boolean, nullable=False)
    response_time_ms = Column(Integer, nullable=True)
    http_status = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    metadata_json = Column(JSONB, default={}, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)

    # Relationships
    supplier = relationship("Supplier", back_populates="health_logs")


class ChannelPost(Base):
    __tablename__ = "channel_posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    master_product_id = Column(UUID(as_uuid=True), ForeignKey("master_products.id", ondelete="CASCADE"), unique=True, nullable=False)
    telegram_chat_id = Column(BigInteger, nullable=False)
    telegram_message_id = Column(BigInteger, nullable=False)
    last_rendered_text = Column(Text, nullable=True)
    last_rendered_price = Column(Numeric(18, 4), nullable=True)
    last_rendered_stock_status = Column(Boolean, default=True, nullable=False)
    last_rendered_stock_qty = Column(Integer, nullable=True)
    needs_update = Column(Boolean, default=False, nullable=False)
    last_edited_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    # Relationships
    master_product = relationship("MasterProduct", back_populates="channel_post")


class BotConfig(Base):
    __tablename__ = "bot_config"

    key = Column(String(100), primary_key=True)
    value = Column(JSONB, nullable=False)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)
    updated_by = Column(String(100), nullable=True)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(200), nullable=False)
    display_name = Column(String(200), nullable=True)
    role = Column(String(30), default="admin", nullable=False)
    telegram_id = Column(BigInteger, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_user_id = Column(UUID(as_uuid=True), ForeignKey("admin_users.id"), nullable=True)
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    old_values = Column(JSONB, nullable=True)
    new_values = Column(JSONB, nullable=True)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)


class FormalInvoice(Base):
    """Sequential numbered invoice generated on order completion or confirmed wallet deposit."""
    __tablename__ = "formal_invoices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_number = Column(String(30), unique=True, nullable=False, index=True)  # INV-2026-00001
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)

    item_title = Column(String(300), nullable=False)
    quantity = Column(Integer, default=1, nullable=False)
    unit_price = Column(Numeric(18, 4), nullable=False)
    discount = Column(Numeric(18, 4), default=Decimal("0.0"), nullable=False)
    net_total = Column(Numeric(18, 4), nullable=False)
    payment_method = Column(SQLEnum(PaymentMethod), nullable=False)
    tx_id_or_ref = Column(String(200), nullable=True)  # On-chain TXID or Sham Cash ref
    warranty_expires_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)


class InvoiceSequenceCounter(Base):
    """Singleton row for atomic sequential invoice numbering per year."""
    __tablename__ = "invoice_sequence_counter"

    year = Column(Integer, primary_key=True)
    last_sequence = Column(Integer, default=0, nullable=False)


class Coupon(Base):
    """Promotional discount coupons with usage quotas and expiry."""
    __tablename__ = "coupons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, nullable=False, index=True)
    discount_type = Column(SQLEnum(DiscountType), default=DiscountType.PERCENT, nullable=False)
    value = Column(Numeric(18, 4), nullable=False)  # 15.0 for 15% or $5.00 fixed
    min_order_usd = Column(Numeric(18, 4), default=Decimal("0.0"), nullable=False)
    max_uses = Column(Integer, nullable=True)  # None = unlimited
    used_count = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    @property
    def discount_value(self):
        return self.value

    @property
    def valid_until(self):
        return self.expires_at

    @property
    def valid_from(self):
        return None


class CustomerReview(Base):
    """Verified customer reviews submitted post-delivery."""
    __tablename__ = "customer_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False, unique=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    rating = Column(Integer, nullable=False)  # 1 to 5
    comment = Column(Text, nullable=True)
    is_published_to_channel = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class TopUpBonusTier(Base):
    """Configurable top-up bonus rules (e.g. deposit >= $50 -> +$5.00 bonus)."""
    __tablename__ = "topup_bonus_tiers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    min_deposit_usd = Column(Numeric(18, 4), nullable=False)
    bonus_usd = Column(Numeric(18, 4), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class BroadcastCampaign(Base):
    """Safe rate-limited broadcast campaigns launched from Dashboard."""
    __tablename__ = "broadcast_campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(200), nullable=False)
    message_text = Column(Text, nullable=False)
    image_url = Column(String(500), nullable=True)
    cta_button_text = Column(String(100), nullable=True)
    cta_button_url = Column(String(500), nullable=True)
    target_audience = Column(String(50), default="all", nullable=False)  # 'all', 'buyers_only', 'balance_holders'
    status = Column(String(30), default="pending", nullable=False)       # 'pending', 'running', 'completed', 'failed'
    total_targets = Column(Integer, default=0, nullable=False)
    sent_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class ReferralCampaign(Base):
    """Viral referral milestone giveaway (e.g. invite 3 friends → free account)."""
    __tablename__ = "referral_campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(200), nullable=False)
    reward_name = Column(String(200), nullable=False)
    required_invites = Column(Integer, nullable=False, default=3)
    max_claims_per_user = Column(Integer, nullable=False, default=1)
    require_channel_sub = Column(Boolean, nullable=False, default=True)
    auto_deliver = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    reward_items = relationship(
        "CampaignRewardItem", back_populates="campaign", cascade="all, delete-orphan"
    )
    invite_records = relationship(
        "CampaignInviteRecord", back_populates="campaign", cascade="all, delete-orphan"
    )


class CampaignRewardItem(Base):
    """Encrypted local stock pool for a referral campaign."""
    __tablename__ = "campaign_reward_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id = Column(
        UUID(as_uuid=True), ForeignKey("referral_campaigns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    payload_encrypted = Column(LargeBinary, nullable=False)
    is_claimed = Column(Boolean, nullable=False, default=False, index=True)
    claimed_by_customer_id = Column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    campaign = relationship("ReferralCampaign", back_populates="reward_items")


class CampaignInviteRecord(Base):
    """One invitee can count at most once per campaign (anti-fraud)."""
    __tablename__ = "campaign_invite_records"
    __table_args__ = (
        UniqueConstraint("campaign_id", "invitee_id", name="uq_campaign_invitee"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id = Column(
        UUID(as_uuid=True), ForeignKey("referral_campaigns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    referrer_id = Column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    invitee_id = Column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    is_verified = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    campaign = relationship("ReferralCampaign", back_populates="invite_records")


class StockAlertSubscription(Base):
    """Notify-me-when-back-in-stock subscriptions (customer × product)."""
    __tablename__ = "stock_alert_subscriptions"
    __table_args__ = (
        UniqueConstraint("customer_id", "master_product_id", name="uq_stock_alert_customer_product"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    master_product_id = Column(
        UUID(as_uuid=True), ForeignKey("master_products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    notified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class WarrantyClaim(Base):
    """Customer warranty / replacement claim within warranty_days window."""
    __tablename__ = "warranty_claims"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(30), default="open", nullable=False, index=True)  # open|resolved|refunded|rejected
    issue_description = Column(Text, nullable=False)
    screenshot_file_id = Column(String(200), nullable=True)
    original_delivered = Column(Text, nullable=True)
    replacement_delivered = Column(Text, nullable=True)
    reject_reason = Column(Text, nullable=True)
    resolved_by = Column(String(100), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    order = relationship("Order")
    customer = relationship("Customer")


class LocalStockItem(Base):
    """Fernet-encrypted local inventory keys/accounts (no supplier API required)."""
    __tablename__ = "local_stock_items"
    __table_args__ = (
        Index("idx_local_stock_product_avail", "master_product_id", "is_sold"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    master_product_id = Column(
        UUID(as_uuid=True), ForeignKey("master_products.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    payload_encrypted = Column(LargeBinary, nullable=False)
    is_sold = Column(Boolean, default=False, nullable=False, index=True)
    sold_order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=True)
    sold_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    created_by = Column(String(100), nullable=True)

    master_product = relationship("MasterProduct")


class CompetitorSource(Base):
    """Public channel/URL watched by Competitor Radar."""
    __tablename__ = "competitor_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(120), nullable=False)
    source_type = Column(String(30), default="telegram_web", nullable=False)  # telegram_web | url
    handle_or_url = Column(String(300), nullable=False)  # @channel or https://...
    is_active = Column(Boolean, default=True, nullable=False)
    # keyword → master_product_id mappings: [{"keyword": "chatgpt", "product_id": "..."}]
    keyword_map = Column(JSONB, default=list, nullable=False)
    created_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False)


class CompetitorPriceSnapshot(Base):
    __tablename__ = "competitor_price_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(
        UUID(as_uuid=True), ForeignKey("competitor_sources.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    master_product_id = Column(
        UUID(as_uuid=True), ForeignKey("master_products.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    competitor_name = Column(String(120), nullable=True)
    product_label = Column(String(300), nullable=True)
    price_usd = Column(Numeric(18, 4), nullable=False)
    raw_snippet = Column(Text, nullable=True)
    observed_at = Column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)

    source = relationship("CompetitorSource")
    master_product = relationship("MasterProduct")

