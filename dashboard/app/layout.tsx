"use client";

import "./globals.css";
import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard, Server, Package, ShoppingCart,
  Users, Settings, LogOut, Activity,
} from "lucide-react";
import { logout } from "../lib/auth";

const NAV = [
  { href: "/", label: "الرئيسية", icon: LayoutDashboard },
  { href: "/suppliers", label: "الموردين", icon: Server },
  { href: "/products", label: "المنتجات والأسعار", icon: Package },
  { href: "/orders", label: "الطلبات", icon: ShoppingCart },
  { href: "/customers", label: "العملاء والرصيد", icon: Users },
  { href: "/bot-config", label: "إعدادات البوت والقنوات", icon: Settings },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [wsConnected, setWsConnected] = useState(false);
  const [pendingReviews, setPendingReviews] = useState(0);

  useEffect(() => {
    if (pathname?.startsWith("/miniapp") || pathname?.startsWith("/login")) return;

    const load = async () => {
      try {
        const res = await fetch("/api/dashboard/metrics", { credentials: "include" });
        if (res.ok) {
          const data = await res.json();
          setPendingReviews(Number(data?.kpis?.pending_reviews || data?.kpis?.pending_topups || 0));
        }
      } catch { /* offline */ }
    };
    load();
    const interval = setInterval(load, 15000);

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => setWsConnected(false);
    ws.onerror = () => setWsConnected(false);

    return () => {
      clearInterval(interval);
      ws.close();
    };
  }, [pathname]);

  if (pathname?.startsWith("/miniapp") || pathname?.startsWith("/login")) {
    return (
      <html lang="ar" dir="rtl">
        <head>
          <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
          {pathname?.startsWith("/miniapp") && (
            <script src="https://telegram.org/js/telegram-web-app.js" async />
          )}
        </head>
        <body className="bg-slate-950 text-white min-h-screen antialiased font-sans overflow-x-hidden">
          {children}
        </body>
      </html>
    );
  }

  return (
    <html lang="ar" dir="rtl">
      <body className="flex h-screen overflow-hidden bg-slate-950 text-slate-100 antialiased">
        <aside className="w-64 border-l border-slate-800 bg-slate-900/80 flex flex-col shrink-0">
          <div className="h-14 px-5 border-b border-slate-800 flex items-center">
            <span className="font-bold text-white tracking-tight">لوحة المتجر</span>
          </div>
          <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
            {NAV.map((item) => {
              const active = item.href === "/"
                ? pathname === "/"
                : pathname?.startsWith(item.href);
              const Icon = item.icon;
              const showBadge = item.href === "/orders" && pendingReviews > 0;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm transition ${
                    active
                      ? "bg-sky-500/15 text-sky-200 border border-sky-500/20"
                      : "text-slate-400 hover:text-white hover:bg-slate-800/60"
                  }`}
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  <span className="flex-1 font-medium">{item.label}</span>
                  {showBadge && (
                    <span className="text-[10px] font-bold bg-amber-500/20 text-amber-300 px-1.5 py-0.5 rounded-md">
                      {pendingReviews}
                    </span>
                  )}
                </Link>
              );
            })}
          </nav>
          <div className="p-3 border-t border-slate-800 space-y-2">
            <div className="flex items-center gap-2 px-2 text-[11px] text-slate-500">
              <Activity className={`w-3.5 h-3.5 ${wsConnected ? "text-emerald-400" : "text-slate-600"}`} />
              <span>{wsConnected ? "متصل" : "غير متصل"}</span>
            </div>
            <button
              type="button"
              onClick={() => logout()}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm text-slate-400 hover:text-rose-300 hover:bg-rose-500/10 transition"
            >
              <LogOut className="w-4 h-4" />
              تسجيل الخروج
            </button>
          </div>
        </aside>
        <main className="flex-1 overflow-y-auto">
          <div className="max-w-6xl mx-auto p-6 md:p-8">{children}</div>
        </main>
      </body>
    </html>
  );
}
