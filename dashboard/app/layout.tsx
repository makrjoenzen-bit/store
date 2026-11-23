"use client";

import "./globals.css";
import React, { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { 
  LayoutDashboard, Server, Package, DollarSign, 
  ShoppingCart, Banknote, Bot, Bell, ShieldCheck, 
  Activity, Fuel, Wallet, ChevronLeft, ChevronRight,
  ExternalLink, Sparkles, LifeBuoy, Users, Landmark, Megaphone
} from "lucide-react";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [pendingShamCashCount, setPendingShamCashCount] = useState(0);
  const [hotWallet, setHotWallet] = useState({
    usdt_balance: 0,
    bnb_balance: 0,
    is_gas_healthy: true,
  });

  // Fetch metrics & pending Sham Cash count
  const fetchTopBarData = async () => {
    try {
      // Metrics
      const mRes = await fetch("/api/dashboard/metrics");
      if (mRes.ok) {
        const mData = await mRes.json();
        if (mData.hot_wallet) {
          setHotWallet(mData.hot_wallet);
        }
      }
      // Sham Cash pending count
      const scRes = await fetch("/api/orders/sham-cash/pending");
      if (scRes.ok) {
        const scData = await scRes.json();
        setPendingShamCashCount(Array.isArray(scData) ? scData.length : 0);
      }
    } catch (e) {
      // Backend not reached or offline
    }
  };

  useEffect(() => {
    fetchTopBarData();
    const interval = setInterval(fetchTopBarData, 12000);

    // WebSocket health check
    const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
      ws.onopen = () => setWsConnected(true);
      ws.onclose = () => setWsConnected(false);
      ws.onerror = () => setWsConnected(false);
    } catch (e) {
      setWsConnected(false);
    }

    return () => {
      clearInterval(interval);
      if (ws) ws.close();
    };
  }, []);

  const navItems = [
    { href: "/", label: "مركز القيادة", sublabel: "Command Center", icon: LayoutDashboard, color: "text-blue-400" },
    { href: "/suppliers", label: "الموردين والشبكات", sublabel: "Suppliers & Radar", icon: Server, color: "text-emerald-400" },
    { href: "/products", label: "المنتجات وتعيين SKU", sublabel: "Master Products", icon: Package, color: "text-purple-400" },
    { href: "/pricing", label: "التسعير ومصفوفة المراجحة", sublabel: "Pricing & Arbitrage", icon: DollarSign, color: "text-amber-400" },
    { href: "/orders", label: "تدفق الطلبات الحي", sublabel: "Live Orders", icon: ShoppingCart, color: "text-sky-400" },
    { href: "/treasury", label: "الخزينة والمحاسبة", sublabel: "Treasury & Solvency", icon: Landmark, color: "text-emerald-400" },
    { href: "/customers", label: "إدارة العملاء (CRM)", sublabel: "Customers & CRM", icon: Users, color: "text-cyan-400" },
    { href: "/marketing", label: "التسويق والبث الجماعي", sublabel: "Growth & Broadcast", icon: Megaphone, color: "text-pink-400" },
    { 
      href: "/sham-cash", 
      label: "مكتب تدقيق شام كاش", 
      sublabel: "Sham Cash Queue", 
      icon: Banknote, 
      color: "text-rose-400",
      badge: pendingShamCashCount > 0 ? pendingShamCashCount : null 
    },
    { href: "/support", label: "الدعم الفني والنزاعات", sublabel: "Dispute Desk", icon: LifeBuoy, color: "text-orange-400" },
    { href: "/bot-config", label: "استوديو البوت الحي", sublabel: "Live Bot Studio", icon: Bot, color: "text-indigo-400" },
  ];

  if (pathname?.startsWith("/miniapp")) {
    return (
      <html lang="ar" dir="rtl">
        <head>
          <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
          <script src="https://telegram.org/js/telegram-web-app.js" async />
        </head>
        <body className="bg-[#060B14] text-white min-h-screen antialiased select-none font-sans overflow-x-hidden">
          {children}
        </body>
      </html>
    );
  }

  return (
    <html lang="ar" dir="rtl">
      <body className="flex h-screen overflow-hidden bg-obsidian-900 text-slate-100 antialiased selection:bg-indigo-500/30 selection:text-white">
        
        {/* Sleek Obsidian Glass Sidebar */}
        <aside 
          className={`relative border-l border-white/[0.07] bg-obsidian-950/70 backdrop-blur-2xl flex flex-col justify-between transition-all duration-300 z-30 ${
            collapsed ? "w-20" : "w-72"
          }`}
        >
          {/* Header Brand */}
          <div>
            <div className="h-16 px-5 border-b border-white/[0.07] flex items-center justify-between">
              <div className="flex items-center space-x-3 space-x-reverse">
                <div className="relative flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-600 via-indigo-500 to-emerald-400 shadow-lg shadow-indigo-500/20 text-white font-black text-sm">
                  ⚡
                  <span className="absolute -top-1 -left-1 w-2.5 h-2.5 rounded-full bg-emerald-400 animate-ping opacity-75"></span>
                </div>
                {!collapsed && (
                  <div>
                    <h1 className="font-bold text-sm tracking-wide text-white flex items-center gap-1.5">
                      <span>JIT TERMINAL</span>
                      <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded-full bg-white/[0.06] text-indigo-300 border border-white/[0.08]">
                        PRO
                      </span>
                    </h1>
                    <p className="text-[11px] text-slate-400 font-mono tracking-tighter">Zero-Capital FinTech</p>
                  </div>
                )}
              </div>

              {/* Collapse Toggle */}
              <button
                onClick={() => setCollapsed(!collapsed)}
                className="w-7 h-7 rounded-lg border border-white/[0.08] hover:bg-white/[0.06] flex items-center justify-center text-slate-400 hover:text-white transition"
                title={collapsed ? "توسيع" : "تصغير"}
              >
                {collapsed ? <ChevronLeft className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              </button>
            </div>

            {/* Navigation links */}
            <nav className="p-3 space-y-1.5 text-xs font-medium">
              {navItems.map((item) => {
                const isActive = pathname === item.href;
                const IconComponent = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    prefetch={true}
                    className={`group relative flex items-center ${
                      collapsed ? "justify-center px-0 py-3" : "justify-between px-3.5 py-2.5"
                    } rounded-xl transition-all duration-200 ${
                      isActive
                        ? "bg-gradient-to-r from-indigo-600/20 via-indigo-500/10 to-transparent text-white border border-indigo-500/30 shadow-sm"
                        : "text-slate-400 hover:text-slate-200 hover:bg-white/[0.04]"
                    }`}
                  >
                    <div className="flex items-center space-x-3 space-x-reverse">
                      <div className={`p-1.5 rounded-lg ${isActive ? "bg-indigo-500/20" : "group-hover:bg-white/[0.06]"} transition`}>
                        <IconComponent className={`w-4 h-4 ${item.color}`} />
                      </div>
                      {!collapsed && (
                        <div className="leading-tight">
                          <div className={`font-semibold text-xs ${isActive ? "text-white" : "text-slate-300"}`}>
                            {item.label}
                          </div>
                          <div className="text-[10px] text-slate-500 font-mono">
                            {item.sublabel}
                          </div>
                        </div>
                      )}
                    </div>

                    {!collapsed && item.badge !== null && item.badge !== undefined && (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold font-mono bg-rose-500/20 text-rose-400 border border-rose-500/30 animate-pulse">
                        {item.badge}
                      </span>
                    )}

                    {/* Active Indicator bar */}
                    {isActive && (
                      <span className="absolute right-0 top-1.5 bottom-1.5 w-1 rounded-l-full bg-indigo-500 shadow-md shadow-indigo-500/50"></span>
                    )}
                  </Link>
                );
              })}
            </nav>
          </div>

          {/* Footer Info */}
          <div className="p-3 border-t border-white/[0.07] bg-obsidian-950/40">
            {!collapsed ? (
              <div className="p-3 rounded-xl bg-white/[0.02] border border-white/[0.05] space-y-2">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-slate-400">حالة الربط الآني</span>
                  <span className={`flex items-center gap-1.5 font-medium ${wsConnected ? "text-emerald-400" : "text-rose-400"}`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? "bg-emerald-400 animate-ping" : "bg-rose-400"}`}></span>
                    <span className="font-mono text-[10px]">{wsConnected ? "LIVE WS" : "OFFLINE"}</span>
                  </span>
                </div>
                <div className="flex items-center justify-between text-[10px] text-slate-500 font-mono">
                  <span>FastAPI Core</span>
                  <span>v1.2.0-STABLE</span>
                </div>
              </div>
            ) : (
              <div className="flex justify-center">
                <span className={`w-2.5 h-2.5 rounded-full ${wsConnected ? "bg-emerald-400 animate-pulse" : "bg-rose-400"}`} />
              </div>
            )}
          </div>
        </aside>

        {/* Main Content Viewport */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Top Command Bar */}
          <header className="h-16 border-b border-white/[0.07] bg-obsidian-950/60 backdrop-blur-xl flex items-center justify-between px-6 z-20">
            
            {/* Left/Right System Indicators */}
            <div className="flex items-center space-x-3 space-x-reverse">
              {/* Network Pill */}
              <div className="flex items-center space-x-2 space-x-reverse px-3 py-1.5 rounded-xl bg-white/[0.03] border border-white/[0.08] text-xs">
                <div className="w-2 h-2 rounded-full bg-amber-400 animate-pulse"></div>
                <span className="font-mono font-medium text-slate-300 text-[11px]">BSC Mainnet</span>
                <span className="text-slate-600">|</span>
                <span className="font-mono text-[10px] text-slate-400">Chain #56</span>
              </div>

              {/* WebSocket Connection Badge */}
              <div className={`flex items-center space-x-1.5 space-x-reverse px-3 py-1.5 rounded-xl border text-xs font-medium ${
                wsConnected 
                  ? "bg-emerald-500/[0.07] border-emerald-500/20 text-emerald-300"
                  : "bg-rose-500/[0.07] border-rose-500/20 text-rose-300"
              }`}>
                <span className={`w-2 h-2 rounded-full ${wsConnected ? "bg-emerald-400" : "bg-rose-400"}`}></span>
                <span className="text-[11px] font-mono">{wsConnected ? "متصل لحظياً (WebSockets)" : "انقطع الاتصال"}</span>
              </div>
            </div>

            {/* Right/Left Quick Widgets */}
            <div className="flex items-center space-x-4 space-x-reverse">
              
              {/* Hot Wallet Balance Quick Pill */}
              <div className="flex items-center space-x-3 space-x-reverse px-3.5 py-1.5 rounded-xl bg-gradient-to-r from-white/[0.03] to-white/[0.01] border border-white/[0.08] shadow-inner">
                {/* USDT Balance */}
                <div className="flex items-center space-x-1.5 space-x-reverse">
                  <Wallet className="w-3.5 h-3.5 text-blue-400" />
                  <span className="text-xs text-slate-400">محفظة JIT:</span>
                  <span className="font-mono font-bold text-xs text-white">
                    ${hotWallet.usdt_balance.toFixed(2)}
                  </span>
                  <span className="text-[10px] font-mono text-blue-400">USDT</span>
                </div>

                <span className="text-slate-700">|</span>

                {/* BNB Gas Level */}
                <div className="flex items-center space-x-1.5 space-x-reverse">
                  <Fuel className={`w-3.5 h-3.5 ${hotWallet.is_gas_healthy ? "text-amber-400" : "text-rose-400 animate-bounce"}`} />
                  <span className="font-mono text-xs text-amber-300">
                    {hotWallet.bnb_balance.toFixed(3)} BNB
                  </span>
                  {!hotWallet.is_gas_healthy && (
                    <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-rose-500/20 text-rose-300 border border-rose-500/30">
                      غاز منخفض!
                    </span>
                  )}
                </div>
              </div>

              {/* Pending Sham Cash Bell Quick Alert */}
              {pendingShamCashCount > 0 && (
                <Link
                  href="/sham-cash"
                  className="flex items-center space-x-1.5 space-x-reverse px-3 py-1.5 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 border border-rose-500/30 transition text-xs font-semibold animate-pulse"
                >
                  <Bell className="w-3.5 h-3.5 text-rose-400" />
                  <span>{pendingShamCashCount} إيصالات بانتظار الاعتماد</span>
                </Link>
              )}

              {/* Admin Profile */}
              <div className="flex items-center space-x-2.5 space-x-reverse pl-2 border-r border-white/[0.08]">
                <div className="w-8 h-8 rounded-xl bg-gradient-to-tr from-indigo-600 to-purple-600 flex items-center justify-center font-bold text-xs text-white shadow-md shadow-indigo-500/20">
                  AD
                </div>
                <div className="hidden sm:block text-right">
                  <div className="text-xs font-semibold text-white leading-none">مدير النظام</div>
                  <div className="text-[10px] font-mono text-slate-500">Super Admin</div>
                </div>
              </div>
            </div>
          </header>

          {/* Page Body Viewport */}
          <main className="flex-1 overflow-y-auto p-6 lg:p-8">
            <div className="max-w-[1600px] mx-auto">
              {children}
            </div>
          </main>
        </div>
      </body>
    </html>
  );
}
