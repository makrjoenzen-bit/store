"use client";

import React, { useState, useEffect } from "react";
import { 
  ShoppingCart, Clock, CheckCircle2, AlertTriangle, 
  ArrowRight, Search, Filter, ExternalLink, ShieldCheck, 
  Terminal, X, Copy, Check 
} from "lucide-react";

export default function OrdersPage() {
  const [orders, setOrders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filterState, setFilterState] = useState("all");
  const [selectedOrder, setSelectedOrder] = useState<any>(null);
  const [orderDetails, setOrderDetails] = useState<any>(null);
  const [copiedKey, setCopiedKey] = useState(false);
  const [manualPayload, setManualPayload] = useState("");
  const [manualBusy, setManualBusy] = useState(false);
  const [manualMsg, setManualMsg] = useState<string | null>(null);
  const [proofBusy, setProofBusy] = useState(false);
  const [proofMsg, setProofMsg] = useState<string | null>(null);

  const stateLabel = (state: string) => {
    const map: Record<string, string> = {
      pending_manual_delivery: "بانتظار تسليم يدوي",
      failed_manual_review: "مراجعة يدوية فاشلة",
      awaiting_payment: "بانتظار الدفع",
      completed: "مكتمل",
      delivered: "تم التسليم",
      failed_refunded: "مسترد",
    };
    return map[state] || state.replace(/_/g, " ");
  };

  const publishProof = async (orderId?: string) => {
    const id = orderId || selectedOrder?.id;
    if (!id) return;
    setProofBusy(true);
    setProofMsg(null);
    try {
      const res = await fetch(`/api/orders/${id}/publish-proof`, {
        method: "POST",
        credentials: "include",
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      if (body.status === "skipped" && body.reason === "already_posted") {
        setProofMsg("تم النشر مسبقاً — لا تكرار");
      } else {
        setProofMsg("تم النشر في قناة التوثيق ✅");
      }
    } catch (e: any) {
      setProofMsg(e?.message || "فشل النشر");
    } finally {
      setProofBusy(false);
    }
  };

  const submitManualDeliver = async () => {
    if (!selectedOrder?.id || !manualPayload.trim()) return;
    setManualBusy(true);
    setManualMsg(null);
    try {
      const res = await fetch(`/api/orders/${selectedOrder.id}/manual-deliver`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ payload: manualPayload.trim() }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
      setManualMsg("تم التسليم اليدوي بنجاح");
      setManualPayload("");
      await handleOpenOrderDetails(selectedOrder.id);
      await fetchOrders();
    } catch (e: any) {
      setManualMsg(e?.message || "فشل التسليم");
    } finally {
      setManualBusy(false);
    }
  };

  const fetchOrders = async () => {
    try {
      const url = filterState === "all" ? "/api/orders?limit=60" : `/api/orders?state=${filterState}&limit=60`;
      const res = await fetch(url, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setOrders(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOrders();
    const interval = setInterval(fetchOrders, 10000);
    return () => clearInterval(interval);
  }, [filterState]);

  const handleOpenOrderDetails = async (orderId: string) => {
    try {
      const res = await fetch(`/api/orders/${orderId}`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setOrderDetails(data);
        setSelectedOrder(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const filteredOrders = orders.filter((o) => {
    const q = search.toLowerCase();
    return (
      o.order_number.toLowerCase().includes(q) ||
      (o.customer_username && o.customer_username.toLowerCase().includes(q)) ||
      (o.product_title && o.product_title.toLowerCase().includes(q))
    );
  });

  return (
    <div className="space-y-8 pb-12">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">الطلبات</h1>
          <p className="text-sm text-slate-400 mt-1">
            متابعة الطلبات ومراجعة المدفوعات اليدوية (شام كاش) من مكان واحد.
          </p>
        </div>

        {/* Search & State Filters */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative">
            <Search className="w-4 h-4 absolute right-3.5 top-3 text-slate-500" />
            <input
              type="text"
              placeholder="ابحث برقم الطلب أو اسم العميل..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-4 pr-10 py-2 rounded-xl bg-obsidian-950 border border-white/[0.08] text-xs text-white focus:outline-none focus:border-indigo-500 transition w-64"
            />
          </div>

          <div className="flex p-1 rounded-xl bg-white/[0.03] border border-white/[0.08] text-xs font-medium">
            {[
              { id: "all", label: "الكل" },
              { id: "pending_manual_delivery", label: "تسليم يدوي" },
              { id: "completed", label: "مكتملة" },
              { id: "awaiting_payment", label: "بانتظار الدفع" },
              { id: "failed_refunded", label: "مستردة" },
            ].map((f) => (
              <button
                key={f.id}
                onClick={() => setFilterState(f.id)}
                className={`px-3 py-1.5 rounded-lg transition ${
                  filterState === f.id ? "bg-indigo-600 text-white shadow-sm" : "text-slate-400 hover:text-white"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Modern Terminal Table */}
      <div className="rounded-2xl glass-panel border border-white/[0.08] overflow-hidden">
        <table className="w-full text-right text-xs text-slate-300">
          <thead className="bg-obsidian-950/60 border-b border-white/[0.06] text-[11px] uppercase text-slate-400 font-semibold tracking-wider font-mono">
            <tr>
              <th className="px-6 py-4">رقم الطلب (Order #)</th>
              <th className="px-6 py-4">العميل</th>
              <th className="px-6 py-4">المنتج</th>
              <th className="px-6 py-4">المورد المعتمد</th>
              <th className="px-6 py-4">الحالة التنفيذية</th>
              <th className="px-6 py-4">السعر والمحصلة</th>
              <th className="px-6 py-4">صافي الربح</th>
              <th className="px-6 py-4 text-left">التفاصيل</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04]">
            {filteredOrders.map((o) => (
              <tr key={o.id} className="hover:bg-white/[0.02] transition">
                <td className="px-6 py-4 font-mono font-bold text-white text-xs">
                  {o.order_number}
                </td>
                <td className="px-6 py-4 font-mono text-slate-300">
                  {o.customer_username ? `@${o.customer_username}` : o.customer_telegram_id}
                </td>
                <td className="px-6 py-4 font-medium text-white">{o.product_title}</td>
                <td className="px-6 py-4 text-slate-400 font-mono text-[11px]">{o.supplier_name || "—"}</td>
                <td className="px-6 py-4">
                  <span className={`px-2.5 py-1 rounded-lg text-[10px] font-mono font-bold uppercase tracking-wider ${
                    o.state === "completed" || o.state === "delivered"
                      ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                      : o.state === "pending_manual_delivery"
                      ? "bg-amber-500/10 text-amber-300 border border-amber-500/20"
                      : o.state.includes("failed")
                      ? "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                      : "bg-blue-500/10 text-blue-400 border border-blue-500/20"
                  }`}>
                    {stateLabel(o.state)}
                  </span>
                </td>
                <td className="px-6 py-4 font-mono font-bold text-white text-sm">
                  ${o.our_price.toFixed(2)}
                </td>
                <td className="px-6 py-4 font-mono font-bold text-emerald-400 text-xs">
                  {o.profit_margin ? `+$${o.profit_margin.toFixed(2)}` : "—"}
                </td>
                <td className="px-6 py-4 text-left">
                  <button
                    onClick={() => handleOpenOrderDetails(o.id)}
                    className="px-3 py-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] text-slate-300 font-medium text-xs border border-white/[0.08] transition"
                  >
                    عرض التفاصيل 🔍
                  </button>
                </td>
              </tr>
            ))}

            {filteredOrders.length === 0 && !loading && (
              <tr>
                <td colSpan={8} className="px-6 py-12 text-center text-slate-500">
                  لا توجد طلبات مسجلة تطابق معايير البحث.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Slide-Over Drawer: Order Lifecycle & Delivered Key Inspection */}
      {selectedOrder && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-xl h-full bg-obsidian-950 border-r border-white/[0.08] shadow-2xl flex flex-col justify-between overflow-hidden animate-in slide-in-from-left duration-300">
            {/* Header */}
            <div className="p-6 border-b border-white/[0.08] bg-obsidian-900/80 flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <span>تفاصيل وسجل الطلب #{selectedOrder.order_number}</span>
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  التحقق من بيانات التسليم ومعاملات البلوكشين وتتبع الخط الزمني للطلب.
                </p>
              </div>
              <button 
                onClick={() => setSelectedOrder(null)}
                className="w-8 h-8 rounded-lg bg-white/[0.05] text-slate-400 hover:text-white flex items-center justify-center"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Content Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-6 text-xs">
              
              {/* Financial Box */}
              <div className="p-4 rounded-2xl bg-white/[0.02] border border-white/[0.06] grid grid-cols-3 gap-3 text-center">
                <div>
                  <div className="text-[10px] text-slate-400">سعر البيع للعميل</div>
                  <div className="text-lg font-black font-mono text-white mt-1">
                    ${selectedOrder.financials.our_price.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400">تكلفة المورد JIT</div>
                  <div className="text-lg font-black font-mono text-slate-300 mt-1">
                    ${selectedOrder.financials.supplier_cost.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400">صافي ربحك المحتجز</div>
                  <div className="text-lg font-black font-mono text-emerald-400 mt-1">
                    +${selectedOrder.financials.profit_margin.toFixed(2)}
                  </div>
                </div>
              </div>

              {/* Manual delivery for queued / failed-manual orders */}
              {(selectedOrder.state === "pending_manual_delivery" ||
                selectedOrder.state === "failed_manual_review") && (
                <div className="space-y-2 p-4 rounded-2xl border border-amber-500/25 bg-amber-500/5">
                  <div className="text-xs font-semibold text-amber-200">تسليم يدوي</div>
                  <textarea
                    value={manualPayload}
                    onChange={(e) => setManualPayload(e.target.value)}
                    rows={4}
                    placeholder="الصق الحساب / المفتاح هنا…"
                    className="w-full rounded-xl bg-obsidian-950 border border-white/[0.08] text-xs text-white p-3 focus:outline-none focus:border-amber-500"
                  />
                  <button
                    type="button"
                    disabled={manualBusy || !manualPayload.trim()}
                    onClick={submitManualDeliver}
                    className="w-full py-2.5 rounded-xl bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white text-xs font-bold"
                  >
                    {manualBusy ? "جارٍ التسليم…" : "تسليم يدوي"}
                  </button>
                  {manualMsg && (
                    <p className="text-[11px] text-slate-300">{manualMsg}</p>
                  )}
                </div>
              )}

              <div className="space-y-2 p-4 rounded-2xl border border-sky-500/20 bg-sky-500/5">
                <div className="text-xs font-semibold text-sky-200">قناة التوثيق</div>
                <button
                  type="button"
                  disabled={proofBusy}
                  onClick={() => publishProof(selectedOrder.id)}
                  className="w-full py-2.5 rounded-xl bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-xs font-bold"
                >
                  {proofBusy ? "جارٍ النشر…" : "📢 نشر في قناة التوثيق"}
                </button>
                {proofMsg && <p className="text-[11px] text-slate-300">{proofMsg}</p>}
              </div>

              {/* Delivered Payload / License Key */}
              {selectedOrder.delivered_payload && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs font-semibold text-slate-300">
                    <span className="flex items-center gap-1.5">
                      <Terminal className="w-3.5 h-3.5 text-emerald-400" />
                      <span>بيانات الحساب / المفتاح المسلم للعميل:</span>
                    </span>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(selectedOrder.delivered_payload);
                        setCopiedKey(true);
                        setTimeout(() => setCopiedKey(false), 2000);
                      }}
                      className="text-indigo-400 hover:text-indigo-300 flex items-center gap-1 text-[11px]"
                    >
                      {copiedKey ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                      <span>{copiedKey ? "تم النسخ!" : "نسخ البيانات"}</span>
                    </button>
                  </div>
                  <div className="p-4 rounded-xl bg-obsidian-900 border border-white/[0.08] font-mono text-xs text-emerald-300 break-all whitespace-pre-wrap">
                    {selectedOrder.delivered_payload}
                  </div>
                </div>
              )}

              {/* JIT On-Chain Transactions */}
              {selectedOrder.jit_transactions && selectedOrder.jit_transactions.length > 0 && (
                <div className="space-y-2">
                  <span className="font-semibold text-slate-300">تحويلات تمويل المورد على البلوكشين (JIT Funding):</span>
                  {selectedOrder.jit_transactions.map((tx: any) => (
                    <div key={tx.id} className="p-3 rounded-xl bg-white/[0.02] border border-white/[0.06] font-mono text-[11px] flex items-center justify-between">
                      <div>
                        <div className="text-white font-bold">{tx.amount} USDT ({tx.network})</div>
                        <div className="text-slate-500 truncate max-w-xs mt-0.5">TX: {tx.tx_hash || "قيد البث..."}</div>
                      </div>
                      <span className="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px]">
                        {tx.state}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* State Timeline */}
              <div className="space-y-3">
                <span className="font-semibold text-slate-300">الخط الزمني للحالات (State Audit Timeline):</span>
                <div className="space-y-2 border-r border-white/[0.08] pr-4">
                  {selectedOrder.timeline.map((log: any, idx: number) => (
                    <div key={idx} className="relative space-y-0.5">
                      <span className="absolute -right-[21px] top-1.5 w-2.5 h-2.5 rounded-full bg-indigo-500 border border-obsidian-950"></span>
                      <div className="font-semibold text-white font-mono text-[11px]">{log.to_state}</div>
                      <div className="text-[10px] text-slate-500 font-mono">
                        المصدر: {log.trigger_source} • {new Date(log.created_at).toLocaleTimeString()}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

            </div>

            {/* Footer */}
            <div className="p-4 border-t border-white/[0.08] bg-obsidian-900/60 flex justify-end">
              <button
                onClick={() => setSelectedOrder(null)}
                className="px-4 py-2 rounded-xl bg-white/[0.06] hover:bg-white/[0.1] text-white font-medium text-xs"
              >
                إغلاق
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
