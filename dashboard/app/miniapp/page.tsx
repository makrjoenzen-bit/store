"use client";

import React, { useState, useEffect, useMemo, useCallback } from "react";
import {
  ShoppingBag, Wallet, Code2, User, Search, Plus, Minus, X,
  Check, Copy, ArrowUpRight, Clock, AlertCircle, RefreshCw,
  Sparkles, ExternalLink, ShieldCheck, Tag, Send, CheckCircle2,
  Bell, ChevronRight, Layers, Flame, DollarSign, TrendingUp,
  Receipt, MessageSquareWarning, HelpCircle, ArrowRight
} from "lucide-react";

// Types
interface Category {
  id: string;
  name_ar: string;
  name_en?: string;
  icon_emoji: string;
  product_count: number;
}

interface Product {
  id: string;
  category_id: string;
  title_ar: string;
  title_en?: string;
  description_ar: string;
  custom_emoji: string;
  photo_url?: string;
  retail_price: number;
  is_in_stock: boolean;
  available_stock?: number | null;
  is_unlimited: boolean;
  warranty_days: number;
  requires_customer_email?: boolean;
  requires_customer_input?: boolean;
  customer_input_type?: string;
  customer_input_label_ar?: string;
  brand_family?: string;
  delivery_type_label?: string;
  is_featured?: boolean;
  search_keywords?: string;
  sort_order: number;
}

interface CustomerProfile {
  id: string;
  telegram_id: number;
  telegram_username?: string;
  first_name?: string;
  language_code: string;
  wallet_balance: number;
  total_deposited: number;
  total_spent: number;
  completed_orders_count: number;
  vip_tier: string;
  vip_progress: {
    next_tier: string | null;
    target_spent: number;
    progress_percent: number;
  };
  referral_code: string;
  referral_earnings: number;
  referral_count: number;
  api_enabled: boolean;
  api_key_prefix?: string;
}

interface PendingInvoice {
  id: string;
  payment_method: string;
  amount_expected: number;
  unique_amount: number;
  deposit_address: string;
  currency: string;
  status: string;
  expires_at: string;
  remaining_seconds: number;
  order_id?: string;
}

interface CartItem {
  product: Product;
  quantity: number;
}

interface WalletTx {
  id: string;
  amount: number;
  balance_after: number;
  tx_type: string;
  description: string;
  created_at: string;
}

interface OrderHistoryItem {
  id: string;
  order_number: string;
  product_title: string;
  product_emoji: string;
  quantity: number;
  our_price: number;
  state: string;
  delivered_product?: string;
  warranty_expires_at?: string;
  is_warranty_active: boolean;
  formal_invoice_number?: string;
  created_at: string;
}

export default function MiniAppPage() {
  // Navigation Tabs: 'store' | 'wallet' | 'api' | 'account'
  const [activeTab, setActiveTab] = useState<"store" | "wallet" | "api" | "account">("store");

  // State
  const [profile, setProfile] = useState<CustomerProfile | null>(null);
  const [pendingInvoices, setPendingInvoices] = useState<PendingInvoice[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lang, setLang] = useState<"ar" | "en">("ar");

  // Store Filters
  const [selectedCatId, setSelectedCatId] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [onlyInStock, setOnlyInStock] = useState(false);
  const [sortBy, setSortBy] = useState<"default" | "price_asc" | "alpha">("default");

  // Cart & Drawers
  const [cart, setCart] = useState<CartItem[]>([]);
  const [isCartOpen, setIsCartOpen] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [drawerQty, setDrawerQty] = useState(1);
  const [couponCode, setCouponCode] = useState("");
  const [buyerEmail, setBuyerEmail] = useState("");
  const [checkingOut, setCheckingOut] = useState(false);

  // Active Payment Modal
  const [activeInvoice, setActiveInvoice] = useState<PendingInvoice | null>(null);
  const [invoiceTimer, setInvoiceTimer] = useState<number>(1800);
  const [isCheckingPayment, setIsCheckingPayment] = useState(false);

  // Wallet Top-Up Form Modal
  const [isTopUpOpen, setIsTopUpOpen] = useState(false);
  const [topUpAmount, setTopUpAmount] = useState<string>("25");
  const [topUpNetwork, setTopUpNetwork] = useState<string>("usdt_bep20");
  const [submittingTopUp, setSubmittingTopUp] = useState(false);

  // Reseller API
  const [resellerKey, setResellerKey] = useState<string | null>(null);
  const [isGeneratingKey, setIsGeneratingKey] = useState(false);
  const [activeApiDoc, setActiveApiDoc] = useState<"balance" | "products" | "orders">("balance");

  // Account
  const [orders, setOrders] = useState<OrderHistoryItem[]>([]);
  const [ledgerTxs, setLedgerTxs] = useState<WalletTx[]>([]);
  const [isTicketOpen, setIsTicketOpen] = useState(false);
  const [ticketOrder, setTicketOrder] = useState<string>("");
  const [ticketSubject, setTicketSubject] = useState("");
  const [ticketMessage, setTicketMessage] = useState("");
  const [submittingTicket, setSubmittingTicket] = useState(false);

  // UI Toast
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" | "info" } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const showToast = (msg: string, type: "success" | "error" | "info" = "info") => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3500);
  };

  const hapticFeedback = useCallback((type: "light" | "medium" | "heavy" | "success" | "warning" = "light") => {
    try {
      if (typeof window !== "undefined") {
        const tg = (window as any).Telegram?.WebApp;
        if (tg?.HapticFeedback) {
          if (type === "success" || type === "warning") {
            tg.HapticFeedback.notificationOccurred(type);
          } else {
            tg.HapticFeedback.impactOccurred(type);
          }
        }
      }
    } catch (e) {
      // Haptics not available in browser
    }
  }, []);

  const getAuthHeaders = useCallback((): HeadersInit => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    try {
      if (typeof window !== "undefined") {
        const tg = (window as any).Telegram?.WebApp;
        if (tg?.initData) {
          headers["X-Telegram-Init-Data"] = tg.initData;
        }
      }
    } catch (e) {
      // fallback
    }
    // Dev fallback for browser testing
    headers["X-Dev-Telegram-Id"] = "987654321";
    return headers;
  }, []);

  // Fetch Core Profile & Invoices
  const fetchProfile = useCallback(async () => {
    try {
      const res = await fetch("/api/miniapp/profile", { headers: getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        setProfile(data.customer);
        setPendingInvoices(data.pending_invoices || []);
        if (data.customer?.language_code) {
          setLang(data.customer.language_code === "en" ? "en" : "ar");
        }
        setLoadError(null);
      } else {
        setLoadError("تعذر تحميل الملف الشخصي");
      }
    } catch (e) {
      console.error("Profile fetch error:", e);
      setLoadError("فشل الاتصال — تحقق من الشبكة");
    }
  }, [getAuthHeaders]);

  // Fetch Catalog
  const fetchCatalog = useCallback(async () => {
    try {
      const res = await fetch("/api/miniapp/catalog", { headers: getAuthHeaders() });
      if (res.ok) {
        const data = await res.json();
        setCategories(data.categories || []);
        setProducts(data.products || []);
        setLoadError(null);
      } else {
        setLoadError("تعذر تحميل الكتالوج");
      }
    } catch (e) {
      console.error("Catalog fetch error:", e);
      setLoadError("فشل تحميل المنتجات");
    }
  }, [getAuthHeaders]);

  const reloadAll = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    await Promise.all([fetchProfile(), fetchCatalog()]);
    setLoading(false);
  }, [fetchProfile, fetchCatalog]);

  // Initial Load & Telegram Init
  useEffect(() => {
    try {
      const tg = (window as any).Telegram?.WebApp;
      if (tg) {
        tg.ready();
        tg.expand();
      }
    } catch (e) {
      // Not in telegram webview
    }
    reloadAll();
  }, [reloadAll]);

  // Closing confirmation ONLY while payment / cart / product drawer open
  useEffect(() => {
    try {
      const tg = (window as any).Telegram?.WebApp;
      if (!tg) return;
      const lock = !!(activeInvoice || isCartOpen || selectedProduct || isTopUpOpen);
      if (lock) tg.enableClosingConfirmation?.();
      else tg.disableClosingConfirmation?.();
    } catch (_) {
      /* ignore */
    }
  }, [activeInvoice, isCartOpen, selectedProduct, isTopUpOpen]);

  // Native Telegram BackButton
  useEffect(() => {
    try {
      const tg = (window as any).Telegram?.WebApp;
      if (!tg?.BackButton) return;

      const onBack = () => {
        hapticFeedback("light");
        if (activeInvoice) {
          setActiveInvoice(null);
          return;
        }
        if (isTopUpOpen) {
          setIsTopUpOpen(false);
          return;
        }
        if (isCartOpen) {
          setIsCartOpen(false);
          return;
        }
        if (selectedProduct) {
          setSelectedProduct(null);
          return;
        }
        if (isTicketOpen) {
          setIsTicketOpen(false);
          return;
        }
        if (activeTab !== "store") {
          setActiveTab("store");
          return;
        }
        tg.BackButton.hide();
      };

      const nested =
        !!(activeInvoice || isTopUpOpen || isCartOpen || selectedProduct || isTicketOpen) ||
        activeTab !== "store";
      if (nested) {
        tg.BackButton.show();
        tg.BackButton.onClick(onBack);
      } else {
        tg.BackButton.hide();
      }
      return () => {
        try {
          tg.BackButton.offClick?.(onBack);
        } catch (_) {
          /* ignore */
        }
      };
    } catch (_) {
      /* ignore */
    }
  }, [
    activeInvoice,
    isTopUpOpen,
    isCartOpen,
    selectedProduct,
    isTicketOpen,
    activeTab,
    hapticFeedback,
  ]);

  // Coherence: refresh catalog when tab becomes visible (Dashboard/Admin Bot edits)
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "visible") {
        fetchCatalog();
        fetchProfile();
      }
    };
    document.addEventListener("visibilitychange", onVis);
    const poll = setInterval(() => {
      if (document.visibilityState === "visible") fetchCatalog();
    }, 20000);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      clearInterval(poll);
    };
  }, [fetchCatalog, fetchProfile]);

  // Countdown timer for active invoice
  useEffect(() => {
    if (!activeInvoice) return;
    setInvoiceTimer(activeInvoice.remaining_seconds || 1800);

    const interval = setInterval(() => {
      setInvoiceTimer((prev) => {
        if (prev <= 1) {
          clearInterval(interval);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(interval);
  }, [activeInvoice]);

  // Refresh Orders & Ledger when tab changes
  useEffect(() => {
    if (activeTab === "wallet") {
      fetch("/api/miniapp/wallet/history", { headers: getAuthHeaders() })
        .then((r) => r.json())
        .then((d) => setLedgerTxs(d.transactions || []))
        .catch(console.error);
    } else if (activeTab === "account") {
      fetch("/api/miniapp/orders", { headers: getAuthHeaders() })
        .then((r) => r.json())
        .then((d) => setOrders(d.orders || []))
        .catch(console.error);
    }
  }, [activeTab, getAuthHeaders]);

  // Filtered & Sorted Products
  const filteredProducts = useMemo(() => {
    let list = [...products];

    if (selectedCatId !== "all") {
      list = list.filter((p) => p.category_id === selectedCatId);
    }

    if (onlyInStock) {
      list = list.filter((p) => p.is_in_stock);
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter(
        (p) =>
          p.title_ar.toLowerCase().includes(q) ||
          (p.title_en && p.title_en.toLowerCase().includes(q)) ||
          p.description_ar.toLowerCase().includes(q)
      );
    }

    if (sortBy === "price_asc") {
      list.sort((a, b) => a.retail_price - b.retail_price);
    } else if (sortBy === "alpha") {
      list.sort((a, b) => a.title_ar.localeCompare(b.title_ar, "ar"));
    }

    return list;
  }, [products, selectedCatId, onlyInStock, searchQuery, sortBy]);

  // Cart total & item count
  const cartTotal = useMemo(() => {
    return cart.reduce((sum, item) => sum + item.product.retail_price * item.quantity, 0);
  }, [cart]);

  const cartItemCount = useMemo(() => {
    return cart.reduce((count, item) => count + item.quantity, 0);
  }, [cart]);

  // Add product to cart
  const addToCart = (product: Product, qty: number = 1) => {
    hapticFeedback("medium");
    setCart((prev) => {
      const idx = prev.findIndex((i) => i.product.id === product.id);
      if (idx >= 0) {
        const next = [...prev];
        next[idx].quantity += qty;
        return next;
      }
      return [...prev, { product, quantity: qty }];
    });
    showToast(`تمت إضافة ${qty} × ${product.title_ar} إلى السلة! 🛍️`, "success");
    setSelectedProduct(null);
  };

  // Remove or update cart item
  const updateCartQty = (productId: string, newQty: number) => {
    hapticFeedback("light");
    if (newQty <= 0) {
      setCart((prev) => prev.filter((i) => i.product.id !== productId));
    } else {
      setCart((prev) =>
        prev.map((i) => (i.product.id === productId ? { ...i, quantity: newQty } : i))
      );
    }
  };

  // 1-Click Instant Checkout
  const handleCheckout = async (items: { product_id: string; quantity: number }[], payViaWallet: boolean = true) => {
    const needsEmail =
      items.some((it) => {
        const p =
          cart.find((c) => c.product.id === it.product_id)?.product ||
          (selectedProduct?.id === it.product_id ? selectedProduct : null);
        return !!p?.requires_customer_email;
      });
    if (needsEmail && !buyerEmail.trim()) {
      showToast("📧 أدخل البريد الإلكتروني للتفعيل قبل إتمام الطلب", "error");
      return;
    }
    setCheckingOut(true);
    hapticFeedback("medium");
    try {
      const res = await fetch("/api/miniapp/cart/checkout", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          items,
          pay_via_wallet: payViaWallet,
          coupon_code: couponCode.trim() || undefined,
          buyer_email: buyerEmail.trim() || undefined,
        }),
      });

      const data = await res.json();

      if (res.ok) {
        if (data.status === "paid") {
          hapticFeedback("success");
          showToast("🎉 تم تنفيذ طلبك بنجاح وتسليم المفتاح!", "success");
          setCart([]);
          setIsCartOpen(false);
          setSelectedProduct(null);
          await fetchProfile();
          setActiveTab("account");
        } else if (data.status === "insufficient_balance") {
          // Open Top-Up with exact deficit!
          hapticFeedback("warning");
          setTopUpAmount(String(Math.ceil(data.deficit)));
          setIsTopUpOpen(true);
          showToast(`ينقصك $${data.deficit.toFixed(2)} لإتمام هذا الطلب. اشحن الرصيد مباشرة أدناه:`, "info");
        } else if (data.status === "invoice_created") {
          setActiveInvoice(data.invoice);
          setPendingInvoices((prev) => [data.invoice, ...prev]);
          setIsCartOpen(false);
          setSelectedProduct(null);
          showToast("تم إنشاء الفاتورة بنجاح. أرسل المبلغ لتأكيد الطلب ⚡", "info");
        }
      } else {
        showToast(data.detail || "حدث خطأ أثناء إتمام الطلب", "error");
      }
    } catch (e) {
      showToast("تعذر الاتصال بالخادم", "error");
    } finally {
      setCheckingOut(false);
    }
  };

  // Create Direct Top-Up Invoice
  const handleCreateTopUp = async () => {
    const val = parseFloat(topUpAmount);
    if (isNaN(val) || val <= 0.5) {
      showToast("يرجى إدخال مبلغ صالح (أكبر من $0.50)", "error");
      return;
    }

    setSubmittingTopUp(true);
    hapticFeedback("medium");
    try {
      const res = await fetch("/api/miniapp/wallet/topup", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          amount: val,
          network: topUpNetwork,
        }),
      });

      const data = await res.json();
      if (res.ok && data.invoice) {
        setActiveInvoice(data.invoice);
        setPendingInvoices((prev) => [data.invoice, ...prev]);
        setIsTopUpOpen(false);
        showToast("تم تجهيز عنوان الإيداع ومبلغ الشحن بنجاح! ⚡", "success");
      } else {
        showToast(data.detail || "تعذر إنشاء فاتورة الشحن", "error");
      }
    } catch (e) {
      showToast("حدث خطأ في الاتصال", "error");
    } finally {
      setSubmittingTopUp(false);
    }
  };

  // Poll Invoice Status
  const handleCheckPaymentStatus = async () => {
    if (!activeInvoice) return;
    setIsCheckingPayment(true);
    hapticFeedback("light");
    try {
      const res = await fetch(`/api/miniapp/payments/${activeInvoice.id}/status`, {
        headers: getAuthHeaders(),
      });
      const data = await res.json();
      if (res.ok) {
        if (data.is_confirmed) {
          hapticFeedback("success");
          showToast("✅ تم تأكيد استلام الدفعة وقيد الرصيد بنجاح!", "success");
          setActiveInvoice(null);
          await fetchProfile();
        } else if (data.is_expired) {
          showToast("⚠️ انتهت صلاحية هذه الفاتورة.", "error");
          setActiveInvoice(null);
          await fetchProfile();
        } else {
          showToast("⏳ جاري التحقق من المعاملة عبر البلوكشين... لم يتم رصد التحويل بعد.", "info");
        }
      }
    } catch (e) {
      showToast("خطأ في التحقق من الدفع", "error");
    } finally {
      setIsCheckingPayment(false);
    }
  };

  // Cancel Invoice
  const handleCancelInvoice = async (invoiceId: string) => {
    hapticFeedback("warning");
    try {
      const res = await fetch(`/api/miniapp/payments/${invoiceId}/cancel`, {
        method: "POST",
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        showToast("تم إلغاء الفاتورة بنجاح.", "info");
        setActiveInvoice(null);
        setPendingInvoices((prev) => prev.filter((i) => i.id !== invoiceId));
      }
    } catch (e) {
      showToast("فشل إلغاء الفاتورة", "error");
    }
  };

  // Reseller Generate Key
  const handleGenerateResellerKey = async () => {
    setIsGeneratingKey(true);
    hapticFeedback("medium");
    try {
      const res = await fetch("/api/miniapp/reseller/generate-key", {
        method: "POST",
        headers: getAuthHeaders(),
      });
      const data = await res.json();
      if (res.ok) {
        setResellerKey(data.api_key);
        showToast("✅ تم توليد مفتاح API الخاص بك بنجاح! انسخه الآن.", "success");
        await fetchProfile();
      } else {
        showToast(data.detail || "فشل توليد المفتاح", "error");
      }
    } catch (e) {
      showToast("خطأ في الاتصال", "error");
    } finally {
      setIsGeneratingKey(false);
    }
  };

  // Submit Support Ticket
  const handleSubmitTicket = async () => {
    if (!ticketSubject.trim() || !ticketMessage.trim()) {
      showToast("يرجى ملء موضوع التذكرة وشرح المشكلة بالتفصيل.", "error");
      return;
    }
    setSubmittingTicket(true);
    hapticFeedback("medium");
    try {
      const res = await fetch("/api/miniapp/tickets", {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          order_id: ticketOrder || undefined,
          subject: ticketSubject.trim(),
          message: ticketMessage.trim(),
        }),
      });
      const data = await res.json();
      if (res.ok) {
        showToast("📩 تم إرسال تذكرتك لفريق الدعم، سيصلك الرد هنا وبالبوت!", "success");
        setIsTicketOpen(false);
        setTicketSubject("");
        setTicketMessage("");
        setTicketOrder("");
      } else {
        showToast(data.detail || "فشل إرسال التذكرة", "error");
      }
    } catch (e) {
      showToast("خطأ في الاتصال", "error");
    } finally {
      setSubmittingTicket(false);
    }
  };

  const copyToClipboard = (text: string, label: string = "النص") => {
    hapticFeedback("light");
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(text);
    }
    showToast(`تم نسخ ${label} بنجاح! 📋`, "success");
  };

  // Format seconds to MM:SS
  const formatTimer = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  };

  const orderStateBadge = (state: string) => {
    const map: Record<string, { label: string; cls: string }> = {
      awaiting_payment: { label: "⏳ بانتظار الدفع", cls: "bg-amber-500/20 text-amber-300 border-amber-500/30" },
      created: { label: "⏳ بانتظار الدفع", cls: "bg-amber-500/20 text-amber-300 border-amber-500/30" },
      sham_cash_review: { label: "⏳ مراجعة شام كاش", cls: "bg-amber-500/20 text-amber-300 border-amber-500/30" },
      payment_confirmed: { label: "⚡ جاري التسليم", cls: "bg-blue-500/20 text-blue-300 border-blue-500/30" },
      quote_locked: { label: "⚡ جاري التسليم", cls: "bg-blue-500/20 text-blue-300 border-blue-500/30" },
      order_fulfilled: { label: "⚡ جاري التسليم", cls: "bg-blue-500/20 text-blue-300 border-blue-500/30" },
      delivered: { label: "⚡ جاري التسليم", cls: "bg-blue-500/20 text-blue-300 border-blue-500/30" },
      completed: { label: "✅ مكتمل", cls: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30" },
      rejected: { label: "❌ مرفوض", cls: "bg-rose-500/20 text-rose-300 border-rose-500/30" },
      cancelled: { label: "❌ ملغى", cls: "bg-slate-500/20 text-slate-300 border-slate-500/30" },
      failed_refunded: { label: "💸 مسترد للمحفظة", cls: "bg-violet-500/20 text-violet-300 border-violet-500/30" },
      failed_manual_review: { label: "⚠️ قيد المراجعة", cls: "bg-orange-500/20 text-orange-300 border-orange-500/30" },
    };
    return map[state] || { label: state, cls: "bg-slate-500/20 text-slate-300 border-slate-500/30" };
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-[#060B14] text-white max-w-md mx-auto px-4 pt-6 space-y-4">
        <div className="h-10 w-40 rounded-xl bg-white/[0.06] animate-pulse" />
        <div className="grid grid-cols-2 gap-3">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-36 rounded-2xl bg-white/[0.05] border border-white/[0.06] animate-pulse" />
          ))}
        </div>
        <div className="h-24 rounded-2xl bg-white/[0.04] animate-pulse" />
        <p className="text-center text-xs text-slate-500 pt-4">جاري التحميل…</p>
      </div>
    );
  }

  if (loadError && !profile && products.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-[#060B14] text-white px-6 gap-4">
        <AlertCircle className="w-10 h-10 text-rose-400" />
        <p className="text-sm font-bold text-center text-rose-200">{loadError}</p>
        <button
          onClick={() => reloadAll()}
          className="px-5 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold flex items-center gap-2"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span>إعادة المحاولة</span>
        </button>
      </div>
    );
  }

  return (
    <div className="relative min-h-screen bg-[#060B14] text-white font-sans pb-28 select-none max-w-md mx-auto border-x border-white/[0.05] shadow-2xl">
      {/* Toast Notification */}
      {toast && (
        <div className={`fixed top-4 left-4 right-4 z-50 max-w-sm mx-auto px-4 py-3 rounded-2xl shadow-2xl backdrop-blur-xl border flex items-center space-x-3 space-x-reverse transition-all animate-in fade-in slide-in-from-top-4 ${
          toast.type === "success"
            ? "bg-emerald-950/90 border-emerald-500/40 text-emerald-200"
            : toast.type === "error"
            ? "bg-rose-950/90 border-rose-500/40 text-rose-200"
            : "bg-blue-950/90 border-blue-500/40 text-blue-200"
        }`}>
          {toast.type === "success" ? <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" /> : <AlertCircle className="w-5 h-5 text-blue-400 shrink-0" />}
          <span className="text-xs font-semibold leading-snug">{toast.msg}</span>
        </div>
      )}

      {/* Persistent Top Header */}
      <header className="sticky top-0 z-30 px-4 py-3 bg-[#060B14]/80 backdrop-blur-xl border-b border-white/[0.06] flex items-center justify-between">
        {/* Right: Brand Logo */}
        <div className="flex items-center space-x-2 space-x-reverse">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-600 flex items-center justify-center text-sm font-black shadow-lg shadow-blue-600/30 border border-blue-400/30">
            ⚡
          </div>
          <div>
            <div className="font-extrabold text-sm tracking-tight text-white flex items-center gap-1">
              <span>متجر الاشتراكات</span>
              <span className="text-[9px] font-mono uppercase px-1.5 py-0.2 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30">
                LARA
              </span>
            </div>
            <div className="text-[10px] text-slate-400 font-mono">Digital Instant JIT</div>
          </div>
        </div>

        {/* Left: Controls (Bell, Wallet Pill, Lang Toggle) */}
        <div className="flex items-center space-x-2 space-x-reverse">
          {/* Wallet Balance Pill */}
          <button
            onClick={() => {
              hapticFeedback("light");
              setActiveTab("wallet");
            }}
            className="flex items-center space-x-1.5 space-x-reverse px-2.5 py-1 rounded-xl bg-[#0B1528] hover:bg-[#11203D] border border-blue-500/30 text-xs shadow-inner transition"
          >
            <Wallet className="w-3.5 h-3.5 text-blue-400" />
            <span className="font-mono font-bold text-white text-xs">
              ${profile?.wallet_balance.toFixed(2) || "0.00"}
            </span>
            <span className="text-[9px] text-blue-400 font-bold">💳</span>
          </button>

          {/* Lang Toggle */}
          <button
            onClick={() => {
              hapticFeedback("light");
              setLang(lang === "ar" ? "en" : "ar");
            }}
            className="w-7 h-7 rounded-xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center text-[10px] font-bold text-slate-300 hover:text-white"
          >
            {lang === "ar" ? "EN" : "عربي"}
          </button>
        </div>
      </header>

      {/* Pending Payment Alert Banner (If Any Awaiting Payment) */}
      {pendingInvoices.length > 0 && (
        <div className="mx-4 mt-3 p-3 rounded-2xl bg-gradient-to-r from-amber-500/15 via-amber-500/10 to-transparent border border-amber-500/30 flex items-center justify-between shadow-lg shadow-amber-500/5 animate-pulse">
          <div className="flex items-center space-x-2.5 space-x-reverse">
            <div className="w-7 h-7 rounded-lg bg-amber-500/20 flex items-center justify-center text-amber-400 shrink-0">
              <Clock className="w-4 h-4 animate-spin" />
            </div>
            <div>
              <div className="text-xs font-bold text-amber-200">
                بانتظار دفعتك • {pendingInvoices[0].payment_method.toUpperCase()} • ${pendingInvoices[0].unique_amount.toFixed(4)}
              </div>
              <div className="text-[10px] text-amber-400/80 font-mono">
                متبقي {formatTimer(pendingInvoices[0].remaining_seconds || 1800)} للإلغاء التلقائي
              </div>
            </div>
          </div>
          <button
            onClick={() => {
              hapticFeedback("medium");
              setActiveInvoice(pendingInvoices[0]);
            }}
            className="px-3 py-1 rounded-xl bg-amber-500 text-black text-xs font-bold shadow-md hover:bg-amber-400 transition shrink-0"
          >
            فتح الفاتورة •
          </button>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 1: المتجر (STOREFRONT) */}
      {/* ========================================================================= */}
      {activeTab === "store" && (
        <div className="p-4 space-y-4">
          {/* Hero Row & Floating Cart Trigger */}
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-extrabold text-white">
                أهلاً، {profile?.first_name || "يا بطل"} 👋
              </h2>
              <p className="text-xs text-slate-400">شو حاب تشتري وتسلم فوري اليوم؟</p>
            </div>
            <button
              onClick={() => {
                hapticFeedback("medium");
                setIsCartOpen(true);
              }}
              className="relative p-2.5 rounded-2xl bg-[#0B1528] border border-blue-500/40 text-blue-400 hover:text-white shadow-lg shadow-blue-500/10 transition"
            >
              <ShoppingBag className="w-5 h-5" />
              {cartItemCount > 0 && (
                <span className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-blue-600 text-white text-[10px] font-black flex items-center justify-center border-2 border-[#060B14] shadow animate-bounce">
                  {cartItemCount}
                </span>
              )}
            </button>
          </div>

          {/* Search Bar */}
          <div className="relative">
            <Search className="absolute right-3.5 top-3 w-4 h-4 text-slate-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="🔍 ابحث عن منتج، اشتراك، أو مفتاح..."
              className="w-full pr-10 pl-4 py-2.5 rounded-2xl bg-[#0B1528] border border-white/[0.08] text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500/60 transition shadow-inner"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute left-3 top-3 text-slate-500 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>

          {/* Filter Chips: In-Stock Toggle & Sort */}
          <div className="flex items-center justify-between gap-2 text-xs">
            <button
              onClick={() => {
                hapticFeedback("light");
                setOnlyInStock(!onlyInStock);
              }}
              className={`px-3 py-1.5 rounded-xl border text-[11px] font-semibold transition flex items-center gap-1.5 ${
                onlyInStock
                  ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-300"
                  : "bg-white/[0.03] border-white/[0.08] text-slate-400"
              }`}
            >
              <Check className={`w-3 h-3 ${onlyInStock ? "opacity-100" : "opacity-40"}`} />
              <span>المتوفر فقط ✅</span>
            </button>

            <div className="flex items-center gap-1">
              <button
                onClick={() => {
                  hapticFeedback("light");
                  setSortBy(sortBy === "price_asc" ? "default" : "price_asc");
                }}
                className={`px-2.5 py-1.5 rounded-xl border text-[11px] font-semibold transition ${
                  sortBy === "price_asc"
                    ? "bg-blue-600/30 border-blue-500/40 text-blue-300"
                    : "bg-white/[0.03] border-white/[0.08] text-slate-400"
                }`}
              >
                الأقل سعراً 💵
              </button>
            </div>
          </div>

          {/* Category Horizontal Scrollable Pills */}
          <div className="flex items-center space-x-2 space-x-reverse overflow-x-auto pb-1 scrollbar-none">
            <button
              onClick={() => {
                hapticFeedback("light");
                setSelectedCatId("all");
              }}
              className={`px-3.5 py-1.5 rounded-xl text-xs font-bold shrink-0 transition ${
                selectedCatId === "all"
                  ? "bg-blue-600 text-white shadow-lg shadow-blue-600/40"
                  : "bg-[#0B1528] text-slate-400 border border-white/[0.08] hover:text-white"
              }`}
            >
              الكل ({products.length})
            </button>
            {categories.map((c) => (
              <button
                key={c.id}
                onClick={() => {
                  hapticFeedback("light");
                  setSelectedCatId(c.id);
                }}
                className={`px-3.5 py-1.5 rounded-xl text-xs font-bold shrink-0 transition flex items-center gap-1.5 ${
                  selectedCatId === c.id
                    ? "bg-blue-600 text-white shadow-lg shadow-blue-600/40"
                    : "bg-[#0B1528] text-slate-400 border border-white/[0.08] hover:text-white"
                }`}
              >
                <span>{c.icon_emoji}</span>
                <span>{c.name_ar}</span>
                <span className="text-[10px] opacity-70">({c.product_count})</span>
              </button>
            ))}
          </div>

          {/* 2-Column Product Grid */}
          <div className="grid grid-cols-2 gap-3 pt-1">
            {filteredProducts.map((p) => {
              const isStockWarn = p.available_stock !== null && p.available_stock !== undefined && p.available_stock <= 3 && p.available_stock > 0;
              return (
                <div
                  key={p.id}
                  onClick={() => {
                    hapticFeedback("medium");
                    setSelectedProduct(p);
                    setDrawerQty(1);
                  }}
                  className="group relative p-3 rounded-2xl bg-[#0B1528]/80 hover:bg-[#11203D] border border-white/[0.08] hover:border-blue-500/40 shadow-lg hover:shadow-blue-500/10 backdrop-blur-xl transition flex flex-col justify-between cursor-pointer active:scale-95"
                >
                  {/* Card Header Badges */}
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30 flex items-center gap-0.5">
                      <span>⚡</span> فوري
                    </span>
                    <span
                      className={`text-[10px] font-mono font-bold px-1.5 py-0.5 rounded ${
                        !p.is_in_stock
                          ? "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                          : isStockWarn
                          ? "bg-amber-500/20 text-amber-300 border border-amber-500/30"
                          : "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30"
                      }`}
                    >
                      {!p.is_in_stock
                        ? "نفد"
                        : isStockWarn
                        ? `باقي ${p.available_stock}`
                        : p.is_unlimited
                        ? "متوفر ∞"
                        : `${p.available_stock} 📦`}
                    </span>
                  </div>

                  {/* Center Emoji / Icon */}
                  <div className="my-3 flex items-center justify-center">
                    <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-white/[0.05] to-transparent border border-white/[0.08] flex items-center justify-center text-3xl shadow-inner group-hover:scale-110 transition">
                      {p.custom_emoji || "📦"}
                    </div>
                  </div>

                  {/* Title & Price */}
                  <div>
                    <h3 className="font-bold text-xs text-white line-clamp-1 group-hover:text-blue-300 transition">
                      {p.title_ar}
                    </h3>
                    <div className="mt-1 flex items-baseline justify-between">
                      <span className="font-mono font-black text-sm text-blue-400">
                        ${p.retail_price.toFixed(2)}
                      </span>
                      <span className="text-[10px] text-slate-500 font-mono">USD</span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {filteredProducts.length === 0 && (
            <div className="p-8 text-center bg-[#0B1528] rounded-2xl border border-white/[0.08]">
              <div className="text-3xl mb-2">🔍</div>
              <p className="text-xs text-slate-400">لا توجد منتجات مطابقة لهذا البحث أو الفلتر.</p>
            </div>
          )}
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 2: الرصيد (WALLET & LEDGER) */}
      {/* ========================================================================= */}
      {activeTab === "wallet" && (
        <div className="p-4 space-y-4">
          {/* Vibrant Blue Gradient Balance Card */}
          <div className="relative overflow-hidden p-5 rounded-3xl bg-gradient-to-br from-blue-600 via-indigo-600 to-blue-900 border border-blue-400/30 shadow-2xl shadow-blue-600/30">
            <div className="flex items-center justify-between text-xs text-blue-200">
              <span>الرصيد المتاح بالمحفظة</span>
              <button
                onClick={() => copyToClipboard(String(profile?.telegram_id), "معرف الحساب")}
                className="px-2 py-0.5 rounded-lg bg-white/10 hover:bg-white/20 text-white font-mono text-[10px] flex items-center gap-1 transition"
              >
                <span>ID: {profile?.telegram_id}</span>
                <Copy className="w-3 h-3" />
              </button>
            </div>

            <div className="mt-2 flex items-baseline gap-1">
              <span className="text-3xl font-black font-mono tracking-tight text-white">
                ${profile?.wallet_balance.toFixed(2) || "0.00"}
              </span>
              <span className="text-xs font-mono font-bold text-blue-200">USDT</span>
            </div>

            {/* Circular Action Buttons */}
            <div className="mt-5 grid grid-cols-3 gap-2">
              <button
                onClick={() => {
                  hapticFeedback("medium");
                  setIsTopUpOpen(true);
                }}
                className="py-2.5 rounded-2xl bg-emerald-500 hover:bg-emerald-400 text-black font-bold text-xs flex items-center justify-center gap-1.5 shadow-lg shadow-emerald-500/20 transition active:scale-95"
              >
                <Plus className="w-4 h-4 stroke-[3]" />
                <span>شحن رصيد</span>
              </button>

              <button
                onClick={() => {
                  hapticFeedback("light");
                  fetchProfile();
                  showToast("تم تحديث الرصيد لحظياً 🔄", "info");
                }}
                className="py-2.5 rounded-2xl bg-white/10 hover:bg-white/20 text-white font-semibold text-xs flex items-center justify-center gap-1.5 transition active:scale-95"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                <span>تحديث</span>
              </button>

              <button
                onClick={() => copyToClipboard(profile?.referral_code || "", "كود الإحالة")}
                className="py-2.5 rounded-2xl bg-white/10 hover:bg-white/20 text-white font-semibold text-xs flex items-center justify-center gap-1.5 transition active:scale-95"
              >
                <Sparkles className="w-3.5 h-3.5 text-amber-300" />
                <span>كودك</span>
              </button>
            </div>
          </div>

          {/* 3 KPI Mini-Cards */}
          <div className="grid grid-cols-3 gap-2">
            <div className="p-3 rounded-2xl bg-[#0B1528] border border-white/[0.08] text-center">
              <div className="flex items-center justify-center gap-1 text-[10px] text-slate-400 mb-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
                <span>إجمالي الشحن</span>
              </div>
              <div className="font-mono font-bold text-xs text-white">
                ${profile?.total_deposited.toFixed(2) || "0.00"}
              </div>
            </div>

            <div className="p-3 rounded-2xl bg-[#0B1528] border border-white/[0.08] text-center">
              <div className="flex items-center justify-center gap-1 text-[10px] text-slate-400 mb-1">
                <span className="w-1.5 h-1.5 rounded-full bg-rose-400"></span>
                <span>المصروف</span>
              </div>
              <div className="font-mono font-bold text-xs text-white">
                ${profile?.total_spent.toFixed(2) || "0.00"}
              </div>
            </div>

            <div className="p-3 rounded-2xl bg-[#0B1528] border border-white/[0.08] text-center">
              <div className="flex items-center justify-center gap-1 text-[10px] text-slate-400 mb-1">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-400"></span>
                <span>المشتريات</span>
              </div>
              <div className="font-mono font-bold text-xs text-white">
                {profile?.completed_orders_count || 0} طلب
              </div>
            </div>
          </div>

          {/* Pending Invoices List (if any) */}
          {pendingInvoices.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-bold text-amber-300 flex items-center gap-1.5">
                <Clock className="w-3.5 h-3.5" />
                <span>فواتير قيد الانتظار ({pendingInvoices.length}):</span>
              </h3>
              {pendingInvoices.map((inv) => (
                <div
                  key={inv.id}
                  className="p-3 rounded-2xl bg-amber-500/[0.07] border border-amber-500/30 flex items-center justify-between"
                >
                  <div>
                    <div className="font-mono font-bold text-xs text-white">
                      ${inv.unique_amount.toFixed(4)} {inv.currency}
                    </div>
                    <div className="text-[10px] text-slate-400 font-mono">
                      {inv.payment_method.toUpperCase()}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleCancelInvoice(inv.id)}
                      className="px-2.5 py-1 rounded-xl bg-white/[0.05] hover:bg-rose-500/20 text-slate-400 hover:text-rose-300 border border-white/[0.08] text-[11px] font-bold"
                    >
                      إلغاء ❌
                    </button>
                    <button
                      onClick={() => {
                        hapticFeedback("medium");
                        setActiveInvoice(inv);
                      }}
                      className="px-3 py-1 rounded-xl bg-amber-500 hover:bg-amber-400 text-black text-[11px] font-bold shadow"
                    >
                      فتح 🔍
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Transaction Ledger */}
          <div>
            <h3 className="text-xs font-bold text-slate-400 mb-2 flex items-center gap-1.5">
              <Receipt className="w-3.5 h-3.5" />
              <span>سجل حركات المحفظة:</span>
            </h3>

            <div className="space-y-2">
              {ledgerTxs.map((tx) => {
                const isPositive = tx.amount > 0 && !tx.tx_type.includes("debit") && !tx.tx_type.includes("charge");
                return (
                  <div
                    key={tx.id}
                    className="p-3 rounded-2xl bg-[#0B1528] border border-white/[0.06] flex items-center justify-between"
                  >
                    <div>
                      <div className="text-xs font-bold text-white line-clamp-1">{tx.description}</div>
                      <div className="text-[10px] font-mono text-slate-500">
                        {tx.created_at ? new Date(tx.created_at).toLocaleDateString("ar-EG") : ""}
                      </div>
                    </div>
                    <div className="text-left font-mono">
                      <div className={`text-xs font-bold ${isPositive ? "text-emerald-400" : "text-rose-400"}`}>
                        {isPositive ? `+$${tx.amount.toFixed(2)}` : `-$${Math.abs(tx.amount).toFixed(2)}`}
                      </div>
                      <div className="text-[10px] text-slate-500">
                        ${tx.balance_after.toFixed(2)}
                      </div>
                    </div>
                  </div>
                );
              })}

              {ledgerTxs.length === 0 && (
                <div className="p-6 text-center bg-[#0B1528] rounded-2xl border border-white/[0.06] text-xs text-slate-400">
                  لا توجد حركات سابقة حتى الآن.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 3: API (B2B RESELLER PORTAL) */}
      {/* ========================================================================= */}
      {activeTab === "api" && (
        <div className="p-4 space-y-4">
          {/* Header Card */}
          <div className="p-4 rounded-3xl bg-[#0B1528] border border-blue-500/30 shadow-xl">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-xl bg-blue-500/20 text-blue-400 flex items-center justify-center">
                  <Code2 className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="font-extrabold text-sm text-white">بوابة الموزعين (B2B API)</h3>
                  <p className="text-[10px] text-slate-400">ربط المتاجر الخارجية والتوريد الآلي</p>
                </div>
              </div>
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/40">
                {profile?.vip_tier} TIER
              </span>
            </div>

            {/* API Key Box */}
            <div className="p-3 rounded-2xl bg-black/40 border border-white/[0.08] space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">مفتاح الربط (API Key):</span>
                {resellerKey ? (
                  <button
                    onClick={() => copyToClipboard(resellerKey, "مفتاح API")}
                    className="text-emerald-400 font-bold text-xs flex items-center gap-1 hover:underline"
                  >
                    <span>نسخ المفتاح</span>
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                ) : (
                  <span className="font-mono text-slate-500 text-[11px]">
                    {profile?.api_enabled ? profile.api_key_prefix : "غير مفعل"}
                  </span>
                )}
              </div>

              {resellerKey && (
                <div className="p-2 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 font-mono text-[11px] break-all">
                  {resellerKey}
                </div>
              )}

              <button
                onClick={handleGenerateResellerKey}
                disabled={isGeneratingKey}
                className="w-full py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs shadow-md transition disabled:opacity-50"
              >
                {isGeneratingKey
                  ? "جاري التوليد..."
                  : profile?.api_enabled
                  ? "توليد مفتاح جديد (Regenerate)"
                  : "تفعيل وتوليد مفتاح API 🚀"}
              </button>
            </div>
          </div>

          {/* Interactive Documentation Card */}
          <div className="p-4 rounded-3xl bg-[#0B1528] border border-white/[0.08] space-y-3">
            <h4 className="font-bold text-xs text-white">وثائق الـ REST Endpoints:</h4>

            {/* Doc Tabs */}
            <div className="grid grid-cols-3 gap-1.5 p-1 rounded-2xl bg-black/40 border border-white/[0.08] text-xs">
              <button
                onClick={() => setActiveApiDoc("balance")}
                className={`py-1.5 rounded-xl font-bold transition ${
                  activeApiDoc === "balance" ? "bg-blue-600 text-white" : "text-slate-400 hover:text-white"
                }`}
              >
                1. الرصيد
              </button>
              <button
                onClick={() => setActiveApiDoc("products")}
                className={`py-1.5 rounded-xl font-bold transition ${
                  activeApiDoc === "products" ? "bg-blue-600 text-white" : "text-slate-400 hover:text-white"
                }`}
              >
                2. المنتجات
              </button>
              <button
                onClick={() => setActiveApiDoc("orders")}
                className={`py-1.5 rounded-xl font-bold transition ${
                  activeApiDoc === "orders" ? "bg-blue-600 text-white" : "text-slate-400 hover:text-white"
                }`}
              >
                3. شراء فوري
              </button>
            </div>

            {/* cURL Snippet */}
            <div className="relative p-3 rounded-2xl bg-black/60 border border-white/[0.06] font-mono text-[10px] text-slate-300 overflow-x-auto">
              <button
                onClick={() => {
                  const snippet =
                    activeApiDoc === "balance"
                      ? `curl -X GET "https://api.yourdomain.com/api/v1/reseller/balance" \\\n  -H "X-API-Key: sk_live_..."`
                      : activeApiDoc === "products"
                      ? `curl -X GET "https://api.yourdomain.com/api/v1/reseller/products" \\\n  -H "X-API-Key: sk_live_..."`
                      : `curl -X POST "https://api.yourdomain.com/api/v1/reseller/orders" \\\n  -H "X-API-Key: sk_live_..." \\\n  -H "Content-Type: application/json" \\\n  -d '{"product_id": "...", "quantity": 1}'`;
                  copyToClipboard(snippet, "أمر cURL");
                }}
                className="absolute left-2.5 top-2.5 p-1 rounded bg-white/10 hover:bg-white/20 text-white"
                title="نسخ"
              >
                <Copy className="w-3.5 h-3.5" />
              </button>

              {activeApiDoc === "balance" && (
                <pre className="text-emerald-400 whitespace-pre-wrap">
{`# 1. فحص الرصيد والخصم
GET /api/v1/reseller/balance
Header: X-API-Key: sk_live_...

Response:
{
  "wallet_balance_usdt": 150.00,
  "vip_tier": "GOLD",
  "wholesale_discount_percent": 5.0
}`}
                </pre>
              )}

              {activeApiDoc === "products" && (
                <pre className="text-blue-300 whitespace-pre-wrap">
{`# 2. جلب كتالوج المنتجات بأسعار الجملة
GET /api/v1/reseller/products
Header: X-API-Key: sk_live_...

Response:
{
  "discount_applied": 5.0,
  "products": [
    {
      "product_id": "9cb2...",
      "title": "Telegram Premium 3M",
      "retail_price_usd": 12.00,
      "reseller_price_usd": 11.40,
      "is_in_stock": true
    }
  ]
}`}
                </pre>
              )}

              {activeApiDoc === "orders" && (
                <pre className="text-amber-300 whitespace-pre-wrap">
{`# 3. إرسال طلب شراء فوري (خصم من الرصيد)
POST /api/v1/reseller/orders
Header: X-API-Key: sk_live_...
Body:
{
  "product_id": "9cb2...",
  "quantity": 1,
  "custom_ref": "MY_STORE_ORD_101"
}

Response:
{
  "status": "success",
  "order_number": "ORD-1234",
  "delivered_payload": "KEY-XXXX-YYYY",
  "remaining_wallet_balance": 138.60
}`}
                </pre>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 4: حسابي (MY ACCOUNT HUB) */}
      {/* ========================================================================= */}
      {activeTab === "account" && (
        <div className="p-4 space-y-4">
          {/* Profile Header & VIP Tier Progress */}
          <div className="p-4 rounded-3xl bg-[#0B1528] border border-white/[0.08] space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-3 space-x-reverse">
                <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-indigo-600 to-purple-600 flex items-center justify-center text-lg font-black text-white shadow-lg shadow-indigo-600/30">
                  {profile?.first_name?.charAt(0) || "U"}
                </div>
                <div>
                  <h3 className="font-extrabold text-sm text-white">
                    {profile?.first_name} {profile?.telegram_username ? `(@${profile.telegram_username})` : ""}
                  </h3>
                  <div className="text-[11px] text-slate-400 font-mono">
                    ID: {profile?.telegram_id}
                  </div>
                </div>
              </div>
              <span className="px-2.5 py-1 rounded-xl bg-gradient-to-r from-amber-500/20 to-amber-600/20 border border-amber-500/30 text-amber-300 text-xs font-bold font-mono">
                {profile?.vip_tier}
              </span>
            </div>

            {/* VIP Tier Progress Bar */}
            {profile?.vip_progress?.next_tier && (
              <div className="p-3 rounded-2xl bg-black/40 border border-white/[0.06] space-y-1.5">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-slate-400">الترقية إلى {profile.vip_progress.next_tier}</span>
                  <span className="font-mono text-blue-400 font-bold">
                    ${profile.total_spent.toFixed(1)} / ${profile.vip_progress.target_spent}
                  </span>
                </div>
                <div className="w-full h-2 rounded-full bg-white/[0.06] overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-blue-500 to-indigo-500 rounded-full transition-all duration-500"
                    style={{ width: `${profile.vip_progress.progress_percent}%` }}
                  ></div>
                </div>
              </div>
            )}
          </div>

          {/* Referral Support Card */}
          <div className="p-4 rounded-3xl bg-gradient-to-br from-indigo-950/40 via-[#0B1528] to-purple-950/40 border border-indigo-500/30 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-white flex items-center gap-1.5">
                <Sparkles className="w-4 h-4 text-amber-300" />
                <span>برنامج دعوة الأصدقاء (عمولة 5% دائمة)</span>
              </span>
              <span className="text-[10px] font-mono text-emerald-400 font-bold">
                أرباحك: ${profile?.referral_earnings.toFixed(2)}
              </span>
            </div>
            <p className="text-[11px] text-slate-400">
              دَعَوْتَ {profile?.referral_count || 0} أصدقاء. شارك رابطك واكسب كاش باك دائم في محفظتك!
            </p>
            <button
              onClick={() => {
                const link = `https://t.me/YourBot?start=ref_${profile?.telegram_id}`;
                copyToClipboard(link, "رابط الإحالة");
              }}
              className="w-full py-2 rounded-xl bg-white/[0.08] hover:bg-white/[0.12] text-xs font-bold text-white border border-white/[0.08] flex items-center justify-center gap-1.5 transition"
            >
              <Copy className="w-3.5 h-3.5" />
              <span>نسخ رابط دعوتك الخاص</span>
            </button>
          </div>

          {/* Past Orders List */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-bold text-slate-300 flex items-center gap-1.5">
                <ShoppingBag className="w-3.5 h-3.5 text-blue-400" />
                <span>طلباتي السابقة ({orders.length}):</span>
              </h3>
              <button
                onClick={() => {
                  setTicketOrder("");
                  setIsTicketOpen(true);
                }}
                className="text-[11px] text-blue-400 hover:underline font-bold"
              >
                + فتح تذكرة
              </button>
            </div>

            <div className="space-y-2.5">
              {orders.map((ord) => (
                <div
                  key={ord.id}
                  className="p-3.5 rounded-2xl bg-[#0B1528] border border-white/[0.08] space-y-2"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-xl">{ord.product_emoji}</span>
                      <div>
                        <h4 className="font-bold text-xs text-white">{ord.product_title}</h4>
                        <div className="text-[10px] font-mono text-slate-500">
                          #{ord.order_number} {ord.formal_invoice_number ? `• فاتورة ${ord.formal_invoice_number}` : ""}
                        </div>
                      </div>
                    </div>
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                        orderStateBadge(ord.state).cls
                      }`}
                    >
                      {orderStateBadge(ord.state).label}
                    </span>
                  </div>

                  {/* Delivered Product Key Display */}
                  {ord.delivered_product && (
                    <div className="p-2.5 rounded-xl bg-black/50 border border-white/[0.08] flex items-center justify-between">
                      <code className="font-mono text-[11px] text-blue-300 select-all break-all line-clamp-1">
                        {ord.delivered_product}
                      </code>
                      <button
                        onClick={() => copyToClipboard(ord.delivered_product || "", "بيانات التفعيل")}
                        className="px-2 py-1 rounded bg-blue-600/30 hover:bg-blue-600 text-blue-200 text-[10px] font-bold transition shrink-0 ml-2"
                      >
                        نسخ
                      </button>
                    </div>
                  )}

                  <div className="flex items-center justify-between pt-1 text-[11px] border-t border-white/[0.04]">
                    <span className="text-slate-400 font-mono">
                      ${ord.our_price.toFixed(2)} USD • {ord.quantity} قطعة
                    </span>
                    <button
                      onClick={() => {
                        setTicketOrder(ord.id);
                        setTicketSubject(`مشكلة في الطلب #${ord.order_number}`);
                        setIsTicketOpen(true);
                      }}
                      className="text-amber-400 hover:text-amber-300 font-semibold flex items-center gap-1"
                    >
                      <MessageSquareWarning className="w-3 h-3" />
                      <span>مشكلة بالطلب</span>
                    </button>
                  </div>
                </div>
              ))}

              {orders.length === 0 && (
                <div className="p-6 text-center bg-[#0B1528] rounded-2xl border border-white/[0.08] text-xs text-slate-400">
                  لا توجد طلبات سابقة حتى الآن.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* FLOATING GLASS BOTTOM NAVIGATION BAR (4 TABS) */}
      {/* ========================================================================= */}
      <nav className="fixed bottom-4 left-4 right-4 z-40 max-w-md mx-auto">
        <div className="p-1.5 rounded-3xl bg-[#0B1528]/85 backdrop-blur-2xl border border-white/[0.12] shadow-2xl shadow-blue-900/30 grid grid-cols-4 gap-1">
          <button
            onClick={() => {
              hapticFeedback("light");
              setActiveTab("store");
            }}
            className={`py-2 rounded-2xl flex flex-col items-center justify-center transition-all ${
              activeTab === "store"
                ? "bg-blue-600 text-white shadow-lg shadow-blue-600/40"
                : "text-slate-400 hover:text-white"
            }`}
          >
            <ShoppingBag className="w-4 h-4 mb-0.5" />
            <span className="text-[10px] font-bold">المتجر</span>
          </button>

          <button
            onClick={() => {
              hapticFeedback("light");
              setActiveTab("wallet");
            }}
            className={`py-2 rounded-2xl flex flex-col items-center justify-center transition-all ${
              activeTab === "wallet"
                ? "bg-blue-600 text-white shadow-lg shadow-blue-600/40"
                : "text-slate-400 hover:text-white"
            }`}
          >
            <Wallet className="w-4 h-4 mb-0.5" />
            <span className="text-[10px] font-bold">الرصيد</span>
          </button>

          <button
            onClick={() => {
              hapticFeedback("light");
              setActiveTab("api");
            }}
            className={`py-2 rounded-2xl flex flex-col items-center justify-center transition-all ${
              activeTab === "api"
                ? "bg-blue-600 text-white shadow-lg shadow-blue-600/40"
                : "text-slate-400 hover:text-white"
            }`}
          >
            <Code2 className="w-4 h-4 mb-0.5" />
            <span className="text-[10px] font-bold">API</span>
          </button>

          <button
            onClick={() => {
              hapticFeedback("light");
              setActiveTab("account");
            }}
            className={`py-2 rounded-2xl flex flex-col items-center justify-center transition-all ${
              activeTab === "account"
                ? "bg-blue-600 text-white shadow-lg shadow-blue-600/40"
                : "text-slate-400 hover:text-white"
            }`}
          >
            <User className="w-4 h-4 mb-0.5" />
            <span className="text-[10px] font-bold">حسابي</span>
          </button>
        </div>
      </nav>

      {/* ========================================================================= */}
      {/* DRAWER: PRODUCT DETAIL BOTTOM SHEET */}
      {/* ========================================================================= */}
      {selectedProduct && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/80 backdrop-blur-sm animate-in fade-in">
          <div className="w-full max-w-md bg-[#0B1528] rounded-t-3xl border-t border-white/[0.12] p-5 space-y-4 max-h-[85vh] overflow-y-auto shadow-2xl animate-in slide-in-from-bottom-10">
            {/* Header */}
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 rounded-2xl bg-white/[0.05] border border-white/[0.08] flex items-center justify-center text-3xl shrink-0">
                  {selectedProduct.custom_emoji || "📦"}
                </div>
                <div>
                  <h3 className="font-extrabold text-sm text-white">{selectedProduct.title_ar}</h3>
                  <div className="flex items-center gap-2 mt-1">
                    <span className="text-[10px] font-bold px-1.5 py-0.2 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30">
                      ⚡ تسليم فوري
                    </span>
                    <span className="text-[10px] font-mono text-emerald-400">
                      {selectedProduct.is_unlimited ? "متوفر" : `${selectedProduct.available_stock || 1} قطعة بالمخزون`}
                    </span>
                  </div>
                </div>
              </div>
              <button
                onClick={() => setSelectedProduct(null)}
                className="w-8 h-8 rounded-full bg-white/[0.06] hover:bg-white/[0.12] flex items-center justify-center text-slate-400 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Description */}
            <div className="p-3 rounded-2xl bg-black/40 border border-white/[0.06] text-xs text-slate-300 leading-relaxed max-h-28 overflow-y-auto">
              <div className="font-bold text-[11px] text-slate-400 mb-1">عن المنتج والضمان:</div>
              {selectedProduct.description_ar || "تسليم آلي وفوري للمفتاح أو الحساب مباشرة بعد إتمام الدفع."}
            </div>

            {/* Quantity Selector Chips & Stepper */}
            <div>
              <div className="text-xs font-bold text-slate-300 mb-2">تحديد الكمية المطلوبة:</div>
              <div className="grid grid-cols-5 gap-1.5 mb-2">
                {[1, 2, 5, 10, 25].map((q) => (
                  <button
                    key={q}
                    onClick={() => {
                      hapticFeedback("light");
                      setDrawerQty(q);
                    }}
                    className={`py-1.5 rounded-xl text-xs font-bold font-mono transition ${
                      drawerQty === q
                        ? "bg-blue-600 text-white shadow-md shadow-blue-600/40"
                        : "bg-white/[0.04] text-slate-400 hover:text-white border border-white/[0.06]"
                    }`}
                  >
                    ×{q}
                  </button>
                ))}
              </div>

              {/* Stepper */}
              <div className="flex items-center justify-between p-2 rounded-2xl bg-black/40 border border-white/[0.06]">
                <button
                  onClick={() => {
                    hapticFeedback("light");
                    setDrawerQty((prev) => Math.max(1, prev - 1));
                  }}
                  className="w-8 h-8 rounded-xl bg-white/[0.08] hover:bg-white/[0.15] flex items-center justify-center text-white"
                >
                  <Minus className="w-4 h-4" />
                </button>
                <span className="font-mono font-black text-sm text-white">{drawerQty} قطعة</span>
                <button
                  onClick={() => {
                    hapticFeedback("light");
                    setDrawerQty((prev) => prev + 1);
                  }}
                  className="w-8 h-8 rounded-xl bg-white/[0.08] hover:bg-white/[0.15] flex items-center justify-center text-white"
                >
                  <Plus className="w-4 h-4" />
                </button>
              </div>
            </div>

            {(selectedProduct.requires_customer_email || selectedProduct.requires_customer_input) && (
              <div className="space-y-1.5">
                <label className="text-[11px] text-slate-400 font-medium">
                  {selectedProduct.customer_input_label_ar || "📧 البريد الإلكتروني للتفعيل"}
                </label>
                <input
                  type={selectedProduct.customer_input_type === "email" ? "email" : "text"}
                  value={buyerEmail}
                  onChange={(e) => setBuyerEmail(e.target.value)}
                  placeholder="أدخل القيمة المطلوبة..."
                  className="w-full px-3 py-2.5 rounded-xl bg-black/40 border border-amber-500/30 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-amber-400"
                />
              </div>
            )}

            {/* Live Financial Breakdown */}
            {(() => {
              const totalCost = selectedProduct.retail_price * drawerQty;
              const balance = profile?.wallet_balance || 0;
              const hasDeficit = totalCost > balance;
              const deficit = totalCost - balance;

              return (
                <div className="p-3.5 rounded-2xl bg-black/50 border border-white/[0.08] space-y-1.5 text-xs font-mono">
                  <div className="flex justify-between text-slate-400">
                    <span>سعر القطعة:</span>
                    <span>${selectedProduct.retail_price.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between text-slate-400">
                    <span>الكمية:</span>
                    <span>{drawerQty}</span>
                  </div>
                  <div className="flex justify-between text-slate-400">
                    <span>رصيدك الحالي:</span>
                    <span className="text-white">${balance.toFixed(2)}</span>
                  </div>
                  <div className="flex justify-between font-bold text-white pt-1 border-t border-white/[0.08]">
                    <span>الإجمالي:</span>
                    <span className="text-blue-400 text-sm">${totalCost.toFixed(2)} USD</span>
                  </div>

                  {hasDeficit && (
                    <div className="p-2 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 font-sans text-[11px] flex items-center justify-between mt-1">
                      <span>ينقصك لإتمام الشراء:</span>
                      <span className="font-mono font-bold">${deficit.toFixed(2)} USD</span>
                    </div>
                  )}
                </div>
              );
            })()}

            {/* Actions Bar */}
            {(() => {
              const totalCost = selectedProduct.retail_price * drawerQty;
              const balance = profile?.wallet_balance || 0;
              const hasDeficit = totalCost > balance;
              const deficit = totalCost - balance;

              return (
                <div className="grid grid-cols-2 gap-2 pt-2">
                  <button
                    onClick={() => addToCart(selectedProduct, drawerQty)}
                    className="py-3 rounded-2xl bg-white/[0.08] hover:bg-white/[0.14] text-white font-bold text-xs flex items-center justify-center gap-1.5 border border-white/[0.1] transition active:scale-95"
                  >
                    <ShoppingBag className="w-4 h-4" />
                    <span>أضف للسلة</span>
                  </button>

                  {hasDeficit ? (
                    <button
                      onClick={() => {
                        hapticFeedback("medium");
                        setSelectedProduct(null);
                        setTopUpAmount(String(Math.ceil(deficit)));
                        setIsTopUpOpen(true);
                      }}
                      className="py-3 rounded-2xl bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white font-black text-xs flex items-center justify-center gap-1.5 shadow-lg shadow-blue-600/30 transition active:scale-95"
                    >
                      <Wallet className="w-4 h-4" />
                      <span>شحن (${deficit.toFixed(2)})</span>
                    </button>
                  ) : (
                    <button
                      onClick={() =>
                        handleCheckout([{ product_id: selectedProduct.id, quantity: drawerQty }], true)
                      }
                      disabled={checkingOut}
                      className="py-3 rounded-2xl bg-gradient-to-r from-emerald-500 to-emerald-600 hover:from-emerald-400 hover:to-emerald-500 text-black font-black text-xs flex items-center justify-center gap-1.5 shadow-lg shadow-emerald-500/30 transition active:scale-95 disabled:opacity-50"
                    >
                      <span>⚡ شراء فوري · ${totalCost.toFixed(2)}</span>
                    </button>
                  )}
                </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* DRAWER: SHOPPING CART */}
      {/* ========================================================================= */}
      {isCartOpen && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/80 backdrop-blur-sm animate-in fade-in">
          <div className="w-full max-w-md bg-[#0B1528] rounded-t-3xl border-t border-white/[0.12] p-5 space-y-4 max-h-[85vh] overflow-y-auto shadow-2xl animate-in slide-in-from-bottom-10">
            <div className="flex items-center justify-between pb-2 border-b border-white/[0.06]">
              <div className="flex items-center gap-2">
                <ShoppingBag className="w-5 h-5 text-blue-400" />
                <h3 className="font-extrabold text-sm text-white">سلة المشتريات ({cartItemCount} قطع)</h3>
              </div>
              <button
                onClick={() => setIsCartOpen(false)}
                className="w-7 h-7 rounded-full bg-white/[0.06] flex items-center justify-center text-slate-400 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Cart Items List */}
            <div className="space-y-2.5 max-h-56 overflow-y-auto">
              {cart.map((item) => (
                <div
                  key={item.product.id}
                  className="p-3 rounded-2xl bg-black/40 border border-white/[0.06] flex items-center justify-between"
                >
                  <div className="flex items-center gap-2.5">
                    <span className="text-2xl">{item.product.custom_emoji || "📦"}</span>
                    <div>
                      <h4 className="font-bold text-xs text-white line-clamp-1">{item.product.title_ar}</h4>
                      <div className="text-[11px] font-mono text-blue-400">
                        ${(item.product.retail_price * item.quantity).toFixed(2)}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => updateCartQty(item.product.id, item.quantity - 1)}
                      className="w-6 h-6 rounded-lg bg-white/[0.08] flex items-center justify-center text-white"
                    >
                      <Minus className="w-3 h-3" />
                    </button>
                    <span className="font-mono font-bold text-xs text-white">{item.quantity}</span>
                    <button
                      onClick={() => updateCartQty(item.product.id, item.quantity + 1)}
                      className="w-6 h-6 rounded-lg bg-white/[0.08] flex items-center justify-center text-white"
                    >
                      <Plus className="w-3 h-3" />
                    </button>
                  </div>
                </div>
              ))}

              {cart.length === 0 && (
                <div className="py-8 text-center text-xs text-slate-400">السلة فارغة حالياً.</div>
              )}
            </div>

            {/* Coupon Code Input */}
            {cart.length > 0 && (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={couponCode}
                  onChange={(e) => setCouponCode(e.target.value.toUpperCase())}
                  placeholder="كود الخصم (كوبون) إن وجد..."
                  className="flex-1 px-3 py-2 rounded-xl bg-black/40 border border-white/[0.08] text-xs text-white uppercase placeholder-slate-500 focus:outline-none focus:border-blue-500"
                />
                <button
                  onClick={() => showToast("سيتم فحص الكوبون وتطبيقه عند الدفع", "info")}
                  className="px-3 py-2 rounded-xl bg-white/[0.08] hover:bg-white/[0.15] text-xs font-bold text-white border border-white/[0.08]"
                >
                  تطبيق
                </button>
              </div>
            )}

            {cart.length > 0 && cart.some((i) => i.product.requires_customer_email) && (
              <div className="space-y-1.5">
                <label className="text-[11px] text-slate-400 font-medium">📧 البريد الإلكتروني للتفعيل</label>
                <input
                  type="email"
                  value={buyerEmail}
                  onChange={(e) => setBuyerEmail(e.target.value)}
                  placeholder="name@example.com"
                  required
                  className="w-full px-3 py-2 rounded-xl bg-black/40 border border-amber-500/30 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-amber-400"
                />
              </div>
            )}

            {/* Total & Checkout Button */}
            {cart.length > 0 && (
              <div className="pt-2 border-t border-white/[0.06] space-y-3">
                <div className="flex items-baseline justify-between">
                  <span className="text-xs text-slate-400">المجموع الكلي:</span>
                  <span className="font-mono font-black text-lg text-white">${cartTotal.toFixed(2)} USD</span>
                </div>

                <button
                  onClick={() =>
                    handleCheckout(
                      cart.map((i) => ({ product_id: i.product.id, quantity: i.quantity })),
                      true
                    )
                  }
                  disabled={checkingOut}
                  className="w-full py-3.5 rounded-2xl bg-gradient-to-r from-emerald-500 to-emerald-600 hover:from-emerald-400 hover:to-emerald-500 text-black font-black text-sm shadow-xl shadow-emerald-500/20 transition active:scale-95 disabled:opacity-50 flex items-center justify-center gap-2"
                >
                  <span>⚡ إتمام الشراء والتسليم الفوري</span>
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* MODAL: INTERACTIVE PAYMENT MODAL (LIVE COUNTDOWN & QR) */}
      {/* ========================================================================= */}
      {activeInvoice && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-in fade-in">
          <div className="w-full max-w-sm bg-[#0B1528] rounded-3xl border border-blue-500/40 p-5 space-y-4 shadow-2xl relative">
            <button
              onClick={() => setActiveInvoice(null)}
              className="absolute left-4 top-4 w-7 h-7 rounded-full bg-white/[0.08] flex items-center justify-center text-slate-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="text-center">
              <span className="text-[10px] font-mono font-bold px-2 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/40">
                {activeInvoice.payment_method.toUpperCase()}
              </span>
              <h3 className="text-base font-black text-white mt-1.5">بانتظار تأكيد التحويل ⚡</h3>
              <div className="flex items-center justify-center gap-1.5 text-xs text-amber-300 font-mono mt-1">
                <Clock className="w-3.5 h-3.5 animate-spin" />
                <span>متبقي: {formatTimer(invoiceTimer)}</span>
              </div>
            </div>

            {/* Exact Decimal Amount Card */}
            <div className="p-3 rounded-2xl bg-black/50 border border-white/[0.08] text-center space-y-1">
              <div className="text-[11px] text-slate-400">المبلغ المطلوب إرساله بالضبط:</div>
              <div className="flex items-center justify-center gap-2">
                <span className="font-mono font-black text-lg text-amber-400 select-all">
                  {activeInvoice.unique_amount.toFixed(6)}
                </span>
                <span className="font-mono text-xs text-white">{activeInvoice.currency}</span>
                <button
                  onClick={() => copyToClipboard(activeInvoice.unique_amount.toFixed(6), "المبلغ")}
                  className="p-1 rounded bg-white/10 hover:bg-white/20 text-white"
                >
                  <Copy className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="text-[9px] text-rose-300 font-mono">
                ⚠️ يرجى إرسال هذا الرقم العشري الفريد تماماً لتأكيد الدفعة آلياً
              </div>
            </div>

            {/* QR Code & Address */}
            <div className="flex flex-col items-center p-3 rounded-2xl bg-black/40 border border-white/[0.08] space-y-2.5">
              {/* Dynamic QR Code Render via external safe SVG service */}
              <div className="w-36 h-36 bg-white p-2 rounded-xl flex items-center justify-center shadow-lg">
                <img
                  src={`https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=${encodeURIComponent(
                    activeInvoice.deposit_address
                  )}`}
                  alt="QR Code"
                  className="w-full h-full object-contain"
                />
              </div>

              {/* Deposit Address */}
              <div className="w-full">
                <div className="text-[10px] text-slate-400 mb-1 text-center">عنوان الإيداع:</div>
                <div className="p-2 rounded-xl bg-black/60 border border-white/[0.08] flex items-center justify-between">
                  <span className="font-mono text-[10px] text-blue-200 select-all break-all line-clamp-1">
                    {activeInvoice.deposit_address}
                  </span>
                  <button
                    onClick={() => copyToClipboard(activeInvoice.deposit_address, "عنوان المحفظة")}
                    className="p-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white shrink-0 ml-1.5"
                  >
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>

            {/* Action Buttons */}
            <div className="space-y-2">
              <button
                onClick={handleCheckPaymentStatus}
                disabled={isCheckingPayment}
                className="w-full py-3 rounded-2xl bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs flex items-center justify-center gap-2 shadow-lg shadow-blue-600/30 transition disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isCheckingPayment ? "animate-spin" : ""}`} />
                <span>{isCheckingPayment ? "جاري الفحص على البلوكشين..." : "فحص الدفع 🔍"}</span>
              </button>

              <button
                onClick={() => handleCancelInvoice(activeInvoice.id)}
                className="w-full py-2.5 rounded-2xl bg-white/[0.04] hover:bg-rose-500/20 text-slate-400 hover:text-rose-300 border border-white/[0.08] font-semibold text-xs transition"
              >
                إلغاء دفع ❌
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* MODAL: DIRECT WALLET TOP-UP */}
      {/* ========================================================================= */}
      {isTopUpOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-in fade-in">
          <div className="w-full max-w-sm bg-[#0B1528] rounded-3xl border border-blue-500/40 p-5 space-y-4 shadow-2xl relative">
            <button
              onClick={() => setIsTopUpOpen(false)}
              className="absolute left-4 top-4 w-7 h-7 rounded-full bg-white/[0.08] flex items-center justify-center text-slate-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <div>
              <h3 className="text-base font-extrabold text-white">شحن رصيد المحفظة 💳</h3>
              <p className="text-xs text-slate-400">اختر المبلغ وشبكة التحويل للشحن الفوري</p>
            </div>

            {/* Presets */}
            <div className="grid grid-cols-4 gap-2">
              {["10", "25", "50", "100"].map((amt) => (
                <button
                  key={amt}
                  onClick={() => {
                    hapticFeedback("light");
                    setTopUpAmount(amt);
                  }}
                  className={`py-2 rounded-xl text-xs font-bold font-mono transition ${
                    topUpAmount === amt
                      ? "bg-blue-600 text-white shadow-md shadow-blue-600/40"
                      : "bg-white/[0.04] text-slate-400 hover:text-white border border-white/[0.06]"
                  }`}
                >
                  ${amt}
                </button>
              ))}
            </div>

            {/* Custom Amount Input */}
            <div>
              <label className="text-[11px] text-slate-400 block mb-1">المبلغ المطلوب (USD):</label>
              <input
                type="number"
                step="0.5"
                value={topUpAmount}
                onChange={(e) => setTopUpAmount(e.target.value)}
                className="w-full px-3 py-2 rounded-xl bg-black/40 border border-white/[0.08] text-sm font-mono font-bold text-white focus:outline-none focus:border-blue-500"
              />
            </div>

            {/* Network Selector */}
            <div>
              <label className="text-[11px] text-slate-400 block mb-1.5">شبكة الدفع:</label>
              <div className="space-y-1.5">
                {[
                  { id: "usdt_bep20", label: "USDT (BEP-20 / شبكة BSC)", tag: "رسوم منخفضة ⚡" },
                  { id: "usdt_trc20", label: "USDT (TRC-20 / شبكة TRON)", tag: "سريعة" },
                  { id: "binance_pay", label: "Binance Pay", tag: "رسوم 0% 🔥" },
                  { id: "sham_cash", label: "شام كاش (Sham Cash)", tag: "تحويل محلي 🏦" },
                ].map((net) => (
                  <button
                    key={net.id}
                    onClick={() => {
                      hapticFeedback("light");
                      setTopUpNetwork(net.id);
                    }}
                    className={`w-full p-2.5 rounded-xl border text-xs font-semibold flex items-center justify-between transition ${
                      topUpNetwork === net.id
                        ? "bg-blue-600/20 border-blue-500 text-white"
                        : "bg-black/30 border-white/[0.06] text-slate-400 hover:text-white"
                    }`}
                  >
                    <span>{net.label}</span>
                    <span className="text-[10px] font-mono font-normal opacity-80">{net.tag}</span>
                  </button>
                ))}
              </div>
            </div>

            <button
              onClick={handleCreateTopUp}
              disabled={submittingTopUp}
              className="w-full py-3 rounded-2xl bg-gradient-to-r from-emerald-500 to-emerald-600 hover:from-emerald-400 hover:to-emerald-500 text-black font-black text-xs shadow-lg shadow-emerald-500/20 transition active:scale-95 disabled:opacity-50"
            >
              {submittingTopUp ? "جاري إنشاء الفاتورة..." : `متابعة شحن $${topUpAmount} USD ⚡`}
            </button>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* MODAL: SUPPORT TICKET */}
      {/* ========================================================================= */}
      {isTicketOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/85 backdrop-blur-md animate-in fade-in">
          <div className="w-full max-w-sm bg-[#0B1528] rounded-3xl border border-white/[0.12] p-5 space-y-3.5 shadow-2xl relative">
            <button
              onClick={() => setIsTicketOpen(false)}
              className="absolute left-4 top-4 w-7 h-7 rounded-full bg-white/[0.08] flex items-center justify-center text-slate-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <div>
              <h3 className="text-base font-extrabold text-white">فتح تذكرة دعم / نزاع 🛟</h3>
              <p className="text-xs text-slate-400">سيتم الرد عليك مباشرة هنا وعبر البوت</p>
            </div>

            <div>
              <label className="text-[11px] text-slate-400 block mb-1">عنوان الموضوع:</label>
              <input
                type="text"
                value={ticketSubject}
                onChange={(e) => setTicketSubject(e.target.value)}
                placeholder="مثال: مشكلة في تفعيل الكود أو الحساب"
                className="w-full px-3 py-2 rounded-xl bg-black/40 border border-white/[0.08] text-xs text-white focus:outline-none focus:border-blue-500"
              />
            </div>

            <div>
              <label className="text-[11px] text-slate-400 block mb-1">تفاصيل المشكلة:</label>
              <textarea
                rows={3}
                value={ticketMessage}
                onChange={(e) => setTicketMessage(e.target.value)}
                placeholder="اشرح المشكلة وما ظهر لك أثناء التفعيل..."
                className="w-full px-3 py-2 rounded-xl bg-black/40 border border-white/[0.08] text-xs text-white focus:outline-none focus:border-blue-500"
              />
            </div>

            <button
              onClick={handleSubmitTicket}
              disabled={submittingTicket}
              className="w-full py-2.5 rounded-2xl bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs shadow-md transition disabled:opacity-50"
            >
              {submittingTicket ? "جاري الإرسال..." : "إرسال التذكرة الآن 📩"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
