"use client";

import React, { useEffect, useState } from "react";
import { 
  TrendingUp, DollarSign, ShoppingCart, Server, 
  Wallet, AlertTriangle, ArrowUpRight, CheckCircle2, 
  Clock, ShieldCheck, Zap, Activity, ExternalLink, ArrowRight
} from "lucide-react";

export default function CommandCenter() {
  const [metrics, setMetrics] = useState({
    kpis: { revenue_today: 0, profit_today: 0, orders_today: 0, active_suppliers: "0/0" },
    hot_wallet: { usdt_balance: 0, bnb_balance: 0, is_gas_healthy: true },
    stranded_supplier_credits: 0,
  });

  const [liveOrders, setLiveOrders] = useState<any[]>([
    {
      id: "ord-demo-1",
      order_number: "ORD-20261117-0042",
      product: "ChatGPT Plus 1M",
      supplier: "VenteBot",
      amount: 23.06,
      cost: 18.00,
      profit: 4.94,
      state: "completed",
      step: 4,
      time: "منذ دقيقتين",
    },
    {
      id: "ord-demo-2",
      order_number: "ORD-20261117-0041",
      product: "Midjourney Pro",
      supplier: "VenteBot",
      amount: 33.81,
      cost: 27.00,
      profit: 6.75,
      state: "jit_funding_confirmed",
      step: 2,
      time: "منذ 5 دقائق",
    },
    {
      id: "ord-demo-3",
      order_number: "ORD-20261117-0040",
      product: "Office 365 ProPlus",
      supplier: "SupplrB",
      amount: 8.86,
      cost: 3.80,
      profit: 5.00,
      state: "supplier_order_placed",
      step: 3,
      time: "منذ 8 دقائق",
    },
  ]);

  const [timeFilter, setTimeFilter] = useState("24h");

  // Fetch initial metrics & connect WebSocket
  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const res = await fetch("/api/dashboard/metrics");
        if (res.ok) {
          const data = await res.json();
          setMetrics(data);
        }
      } catch (e) {
        console.error("Failed to load metrics:", e);
      }
    };
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 12000);

    // WebSocket listener
    const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.channel === "orders:live") {
            const ev = payload.data;
            const state = ev.new_state;
            let step = 1;
            if (state === "payment_confirmed" || state === "quote_locked") step = 1;
            else if (state.includes("funding")) step = 2;
            else if (state.includes("supplier") || state === "order_fulfilled") step = 3;
            else if (state === "completed" || state === "delivered") step = 4;

            setLiveOrders((prev) => [
              {
                id: ev.order_id,
                order_number: ev.details?.order_number || `ORD-${ev.order_id.substring(0, 8)}`,
                product: ev.details?.product || "اشتراك رقمي فوري",
                supplier: ev.details?.supplier || "VenteBot",
                amount: ev.details?.our_price || 23.00,
                cost: ev.details?.supplier_cost || 18.00,
                profit: ev.details?.profit || 5.00,
                state: state,
                step: step,
                time: "الآن",
              },
              ...prev.slice(0, 11),
            ]);
          }
        } catch (err) {}
      };
    } catch (e) {}

    return () => {
      clearInterval(interval);
      if (ws) ws.close();
    };
  }, []);

  // Compute status step visual labels
  const getStepVisual = (step: number, state: string) => {
    const isFailed = state.includes("failed") || state.includes("rejected");
    if (isFailed) {
      return (
        <span className="px-2.5 py-1 rounded-full text-[10px] font-mono font-bold bg-rose-500/10 text-rose-400 border border-rose-500/20">
          فشل وتراجع
        </span>
      );
    }
    const steps = [
      { name: "الدفع", done: step >= 1 },
      { name: "تمويل JIT", done: step >= 2 },
      { name: "شراء المورد", done: step >= 3 },
      { name: "التسليم", done: step >= 4 },
    ];
    return (
      <div className="flex items-center space-x-1.5 space-x-reverse">
        {steps.map((st, i) => (
          <div key={i} className="flex items-center space-x-1 space-x-reverse">
            <span
              className={`w-2 h-2 rounded-full transition-all duration-300 ${
                st.done
                  ? i === step - 1 && step < 4
                    ? "bg-amber-400 animate-ping"
                    : "bg-emerald-400"
                  : "bg-white/[0.12]"
              }`}
            />
            <span className={`text-[10px] font-mono ${st.done ? "text-slate-200" : "text-slate-600"}`}>
              {st.name}
            </span>
            {i < steps.length - 1 && <span className="text-[10px] text-slate-700">➔</span>}
          </div>
        ))}
      </div>
    );
  };

  return (
    <div className="space-y-8 pb-12">
      {/* Page Header with Real-Time Terminal Title */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <div className="flex items-center space-x-3 space-x-reverse">
            <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
              <span>مركز العمليات والتداول المالي</span>
              <span className="text-xs font-mono font-normal text-emerald-400 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20">
                LIVE TERMINAL
              </span>
            </h1>
          </div>
          <p className="text-sm text-slate-400 mt-1">
            مراقبة لحظية للتسليم الآلي، هوامش الربح الصافية، وتدفق التمويل اللحظي (JIT).
          </p>
        </div>

        {/* Quick Filter Pill */}
        <div className="flex items-center p-1 rounded-xl bg-white/[0.03] border border-white/[0.08] text-xs font-medium">
          {["24h", "7d", "30d", "all"].map((f) => (
            <button
              key={f}
              onClick={() => setTimeFilter(f)}
              className={`px-3 py-1.5 rounded-lg transition ${
                timeFilter === f
                  ? "bg-indigo-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              {f === "24h" ? "اليوم" : f === "7d" ? "7 أيام" : f === "30d" ? "هذا الشهر" : "الكل"}
            </button>
          ))}
        </div>
      </div>

      {/* Stranded Supplier Deposit Notification (If applicable) */}
      {metrics.stranded_supplier_credits > 0 && (
        <div className="relative overflow-hidden p-4 rounded-2xl border border-amber-500/30 bg-gradient-to-r from-amber-500/10 via-amber-500/5 to-transparent flex items-center justify-between text-amber-200 text-sm">
          <div className="flex items-center space-x-3 space-x-reverse">
            <div className="w-8 h-8 rounded-xl bg-amber-500/20 flex items-center justify-center text-amber-400 font-bold">
              <AlertTriangle className="w-4 h-4" />
            </div>
            <div>
              <span className="font-bold">رصيد تمويل محجوز لدى الموردين:</span>{" "}
              <span className="font-mono font-bold text-amber-300">
                ${metrics.stranded_supplier_credits.toFixed(2)}
              </span>{" "}
              (سيعوض تلقائياً من تكلفة أول طلب جديد لنفس المورد دون سحب غاز أو دولارات جديدة).
            </div>
          </div>
          <span className="text-xs font-mono text-amber-400 bg-amber-500/20 px-2.5 py-1 rounded-lg border border-amber-500/30">
            AUTO-OFFSET ACTIVE
          </span>
        </div>
      )}

      {/* Hero Bento Grid: 4 High-Impact Glass Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
        
        {/* Card 1: Captured Net Profit */}
        <div className="relative overflow-hidden p-6 rounded-2xl glass-panel glass-panel-hover border border-white/[0.08] glow-emerald group">
          <div className="absolute top-0 right-0 left-0 h-1 bg-gradient-to-r from-emerald-500 via-teal-400 to-transparent"></div>
          <div className="flex items-center justify-between text-slate-400 mb-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">صافي الأرباح المحققة</span>
            <div className="w-8 h-8 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
              <TrendingUp className="w-4 h-4" />
            </div>
          </div>
          <div className="text-3xl font-black font-mono tracking-tight text-emerald-400">
            ${metrics.kpis.profit_today.toFixed(2)}
          </div>
          <div className="flex items-center justify-between mt-3 text-xs">
            <span className="text-slate-400">أرباح صافية 100% في محفظتك</span>
            <span className="font-mono text-emerald-400 font-semibold bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/20">
              +100% آمن
            </span>
          </div>
          {/* Mini Sparkline SVG */}
          <div className="mt-4 pt-2 border-t border-white/[0.05]">
            <svg className="w-full h-8 stroke-emerald-400 fill-emerald-500/10" viewBox="0 0 100 20">
              <path d="M0 15 Q 25 5, 50 12 T 100 4 L 100 20 L 0 20 Z" />
              <path d="M0 15 Q 25 5, 50 12 T 100 4" fill="none" strokeWidth="2" />
            </svg>
          </div>
        </div>

        {/* Card 2: Gross Revenue */}
        <div className="relative overflow-hidden p-6 rounded-2xl glass-panel glass-panel-hover border border-white/[0.08] glow-indigo group">
          <div className="absolute top-0 right-0 left-0 h-1 bg-gradient-to-r from-indigo-500 via-blue-400 to-transparent"></div>
          <div className="flex items-center justify-between text-slate-400 mb-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">إجمالي المبيعات (Revenue)</span>
            <div className="w-8 h-8 rounded-xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
              <DollarSign className="w-4 h-4" />
            </div>
          </div>
          <div className="text-3xl font-black font-mono tracking-tight text-white">
            ${metrics.kpis.revenue_today.toFixed(2)}
          </div>
          <div className="flex items-center justify-between mt-3 text-xs">
            <span className="text-slate-400">مقبوضات البلوكشين وشام كاش</span>
            <span className="font-mono text-indigo-300 font-semibold bg-indigo-500/10 px-2 py-0.5 rounded-full border border-indigo-500/20">
              JIT INGEST
            </span>
          </div>
          <div className="mt-4 pt-2 border-t border-white/[0.05]">
            <svg className="w-full h-8 stroke-indigo-400 fill-indigo-500/10" viewBox="0 0 100 20">
              <path d="M0 18 Q 30 14, 60 8 T 100 2 L 100 20 L 0 20 Z" />
              <path d="M0 18 Q 30 14, 60 8 T 100 2" fill="none" strokeWidth="2" />
            </svg>
          </div>
        </div>

        {/* Card 3: Completed Orders Today */}
        <div className="relative overflow-hidden p-6 rounded-2xl glass-panel glass-panel-hover border border-white/[0.08] group">
          <div className="absolute top-0 right-0 left-0 h-1 bg-gradient-to-r from-sky-500 via-cyan-400 to-transparent"></div>
          <div className="flex items-center justify-between text-slate-400 mb-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">الطلبات المنفذة</span>
            <div className="w-8 h-8 rounded-xl bg-sky-500/10 border border-sky-500/20 flex items-center justify-center text-sky-400">
              <ShoppingCart className="w-4 h-4" />
            </div>
          </div>
          <div className="text-3xl font-black font-mono tracking-tight text-white">
            {metrics.kpis.orders_today}
          </div>
          <div className="flex items-center justify-between mt-3 text-xs">
            <span className="text-slate-400">سرعة التسليم الفوري</span>
            <span className="font-mono text-sky-400 font-semibold bg-sky-500/10 px-2 py-0.5 rounded-full border border-sky-500/20">
              &lt; 15s avg
            </span>
          </div>
          <div className="mt-4 pt-2 border-t border-white/[0.05]">
            <svg className="w-full h-8 stroke-sky-400 fill-sky-500/10" viewBox="0 0 100 20">
              <path d="M0 12 Q 20 18, 50 6 T 100 3 L 100 20 L 0 20 Z" />
              <path d="M0 12 Q 20 18, 50 6 T 100 3" fill="none" strokeWidth="2" />
            </svg>
          </div>
        </div>

        {/* Card 4: Supplier Ecosystem Health */}
        <div className="relative overflow-hidden p-6 rounded-2xl glass-panel glass-panel-hover border border-white/[0.08] group">
          <div className="absolute top-0 right-0 left-0 h-1 bg-gradient-to-r from-purple-500 via-fuchsia-400 to-transparent"></div>
          <div className="flex items-center justify-between text-slate-400 mb-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">جاهزية الموردين</span>
            <div className="w-8 h-8 rounded-xl bg-purple-500/10 border border-purple-500/20 flex items-center justify-center text-purple-400">
              <Server className="w-4 h-4" />
            </div>
          </div>
          <div className="text-3xl font-black font-mono tracking-tight text-white">
            {metrics.kpis.active_suppliers}
          </div>
          <div className="flex items-center justify-between mt-3 text-xs">
            <span className="text-slate-400">قواطع الحماية الذكية (Circuit Breakers)</span>
            <span className="font-mono text-purple-400 font-semibold bg-purple-500/10 px-2 py-0.5 rounded-full border border-purple-500/20">
              99.8% UPTIME
            </span>
          </div>
          <div className="mt-4 pt-2 border-t border-white/[0.05]">
            <svg className="w-full h-8 stroke-purple-400 fill-purple-500/10" viewBox="0 0 100 20">
              <path d="M0 8 Q 30 5, 60 7 T 100 2 L 100 20 L 0 20 Z" />
              <path d="M0 8 Q 30 5, 60 7 T 100 2" fill="none" strokeWidth="2" />
            </svg>
          </div>
        </div>

      </div>

      {/* Middle Interactive Grid: Financial Analytics Canvas + Hot Wallet Radar */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Financial Flow Breakdown (2 cols) */}
        <div className="lg:col-span-2 p-6 rounded-2xl glass-panel border border-white/[0.08] flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-4 mb-5">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <Activity className="w-4 h-4 text-emerald-400" />
                  <span>تحليل كفاءة رأس المال وصافي الهوامش</span>
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  توزيع السيولة النقدية: ما يدفعه العميل vs تكلفة المورد بالجملة vs ما يتبقى في محفظتك.
                </p>
              </div>
              <span className="text-xs font-mono font-medium text-slate-400 bg-white/[0.04] px-2.5 py-1 rounded-lg">
                ZERO-CAPITAL RATIO: 100%
              </span>
            </div>

            {/* Stylized Flow Bar Visualizer */}
            <div className="space-y-4 my-4">
              <div>
                <div className="flex justify-between text-xs font-medium mb-1.5">
                  <span className="text-slate-300">مقبوضات العملاء (100% Inflow)</span>
                  <span className="font-mono text-white font-bold">${metrics.kpis.revenue_today.toFixed(2)}</span>
                </div>
                <div className="w-full h-3 rounded-full bg-white/[0.05] overflow-hidden flex">
                  <div className="h-full bg-gradient-to-r from-blue-500 to-indigo-500 w-full rounded-full"></div>
                </div>
              </div>

              <div>
                <div className="flex justify-between text-xs font-medium mb-1.5">
                  <span className="text-slate-400">المحول للموردين لحظياً (JIT Outflow)</span>
                  <span className="font-mono text-slate-300">
                    ${(metrics.kpis.revenue_today - metrics.kpis.profit_today).toFixed(2)}
                  </span>
                </div>
                <div className="w-full h-3 rounded-full bg-white/[0.05] overflow-hidden flex">
                  <div 
                    className="h-full bg-gradient-to-r from-slate-600 to-slate-500 rounded-full"
                    style={{ 
                      width: `${metrics.kpis.revenue_today > 0 ? ((metrics.kpis.revenue_today - metrics.kpis.profit_today) / metrics.kpis.revenue_today) * 100 : 75}%` 
                    }}
                  ></div>
                </div>
              </div>

              <div>
                <div className="flex justify-between text-xs font-medium mb-1.5">
                  <span className="text-emerald-400 font-bold">الأرباح الصافية المحتجزة (Your Retained Margin)</span>
                  <span className="font-mono text-emerald-400 font-bold">+${metrics.kpis.profit_today.toFixed(2)}</span>
                </div>
                <div className="w-full h-3 rounded-full bg-white/[0.05] overflow-hidden flex">
                  <div 
                    className="h-full bg-gradient-to-r from-emerald-500 to-teal-400 rounded-full glow-emerald"
                    style={{ 
                      width: `${metrics.kpis.revenue_today > 0 ? (metrics.kpis.profit_today / metrics.kpis.revenue_today) * 100 : 25}%` 
                    }}
                  ></div>
                </div>
              </div>
            </div>
          </div>

          <div className="pt-4 border-t border-white/[0.06] flex items-center justify-between text-xs text-slate-400">
            <span>🛡️ جميع التحويلات مؤمنة بـ Atomic Nonce ومفاتيح سرية معزولة محلياً.</span>
            <span className="font-mono text-emerald-400">Zero Idle Money</span>
          </div>
        </div>

        {/* Hot Wallet Telemetry Box (1 col) */}
        <div className="p-6 rounded-2xl glass-panel border border-white/[0.08] flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-4 mb-4">
              <h3 className="text-base font-bold text-white flex items-center gap-2">
                <Wallet className="w-4 h-4 text-blue-400" />
                <span>رادار محفظة الإطلاق JIT</span>
              </h3>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">
                HOT WALLET
              </span>
            </div>

            <div className="space-y-4">
              <div className="p-4 rounded-xl bg-obsidian-950/60 border border-white/[0.06]">
                <div className="text-xs text-slate-400">رصيد USDT الجاهز للتمويل الفوري</div>
                <div className="text-2xl font-black font-mono text-white mt-1">
                  ${metrics.hot_wallet.usdt_balance.toFixed(2)}{" "}
                  <span className="text-xs text-blue-400 font-sans">USDT</span>
                </div>
                <div className="text-[11px] text-slate-500 mt-1 font-mono">
                  شبكة BNB Smart Chain (BEP-20)
                </div>
              </div>

              <div className="p-4 rounded-xl bg-obsidian-950/60 border border-white/[0.06]">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-slate-400">احتياطي الغاز (Gas Float)</span>
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                    metrics.hot_wallet.is_gas_healthy
                      ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                      : "bg-rose-500/20 text-rose-300 border border-rose-500/30"
                  }`}>
                    {metrics.hot_wallet.is_gas_healthy ? "غاز صحي" : "تنبيه غاز منخفض"}
                  </span>
                </div>
                <div className="text-xl font-black font-mono text-amber-300 mt-1">
                  {metrics.hot_wallet.bnb_balance.toFixed(4)}{" "}
                  <span className="text-xs text-amber-400 font-sans">BNB</span>
                </div>
                <div className="text-[11px] text-slate-500 mt-1">
                  يكفي لتنفيذ ~{Math.floor(metrics.hot_wallet.bnb_balance / 0.0002)} تحويلة JIT تلقائية
                </div>
              </div>
            </div>
          </div>

          <div className="text-[11px] text-slate-500 border-t border-white/[0.06] pt-3 flex items-center justify-between">
            <span>الرسوم التقديرية للعملية:</span>
            <span className="font-mono text-slate-300">~$0.02 USD / tx</span>
          </div>
        </div>

      </div>

      {/* Real-Time JIT Execution Pipeline Terminal Stream */}
      <div className="rounded-2xl glass-panel border border-white/[0.08] overflow-hidden">
        {/* Terminal Header */}
        <div className="px-6 py-4 border-b border-white/[0.06] bg-obsidian-950/60 flex items-center justify-between">
          <div className="flex items-center space-x-2 space-x-reverse">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
            <h3 className="font-bold text-sm text-white flex items-center gap-2">
              <span>خط الأنابيب التنفيذي للطلبات الحية (JIT Execution Pipeline)</span>
              <span className="text-[10px] font-mono font-normal text-slate-400 px-2 py-0.5 rounded-full bg-white/[0.04]">
                WebSockets Stream
              </span>
            </h3>
          </div>
          <span className="text-xs text-slate-400 font-mono">
            {liveOrders.length} طلبات نشطة في الأنبوب
          </span>
        </div>

        {/* Visual Pipeline Feed Table */}
        <div className="divide-y divide-white/[0.04] text-xs">
          {liveOrders.map((ord) => (
            <div 
              key={ord.id} 
              className="p-4 sm:px-6 hover:bg-white/[0.02] transition flex flex-col md:flex-row items-start md:items-center justify-between gap-4"
            >
              {/* Order Info & Product */}
              <div className="flex items-center space-x-3 space-x-reverse min-w-[220px]">
                <div className="w-9 h-9 rounded-xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center font-bold text-white text-xs">
                  ⚡
                </div>
                <div>
                  <div className="font-mono font-bold text-white text-sm flex items-center gap-2">
                    <span>{ord.order_number}</span>
                    <span className="text-[10px] font-sans font-medium px-2 py-0.5 rounded-md bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">
                      {ord.supplier}
                    </span>
                  </div>
                  <div className="text-slate-400 text-xs mt-0.5 font-medium">{ord.product}</div>
                </div>
              </div>

              {/* Progress Pipeline Multi-Step Bar */}
              <div className="flex-1 max-w-md w-full px-2">
                <div className="p-2 rounded-xl bg-obsidian-950/70 border border-white/[0.05]">
                  {getStepVisual(ord.step, ord.state)}
                </div>
              </div>

              {/* Financial Outcome */}
              <div className="text-left md:text-right font-mono">
                <div className="font-bold text-sm text-white">${ord.amount.toFixed(2)}</div>
                <div className="text-emerald-400 font-bold text-xs">
                  +${ord.profit.toFixed(2)} ربح صافي
                </div>
              </div>

              {/* Timestamp & Status Badge */}
              <div className="flex items-center space-x-3 space-x-reverse min-w-[130px] justify-end">
                <div className="text-left">
                  <span className={`px-2.5 py-1 rounded-lg text-[10px] font-mono font-bold uppercase tracking-wider ${
                    ord.state === "completed"
                      ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                      : ord.state.includes("failed")
                      ? "bg-rose-500/10 text-rose-400 border border-rose-500/20"
                      : "bg-blue-500/10 text-blue-400 border border-blue-500/20"
                  }`}>
                    {ord.state.replace(/_/g, " ")}
                  </span>
                  <div className="text-[10px] text-slate-500 mt-1 font-mono text-left">{ord.time}</div>
                </div>
              </div>
            </div>
          ))}

          {liveOrders.length === 0 && (
            <div className="p-12 text-center text-slate-500 font-medium">
              بانتظار استقبال طلبات جديدة عبر البلوكشين أو شام كاش...
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
