"use client";

import React, { useState, useEffect } from "react";
import { 
  DollarSign, Trophy, ArrowRight, ShieldCheck, Zap, 
  Plus, Trash2, Calculator, CheckCircle2, Sliders, Sparkles 
} from "lucide-react";

export default function PricingMatrixPage() {
  const [matrix, setMatrix] = useState<any[]>([]);
  const [rules, setRules] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  // New Rule Form
  const [newRule, setNewRule] = useState({
    scope: "global",
    scope_id: null,
    rule_type: "fixed",
    value: 5.0,
    gas_buffer_enabled: true,
    gas_buffer_fixed: 0.08,
    priority: 0,
    is_active: true,
  });

  // Simulator Input state
  const [simWholesaleCost, setSimWholesaleCost] = useState(18.0);
  const [simRuleValue, setSimRuleValue] = useState(5.0);
  const [simRuleType, setSimRuleType] = useState("fixed");
  const [simGasBuffer, setSimGasBuffer] = useState(0.08);

  const fetchData = async () => {
    try {
      const [mRes, rRes] = await Promise.all([
        fetch("/api/pricing/matrix"),
        fetch("/api/pricing/rules"),
      ]);
      if (mRes.ok) setMatrix(await mRes.json());
      if (rRes.ok) setRules(await rRes.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleCreateRule = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/pricing/rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newRule),
      });
      if (res.ok) {
        fetchData();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeleteRule = async (id: string) => {
    try {
      await fetch(`/api/pricing/rules/${id}`, { method: "DELETE" });
      fetchData();
    } catch (e) {
      console.error(e);
    }
  };

  // Simulator calculations
  const calculateSimulatedPrice = () => {
    let margin = simRuleValue;
    if (simRuleType === "percentage") {
      margin = simWholesaleCost * (simRuleValue / 100);
    } else if (simRuleType === "floor") {
      const calc = simWholesaleCost + 3.0 + simGasBuffer;
      return Math.max(calc, simRuleValue);
    }
    return simWholesaleCost + margin + simGasBuffer;
  };

  return (
    <div className="space-y-8 pb-12">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
            <span>محرك التسعير ومصفوفة المراجحة</span>
            <span className="text-xs font-mono font-normal text-amber-400 px-2 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20">
              ARBITRAGE MATRIX
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            مقارنة تكاليف الجملة لجميع الموردين، حساب هامش الربح، وإضافة بدل غاز البلوكشين تلقائياً.
          </p>
        </div>

        <button
          onClick={fetchData}
          className="flex items-center space-x-2 space-x-reverse px-4 py-2.5 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-slate-200 font-semibold text-xs border border-white/[0.08] transition"
        >
          <span>تحديث التكاليف الحية</span>
        </button>
      </div>

      {/* Live Arbitrage Matrix Table */}
      <div className="rounded-2xl glass-panel border border-white/[0.08] overflow-hidden">
        <div className="p-6 border-b border-white/[0.06] bg-obsidian-950/60 flex items-center justify-between">
          <div className="flex items-center space-x-2 space-x-reverse">
            <Trophy className="w-4 h-4 text-amber-400" />
            <h3 className="font-bold text-sm text-white">
              مصفوفة المراجحة الحية واختيار المورد الفائز تلقائياً
            </h3>
          </div>
          <span className="text-xs font-mono text-slate-400">
            الصيغة: التكلفة + الهامش + الغاز = السعر النهائي
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-right text-xs text-slate-300">
            <thead className="bg-obsidian-950/40 border-b border-white/[0.06] text-[11px] uppercase text-slate-400 font-semibold tracking-wider font-mono">
              <tr>
                <th className="px-6 py-4">المنتج والـ SKU</th>
                <th className="px-6 py-4">مقارنة الموردين (المخزون والتكلفة)</th>
                <th className="px-6 py-4">المورد الفائز 🏆</th>
                <th className="px-6 py-4">تكلفة الجملة</th>
                <th className="px-6 py-4">صيغة التسعير</th>
                <th className="px-6 py-4 font-bold text-white">سعر البيع النهائي</th>
                <th className="px-6 py-4 font-bold text-emerald-400">صافي ربحك</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {matrix.map((row) => (
                <tr key={row.master_product_id} className="hover:bg-white/[0.02] transition">
                  {/* SKU & Title */}
                  <td className="px-6 py-4">
                    <div className="font-semibold text-white">{row.title_ar}</div>
                    <div className="font-mono text-[10px] text-blue-400 mt-0.5">{row.internal_sku}</div>
                  </td>

                  {/* Supplier Side-by-Side Breakdown */}
                  <td className="px-6 py-4">
                    <div className="flex flex-wrap gap-1.5">
                      {row.suppliers.map((s: any, idx: number) => {
                        const isWinner = row.winning_supplier === s.supplier_name;
                        return (
                          <div
                            key={idx}
                            className={`px-2.5 py-1 rounded-lg text-[11px] border flex items-center space-x-1.5 space-x-reverse font-mono transition ${
                              isWinner
                                ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300 font-bold"
                                : s.is_in_stock
                                ? "bg-white/[0.03] border-white/[0.06] text-slate-400"
                                : "bg-rose-500/10 border-rose-500/20 text-rose-400 line-through"
                            }`}
                          >
                            <span>{s.supplier_name}:</span>
                            <span>${s.cost !== null ? s.cost.toFixed(2) : "—"}</span>
                          </div>
                        );
                      })}
                    </div>
                  </td>

                  {/* Winner */}
                  <td className="px-6 py-4">
                    {row.winning_supplier ? (
                      <span className="flex items-center space-x-1 space-x-reverse text-xs font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 rounded-full w-fit">
                        <span>🏆</span>
                        <span>{row.winning_supplier}</span>
                      </span>
                    ) : (
                      <span className="text-xs text-rose-400 font-medium">غير متوفر</span>
                    )}
                  </td>

                  {/* Wholesale Cost */}
                  <td className="px-6 py-4 font-mono font-bold text-slate-300">
                    {row.wholesale_cost !== null ? `$${row.wholesale_cost.toFixed(2)}` : "—"}
                  </td>

                  {/* Formula Breakdown */}
                  <td className="px-6 py-4 font-mono text-[11px] text-slate-400">
                    {row.wholesale_cost !== null && row.profit_margin !== null ? (
                      <span>
                        ${row.wholesale_cost.toFixed(2)} + <span className="text-emerald-400 font-bold">${row.profit_margin.toFixed(2)}</span> + ${row.gas_buffer?.toFixed(2) || "0.08"}
                      </span>
                    ) : "—"}
                  </td>

                  {/* Final Retail Price */}
                  <td className="px-6 py-4 font-mono font-black text-white text-base">
                    {row.final_price !== null ? `$${row.final_price.toFixed(2)}` : "—"}
                  </td>

                  {/* Net Profit */}
                  <td className="px-6 py-4 font-mono font-black text-emerald-400 text-sm">
                    {row.profit_margin !== null ? `+$${row.profit_margin.toFixed(2)}` : "—"}
                  </td>
                </tr>
              ))}

              {matrix.length === 0 && !loading && (
                <tr>
                  <td colSpan={7} className="px-6 py-12 text-center text-slate-500">
                    لا توجد منتجات مسجلة في مصفوفة التسعير. قم بربط المنتجات بالموردين لتفعيل الحساب التلقائي.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Bottom Section: Visual Rule Builder & Live Interactive Price Simulator */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        
        {/* Interactive Price Simulator Card */}
        <div className="p-6 rounded-2xl glass-panel border border-white/[0.08] space-y-6">
          <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
            <h3 className="font-bold text-base text-white flex items-center gap-2">
              <Calculator className="w-4 h-4 text-emerald-400" />
              <span>محاكي التسعير اللحظي (Live Price Simulator)</span>
            </h3>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              SIMULATOR
            </span>
          </div>

          <div className="grid grid-cols-2 gap-4 text-xs">
            <div>
              <label className="block text-slate-400 font-medium mb-1">تكلفة الجملة الافتراضية ($)</label>
              <input
                type="number"
                step="0.1"
                value={simWholesaleCost}
                onChange={(e) => setSimWholesaleCost(parseFloat(e.target.value) || 0)}
                className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-400 font-medium mb-1">نوع القاعدة</label>
              <select
                value={simRuleType}
                onChange={(e) => setSimRuleType(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
              >
                <option value="fixed">هامش ثابت (Fixed +$X)</option>
                <option value="percentage">نسبة مئوية (Percentage +X%)</option>
                <option value="floor">الحد الأدنى للسعر (Floor Price)</option>
              </select>
            </div>

            <div>
              <label className="block text-slate-400 font-medium mb-1">قيمة الهامش ({simRuleType === "percentage" ? "%" : "$"})</label>
              <input
                type="number"
                step="0.1"
                value={simRuleValue}
                onChange={(e) => setSimRuleValue(parseFloat(e.target.value) || 0)}
                className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-400 font-medium mb-1">بدل غاز البلوكشين ($)</label>
              <input
                type="number"
                step="0.01"
                value={simGasBuffer}
                onChange={(e) => setSimGasBuffer(parseFloat(e.target.value) || 0)}
                className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
              />
            </div>
          </div>

          {/* Interactive Result Screen */}
          <div className="p-5 rounded-2xl bg-gradient-to-r from-emerald-500/10 via-emerald-500/5 to-transparent border border-emerald-500/20 text-xs">
            <div className="text-slate-400">السعر النهائي الذي سيظهر للعميل في بوت التلغرام:</div>
            <div className="text-3xl font-black font-mono text-emerald-400 mt-2">
              ${calculateSimulatedPrice().toFixed(2)}{" "}
              <span className="text-xs text-white font-sans font-medium">USD</span>
            </div>
            <div className="text-[11px] text-slate-400 font-mono mt-2">
              تفاصيل المحاكاة: ${simWholesaleCost.toFixed(2)} جملة + $
              {(calculateSimulatedPrice() - simWholesaleCost - simGasBuffer).toFixed(2)} ربحك + $
              {simGasBuffer.toFixed(2)} غاز
            </div>
          </div>
        </div>

        {/* Active Rules List */}
        <div className="p-6 rounded-2xl glass-panel border border-white/[0.08] space-y-4">
          <div className="flex items-center justify-between border-b border-white/[0.06] pb-4">
            <h3 className="font-bold text-base text-white flex items-center gap-2">
              <Sliders className="w-4 h-4 text-indigo-400" />
              <span>قواعد التسعير المفعلة (Rules Engine)</span>
            </h3>
            <span className="text-xs text-slate-400 font-mono">{rules.length} قواعد</span>
          </div>

          <div className="space-y-2.5 max-h-[300px] overflow-y-auto">
            {rules.map((r) => (
              <div 
                key={r.id} 
                className="p-3.5 rounded-xl bg-obsidian-950/70 border border-white/[0.06] flex items-center justify-between text-xs"
              >
                <div>
                  <div className="font-semibold text-white flex items-center gap-2">
                    <span className="uppercase text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">
                      {r.scope}
                    </span>
                    <span>
                      {r.rule_type === "fixed" ? `+$${r.value} ثابت` : r.rule_type === "percentage" ? `+${r.value}% نسبة` : `حد أدنى $${r.value}`}
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1 font-mono">
                    بدل غاز: {r.gas_buffer_enabled ? `$${r.gas_buffer_fixed || "0.08"}` : "معطل"}
                  </div>
                </div>

                <button
                  onClick={() => handleDeleteRule(r.id)}
                  className="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 hover:bg-white/[0.05] transition"
                  title="حذف القاعدة"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}

            {rules.length === 0 && (
              <div className="p-8 text-center text-xs text-slate-500">
                لا توجد قواعد مخصصة مضافة حالياً (يتم تطبيق الهامش الافتراضي +$5.00).
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
