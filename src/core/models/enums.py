from enum import Enum


class SupplierStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    CIRCUIT_OPEN = "circuit_open"
    HALF_OPEN = "half_open"
    MAINTENANCE = "maintenance"


class PaymentNetwork(str, Enum):
    USDT_BEP20 = "usdt_bep20"
    USDT_TRC20 = "usdt_trc20"
    BINANCE_PAY = "binance_pay"
    MANUAL = "manual"


class PricingRuleType(str, Enum):
    FIXED = "fixed"            # e.g. +$5.00 fixed dollar markup
    PERCENTAGE = "percentage"  # e.g. +25%
    FLOOR = "floor"            # Fixed retail price override (e.g. $15.00), raised by profit guard if cost spikes
    FIXED_PRICE = "fixed_price"  # Alias of FLOOR — fixed retail override


class PricingScope(str, Enum):
    GLOBAL = "global"
    CATEGORY = "category"
    PRODUCT = "product"


class PaymentMethod(str, Enum):
    USDT_BEP20_DIRECT = "usdt_bep20_direct"
    USDT_BEP20_GATEWAY = "usdt_bep20_gateway"
    USDT_TRC20_DIRECT = "usdt_trc20_direct"
    BINANCE_PAY = "binance_pay"
    SHAM_CASH = "sham_cash"
    INTERNAL_BALANCE = "internal_balance"


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    DETECTED = "detected"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    REJECTED = "rejected"
    PARTIAL = "partial"


class OrderState(str, Enum):
    CREATED = "created"
    AWAITING_PAYMENT = "awaiting_payment"
    SHAM_CASH_REVIEW = "sham_cash_review"
    PAYMENT_CONFIRMED = "payment_confirmed"
    QUOTE_LOCKED = "quote_locked"
    JIT_FUNDING_INITIATED = "jit_funding_initiated"
    JIT_FUNDING_CONFIRMED = "jit_funding_confirmed"
    SUPPLIER_ORDER_PLACED = "supplier_order_placed"
    SUPPLIER_ORDER_RECOVERY = "supplier_order_recovery"
    PENDING_SUPPLIER_DELIVERY = "pending_supplier_delivery"
    PENDING_MANUAL_DELIVERY = "pending_manual_delivery"
    ORDER_FULFILLED = "order_fulfilled"
    DELIVERED = "delivered"
    COMPLETED = "completed"
    FALLBACK_ROUTING = "fallback_routing"
    FAILED_REFUNDED = "failed_refunded"
    FAILED_MANUAL_REVIEW = "failed_manual_review"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    DISPUTE_OPEN = "dispute_open"


class JitTxState(str, Enum):
    PENDING = "pending"
    BROADCASTED = "broadcasted"
    CONFIRMING = "confirming"
    CONFIRMED = "confirmed"
    CREDITED = "credited"
    FAILED = "failed"
    EXPIRED = "expired"


class DiscountType(str, Enum):
    FIXED = "fixed"
    PERCENT = "percent"


class WalletTxType(str, Enum):
    CUSTOMER_DEPOSIT = "customer_deposit"
    ORDER_CHARGE = "order_charge"
    JIT_OUTBOUND = "jit_outbound"
    PROFIT_CAPTURE = "profit_capture"
    REFUND_CREDIT = "refund_credit"
    GAS_EXPENSE = "gas_expense"
    MANUAL_ADJUSTMENT = "manual_adjustment"
    PURCHASE_DEBIT = "purchase_debit"
    ADMIN_CREDIT = "admin_credit"
    ADMIN_DEBIT = "admin_debit"
    PARTIAL_PAYMENT_CREDITED = "partial_payment_credited"
    LATE_PAYMENT_CREDITED = "late_payment_credited"
    OVERPAYMENT_CREDITED = "overpayment_credited"
    SPLIT_PAYMENT_RESERVE = "split_payment_reserve"
    SPLIT_PAYMENT_REFUND = "split_payment_refund"
    CASHBACK = "cashback"
    REFERRAL_COMMISSION = "referral_commission"
    TOPUP_BONUS = "topup_bonus"
    INCENTIVE_CLAWBACK = "incentive_clawback"


class CustomerStatus(str, Enum):
    ACTIVE = "active"
    BANNED = "banned"


class TicketStatus(str, Enum):
    OPEN = "open"
    AWAITING_CUSTOMER = "awaiting_customer"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class VipTier(str, Enum):
    STANDARD = "STANDARD"
    SILVER = "SILVER"
    GOLD = "GOLD"
    WHOLESALE = "WHOLESALE"


class WarrantyClaimStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    REFUNDED = "refunded"
    REJECTED = "rejected"


class FulfillmentMode(str, Enum):
    API_FIRST = "api_first"
    LOCAL_FIRST = "local_first"
    LOCAL_ONLY = "local_only"


class CustomerInputType(str, Enum):
    EMAIL = "email"
    LINK = "link"
    USERNAME = "username"
    TEXT = "text"
