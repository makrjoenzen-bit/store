"use client";

import React, { useState, useEffect } from "react";
import { 
  Landmark, Download, TrendingUp, AlertCircle, ShieldCheck, 
  Wallet, ArrowDownRight, ArrowUpRight, Scale, Clock, 
  Calendar, RefreshCw, CheckCircle2, ChevronRight, ChevronLeft
} from "lucide-react";

interface TreasurySummary {
  total_assets: number;
  hot_wallet_usdt: number;
  supplier_credits: number;
  sham_cash_collected: number;
  total_liabilities: number;
  net_withdrawable: number;
  total_revenue: number;
  total_refunds: number;
  profit_today: { gross_profit: number; gas_expenses: number; net_profit: number; order_count: number };
  profit_7d: { gross_profit: number; gas_expenses: number; net_profit: number; order_count: number };
  profit_30d: { gross_profit: number; gas_expenses: number; net_profit: number; order_count: number };
  profit_all_time: { gross_profit: number; gas_expenses: number; net_profit: number; order_count: number };
}

interface LedgerItem {
  id: string;
  customer_id: string;
  customer_name: string;
  tx_type: string;
  amount: number;
  balance_after: number;
  description: string;
  order_id: string | null;
  created_at: string;
}

export default function TreasuryPage() {
  const [summary, setSummary] = useState<TreasurySummary | null>(null);
  const [ledger, setLedger] = useState<LedgerItem[]>([]);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [txTypeFilter, setTxTypeFilter] = useState<string>("");
  const [loading, setLoading] = useState(true);

  const fetchSummary = async () => {
    try {
      const res = await fetch("/api/treasury/summary");
      if (res.ok) {
        const data = await res.json();
        setSummary(data);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchLedger = async (p = 1, filter = txTypeFilter) => {
    setLoading(true);
    try {
      const url = `/api/treasury/ledger?page=${p}&per_page=30${filter ? `&tx_type=${filter}` : ""}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        setLedger(data.items);
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

  useEffect(() => {
    fetchSummary();
    fetchLedger(1);
    const interval = setInterval(fetchSummary, 15000);
    return () => clearInterval(interval);
  }, []);

  const handleFilterChange = (filter: string) => {
    setTxTypeFilter(filter);
    fetchLedger(1, filter);
  };

  const getTypeBadge = (type: string) => {
    switch (type) {
      case "customer_deposit":
      case "admin_credit":
      case "overpayment_credited":
      case "late_payment_credited":
        return { label: "إيداع / شحن", bg: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" };
      case "order_charge":
      case "purchase_debit":
      case "admin_debit":
        return { label: "شراء / خصم", bg: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20" };
      case "refund_credit":
      case "split_payment_refund":
        return { label: "استرداد تعويضي", bg: "bg-amber-500/10 text-amber-400 border-amber-500/20" };
      default:
        return { label: type, bg: "bg-slate-500/10 text-slate-400 border-slate-500/20" };
    }
  };

  return (
    <div className="space-y-8 pb-12 font-sans">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
            <span>الخزينة والمحاسبة المالية</span>
            <span className="text-xs font-mono font-normal text-emerald-400 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20">
              SOLVENCY & ACCOUNTING
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            إدارة الملاءة المالية، حساب الأصول والخصوم، الأرباح القابلة للسحب، وسجل قيود العمليات غير القابل للتعديل.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => { fetchSummary(); fetchLedger(page); }}
            className="p-2.5 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-slate-300 border border-white/[0.08] transition"
            title="تحديث البيانات"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
          <a
            href="/api/treasury/export-csv"
            download
            className="flex items-center space-x-2 space-x-reverse px-4 py-2.5 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-bold text-xs shadow-lg shadow-emerald-600/20 transition active:scale-95"
          >
            <Download className="w-4 h-4" />
            <span>تصدير تقرير المحاسبة CSV</span>
          </a>
        </div>
      </div>

      {/* Solvency Equation Banner */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Total Assets */}
        <div className="p-6 rounded-3xl glass-panel border border-emerald-500/20 relative overflow-hidden bg-gradient-to-br from-emerald-950/20 via-transparent to-transparent">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-emerald-300">إجمالي الأصول (Total Assets)</span>
            <div className="w-8 h-8 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
              <Scale className="w-4 h-4" />
            </div>
          </div>
          <div className="text-3xl font-black font-mono text-white mb-3">
            ${summary?.total_assets.toFixed(2) || "0.00"}
          </div>
          <div className="space-y-1 text-[11px] text-slate-400 border-t border-white/[0.06] pt-2 font-mono">
            <div className="flex justify-between">
              <span>محفظة JIT الساخنة (USDT):</span>
              <span className="text-white">${summary?.hot_wallet_usdt.toFixed(2) || "0.00"}</span>
            </div>
            <div className="flex justify-between">
              <span>أرصدة الموردين المستردة:</span>
              <span className="text-white">${summary?.supplier_credits.toFixed(2) || "0.00"}</span>
            </div>
            <div className="flex justify-between">
              <span>تحصيلات شام كاش المؤكدة:</span>
              <span className="text-white">${summary?.sham_cash_collected.toFixed(2) || "0.00"}</span>
            </div>
          </div>
        </div>

        {/* Total Liabilities */}
        <div className="p-6 rounded-3xl glass-panel border border-rose-500/20 relative overflow-hidden bg-gradient-to-br from-rose-950/20 via-transparent to-transparent">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-rose-300">إجمالي الخصوم (Total Liabilities)</span>
            <div className="w-8 h-8 rounded-xl bg-rose-500/10 border border-rose-500/20 flex items-center justify-center text-rose-400">
              <AlertCircle className="w-4 h-4" />
            </div>
          </div>
          <div className="text-3xl font-black font-mono text-white mb-3">
            ${summary?.total_liabilities.toFixed(2) || "0.00"}
          </div>
          <p className="text-xs text-slate-400 leading-relaxed border-t border-white/[0.06] pt-2">
            مجموع أرصدة محافظ العملاء داخل البوت. أموال مملوكة للزبائن لا يجوز سحبها كأرباح لضمان الملاءة 100%.
          </p>
        </div>

        {/* Net Withdrawable Profit */}
        <div className="p-6 rounded-3xl glass-panel border border-indigo-500/30 relative overflow-hidden bg-gradient-to-br from-indigo-950/30 via-transparent to-transparent">
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs font-semibold text-indigo-300">صافي الأرباح القابلة للسحب</span>
            <div className="w-8 h-8 rounded-xl bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
              <ShieldCheck className="w-4 h-4" />
            </div>
          </div>
          <div className="text-3xl font-black font-mono text-emerald-400 mb-3">
            ${summary?.net_withdrawable.toFixed(2) || "0.00"}
          </div>
          <div className="text-xs text-slate-400 flex items-center justify-between border-t border-white/[0.06] pt-2 font-mono">
            <span>الصيغة المحاسبية:</span>
            <span className="text-indigo-300">الأصول - الخصوم</span>
          </div>
        </div>
      </div>

      {/* Profit Time Windows */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { label: "أرباح اليوم (Today)", data: summary?.profit_today, tag: "اليوم" },
          { label: "آخر 7 أيام (7 Days)", data: summary?.profit_7d, tag: "7 أيام" },
          { label: "آخر 30 يوماً (30 Days)", data: summary?.profit_30d, tag: "30 يوم" },
          { label: "الإجمالي التراكمي (All-Time)", data: summary?.profit_all_time, tag: "تراكمي" },
        ].map((w, idx) => (
          <div key={idx} className="p-5 rounded-2xl glass-panel border border-white/[0.08] space-y-2">
            <div className="flex items-center justify-between text-xs text-slate-400">
              <span>{w.label}</span>
              <span className="font-mono text-[10px] px-2 py-0.5 rounded-full bg-white/[0.06]">{w.data?.order_count || 0} طلب</span>
            </div>
            <div className="text-2xl font-black font-mono text-white">
              ${w.data?.net_profit.toFixed(2) || "0.00"}
            </div>
            <div className="flex items-center justify-between text-[11px] font-mono text-slate-400 pt-1 border-t border-white/[0.04]">
              <span>الربح الإجمالي: ${w.data?.gross_profit.toFixed(2) || "0.00"}</span>
              <span className="text-rose-400">غاز: ${w.data?.gas_expenses.toFixed(2) || "0.00"}</span>
            </div>
          </div>
        ))}
      </div>

      {/* Immutable Ledger Table */}
      <div className="p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-6">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-white/[0.06] pb-4">
          <div>
            <h3 className="text-base font-bold text-white flex items-center gap-2">
              <Clock className="w-4 h-4 text-emerald-400" />
              <span>سجل القيود المحاسبية التراكمي (Immutable Ledger)</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              كل شحن رصيد، شراء، استرداد، أو تعديل إداري مسجل بتوقيت دقيق ورقم معاملة.
            </p>
          </div>

          {/* Filter Pills */}
          <div className="flex items-center gap-1.5 p-1 rounded-xl bg-white/[0.03] border border-white/[0.06] text-xs">
            {[
              { id: "", label: "الكل" },
              { id: "customer_deposit", label: "إيداعات" },
              { id: "purchase_debit", label: "مشتريات" },
              { id: "refund_credit", label: "استردادات" },
              { id: "admin_credit", label: "تعديل إداري" },
            ].map((f) => (
              <button
                key={f.id}
                onClick={() => handleFilterChange(f.id)}
                className={`px-3 py-1.5 rounded-lg font-medium transition ${
                  txTypeFilter === f.id ? "bg-indigo-600 text-white font-bold" : "text-slate-400 hover:text-white"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-right text-xs">
            <thead>
              <tr className="border-b border-white/[0.06] text-slate-400 font-medium">
                <th className="pb-3 px-3">رقم القيد</th>
                <th className="pb-3 px-3">التاريخ والوقت</th>
                <th className="pb-3 px-3">العميل</th>
                <th className="pb-3 px-3">نوع العملية</th>
                <th className="pb-3 px-3">المبلغ</th>
                <th className="pb-3 px-3">الرصيد بعدها</th>
                <th className="pb-3 px-3">الوصف</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {ledger.map((item) => {
                const badge = getTypeBadge(item.tx_type);
                const isPositive = item.amount > 0;
                return (
                  <tr key={item.id} className="hover:bg-white/[0.02] transition">
                    <td className="py-3 px-3 font-mono text-slate-400">
                      {item.id.slice(0, 8)}...
                    </td>
                    <td className="py-3 px-3 font-mono text-slate-300">
                      {new Date(item.created_at).toLocaleString("ar-EG")}
                    </td>
                    <td className="py-3 px-3 text-white font-semibold">
                      {item.customer_name}
                    </td>
                    <td className="py-3 px-3">
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${badge.bg}`}>
                        {badge.label}
                      </span>
                    </td>
                    <td className="py-3 px-3 font-mono font-bold">
                      <span className={isPositive ? "text-emerald-400" : "text-rose-400"}>
                        {isPositive ? `+$${item.amount.toFixed(2)}` : `-$${Math.abs(item.amount).toFixed(2)}`}
                      </span>
                    </td>
                    <td className="py-3 px-3 font-mono text-slate-300">
                      ${item.balance_after.toFixed(2)}
                    </td>
                    <td className="py-3 px-3 text-slate-400 max-w-xs truncate">
                      {item.description}
                    </td>
                  </tr>
                );
              })}
              {ledger.length === 0 && !loading && (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-slate-500">
                    لا توجد قيود مسجلة بهذا الفلتر.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between border-t border-white/[0.06] pt-4 text-xs text-slate-400">
          <span>إجمالي السجلات: {totalCount}</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => fetchLedger(Math.max(1, page - 1))}
              disabled={page <= 1}
              className="p-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] disabled:opacity-40 transition"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
            <span className="font-mono text-white">صفحة {page} من {totalPages}</span>
            <button
              onClick={() => fetchLedger(Math.min(totalPages, page + 1))}
              disabled={page >= totalPages}
              className="p-1.5 rounded-lg bg-white/[0.04] hover:bg-white/[0.08] disabled:opacity-40 transition"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
