"use client";

import React, { useState, useEffect } from "react";
import { 
  Users, Search, Filter, ShieldAlert, ShieldCheck, 
  Wallet, DollarSign, ShoppingBag, LifeBuoy, X,
  CheckCircle2, AlertTriangle, ArrowRight, RefreshCw,
  PlusCircle, MinusCircle, FileText, ChevronRight, ChevronLeft,
  Calendar, Ban, UserCheck
} from "lucide-react";

interface CustomerItem {
  id: string;
  telegram_id: number;
  telegram_username: string | null;
  first_name: string | null;
  last_name: string | null;
  wallet_balance: number;
  total_spent: number;
  total_orders: number;
  active_tickets: number;
  account_status: string;
  is_blocked: boolean;
  first_seen_at: string | null;
  last_active_at: string | null;
}

interface CustomerDetail {
  customer: CustomerItem & { blocked_reason: string | null };
  orders: Array<{
    id: string;
    order_number: string;
    product_title: string;
    our_price: number;
    quantity: number;
    state: string;
    warranty_expires_at: string | null;
    created_at: string;
  }>;
  ledger: Array<{
    id: string;
    tx_type: string;
    amount: number;
    balance_after: number;
    description: string;
    created_at: string;
  }>;
  formal_invoices: Array<{
    id: string;
    invoice_number: string;
    item_title: string;
    net_total: number;
    payment_method: string;
    warranty_expires_at: string | null;
    created_at: string;
  }>;
  tickets: Array<{
    id: string;
    subject: string;
    status: string;
    created_at: string;
  }>;
}

export default function CustomersPage() {
  const [customers, setCustomers] = useState<CustomerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  // Drawer state
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CustomerDetail | null>(null);
  const [detailTab, setDetailTab] = useState<"orders" | "invoices" | "ledger" | "tickets">("orders");
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Balance adjustment modal/form state
  const [adjustAmount, setAdjustAmount] = useState<number | "">("");
  const [adjustDirection, setAdjustDirection] = useState<"credit" | "debit">("credit");
  const [adjustReason, setAdjustReason] = useState("");
  const [adjusting, setAdjusting] = useState(false);
  const [adjustSuccess, setAdjustSuccess] = useState(false);

  // Ban action
  const [banning, setBanning] = useState(false);

  const fetchCustomers = async (p = 1, q = search, st = statusFilter) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set("page", String(p));
      params.set("per_page", "25");
      if (q) params.set("search", q);
      if (st) params.set("status", st);

      const res = await fetch(`/api/customers?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setCustomers(data.items);
        setTotalPages(data.total_pages);
        setTotalCount(data.total);
        setPage(data.page);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const openCustomerProfile = async (id: string) => {
    setSelectedCustomerId(id);
    setLoadingDetail(true);
    setAdjustSuccess(false);
    try {
      const res = await fetch(`/api/customers/${id}`);
      if (res.ok) {
        const data = await res.json();
        setDetail(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingDetail(false);
    }
  };

  useEffect(() => {
    fetchCustomers(1);
  }, []);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    fetchCustomers(1, search, statusFilter);
  };

  const handleAdjustBalance = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedCustomerId || !adjustAmount || adjustAmount <= 0 || !adjustReason) return;
    setAdjusting(true);
    try {
      const res = await fetch(`/api/customers/${selectedCustomerId}/adjust-balance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          amount: Number(adjustAmount),
          direction: adjustDirection,
          reason: adjustReason,
        }),
      });
      if (res.ok) {
        setAdjustSuccess(true);
        setAdjustAmount("");
        setAdjustReason("");
        // Reload detail
        openCustomerProfile(selectedCustomerId);
        fetchCustomers(page, search, statusFilter);
        setTimeout(() => setAdjustSuccess(false), 4000);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setAdjusting(false);
    }
  };

  const handleToggleBan = async () => {
    if (!selectedCustomerId || !detail) return;
    const isCurrentlyBanned = detail.customer.account_status === "banned";
    const nextAction = isCurrentlyBanned ? "unban" : "ban";
    setBanning(true);
    try {
      const res = await fetch(`/api/customers/${selectedCustomerId}/ban`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: nextAction,
          reason: isCurrentlyBanned ? "Unbanned by admin" : "Banned by admin via CRM",
        }),
      });
      if (res.ok) {
        openCustomerProfile(selectedCustomerId);
        fetchCustomers(page, search, statusFilter);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setBanning(false);
    }
  };

  return (
    <div className="space-y-8 pb-12 font-sans relative">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
            <span>إدارة العملاء والمحافظ (CRM)</span>
            <span className="text-xs font-mono font-normal text-cyan-400 px-2 py-0.5 rounded-full bg-cyan-500/10 border border-cyan-500/20">
              CUSTOMERS 360°
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            سجل العملاء، إدارة الأرصدة، الحظر الفوري، سجل المشتريات والفواتير الرسمية لكل مستخدم.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => fetchCustomers(page)}
            className="p-2.5 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-slate-300 border border-white/[0.08] transition"
            title="تحديث البيانات"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Search & Filters */}
      <div className="p-4 rounded-2xl glass-panel border border-white/[0.08] flex flex-col md:flex-row items-center justify-between gap-4">
        <form onSubmit={handleSearchSubmit} className="flex items-center gap-2 flex-1 w-full">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-slate-400 absolute right-3.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="بحث بالمعرف (Telegram ID)، اسم المستخدم (@username)، أو الاسم..."
              className="w-full pl-4 pr-10 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white text-xs focus:outline-none focus:border-cyan-500 transition font-sans"
            />
          </div>
          <button
            type="submit"
            className="px-4 py-2.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-xs transition"
          >
            بحث
          </button>
        </form>

        <div className="flex items-center gap-1.5 p-1 rounded-xl bg-white/[0.03] border border-white/[0.06] text-xs">
          {[
            { id: "", label: "جميع الحالات" },
            { id: "active", label: "نشط" },
            { id: "banned", label: "محظور" },
          ].map((s) => (
            <button
              key={s.id}
              onClick={() => {
                setStatusFilter(s.id);
                fetchCustomers(1, search, s.id);
              }}
              className={`px-3 py-1.5 rounded-lg font-medium transition ${
                statusFilter === s.id ? "bg-cyan-600 text-white font-bold" : "text-slate-400 hover:text-white"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {/* Customers Table */}
      <div className="p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-4">
        <div className="overflow-x-auto">
          <table className="w-full text-right text-xs">
            <thead>
              <tr className="border-b border-white/[0.06] text-slate-400 font-medium">
                <th className="pb-3 px-3">العميل</th>
                <th className="pb-3 px-3">معرف تلغرام (ID)</th>
                <th className="pb-3 px-3">رصيد المحفظة</th>
                <th className="pb-3 px-3">إجمالي الإنفاق</th>
                <th className="pb-3 px-3">الطلبات</th>
                <th className="pb-3 px-3">تذاكر نشطة</th>
                <th className="pb-3 px-3">الحالة</th>
                <th className="pb-3 px-3">تاريخ الانضمام</th>
                <th className="pb-3 px-3 text-center">الإجراء</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {customers.map((c) => {
                const isBanned = c.account_status === "banned" || c.is_blocked;
                return (
                  <tr key={c.id} className="hover:bg-white/[0.02] transition">
                    <td className="py-3 px-3">
                      <div className="font-bold text-white">
                        {c.first_name || ""} {c.last_name || ""}
                      </div>
                      <div className="text-[11px] text-cyan-400 font-mono">
                        {c.telegram_username ? `@${c.telegram_username}` : "بدون يوزر"}
                      </div>
                    </td>
                    <td className="py-3 px-3 font-mono text-slate-300">
                      {c.telegram_id}
                    </td>
                    <td className="py-3 px-3 font-mono font-bold text-emerald-400">
                      ${c.wallet_balance.toFixed(2)}
                    </td>
                    <td className="py-3 px-3 font-mono text-slate-300">
                      ${c.total_spent.toFixed(2)}
                    </td>
                    <td className="py-3 px-3 font-mono text-slate-300">
                      {c.total_orders}
                    </td>
                    <td className="py-3 px-3">
                      {c.active_tickets > 0 ? (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30">
                          {c.active_tickets} مفتوحة
                        </span>
                      ) : (
                        <span className="text-slate-500 text-[11px]">لا يوجد</span>
                      )}
                    </td>
                    <td className="py-3 px-3">
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                        isBanned 
                          ? "bg-rose-500/10 text-rose-400 border-rose-500/20" 
                          : "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                      }`}>
                        {isBanned ? "محظور" : "نشط"}
                      </span>
                    </td>
                    <td className="py-3 px-3 font-mono text-slate-400 text-[11px]">
                      {c.first_seen_at ? new Date(c.first_seen_at).toLocaleDateString("ar-EG") : "-"}
                    </td>
                    <td className="py-3 px-3 text-center">
                      <button
                        onClick={() => openCustomerProfile(c.id)}
                        className="px-3 py-1.5 rounded-xl bg-white/[0.05] hover:bg-cyan-500/20 hover:text-cyan-300 text-slate-300 border border-white/[0.08] transition font-semibold text-[11px]"
                      >
                        ملف العميل 360°
                      </button>
                    </td>
                  </tr>
                );
              })}
              {customers.length === 0 && !loading && (
                <tr>
                  <td colSpan={9} className="py-8 text-center text-slate-500">
                    لم يتم العثور على أي عملاء بهذا البحث.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between border-t border-white/[0.06] pt-4 text-xs text-slate-400">
          <span>إجمالي العملاء: {totalCount}</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => fetchCustomers(Math.max(1, page - 1))}
              disabled={page <= 1}
              className="p-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] disabled:opacity-40 transition"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
            <span className="font-mono text-white">صفحة {page} من {totalPages}</span>
            <button
              onClick={() => fetchCustomers(Math.min(totalPages, page + 1))}
              disabled={page >= totalPages}
              className="p-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] disabled:opacity-40 transition"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Customer 360° Slide-Over Drawer */}
      {selectedCustomerId && (
        <div className="fixed inset-0 z-50 overflow-hidden flex justify-end bg-black/70 backdrop-blur-sm animate-in fade-in">
          <div className="w-full max-w-2xl bg-obsidian-950 border-r border-white/[0.08] h-full overflow-y-auto p-6 md:p-8 space-y-6 shadow-2xl">
            
            {/* Drawer Header */}
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-cyan-600 to-indigo-600 flex items-center justify-center font-bold text-white text-base shadow-lg">
                  {detail?.customer.first_name?.charAt(0) || "U"}
                </div>
                <div>
                  <h2 className="text-lg font-bold text-white flex items-center gap-2">
                    <span>{detail?.customer.first_name || ""} {detail?.customer.last_name || ""}</span>
                    {detail?.customer.account_status === "banned" && (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30">
                        محظور
                      </span>
                    )}
                  </h2>
                  <p className="text-xs text-cyan-400 font-mono">
                    {detail?.customer.telegram_username ? `@${detail.customer.telegram_username}` : "لا يوجد معرف"} • ID: {detail?.customer.telegram_id}
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={handleToggleBan}
                  disabled={banning}
                  className={`flex items-center space-x-1.5 space-x-reverse px-3 py-1.5 rounded-xl border text-xs font-bold transition ${
                    detail?.customer.account_status === "banned"
                      ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20"
                      : "bg-rose-500/10 text-rose-400 border-rose-500/30 hover:bg-rose-500/20"
                  }`}
                >
                  <Ban className="w-3.5 h-3.5" />
                  <span>{detail?.customer.account_status === "banned" ? "إلغاء الحظر" : "حظر العميل"}</span>
                </button>
                <button
                  onClick={() => setSelectedCustomerId(null)}
                  className="p-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-slate-400 hover:text-white transition"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* Quick Metrics */}
            <div className="grid grid-cols-3 gap-3">
              <div className="p-4 rounded-2xl bg-white/[0.03] border border-white/[0.06]">
                <div className="text-[11px] text-slate-400 mb-1">رصيد المحفظة</div>
                <div className="text-xl font-mono font-black text-emerald-400">
                  ${detail?.customer.wallet_balance.toFixed(2) || "0.00"}
                </div>
              </div>
              <div className="p-4 rounded-2xl bg-white/[0.03] border border-white/[0.06]">
                <div className="text-[11px] text-slate-400 mb-1">إجمالي المشتريات</div>
                <div className="text-xl font-mono font-black text-white">
                  ${detail?.customer.total_spent.toFixed(2) || "0.00"}
                </div>
              </div>
              <div className="p-4 rounded-2xl bg-white/[0.03] border border-white/[0.06]">
                <div className="text-[11px] text-slate-400 mb-1">الطلبات المنفذة</div>
                <div className="text-xl font-mono font-black text-indigo-400">
                  {detail?.customer.total_orders || 0}
                </div>
              </div>
            </div>

            {/* Balance Adjustment Widget */}
            <form onSubmit={handleAdjustBalance} className="p-5 rounded-2xl glass-panel border border-cyan-500/20 space-y-3 bg-gradient-to-br from-cyan-950/20 via-transparent to-transparent">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-bold text-white flex items-center gap-1.5">
                  <Wallet className="w-4 h-4 text-cyan-400" />
                  <span>تعديل رصيد العميل يدوياً (Manual Balance Adjustment)</span>
                </h4>
                {adjustSuccess && (
                  <span className="text-[11px] text-emerald-400 flex items-center gap-1">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    <span>تم التعديل وإشعار العميل في تلغرام!</span>
                  </span>
                )}
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 text-xs">
                <div>
                  <label className="block text-slate-400 mb-1 text-[11px]">نوع العملية:</label>
                  <select
                    value={adjustDirection}
                    onChange={(e) => setAdjustDirection(e.target.value as any)}
                    className="w-full px-3 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-cyan-500"
                  >
                    <option value="credit">+ إضافة رصيد (Credit)</option>
                    <option value="debit">- خصم من الرصيد (Debit)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1 text-[11px]">المبلغ (USD $):</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0.01"
                    value={adjustAmount}
                    onChange={(e) => setAdjustAmount(e.target.value === "" ? "" : parseFloat(e.target.value))}
                    placeholder="0.00"
                    className="w-full px-3 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-cyan-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 mb-1 text-[11px]">السبب الإلزامي:</label>
                  <input
                    type="text"
                    value={adjustReason}
                    onChange={(e) => setAdjustReason(e.target.value)}
                    placeholder="مثال: تعويض عطل أو شحن خارجي"
                    className="w-full px-3 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-cyan-500"
                  />
                </div>
              </div>

              <div className="flex justify-end pt-1">
                <button
                  type="submit"
                  disabled={adjusting || !adjustAmount || !adjustReason}
                  className="px-5 py-2 rounded-xl bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 text-white font-bold text-xs shadow transition active:scale-95 disabled:opacity-50"
                >
                  {adjusting ? "جاري التطبيق..." : "تطبيق وتحديث الرصيد"}
                </button>
              </div>
            </form>

            {/* History Tabs */}
            <div className="space-y-4">
              <div className="flex border-b border-white/[0.06] text-xs">
                {[
                  { id: "orders", label: `الطلبات (${detail?.orders.length || 0})` },
                  { id: "invoices", label: `الفواتير الرسمية (${detail?.formal_invoices.length || 0})` },
                  { id: "ledger", label: `حركات المحفظة (${detail?.ledger.length || 0})` },
                  { id: "tickets", label: `تذاكر الدعم (${detail?.tickets.length || 0})` },
                ].map((t) => (
                  <button
                    key={t.id}
                    onClick={() => setDetailTab(t.id as any)}
                    className={`pb-2.5 px-4 font-semibold transition border-b-2 ${
                      detailTab === t.id
                        ? "border-cyan-500 text-white"
                        : "border-transparent text-slate-400 hover:text-white"
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              {/* Tab 1: Orders */}
              {detailTab === "orders" && (
                <div className="space-y-2">
                  {detail?.orders.map((o) => (
                    <div key={o.id} className="p-3.5 rounded-xl bg-white/[0.02] border border-white/[0.05] flex items-center justify-between text-xs">
                      <div>
                        <div className="font-bold text-white flex items-center gap-1.5">
                          <span>#{o.order_number}</span>
                          <span className="text-slate-400">({o.quantity} قطعة)</span>
                        </div>
                        <div className="text-[11px] text-slate-400">{o.product_title}</div>
                        {o.warranty_expires_at && (
                          <div className="text-[10px] text-amber-400 font-mono mt-0.5">
                            ضمان حتى: {new Date(o.warranty_expires_at).toLocaleDateString("ar-EG")}
                          </div>
                        )}
                      </div>
                      <div className="text-left font-mono">
                        <div className="font-bold text-white">${o.our_price.toFixed(2)}</div>
                        <span className="text-[10px] text-indigo-300 font-sans">{o.state}</span>
                      </div>
                    </div>
                  ))}
                  {detail?.orders.length === 0 && (
                    <p className="text-center text-xs text-slate-500 py-6">لا توجد طلبات سابقة.</p>
                  )}
                </div>
              )}

              {/* Tab 2: Formal Invoices */}
              {detailTab === "invoices" && (
                <div className="space-y-2">
                  {detail?.formal_invoices.map((inv) => (
                    <div key={inv.id} className="p-3.5 rounded-xl bg-white/[0.02] border border-white/[0.05] flex items-center justify-between text-xs">
                      <div>
                        <div className="font-mono font-bold text-cyan-400">{inv.invoice_number}</div>
                        <div className="text-[11px] text-slate-300">{inv.item_title}</div>
                        <div className="text-[10px] text-slate-500 font-mono">
                          {new Date(inv.created_at).toLocaleDateString("ar-EG")} • {inv.payment_method}
                        </div>
                      </div>
                      <div className="text-left font-mono">
                        <div className="font-bold text-emerald-400">${inv.net_total.toFixed(2)}</div>
                        {inv.warranty_expires_at && (
                          <span className="text-[10px] text-amber-400">
                            ضمان: {new Date(inv.warranty_expires_at).toLocaleDateString("ar-EG")}
                          </span>
                        )}
                      </div>
                    </div>
                  ))}
                  {detail?.formal_invoices.length === 0 && (
                    <p className="text-center text-xs text-slate-500 py-6">لا توجد فواتير رسمية مسجلة.</p>
                  )}
                </div>
              )}

              {/* Tab 3: Ledger */}
              {detailTab === "ledger" && (
                <div className="space-y-2">
                  {detail?.ledger.map((l) => (
                    <div key={l.id} className="p-3 rounded-xl bg-white/[0.02] border border-white/[0.05] flex items-center justify-between text-xs">
                      <div>
                        <div className="text-slate-300">{l.description}</div>
                        <div className="text-[10px] font-mono text-slate-500">
                          {new Date(l.created_at).toLocaleString("ar-EG")}
                        </div>
                      </div>
                      <div className="text-left font-mono">
                        <div className={`font-bold ${l.amount > 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {l.amount > 0 ? `+$${l.amount.toFixed(2)}` : `-$${Math.abs(l.amount).toFixed(2)}`}
                        </div>
                        <div className="text-[10px] text-slate-400">${l.balance_after.toFixed(2)}</div>
                      </div>
                    </div>
                  ))}
                  {detail?.ledger.length === 0 && (
                    <p className="text-center text-xs text-slate-500 py-6">لا توجد حركات محفظة مسجلة.</p>
                  )}
                </div>
              )}

              {/* Tab 4: Tickets */}
              {detailTab === "tickets" && (
                <div className="space-y-2">
                  {detail?.tickets.map((t) => (
                    <div key={t.id} className="p-3 rounded-xl bg-white/[0.02] border border-white/[0.05] flex items-center justify-between text-xs">
                      <div>
                        <div className="font-bold text-white">{t.subject}</div>
                        <div className="text-[10px] text-slate-500 font-mono">
                          {new Date(t.created_at).toLocaleDateString("ar-EG")}
                        </div>
                      </div>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-white/[0.06] text-slate-300">
                        {t.status}
                      </span>
                    </div>
                  ))}
                  {detail?.tickets.length === 0 && (
                    <p className="text-center text-xs text-slate-500 py-6">لا توجد تذاكر دعم سابقة.</p>
                  )}
                </div>
              )}
            </div>

          </div>
        </div>
      )}
    </div>
  );
}
