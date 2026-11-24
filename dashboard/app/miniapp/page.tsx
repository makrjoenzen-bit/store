"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";

type Lang = "ar" | "en";
type Tab = "store" | "orders" | "balance" | "account";

const COPY = {
  ar: {
    store: "المتجر",
    orders: "طلباتي",
    balance: "الرصيد",
    account: "حسابي",
    search: "ابحث عن منتج…",
    all: "الكل",
    buy: "شراء",
    out: "غير متوفر",
    emptyStore: "لا توجد منتجات حالياً",
    emptyOrders: "لا توجد طلبات بعد",
    emptyTx: "لا حركات رصيد بعد",
    checkout: "تأكيد الشراء من الرصيد",
    topup: "طلب شحن رصيد",
    topupAmount: "المبلغ",
    topupCustom: "مبلغ مخصص",
    topupMethod: "وسيلة الدفع",
    topupConfirm: "تأكيد الشحن",
    railSham: "شام كاش",
    railBep20: "USDT BEP-20",
    railTrc20: "USDT TRC-20",
    railBinance: "Binance Pay",
    support: "الدعم",
    lang: "اللغة",
    loading: "جارٍ التحميل…",
    bal: "رصيدك",
    price: "السعر",
    delivered: "بيانات التسليم",
    email: "البريد / معرف التفعيل",
    success: "تم بنجاح ✨",
    needTopup: "رصيدك غير كافٍ — اشحن ثم أعد المحاولة",
    inStock: "متوفر",
    statePreparing: "جاري التجهيز (30–60 دقيقة)",
    preparingHint: "طلبك قيد التجهيز — التسليم خلال المدة المتبقية",
    stateDelivered: "تم التسليم",
    stateCompleted: "مكتمل",
    stateAwaiting: "بانتظار الدفع",
  },
  en: {
    store: "Store",
    orders: "Orders",
    balance: "Balance",
    account: "Account",
    search: "Search products…",
    all: "All",
    buy: "Buy",
    out: "Out of stock",
    emptyStore: "No products yet",
    emptyOrders: "No orders yet",
    emptyTx: "No transactions yet",
    checkout: "Pay from balance",
    topup: "Request top-up",
    topupAmount: "Amount",
    topupCustom: "Custom amount",
    topupMethod: "Payment method",
    topupConfirm: "Confirm top-up",
    railSham: "Sham Cash",
    railBep20: "USDT BEP-20",
    railTrc20: "USDT TRC-20",
    railBinance: "Binance Pay",
    support: "Support",
    lang: "Language",
    loading: "Loading…",
    bal: "Your balance",
    price: "Price",
    delivered: "Delivery details",
    email: "Email / activation ID",
    success: "Success ✨",
    needTopup: "Insufficient balance — top up then retry",
    inStock: "In stock",
    statePreparing: "Preparing (30–60 min)",
    preparingHint: "Your order is being prepared — delivery within the remaining time",
    stateDelivered: "Delivered",
    stateCompleted: "Completed",
    stateAwaiting: "Awaiting payment",
  },
};

function formatMmSs(totalSec: number): string {
  const s = Math.max(0, Math.floor(totalSec));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

function ManualPrepTimer({
  deadlineIso,
  lang,
  hint,
  hintColor,
  accent,
}: {
  deadlineIso?: string | null;
  lang: Lang;
  hint: string;
  hintColor: string;
  accent: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const deadlineMs = deadlineIso ? new Date(deadlineIso).getTime() : NaN;
  const hasDeadline = Number.isFinite(deadlineMs);
  const remSec = hasDeadline ? Math.max(0, Math.floor((deadlineMs - now) / 1000)) : 0;
  const overdue = hasDeadline && remSec <= 0;
  const slaSec = 60 * 60;
  const elapsedRatio = hasDeadline
    ? Math.min(1, Math.max(0, 1 - remSec / slaSec))
    : 0.15;
  const pct = overdue ? 100 : Math.round(elapsedRatio * 100);
  const pulse = Math.floor(now / 1000) % 2 === 0 ? "⏳" : "⌛";
  const overdueHint =
    lang === "ar"
      ? "نعتذر عن التأخير — طلبك قيد التجهيز العاجل الآن"
      : "Sorry for the delay — your order is being prepared urgently";

  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="inline-flex items-center gap-1.5 font-semibold" style={{ color: accent }}>
          <span className="inline-block animate-pulse text-base leading-none">{pulse}</span>
          {overdue
            ? lang === "ar"
              ? "تأخير بسيط"
              : "Slight delay"
            : lang === "ar"
              ? "العدّاد المباشر"
              : "Live countdown"}
        </span>
        <span className="font-mono tabular-nums text-sm font-bold" style={{ color: accent }}>
          {hasDeadline ? formatMmSs(remSec) : "—"}
        </span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full"
        style={{ background: `${hintColor}33` }}
      >
        <div
          className="h-full rounded-full transition-[width] duration-1000 ease-linear"
          style={{ width: `${pct}%`, background: accent }}
        />
      </div>
      <div className="text-[11px] leading-relaxed" style={{ color: hintColor }}>
        {overdue ? overdueHint : hint}
      </div>
    </div>
  );
}

function formatOrderState(state: string, t: (typeof COPY)["ar"]): string {
  const s = (state || "").toLowerCase();
  if (s === "pending_manual_delivery") return t.statePreparing;
  if (s === "delivered" || s === "order_fulfilled") return t.stateDelivered;
  if (s === "completed") return t.stateCompleted;
  if (s === "awaiting_payment" || s === "created") return t.stateAwaiting;
  return state?.replace(/_/g, " ") || "—";
}

type TgWebApp = {
  ready?: () => void;
  expand?: () => void;
  initData?: string;
  themeParams?: Record<string, string>;
  HapticFeedback?: {
    impactOccurred?: (style: string) => void;
    notificationOccurred?: (type: string) => void;
  };
  MainButton?: {
    text: string;
    show: () => void;
    hide: () => void;
    enable: () => void;
    disable: () => void;
    setText: (t: string) => void;
    onClick: (cb: () => void) => void;
    offClick: (cb: () => void) => void;
    showProgress: (leaveActive?: boolean) => void;
    hideProgress: () => void;
    color?: string;
    textColor?: string;
  };
  BackButton?: {
    show: () => void;
    hide: () => void;
    onClick: (cb: () => void) => void;
    offClick: (cb: () => void) => void;
  };
  setHeaderColor?: (c: string) => void;
  setBackgroundColor?: (c: string) => void;
};

function tg(): TgWebApp | undefined {
  if (typeof window === "undefined") return undefined;
  // @ts-expect-error Telegram WebApp injected
  return window.Telegram?.WebApp as TgWebApp | undefined;
}

function haptic(kind: "light" | "success" | "error" | "warning" = "light") {
  const h = tg()?.HapticFeedback;
  try {
    if (kind === "light") h?.impactOccurred?.("light");
    else h?.notificationOccurred?.(kind === "success" ? "success" : kind === "error" ? "error" : "warning");
  } catch {
    /* ignore */
  }
}

function getInitData(): string {
  return tg()?.initData || "";
}

function authHeaders(): HeadersInit {
  const initData = getInitData();
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (initData) h["X-Telegram-Init-Data"] = initData;
  else if (process.env.NODE_ENV === "development") h["X-Dev-Telegram-Id"] = "987654321";
  return h;
}

async function api(path: string, opts: RequestInit = {}) {
  const res = await fetch(`/api/miniapp${path}`, {
    ...opts,
    headers: { ...authHeaders(), ...(opts.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

/** Lightweight CSS pulse skeleton */
function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 animate-pulse">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-20 rounded-2xl bg-white/5 border border-white/5" />
      ))}
    </div>
  );
}

function EmptyAnim({ label }: { label: string }) {
  return (
    <div className="py-12 text-center space-y-3">
      <div
        className="mx-auto w-14 h-14 rounded-full bg-gradient-to-br from-sky-500/30 to-emerald-500/20 flex items-center justify-center text-2xl"
        style={{ animation: "miniapp-float 2.4s ease-in-out infinite" }}
      >
        ✨
      </div>
      <p className="text-sm text-white/50">{label}</p>
      <style>{`@keyframes miniapp-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}`}</style>
    </div>
  );
}

function SuccessBurst({ label }: { label: string }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 pointer-events-none">
      <div
        className="rounded-3xl px-8 py-6 text-center border border-emerald-400/40 bg-emerald-500/20 backdrop-blur-md"
        style={{ animation: "miniapp-pop 0.45s ease-out" }}
      >
        <div className="text-4xl mb-2">🎉</div>
        <div className="text-emerald-100 font-bold">{label}</div>
      </div>
      <style>{`@keyframes miniapp-pop{0%{transform:scale(.7);opacity:0}100%{transform:scale(1);opacity:1}}`}</style>
    </div>
  );
}

export default function MiniAppPage() {
  const [tab, setTab] = useState<Tab>("store");
  const [lang, setLang] = useState<Lang>("ar");
  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<any>(null);
  const [catalog, setCatalog] = useState<any[]>([]);
  const [categories, setCategories] = useState<any[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string>("");
  const [selected, setSelected] = useState<any>(null);
  const [buyerEmail, setBuyerEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [orderDetail, setOrderDetail] = useState<any>(null);
  const [showSuccess, setShowSuccess] = useState(false);
  const [theme, setTheme] = useState<Record<string, string>>({});
  const [topupAmount, setTopupAmount] = useState<number>(10);
  const [topupCustom, setTopupCustom] = useState("");
  const [topupNetwork, setTopupNetwork] = useState<string>("sham_cash");
  const [shamQrUrl, setShamQrUrl] = useState<string>("");
  const [shamAccount, setShamAccount] = useState<string>("");

  const t = COPY[lang];
  const dir = lang === "ar" ? "rtl" : "ltr";

  const paymentRails = useMemo(() => {
    const rails = profile?.config?.payment_rails || {};
    const opts: { id: string; label: string }[] = [];
    if (rails.sham_cash !== false) opts.push({ id: "sham_cash", label: t.railSham });
    if (rails.usdt_bep20) opts.push({ id: "usdt_bep20", label: t.railBep20 });
    if (rails.usdt_trc20) opts.push({ id: "usdt_trc20", label: t.railTrc20 });
    if (rails.binance_pay !== false) opts.push({ id: "binance_pay", label: t.railBinance });
    if (opts.length === 0) opts.push({ id: "sham_cash", label: t.railSham });
    return opts;
  }, [profile, t.railSham, t.railBep20, t.railTrc20, t.railBinance]);

  useEffect(() => {
    if (!paymentRails.some((r) => r.id === topupNetwork)) {
      setTopupNetwork(paymentRails[0]?.id || "sham_cash");
    }
  }, [paymentRails, topupNetwork]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const w = tg();
      w?.ready?.();
      w?.expand?.();
      const tp = w?.themeParams || {};
      setTheme(tp);
      if (tp.bg_color) w?.setBackgroundColor?.(tp.bg_color);
      if (tp.bg_color) w?.setHeaderColor?.(tp.bg_color);

      const [p, c, o, h] = await Promise.all([
        api("/profile"),
        api("/catalog"),
        api("/orders"),
        api("/wallet/history"),
      ]);
      setProfile(p);
      const lc = (p?.customer?.language_code || "ar").startsWith("en") ? "en" : "ar";
      setLang(lc as Lang);
      const products = Array.isArray(c?.products) ? c.products : Array.isArray(c) ? c : [];
      setCatalog(products);
      setCategories(Array.isArray(c?.categories) ? c.categories : []);
      setOrders(Array.isArray(o) ? o : o?.orders || []);
      setHistory(Array.isArray(h) ? h : h?.transactions || []);
    } catch (e: any) {
      setMsg(e?.message || "Error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    return catalog.filter((p) => {
      const title = `${p.title_ar || ""} ${p.title_en || ""}`.toLowerCase();
      const okQ = !q || title.includes(q.toLowerCase());
      const okC = !cat || String(p.category_id) === cat || p.brand_family === cat;
      return okQ && okC;
    });
  }, [catalog, q, cat]);

  const closeCheckout = useCallback(() => {
    setSelected(null);
    setBuyerEmail("");
    haptic("light");
  }, []);

  const buyWithBalance = useCallback(async () => {
    if (!selected) return;
    const productId = selected.id || selected.product_id;
    const needsEmail = !!(selected.requires_customer_email || selected.requires_customer_input);
    if (needsEmail && !buyerEmail.trim()) {
      setMsg(lang === "ar" ? "أدخل البريد / المعرف" : "Enter email / ID");
      haptic("warning");
      return;
    }
    setBusy(true);
    setMsg(null);
    const mb = tg()?.MainButton;
    try {
      mb?.showProgress?.(false);
      const res = await api("/cart/checkout", {
        method: "POST",
        body: JSON.stringify({
          items: [{ product_id: productId, quantity: 1 }],
          pay_via_wallet: true,
          payment_method: "wallet",
          buyer_email: buyerEmail.trim() || undefined,
        }),
      });
      if (res?.status === "insufficient_balance") {
        haptic("warning");
        setMsg(
          `${t.needTopup} (≈$${Number(res.deficit || 0).toFixed(2)})`
        );
        setTab("balance");
        closeCheckout();
        return;
      }
      haptic("success");
      setShowSuccess(true);
      setTimeout(() => setShowSuccess(false), 1600);
      closeCheckout();
      setMsg(t.success);
      await load();
      setTab("orders");
    } catch (e: any) {
      haptic("error");
      setMsg(e?.message || "Checkout failed");
    } finally {
      mb?.hideProgress?.();
      setBusy(false);
    }
  }, [selected, buyerEmail, lang, t, load, closeCheckout]);

  // Native MainButton + BackButton during checkout
  useEffect(() => {
    const w = tg();
    const mb = w?.MainButton;
    const bb = w?.BackButton;
    if (!selected) {
      mb?.hide?.();
      bb?.hide?.();
      return;
    }
    const onMain = () => {
      void buyWithBalance();
    };
    const onBack = () => closeCheckout();
    mb?.setText?.(t.checkout);
    mb?.show?.();
    mb?.enable?.();
    mb?.onClick?.(onMain);
    bb?.show?.();
    bb?.onClick?.(onBack);
    return () => {
      mb?.offClick?.(onMain);
      bb?.offClick?.(onBack);
      mb?.hide?.();
      bb?.hide?.();
    };
  }, [selected, t.checkout, buyWithBalance, closeCheckout]);

  const switchLang = async () => {
    haptic("light");
    const next: Lang = lang === "ar" ? "en" : "ar";
    try {
      await api("/language", { method: "PUT", body: JSON.stringify({ language_code: next }) });
      setLang(next);
    } catch (e: any) {
      setMsg(e?.message);
    }
  };

  const resolvedTopupAmount = useMemo(() => {
    const custom = Number(topupCustom);
    if (topupCustom.trim() && Number.isFinite(custom) && custom > 0) return custom;
    return topupAmount;
  }, [topupAmount, topupCustom]);

  const requestTopup = async () => {
    haptic("light");
    const amount = Number(resolvedTopupAmount);
    if (!Number.isFinite(amount) || amount < 1) {
      setMsg(lang === "ar" ? "أدخل مبلغاً صالحاً" : "Enter a valid amount");
      return;
    }
    setBusy(true);
    try {
      const res = await api("/wallet/topup", {
        method: "POST",
        body: JSON.stringify({ amount, network: topupNetwork }),
      });
      haptic("success");
      setShowSuccess(true);
      setTimeout(() => setShowSuccess(false), 1400);
      const inv = res?.invoice;
      const addr = inv?.deposit_address || inv?.unique_amount;
      const qr = inv?.sham_cash_qr_url || profile?.config?.sham_cash_qr_url;
      setMsg(
        lang === "ar"
          ? `تم إنشاء فاتورة شحن $${amount.toFixed(2)} — ${addr ? `التفاصيل: ${addr}` : "بانتظار الدفع"}${qr ? " (QR جاهز أدناه)" : ""}`
          : `Top-up invoice $${amount.toFixed(2)} created — ${addr ? `details: ${addr}` : "awaiting payment"}${qr ? " (QR below)" : ""}`
      );
      if (qr) setShamQrUrl(qr);
      if (inv?.sham_cash_account_number) setShamAccount(inv.sham_cash_account_number);
      await load();
    } catch (e: any) {
      haptic("error");
      setMsg(e?.message);
    } finally {
      setBusy(false);
    }
  };

  const openOrder = async (id: string) => {
    haptic("light");
    try {
      const d = await api(`/orders/${id}`);
      setOrderDetail(d);
    } catch (e: any) {
      setMsg(e?.message);
    }
  };

  const bal = Number(profile?.customer?.wallet_balance ?? 0);
  const bg = theme.bg_color || "#0b1220";
  const text = theme.text_color || "#f1f5f9";
  const hint = theme.hint_color || "#94a3b8";
  const button = theme.button_color || "#0284c7";
  const buttonText = theme.button_text_color || "#ffffff";
  const secondary = theme.secondary_bg_color || "#111827";

  return (
    <div
      dir={dir}
      className="min-h-screen pb-24"
      style={{ background: bg, color: text }}
    >
      {showSuccess && <SuccessBurst label={t.success} />}

      <header
        className="sticky top-0 z-20 px-4 py-3 flex items-center justify-between border-b"
        style={{ background: `${bg}f2`, borderColor: `${hint}33` }}
      >
        <div>
          <div className="font-bold text-sm">
            {profile?.customer?.first_name || (lang === "ar" ? "المتجر" : "Store")}
          </div>
          <div className="text-[11px] tabular-nums" style={{ color: hint }}>
            {t.bal}: ${bal.toFixed(2)}
          </div>
        </div>
        <button
          type="button"
          onClick={switchLang}
          className="text-xs px-2.5 py-1 rounded-lg border"
          style={{ background: secondary, borderColor: `${hint}44`, color: text }}
        >
          {lang === "ar" ? "EN" : "ع"}
        </button>
      </header>

      {msg && (
        <div
          className="mx-4 mt-3 text-xs rounded-xl border px-3 py-2"
          style={{ borderColor: `${button}55`, background: `${button}18`, color: text }}
        >
          {msg}
        </div>
      )}

      <main className="px-4 pt-4 space-y-4">
        {loading && <Skeleton rows={4} />}

        {tab === "store" && !loading && (
          <>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t.search}
              className="w-full rounded-xl px-3 py-2.5 text-sm border outline-none"
              style={{ background: secondary, borderColor: `${hint}33`, color: text }}
            />
            <div className="flex gap-2 overflow-x-auto pb-1">
              <button
                type="button"
                onClick={() => { haptic("light"); setCat(""); }}
                className="shrink-0 px-3 py-1.5 rounded-full text-xs border"
                style={{
                  background: !cat ? `${button}33` : "transparent",
                  borderColor: !cat ? `${button}88` : `${hint}44`,
                  color: !cat ? buttonText : hint,
                }}
              >
                {t.all}
              </button>
              {categories.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => { haptic("light"); setCat(String(c.id)); }}
                  className="shrink-0 px-3 py-1.5 rounded-full text-xs border"
                  style={{
                    background: cat === String(c.id) ? `${button}33` : "transparent",
                    borderColor: cat === String(c.id) ? `${button}88` : `${hint}44`,
                    color: cat === String(c.id) ? buttonText : hint,
                  }}
                >
                  {lang === "en" ? (c.name_en || c.name_ar) : (c.name_ar || c.name_en)}
                </button>
              ))}
            </div>
            {filtered.length === 0 ? (
              <EmptyAnim label={t.emptyStore} />
            ) : (
              <div className="grid grid-cols-1 gap-3">
                {filtered.map((p) => {
                  const title = lang === "en" ? (p.title_en || p.title_ar) : (p.title_ar || p.title_en);
                  const inStock = p.is_in_stock !== false;
                  const price = Number(p.retail_price ?? p.final_price ?? p.price_usd ?? 0);
                  return (
                    <div
                      key={p.id}
                      className="rounded-2xl border p-4 space-y-2"
                      style={{ background: `${secondary}cc`, borderColor: `${hint}33` }}
                    >
                      <div className="flex justify-between gap-3">
                        <div>
                          <div className="font-semibold text-sm">{title}</div>
                          <div className="text-xs mt-0.5" style={{ color: hint }}>
                            {inStock ? (
                              <span style={{ color: "#34d399" }}>{t.inStock}</span>
                            ) : (
                              <span style={{ color: "#fb7185" }}>{t.out}</span>
                            )}
                          </div>
                        </div>
                        <div className="text-lg font-bold tabular-nums" style={{ color: button }}>
                          ${price.toFixed(2)}
                        </div>
                      </div>
                      {inStock && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => { haptic("light"); setSelected(p); }}
                          className="w-full py-2 rounded-xl text-sm font-bold disabled:opacity-50"
                          style={{ background: button, color: buttonText }}
                        >
                          {t.buy}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

        {tab === "orders" && !loading && (
          <div className="space-y-3">
            {orders.length === 0 ? (
              <EmptyAnim label={t.emptyOrders} />
            ) : (
              orders.map((o) => (
                <button
                  key={o.id || o.order_id}
                  type="button"
                  onClick={() => openOrder(o.id || o.order_id)}
                  className="w-full text-right rounded-2xl border p-4 space-y-1"
                  style={{ background: `${secondary}cc`, borderColor: `${hint}33` }}
                >
                  <div className="flex justify-between text-sm">
                    <span className="font-mono">{o.order_number}</span>
                    <span style={{ color: hint }}>{formatOrderState(o.state, t)}</span>
                  </div>
                  <div className="text-xs" style={{ color: hint }}>{o.product_title || o.title_ar || "—"}</div>
                  <div className="text-sm font-semibold" style={{ color: button }}>
                    ${Number(o.our_price || o.amount || 0).toFixed(2)}
                  </div>
                  {(o.state || "").toLowerCase() === "pending_manual_delivery" && (
                    <ManualPrepTimer
                      deadlineIso={o.manual_deadline_at}
                      lang={lang}
                      hint={t.preparingHint}
                      hintColor={hint}
                      accent={button}
                    />
                  )}
                </button>
              ))
            )}
            {orderDetail && (
              <div
                className="rounded-2xl border p-4 text-sm space-y-2"
                style={{ borderColor: "#34d39955", background: "#34d39918" }}
              >
                <div className="font-bold" style={{ color: "#6ee7b7" }}>{t.delivered}</div>
                <pre className="whitespace-pre-wrap text-xs font-mono opacity-90">
                  {orderDetail.delivered_product || orderDetail.delivery || "—"}
                </pre>
                <button type="button" className="text-xs opacity-60" onClick={() => setOrderDetail(null)}>
                  ✕
                </button>
              </div>
            )}
          </div>
        )}

        {tab === "balance" && !loading && (
          <div className="space-y-4">
            <div className="rounded-2xl border p-5 space-y-4" style={{ background: secondary, borderColor: `${hint}33` }}>
              <div>
                <div className="text-xs" style={{ color: hint }}>{t.bal}</div>
                <div className="text-3xl font-bold mt-1 tabular-nums">${bal.toFixed(2)}</div>
              </div>

              <div>
                <div className="text-xs mb-2" style={{ color: hint }}>{t.topupAmount}</div>
                <div className="grid grid-cols-4 gap-2">
                  {[5, 10, 25, 50].map((amt) => (
                    <button
                      key={amt}
                      type="button"
                      onClick={() => {
                        setTopupAmount(amt);
                        setTopupCustom("");
                        haptic("light");
                      }}
                      className="py-2 rounded-xl text-sm font-semibold border"
                      style={{
                        background: !topupCustom && topupAmount === amt ? button : `${secondary}`,
                        color: !topupCustom && topupAmount === amt ? buttonText : text,
                        borderColor: `${hint}44`,
                      }}
                    >
                      ${amt}
                    </button>
                  ))}
                </div>
                <input
                  type="number"
                  min={1}
                  step="0.01"
                  inputMode="decimal"
                  placeholder={t.topupCustom}
                  value={topupCustom}
                  onChange={(e) => setTopupCustom(e.target.value)}
                  className="mt-2 w-full rounded-xl border px-3 py-2 text-sm outline-none"
                  style={{ background: bg, borderColor: `${hint}44`, color: text }}
                />
              </div>

              <div>
                <div className="text-xs mb-2" style={{ color: hint }}>{t.topupMethod}</div>
                <div className="flex flex-wrap gap-2">
                  {paymentRails.map((rail) => (
                    <button
                      key={rail.id}
                      type="button"
                      onClick={() => {
                        setTopupNetwork(rail.id);
                        haptic("light");
                      }}
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold border"
                      style={{
                        background: topupNetwork === rail.id ? button : "transparent",
                        color: topupNetwork === rail.id ? buttonText : text,
                        borderColor: `${hint}44`,
                      }}
                    >
                      {rail.label}
                    </button>
                  ))}
                </div>
              </div>

              <button
                type="button"
                disabled={busy}
                onClick={requestTopup}
                className="w-full py-2.5 rounded-xl text-sm font-bold disabled:opacity-50"
                style={{ background: "#059669", color: "#fff" }}
              >
                {t.topupConfirm} · ${Number(resolvedTopupAmount || 0).toFixed(2)}
              </button>

              {topupNetwork === "sham_cash" && (shamQrUrl || profile?.config?.sham_cash_qr_url) && (
                <div className="pt-2 space-y-2 text-center">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={shamQrUrl || profile?.config?.sham_cash_qr_url}
                    alt="Sham Cash QR"
                    className="mx-auto w-40 h-40 rounded-xl object-contain bg-white"
                  />
                  <div className="text-xs font-mono" style={{ color: hint }}>
                    {(shamAccount || profile?.config?.sham_cash_account_number || "").toString()}
                  </div>
                </div>
              )}
            </div>
            <div className="space-y-2">
              {history.length === 0 ? (
                <EmptyAnim label={t.emptyTx} />
              ) : (
                history.slice(0, 20).map((tx: any, i: number) => (
                  <div
                    key={tx.id || i}
                    className="flex justify-between text-sm border rounded-xl px-3 py-2"
                    style={{ borderColor: `${hint}33` }}
                  >
                    <span style={{ color: hint }}>{tx.type || tx.tx_type || "—"}</span>
                    <span className="tabular-nums">${Number(tx.amount || 0).toFixed(2)}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {tab === "account" && !loading && (
          <div className="space-y-4 rounded-2xl border p-5" style={{ background: `${secondary}cc`, borderColor: `${hint}33` }}>
            <div className="text-sm">
              <div className="text-xs" style={{ color: hint }}>Telegram</div>
              <div className="font-semibold">
                {profile?.customer?.first_name} {profile?.customer?.last_name || ""}
              </div>
              <div className="text-xs" style={{ color: hint }}>
                @{profile?.customer?.telegram_username || "—"} · ID {profile?.customer?.telegram_id}
              </div>
            </div>
            {profile?.customer?.referral_code && (
              <div className="text-xs" style={{ color: hint }}>
                Referral: {profile.customer.referral_code}
              </div>
            )}
            <button
              type="button"
              onClick={switchLang}
              className="w-full py-2.5 rounded-xl border text-sm"
              style={{ borderColor: `${hint}55` }}
            >
              {t.lang}: {lang.toUpperCase()}
            </button>
            <a
              href={profile?.config?.main_channel_url || "#"}
              className="block w-full text-center py-2.5 rounded-xl text-sm"
              style={{ background: secondary, color: button }}
              onClick={() => haptic("light")}
            >
              {t.support}
            </a>
          </div>
        )}
      </main>

      {selected && (
        <div className="fixed inset-0 z-40 bg-black/60 flex items-end">
          <div
            className="w-full rounded-t-3xl border-t p-5 space-y-3 pb-28"
            style={{ background: secondary, borderColor: `${hint}44` }}
          >
            <div className="font-bold">
              {lang === "en" ? (selected.title_en || selected.title_ar) : (selected.title_ar || selected.title_en)}
            </div>
            <div className="text-xl font-bold" style={{ color: button }}>
              ${Number(selected.retail_price ?? selected.final_price ?? selected.price_usd ?? 0).toFixed(2)}
            </div>
            {(selected.requires_customer_email || selected.requires_customer_input) && (
              <input
                value={buyerEmail}
                onChange={(e) => setBuyerEmail(e.target.value)}
                placeholder={selected.customer_input_label_ar || t.email}
                className="w-full rounded-xl px-3 py-2.5 text-sm border outline-none"
                style={{ background: bg, borderColor: `${hint}44`, color: text }}
              />
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => void buyWithBalance()}
              className="w-full py-3 rounded-xl font-bold disabled:opacity-50"
              style={{ background: button, color: buttonText }}
            >
              {t.checkout}
            </button>
            <button type="button" onClick={closeCheckout} className="w-full py-2 text-sm" style={{ color: hint }}>
              ✕
            </button>
          </div>
        </div>
      )}

      <nav
        className="fixed bottom-0 inset-x-0 z-30 border-t backdrop-blur"
        style={{ background: `${bg}f5`, borderColor: `${hint}33` }}
      >
        <div className="grid grid-cols-4 gap-1 px-2 py-2">
          {([
            ["store", t.store],
            ["orders", t.orders],
            ["balance", t.balance],
            ["account", t.account],
          ] as [Tab, string][]).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => { haptic("light"); setTab(id); }}
              className="py-2 rounded-xl text-[11px] font-semibold"
              style={{
                background: tab === id ? `${button}22` : "transparent",
                color: tab === id ? button : hint,
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </nav>
    </div>
  );
}
