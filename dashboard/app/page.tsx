"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import KpiCard from "../components/KpiCard";
import EmptyState from "../components/EmptyState";
import StatusBadge from "../components/StatusBadge";

type Metrics = {
  kpis: {
    revenue_today: number;
    orders_today: number;
    active_users: number;
    pending_topups: number;
    pending_reviews: number;
    supplier_count: number;
    active_suppliers: number;
    profit_today?: number;
  };
};

type RecentOrder = {
  id: string;
  order_number: string;
  state: string | null;
  our_price: number;
  product_title: string | null;
  customer: string | null;
  created_at: string | null;
};

export default function OverviewPage() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [orders, setOrders] = useState<RecentOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const [mRes, oRes] = await Promise.all([
        fetch("/api/dashboard/metrics", { credentials: "include" }),
        fetch("/api/dashboard/recent-orders?limit=12", { credentials: "include" }),
      ]);
      if (mRes.status === 401 || oRes.status === 401) {
        window.location.href = "/login";
        return;
      }
      if (!mRes.ok) throw new Error("تعذر تحميل المؤشرات");
      setMetrics(await mRes.json());
      if (oRes.ok) {
        const rows = await oRes.json();
        setOrders(Array.isArray(rows) ? rows : []);
      } else {
        setOrders([]);
      }
      setError(null);
    } catch (e: any) {
      setError(e?.message || "خطأ في الاتصال");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${window.location.host}/ws`);
    ws.onmessage = () => load();
    return () => {
      clearInterval(t);
      ws.close();
    };
  }, []);

  const k = metrics?.kpis;

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-white">الرئيسية</h1>
        <p className="text-sm text-slate-400 mt-1">ملخص اليوم من قاعدة البيانات مباشرة</p>
      </div>

      {error && (
        <div className="panel p-4 text-sm text-rose-300 border-rose-500/30">{error}</div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <KpiCard
          label="طلبات اليوم"
          value={loading ? "—" : (k?.orders_today ?? 0)}
        />
        <KpiCard
          label="الإيرادات اليوم"
          value={loading ? "—" : `$${(k?.revenue_today ?? 0).toFixed(2)}`}
        />
        <KpiCard
          label="عملاء نشطون"
          value={loading ? "—" : (k?.active_users ?? 0)}
        />
        <KpiCard
          label="شحنات بانتظار التدقيق"
          value={loading ? "—" : (k?.pending_topups ?? k?.pending_reviews ?? 0)}
          hint="شام كاش / مراجعة يدوية"
        />
        <KpiCard
          label="الموردين"
          value={loading ? "—" : `${k?.active_suppliers ?? 0}/${k?.supplier_count ?? 0}`}
          hint="نشط / الإجمالي"
        />
      </div>

      <section className="panel overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
          <h2 className="font-bold text-white">أحدث الطلبات</h2>
          <Link href="/orders" className="text-xs text-sky-400 hover:text-sky-300">
            عرض الكل
          </Link>
        </div>
        {orders.length === 0 ? (
          <EmptyState title="لا توجد طلبات بعد" hint="ستظهر الطلبات هنا فور وصولها" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-right">
              <thead className="text-slate-500 text-xs border-b border-slate-800">
                <tr>
                  <th className="px-5 py-3 font-medium">الطلب</th>
                  <th className="px-5 py-3 font-medium">المنتج</th>
                  <th className="px-5 py-3 font-medium">العميل</th>
                  <th className="px-5 py-3 font-medium">المبلغ</th>
                  <th className="px-5 py-3 font-medium">الحالة</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.id} className="border-b border-slate-800/60 hover:bg-slate-900/40">
                    <td className="px-5 py-3 font-mono text-slate-200">{o.order_number}</td>
                    <td className="px-5 py-3 text-slate-300">{o.product_title || "—"}</td>
                    <td className="px-5 py-3 text-slate-400">{o.customer || "—"}</td>
                    <td className="px-5 py-3 tabular-nums text-slate-200">${o.our_price.toFixed(2)}</td>
                    <td className="px-5 py-3"><StatusBadge state={o.state} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
