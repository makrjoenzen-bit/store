"use client";

import React, { useState, useEffect } from "react";
import { 
  Server, Plus, RefreshCw, CheckCircle2, AlertOctagon, 
  Zap, ShieldAlert, Activity, Wifi, Key, Link2, 
  ArrowLeft, Search, Layers, X, Check, Clock
} from "lucide-react";

export default function SuppliersPage() {
  const [suppliers, setSuppliers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<any>(null);

  // Add Supplier Modal — zero JSON: Name + URL + Key + Adapter
  const [showAddModal, setShowAddModal] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoverBanner, setDiscoverBanner] = useState<any>(null);
  const DRIVER_PRESETS: Record<string, { label: string; base_url: string; hint?: string }> = {
    tiger_v1: { label: "Tiger Store", base_url: "https://bot-production-36e9d811.up.railway.app/api/v1" },
    ventebot_v1: { label: "VenteBot", base_url: "https://web-production-1c7b1.up.railway.app/api/reseller" },
    larastock_v1: { label: "Lara Stock", base_url: "", hint: "الصق conn_... أو URL+Key" },
    teslatools_v1: { label: "Tesla Tools", base_url: "https://bunnytoolsbot.tech/api/v1" },
    hangout_v1: { label: "Hangout", base_url: "https://hangoutapi.tg-tools.shop/api/v1" },
    generic_rest: { label: "Generic REST", base_url: "https://api.example.com/v1" },
  };

  const [newSupplier, setNewSupplier] = useState({
    name: "",
    driver_type: "ventebot_v1",
    payment_network: "usdt_bep20",
    priority_weight: 100,
    rate_limit_rpm: 60,
    min_deposit: 0.0713,
    deposit_window_seconds: 300,
    deposit_address: "",
    binance_id: "",
    notes: "",
    config: {
      base_url: "https://web-production-1c7b1.up.railway.app/api/reseller",
      api_key: "",
      timeout_seconds: 15,
    },
  });

  // Catalog Sync Drawer state
  const [activeCatalogSupplier, setActiveCatalogSupplier] = useState<any>(null);
  const [catalogItems, setCatalogItems] = useState<any[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogSearch, setCatalogSearch] = useState("");
  // Smart Auto-Fill Import Modal state
  const [showImportModal, setShowImportModal] = useState(false);
  const [importItem, setImportItem] = useState<any>(null);
  const [importForm, setImportForm] = useState({
    title_ar: "",
    title_en: "",
    description_ar: "",
    custom_emoji: "✨",
    custom_button_label_ar: "🛒 شراء الآن",
    pricing_mode: "fixed" as "fixed" | "percentage" | "floor",
    pricing_value: 5.0,
    map_to_product_id: "",
    is_published_to_channel: true,
    photo_url: "",
    warranty_days: 30,
    link_existing: false,
  });
  const [importLoading, setImportLoading] = useState(false);
  const [masterProducts, setMasterProducts] = useState<any[]>([]);
  const [selectedCatalogIds, setSelectedCatalogIds] = useState<string[]>([]);
  const [uploadingImage, setUploadingImage] = useState(false);

  const fetchSuppliers = async () => {
    try {
      const res = await fetch("/api/suppliers");
      if (res.ok) {
        const data = await res.json();
        setSuppliers(data);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSuppliers();
  }, []);

  const handleTestConnection = async (id: string) => {
    setTestingId(id);
    setTestResult(null);
    try {
      const res = await fetch(`/api/suppliers/${id}/test-connection`, { method: "POST" });
      const data = await res.json();
      setTestResult({ id, ...data });
      fetchSuppliers();
    } catch (e) {
      console.error(e);
    } finally {
      setTestingId(null);
    }
  };

  const handleResetCircuitBreaker = async (id: string) => {
    try {
      await fetch(`/api/suppliers/${id}/reset-circuit-breaker`, { method: "PUT" });
      fetchSuppliers();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDiscoverAndOnboard = async (e: React.FormEvent) => {
    e.preventDefault();
    const key = newSupplier.config.api_key.trim();
    const isConn = key.startsWith("conn_");
    if (!newSupplier.name.trim() || !key) {
      alert("أدخل الاسم و API Key (أو conn_ للارا ستوك)");
      return;
    }
    if (!isConn && !newSupplier.config.base_url.trim()) {
      alert("أدخل Base URL أو الصق كود conn_");
      return;
    }
    setDiscovering(true);
    setDiscoverBanner(null);
    try {
      const res = await fetch("/api/suppliers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newSupplier),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(`فشل الحفظ: ${err.detail || res.status}`);
        return;
      }
      const created = await res.json();
      const testRes = await fetch(`/api/suppliers/${created.id}/test-connection`, { method: "POST" });
      const testData = await testRes.json();
      setShowAddModal(false);
      await fetchSuppliers();
      setDiscoverBanner({
        supplier: created,
        ...testData,
      });
      await handleOpenCatalogDrawer(created);
    } catch (err) {
      console.error(err);
      alert("تعذر الاتصال بالمورد");
    } finally {
      setDiscovering(false);
    }
  };

  const applyAdapterPreset = (driver: string) => {
    const preset = DRIVER_PRESETS[driver] || DRIVER_PRESETS.generic_rest;
    setNewSupplier((prev) => ({
      ...prev,
      name: prev.name || preset.label,
      driver_type: driver,
      config: { ...prev.config, base_url: preset.base_url },
    }));
  };

  const handleOpenCatalogDrawer = async (supplier: any) => {
    setActiveCatalogSupplier(supplier);
    setCatalogLoading(true);
    setCatalogItems([]);
    try {
      const res = await fetch(`/api/suppliers/${supplier.id}/catalog-for-import`);
      if (res.ok) {
        const data = await res.json();
        setCatalogItems(Array.isArray(data) ? data : []);
      } else {
        // Fallback to basic catalog
        const fbRes = await fetch(`/api/products/supplier-catalog/${supplier.id}`);
        if (fbRes.ok) {
          const fbData = await fbRes.json();
          setCatalogItems(Array.isArray(fbData) ? fbData : []);
        }
      }
    } catch (e) {
      console.error(e);
    } finally {
      setCatalogLoading(false);
    }
  };

  const handleOpenImportModal = async (item: any) => {
    setImportItem(item);
    setImportForm({
      title_ar: item.name || item.raw_name || "",
      title_en: item.raw_name || item.name || "",
      description_ar: `تسليم آلي وفوري للمنتج ${item.name}. متاح للاستخدام الفوري.`,
      custom_emoji: "✨",
      custom_button_label_ar: "🛒 شراء الآن",
      pricing_mode: "fixed",
      pricing_value: 5.0,
      map_to_product_id: "",
      is_published_to_channel: true,
      photo_url: "",
      warranty_days: 30,
      link_existing: false,
    });
    try {
      const res = await fetch("/api/products");
      if (res.ok) {
        const data = await res.json();
        setMasterProducts(Array.isArray(data) ? data : []);
      }
    } catch (_) {
      /* ignore */
    }
    setShowImportModal(true);
  };

  const previewRetail = () => {
    const cost = Number(importItem?.cost || 0);
    const v = Number(importForm.pricing_value) || 0;
    if (importForm.pricing_mode === "percentage") return cost * (1 + v / 100);
    if (importForm.pricing_mode === "floor") return v;
    return cost + v;
  };

  const handleExecuteImport = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importItem || !activeCatalogSupplier) return;
    setImportLoading(true);
    try {
      const payload: any = {
        supplier_id: activeCatalogSupplier.id,
        supplier_product_id: importItem.supplier_product_id,
        title_ar: importForm.title_ar,
        title_en: importForm.title_en,
        description_ar: importForm.description_ar,
        custom_emoji: importForm.custom_emoji,
        custom_button_label_ar: importForm.custom_button_label_ar,
        is_published_to_channel: importForm.is_published_to_channel,
        pricing_mode: importForm.pricing_mode,
        pricing_value: importForm.pricing_value,
        photo_url: importForm.photo_url || undefined,
        warranty_days: importForm.warranty_days,
        requires_customer_email: !!importItem.requires_customer_email,
      };
      if (importForm.link_existing && importForm.map_to_product_id.trim()) {
        payload.map_to_product_id = importForm.map_to_product_id.trim();
      }
      const res = await fetch("/api/products/import-from-supplier", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setCatalogItems((prev) =>
          prev.map((it) =>
            it.supplier_product_id === importItem.supplier_product_id
              ? { ...it, is_already_mapped: true }
              : it
          )
        );
        setShowImportModal(false);
        fetchSuppliers();
      } else {
        const err = await res.json();
        alert(`فشل الاستيراد: ${err.detail || "خطأ غير معروف"}`);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setImportLoading(false);
    }
  };

  const filteredCatalog = catalogItems.filter(
    (item) =>
      item.name.toLowerCase().includes(catalogSearch.toLowerCase()) ||
      item.supplier_product_id.includes(catalogSearch)
  );

  return (
    <div className="space-y-8 pb-12">
      {/* Page Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
            <span>رادار الموردين والشبكات</span>
            <span className="text-xs font-mono font-normal text-purple-400 px-2 py-0.5 rounded-full bg-purple-500/10 border border-purple-500/20">
              API RADAR
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            إدارة موصلات REST API الخارجية، مراقبة زمن الاستجابة (Latency)، وحماية النظام بقواطع الأعطال.
          </p>
        </div>

        <button 
          onClick={() => setShowAddModal(true)}
          className="flex items-center space-x-2 space-x-reverse px-4 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 text-white font-semibold text-xs shadow-lg shadow-indigo-600/20 transition"
        >
          <Plus className="w-4 h-4" />
          <span>إضافة مورد API جديد</span>
        </button>
      </div>

      {/* Discovery / Ping banners */}
      {(discoverBanner || testResult) && (
        <div className={`p-4 rounded-2xl border ${
          (discoverBanner || testResult).is_alive
            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
            : "border-rose-500/30 bg-rose-500/10 text-rose-200"
        } flex items-center justify-between text-xs gap-3 flex-wrap`}>
          <div className="flex items-center space-x-3 space-x-reverse">
            <span className="text-base">{(discoverBanner || testResult).is_alive ? "🟢 متصل" : "🔴 فشل"}</span>
            <div>
              <span className="font-bold">
                {discoverBanner ? "فحص واكتشاف المنتجات:" : "نتيجة فحص الاتصال:"}
              </span>{" "}
              <span className="font-mono">
                {(discoverBanner || testResult).latency_ms}ms
                {(discoverBanner || testResult).balance_usd != null
                  ? ` · الرصيد $${Number((discoverBanner || testResult).balance_usd).toFixed(2)}`
                  : ""}
                {discoverBanner ? ` · منتجات: ${catalogItems.length || "…"}` : ""}
              </span>
            </div>
          </div>
          {(discoverBanner || testResult).error_message && (
            <span className="font-mono text-rose-400 bg-rose-500/20 px-2 py-1 rounded">
              {(discoverBanner || testResult).error_message}
            </span>
          )}
        </div>
      )}

      {/* Rich Supplier Telemetry Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {suppliers.map((s) => {
          const isAlive = s.status === "active";
          const isCircuitOpen = s.status === "circuit_open";

          return (
            <div 
              key={s.id}
              className="p-6 rounded-2xl glass-panel glass-panel-hover border border-white/[0.08] flex flex-col justify-between space-y-6 relative overflow-hidden group"
            >
              {/* Top Card Status Line */}
              <div>
                <div className="flex items-center justify-between border-b border-white/[0.06] pb-4 mb-4">
                  <div className="flex items-center space-x-3 space-x-reverse">
                    <div className="w-10 h-10 rounded-xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center text-white font-bold text-sm">
                      <Server className="w-5 h-5 text-indigo-400" />
                    </div>
                    <div>
                      <h3 className="font-bold text-base text-white">{s.name}</h3>
                      <div className="text-[11px] font-mono text-slate-400">{s.driver_type}</div>
                    </div>
                  </div>

                  {/* Status Badge */}
                  <div>
                    {isAlive ? (
                      <span className="flex items-center space-x-1 space-x-reverse text-[11px] font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 rounded-full">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                        <span>نشط وجاهز</span>
                      </span>
                    ) : isCircuitOpen ? (
                      <span className="flex items-center space-x-1 space-x-reverse text-[11px] font-bold text-rose-400 bg-rose-500/10 border border-rose-500/20 px-2.5 py-1 rounded-full animate-pulse">
                        <AlertOctagon className="w-3.5 h-3.5" />
                        <span>القاطع مفتوح (معطل)</span>
                      </span>
                    ) : (
                      <span className="text-[11px] font-medium text-slate-400 bg-slate-800 px-2.5 py-1 rounded-full">
                        {s.status}
                      </span>
                    )}
                  </div>
                </div>

                {/* Telemetry Metrics */}
                {(s.quality_penalty_score > 0 || s.warranty_claim_rate_30d != null) && (
                  <div className="mb-3 p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/25 text-[11px] text-amber-100 space-y-0.5">
                    <div className="font-bold">🏆 موثوقية المورد</div>
                    <div>أولوية فعّالة: {s.effective_priority ?? (s.priority_weight - (s.quality_penalty_score || 0))} · عقوبة: {s.quality_penalty_score || 0}</div>
                    <div>أعطال ضمان 30ي: {s.warranty_claim_rate_30d != null ? `${Number(s.warranty_claim_rate_30d).toFixed(1)}%` : "—"} · فشل API: {s.api_failure_rate_30d != null ? `${Number(s.api_failure_rate_30d).toFixed(1)}%` : "—"}</div>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-3 text-xs mb-4">
                  <div className="p-3 rounded-xl bg-obsidian-950/70 border border-white/[0.05]">
                    <div className="text-[10px] text-slate-400">شبكة التمويل JIT:</div>
                    <div className="font-mono font-bold text-white uppercase mt-0.5">{s.payment_network}</div>
                  </div>

                  <div className="p-3 rounded-xl bg-obsidian-950/70 border border-white/[0.05]">
                    <div className="text-[10px] text-slate-400">الحد الأدنى للإيداع:</div>
                    <div className="font-mono font-bold text-amber-300 mt-0.5">${s.min_deposit.toFixed(4)}</div>
                  </div>

                  <div className="p-3 rounded-xl bg-obsidian-950/70 border border-white/[0.05]">
                    <div className="text-[10px] text-slate-400">نافذة الإيداع (Timer):</div>
                    <div className="font-mono text-slate-300 mt-0.5">
                      {s.deposit_window_seconds ? `${s.deposit_window_seconds} ثانية` : "دائم"}
                    </div>
                  </div>

                  <div className="p-3 rounded-xl bg-obsidian-950/70 border border-white/[0.05]">
                    <div className="text-[10px] text-slate-400">الرصيد المكتشف:</div>
                    <div className="font-mono font-bold text-emerald-400 mt-0.5">
                      {s.cached_balance !== null ? `$${s.cached_balance.toFixed(2)}` : "—"}
                    </div>
                  </div>
                </div>

                {/* Priority Weight Bar */}
                <div className="p-2.5 rounded-xl bg-white/[0.02] border border-white/[0.05] text-[11px] flex items-center justify-between">
                  <span className="text-slate-400">أولوية التوجيه (Priority Weight):</span>
                  <span className="font-mono font-bold text-indigo-300">{s.priority_weight} pts</span>
                </div>
              </div>

              {/* Action Buttons Bar */}
              <div className="pt-4 border-t border-white/[0.06] flex items-center justify-between gap-2">
                <button
                  onClick={() => handleTestConnection(s.id)}
                  disabled={testingId === s.id}
                  className="flex-1 flex items-center justify-center space-x-1.5 space-x-reverse py-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-slate-200 text-xs font-semibold border border-white/[0.08] transition"
                >
                  <Wifi className="w-3.5 h-3.5 text-blue-400" />
                  <span>{testingId === s.id ? "جاري الفحص..." : "فحص Ping"}</span>
                </button>

                <button
                  onClick={() => handleOpenCatalogDrawer(s)}
                  className="flex-1 flex items-center justify-center space-x-1.5 space-x-reverse py-2 rounded-xl bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 text-xs font-semibold border border-indigo-500/30 transition"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  <span>🔍 جلب المنتجات والأسعار</span>
                </button>

                {isCircuitOpen && (
                  <button
                    onClick={() => handleResetCircuitBreaker(s.id)}
                    className="px-3 py-2 rounded-xl bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 text-xs font-bold border border-rose-500/30 transition"
                    title="إعادة تنشيط القاطع"
                  >
                    تنشيط
                  </button>
                )}
              </div>
            </div>
          );
        })}

        {suppliers.length === 0 && !loading && (
          <div className="col-span-full p-12 text-center rounded-2xl glass-panel border border-white/[0.08] text-slate-500">
            لم يتم إضافة أي موردين حتى الآن. اضغط على "إضافة مورد API جديد" للربط الفوري.
          </div>
        )}
      </div>

      {/* Slide-Over Drawer: Interactive Catalog Browser & 1-Click Linker */}
      {activeCatalogSupplier && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm transition-opacity">
          <div className="w-full max-w-xl h-full bg-obsidian-950 border-r border-white/[0.08] shadow-2xl flex flex-col justify-between overflow-hidden animate-in slide-in-from-left duration-300">
            
            {/* Drawer Header */}
            <div className="p-6 border-b border-white/[0.08] bg-obsidian-900/80 flex items-center justify-between">
              <div>
                <h3 className="text-lg font-bold text-white flex items-center gap-2">
                  <span>كتالوج منتجات {activeCatalogSupplier.name}</span>
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  تصفح المنتجات الحية المسحوبة من API المورد مباشرة مع الأسعار الحالية.
                </p>
              </div>
              <button 
                onClick={() => setActiveCatalogSupplier(null)}
                className="w-8 h-8 rounded-lg bg-white/[0.05] hover:bg-white/[0.1] text-slate-400 hover:text-white flex items-center justify-center transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Search Bar */}
            <div className="p-4 border-b border-white/[0.06] bg-obsidian-900/40">
              <div className="relative">
                <Search className="w-4 h-4 absolute right-3.5 top-3 text-slate-500" />
                <input
                  type="text"
                  placeholder="ابحث باسم المنتج أو المعرف..."
                  value={catalogSearch}
                  onChange={(e) => setCatalogSearch(e.target.value)}
                  className="w-full pl-4 pr-10 py-2 rounded-xl bg-obsidian-950 border border-white/[0.08] text-xs text-white focus:outline-none focus:border-indigo-500 transition"
                />
              </div>
            </div>

            {/* Products List Body */}
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {catalogLoading ? (
                <div className="p-12 text-center text-xs text-slate-500">
                  جاري جلب أحدث كتالوج عبر API المورد...
                </div>
              ) : filteredCatalog.length > 0 ? (
                filteredCatalog.map((item) => (
                  <div
                    key={item.supplier_product_id}
                    className="p-4 rounded-xl bg-white/[0.02] border border-white/[0.06] hover:border-white/[0.12] transition flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="font-semibold text-xs text-white flex items-center gap-2 flex-wrap">
                        <span className="truncate">{item.name}</span>
                        <span className="font-mono text-[10px] text-slate-500">
                          {item.supplier_product_id}
                        </span>
                      </div>
                      <div className="flex items-center gap-3 mt-1.5 text-[11px] flex-wrap">
                        <span className="font-mono text-emerald-400 font-bold">
                          ${Number(item.cost || 0).toFixed(2)}
                        </span>
                        <span className={`font-mono ${item.is_in_stock ? "text-slate-300" : "text-rose-400"}`}>
                          مخزون:{" "}
                          {item.available_quantity != null
                            ? item.available_quantity
                            : item.is_in_stock
                              ? "متاح"
                              : "نفد"}
                        </span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded border ${
                          item.delivery_type === "manual"
                            ? "border-amber-500/30 text-amber-300 bg-amber-500/10"
                            : "border-emerald-500/30 text-emerald-300 bg-emerald-500/10"
                        }`}>
                          {item.delivery_type === "manual" ? "🕒 يدوي" : "⚡ فوري"}
                        </span>
                        {item.is_already_mapped && (
                          <span className="text-[10px] text-indigo-300">✅ مربوط</span>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center shrink-0">
                      {item.is_already_mapped ? (
                        <button
                          onClick={() => handleOpenImportModal(item)}
                          className="text-[11px] font-bold px-3 py-1.5 rounded-xl bg-indigo-600/20 text-indigo-200 border border-indigo-500/30"
                        >
                          ربط مورد إضافي
                        </button>
                      ) : (
                        <button
                          onClick={() => handleOpenImportModal(item)}
                          className="text-[11px] font-bold px-3 py-1.5 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-500 text-white shadow-md flex items-center gap-1"
                        >
                          <Plus className="w-3.5 h-3.5" />
                          <span>استيراد</span>
                        </button>
                      )}
                    </div>
                  </div>
                ))
              ) : (
                <div className="p-12 text-center text-xs text-slate-500">
                  لم يتم العثور على أي منتجات مطابقة.
                </div>
              )}
            </div>

            {/* Drawer Footer */}
            <div className="p-4 border-t border-white/[0.08] bg-obsidian-900/60 flex justify-between items-center text-xs">
              <span className="text-slate-400 font-mono">إجمالي العناصر: {catalogItems.length}</span>
              <button
                onClick={() => setActiveCatalogSupplier(null)}
                className="px-4 py-2 rounded-xl bg-white/[0.06] hover:bg-white/[0.1] text-white font-medium"
              >
                إغلاق
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal: Effortless Supplier Onboarding */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-md">
          <div className="w-full max-w-lg bg-obsidian-950 border border-white/[0.1] rounded-3xl shadow-2xl overflow-hidden">
            <div className="p-6 border-b border-white/[0.08] flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-white">إضافة مورد API — بدون JSON</h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  الصق الاسم و Base URL و API Key ثم اضغط فحص واكتشاف المنتجات.
                </p>
              </div>
              <button
                onClick={() => setShowAddModal(false)}
                className="w-8 h-8 rounded-lg bg-white/[0.05] text-slate-400 hover:text-white flex items-center justify-center"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleDiscoverAndOnboard} className="p-6 space-y-4 text-xs">
              <div>
                <label className="block text-slate-400 font-medium mb-1.5">اسم المورد</label>
                <input
                  type="text"
                  required
                  placeholder="مثال: VenteBot Wholesale"
                  value={newSupplier.name}
                  onChange={(e) => setNewSupplier({ ...newSupplier, name: e.target.value })}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 font-medium mb-1.5">نوع الـ Adapter (قوالب جاهزة)</label>
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(DRIVER_PRESETS).map(([key, preset]) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => applyAdapterPreset(key)}
                      className={`px-3 py-2.5 rounded-xl border text-right transition ${
                        newSupplier.driver_type === key
                          ? "border-indigo-500 bg-indigo-500/15 text-white"
                          : "border-white/[0.08] text-slate-300"
                      }`}
                    >
                      <div className="font-bold">{preset.label}</div>
                      <div className="text-[10px] text-slate-400 truncate">
                        {preset.hint || preset.base_url.replace("https://", "")}
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-slate-400 font-medium mb-1.5">Base URL</label>
                <input
                  type="url"
                  required
                  placeholder="https://api.ventebot.com/v1.2"
                  value={newSupplier.config.base_url}
                  onChange={(e) =>
                    setNewSupplier({
                      ...newSupplier,
                      config: { ...newSupplier.config, base_url: e.target.value },
                    })
                  }
                  className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 font-medium mb-1.5">
                  {newSupplier.driver_type === "larastock_v1" ? "API Key أو كود conn_" : "API Key"}
                </label>
                <input
                  type="password"
                  required
                  placeholder={newSupplier.driver_type === "larastock_v1" ? "conn_... أو API Key" : "••••••••••••••••"}
                  value={newSupplier.config.api_key}
                  onChange={(e) => {
                    const val = e.target.value;
                    setNewSupplier({
                      ...newSupplier,
                      config: { ...newSupplier.config, api_key: val },
                    });
                  }}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div className="pt-2 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="px-4 py-2.5 rounded-xl bg-white/[0.05] text-slate-300"
                >
                  إلغاء
                </button>
                <button
                  type="submit"
                  disabled={discovering}
                  className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-violet-500 text-white font-semibold shadow-lg disabled:opacity-60"
                >
                  {discovering ? "جاري الفحص..." : "🔌 فحص واكتشاف المنتجات"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
      {/* Modal: Smart Auto-Fill Import Flow */}
      {showImportModal && importItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md">
          <div className="w-full max-w-xl bg-obsidian-950 border border-white/[0.1] rounded-3xl shadow-2xl p-6 space-y-4 animate-in zoom-in-95 duration-200 text-xs">
            <div className="flex items-center justify-between border-b border-white/[0.08] pb-3">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <span>استيراد ذكي وتوليد SKU موحد</span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    AUTO-FILL IMPORT
                  </span>
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  تم استخراج البيانات آلياً من كتالوج {activeCatalogSupplier?.name}. راجع الهامش واضغط نشر.
                </p>
              </div>
              <button
                onClick={() => setShowImportModal(false)}
                className="w-8 h-8 rounded-lg bg-white/[0.05] text-slate-400 hover:text-white flex items-center justify-center"
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleExecuteImport} className="space-y-4">
              <div className="p-3.5 rounded-2xl bg-obsidian-900 border border-white/[0.06] grid grid-cols-3 gap-3 text-center">
                <div>
                  <div className="text-[10px] text-slate-400">الجملة:</div>
                  <div className="font-mono font-bold text-sm text-amber-300 mt-0.5">
                    ${Number(importItem.cost || 0).toFixed(2)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400">التسعير:</div>
                  <div className="font-mono font-bold text-sm text-indigo-400 mt-0.5">
                    {importForm.pricing_mode === "percentage"
                      ? `+${importForm.pricing_value}%`
                      : importForm.pricing_mode === "floor"
                        ? `Floor $${importForm.pricing_value}`
                        : `+$${Number(importForm.pricing_value).toFixed(2)}`}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-slate-400">سعر البيع:</div>
                  <div className="font-mono font-bold text-sm text-emerald-400 mt-0.5">
                    ${previewRetail().toFixed(2)}
                  </div>
                </div>
              </div>

              <div>
                <label className="block text-slate-400 font-medium mb-1">العنوان العربي</label>
                <input
                  type="text"
                  required
                  value={importForm.title_ar}
                  onChange={(e) => setImportForm({ ...importForm, title_ar: e.target.value })}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-400 font-medium mb-1">نوع الهامش</label>
                  <select
                    value={importForm.pricing_mode}
                    onChange={(e) =>
                      setImportForm({
                        ...importForm,
                        pricing_mode: e.target.value as "fixed" | "percentage" | "floor",
                      })
                    }
                    className="w-full px-3 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white"
                  >
                    <option value="fixed">+$ ثابت</option>
                    <option value="percentage">+% نسبة</option>
                    <option value="floor">سعر تجزئة ثابت (Floor)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-slate-400 font-medium mb-1">القيمة</label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    value={importForm.pricing_value}
                    onChange={(e) =>
                      setImportForm({ ...importForm, pricing_value: parseFloat(e.target.value) || 0 })
                    }
                    className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white font-mono"
                  />
                </div>
              </div>

              <div className="p-3 rounded-xl border border-white/[0.08] bg-obsidian-900/60 space-y-2">
                <label className="flex items-center gap-2 text-slate-200 font-medium cursor-pointer">
                  <input
                    type="checkbox"
                    checked={importForm.link_existing}
                    onChange={(e) => {
                      const on = e.target.checked;
                      setImportForm((prev) => {
                        const next = { ...prev, link_existing: on };
                        if (on && prev.map_to_product_id) {
                          const existing = masterProducts.find((p) => p.id === prev.map_to_product_id);
                          if (existing) {
                            next.title_ar = existing.title_ar || prev.title_ar;
                            next.description_ar = existing.description_ar || prev.description_ar;
                            next.photo_url = existing.photo_url || prev.photo_url;
                          }
                        }
                        return next;
                      });
                    }}
                  />
                  <span>ربط بمنتج موجود في متجري</span>
                </label>
                {importForm.link_existing && (
                  <select
                    value={importForm.map_to_product_id}
                    onChange={(e) => {
                      const id = e.target.value;
                      const existing = masterProducts.find((p) => p.id === id);
                      setImportForm((prev) => ({
                        ...prev,
                        map_to_product_id: id,
                        title_ar: existing?.title_ar || prev.title_ar,
                        description_ar: existing?.description_ar || prev.description_ar,
                        photo_url: existing?.photo_url || prev.photo_url,
                      }));
                    }}
                    className="w-full px-3 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white"
                  >
                    <option value="">— اختر منتجاً —</option>
                    {masterProducts.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.title_ar} ({p.internal_sku})
                      </option>
                    ))}
                  </select>
                )}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-400 font-medium mb-1">أيام الضمان</label>
                  <input
                    type="number"
                    min={0}
                    value={importForm.warranty_days}
                    onChange={(e) =>
                      setImportForm({ ...importForm, warranty_days: parseInt(e.target.value) || 0 })
                    }
                    className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white font-mono"
                  />
                </div>
                <div>
                  <label className="block text-slate-400 font-medium mb-1">صورة المنتج</label>
                  <input
                    type="file"
                    accept="image/*"
                    disabled={uploadingImage}
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      setUploadingImage(true);
                      try {
                        const fd = new FormData();
                        fd.append("file", file);
                        const res = await fetch("/api/products/upload-image", {
                          method: "POST",
                          body: fd,
                        });
                        if (res.ok) {
                          const data = await res.json();
                          setImportForm((prev) => ({ ...prev, photo_url: data.photo_url || data.url }));
                        } else {
                          alert("فشل رفع الصورة");
                        }
                      } finally {
                        setUploadingImage(false);
                      }
                    }}
                    className="w-full text-[11px] text-slate-300"
                  />
                  {importForm.photo_url && (
                    <div className="mt-1 text-[10px] text-emerald-400 font-mono truncate">
                      {importForm.photo_url}
                    </div>
                  )}
                </div>
              </div>

              <div>
                <label className="block text-slate-400 font-medium mb-1">أيقونة العرض</label>
                <div className="flex gap-1.5 pt-1">
                  {["✨", "🤖", "🔑", "⚡", "💎", "📦"].map((em) => (
                    <button
                      key={em}
                      type="button"
                      onClick={() => setImportForm({ ...importForm, custom_emoji: em })}
                      className={`w-8 h-8 rounded-lg border text-sm flex items-center justify-center transition ${
                        importForm.custom_emoji === em
                          ? "bg-emerald-600/30 border-emerald-500 scale-105"
                          : "bg-obsidian-900 border-white/[0.08]"
                      }`}
                    >
                      {em}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-slate-400 font-medium mb-1">نص زر الشراء في البوت</label>
                <input
                  type="text"
                  required
                  value={importForm.custom_button_label_ar}
                  onChange={(e) => setImportForm({ ...importForm, custom_button_label_ar: e.target.value })}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 font-medium mb-1">الوصف المخصص في البوت</label>
                <textarea
                  rows={2}
                  value={importForm.description_ar}
                  onChange={(e) => setImportForm({ ...importForm, description_ar: e.target.value })}
                  className="w-full px-3.5 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-emerald-500"
                />
              </div>

              <div className="flex items-center space-x-2 space-x-reverse pt-2">
                <input
                  type="checkbox"
                  id="pub_channel"
                  checked={importForm.is_published_to_channel}
                  onChange={(e) => setImportForm({ ...importForm, is_published_to_channel: e.target.checked })}
                  className="w-4 h-4 rounded text-emerald-500 bg-obsidian-900 border-white/[0.1] focus:ring-emerald-500"
                />
                <label htmlFor="pub_channel" className="text-slate-300 font-medium cursor-pointer">
                  نشر تلقائي فوري في قناة تيليجرام العامة
                </label>
              </div>

              <div className="pt-4 border-t border-white/[0.08] flex items-center justify-end space-x-3 space-x-reverse">
                <button
                  type="button"
                  onClick={() => setShowImportModal(false)}
                  className="px-4 py-2.5 rounded-xl bg-white/[0.05] hover:bg-white/[0.08] text-slate-300 font-medium"
                >
                  إلغاء
                </button>
                <button
                  type="submit"
                  disabled={importLoading}
                  className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-500 hover:from-emerald-500 hover:to-teal-400 text-white font-semibold shadow-lg shadow-emerald-600/20"
                >
                  {importLoading ? "جاري الاستيراد والربط..." : "حفظ ونشر في البوت ⚡"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
