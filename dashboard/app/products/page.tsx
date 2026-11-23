"use client";

import React, { useState, useEffect } from "react";
import { 
  Package, Plus, Link2, ExternalLink, CheckCircle2, 
  XCircle, Trophy, Sparkles, Trash2, Send, Tag, Layers
} from "lucide-react";

export default function ProductsPage() {
  const [products, setProducts] = useState<any[]>([]);
  const [categories, setCategories] = useState<any[]>([]);
  const [suppliers, setSuppliers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  // Modals state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showMappingModal, setShowMappingModal] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<any>(null);

  const [newProduct, setNewProduct] = useState({
    internal_sku: "",
    title_ar: "",
    title_en: "",
    description_ar: "",
    custom_description_ar: "",
    custom_emoji: "✨",
    custom_button_label_ar: "🛒 شراء الآن",
    category_id: "",
    is_active: true,
    is_published_to_channel: true,
  });

  const [newMapping, setNewMapping] = useState({
    supplier_id: "",
    supplier_product_id: "",
    supplier_product_name: "",
    last_known_cost: 0,
    priority_override: 100,
  });

  const fetchData = async () => {
    try {
      const [pRes, cRes, sRes] = await Promise.all([
        fetch("/api/products"),
        fetch("/api/products/categories"),
        fetch("/api/suppliers"),
      ]);
      if (pRes.ok) setProducts(await pRes.json());
      if (cRes.ok) setCategories(await cRes.json());
      if (sRes.ok) setSuppliers(await sRes.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleCreateProduct = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await fetch("/api/products", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newProduct),
      });
      if (res.ok) {
        setShowCreateModal(false);
        fetchData();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleAddMapping = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedProduct) return;
    try {
      const res = await fetch(`/api/products/${selectedProduct.id}/mappings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newMapping),
      });
      if (res.ok) {
        setShowMappingModal(false);
        fetchData();
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeleteMapping = async (productId: string, linkId: string) => {
    if (!confirm("هل أنت متأكد من حذف هذا الربط؟")) return;
    try {
      await fetch(`/api/products/${productId}/mappings/${linkId}`, { method: "DELETE" });
      fetchData();
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="space-y-8 pb-12">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
            <span>الكتالوج الموحد وتعيين الـ SKU</span>
            <span className="text-xs font-mono font-normal text-indigo-400 px-2 py-0.5 rounded-full bg-indigo-500/10 border border-indigo-500/20">
              MASTER SKU LINKER
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            ربط المنتج الواحد (Master Product) بعدة موردين في وقت واحد لتحقيق المراجحة التلقائية (Arbitrage).
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <a
            href="/suppliers"
            className="flex items-center space-x-2 space-x-reverse px-4 py-2.5 rounded-xl bg-white/[0.04] border border-white/[0.1] hover:bg-white/[0.08] text-slate-200 font-semibold text-xs transition"
          >
            <Sparkles className="w-4 h-4 text-amber-400" />
            <span>🔌 استيراد من API المورد</span>
          </a>
          <button 
            onClick={() => setShowCreateModal(true)}
            className="flex items-center space-x-2 space-x-reverse px-4 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 text-white font-semibold text-xs shadow-lg shadow-indigo-600/20 transition"
          >
            <Plus className="w-4 h-4" />
            <span>إنشاء منتج موحد جديد</span>
          </button>
        </div>
      </div>

      {/* Modern Master Product Cards Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {products.map((p) => {
          // Identify cheapest linked supplier
          const sortedLinks = [...(p.supplier_links || [])].sort((a, b) => (a.last_known_cost || 0) - (b.last_known_cost || 0));
          const winnerLink = sortedLinks[0];

          return (
            <div 
              key={p.id}
              className="p-6 rounded-2xl glass-panel glass-panel-hover border border-white/[0.08] flex flex-col justify-between space-y-6 group"
            >
              <div>
                {/* Header Product Info */}
                <div className="flex items-start justify-between border-b border-white/[0.06] pb-4 mb-4">
                  <div className="flex items-start space-x-3.5 space-x-reverse">
                    <div className="w-12 h-12 rounded-2xl bg-gradient-to-tr from-white/[0.05] to-white/[0.01] border border-white/[0.1] flex items-center justify-center font-bold text-2xl text-white shadow-inner">
                      {p.custom_emoji || "📦"}
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="font-bold text-base text-white">{p.title_ar}</h3>
                        {p.is_published_to_channel && (
                          <span className="flex items-center gap-1 text-[10px] font-bold text-sky-400 bg-sky-500/10 border border-sky-500/20 px-2 py-0.5 rounded-full">
                            <Send className="w-2.5 h-2.5" />
                            <span>بالقناة</span>
                          </span>
                        )}
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                          {p.custom_button_label_ar || "🛒 شراء الآن"}
                        </span>
                      </div>
                      <div className="text-xs text-slate-400 mt-0.5 font-medium">{p.title_en}</div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="font-mono text-[10px] px-2 py-0.5 rounded bg-white/[0.04] text-blue-400 border border-white/[0.06]">
                          SKU: {p.internal_sku}
                        </span>
                        {p.category_name && (
                          <span className="text-[10px] px-2 py-0.5 rounded bg-white/[0.04] text-slate-400">
                            {p.category_name}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>

                  <span className={`text-[10px] font-bold px-2.5 py-1 rounded-full ${
                    p.is_active
                      ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                      : "bg-slate-800 text-slate-400"
                  }`}>
                    {p.is_active ? "نشط" : "معطل"}
                  </span>
                </div>

                {/* Multi-Supplier Mapping Section */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs font-semibold text-slate-300 mb-2">
                    <span className="flex items-center gap-1.5">
                      <Link2 className="w-3.5 h-3.5 text-indigo-400" />
                      <span>الموردون المرتبطون ({p.supplier_links?.length || 0}):</span>
                    </span>
                    <button
                      onClick={() => {
                        setSelectedProduct(p);
                        setShowMappingModal(true);
                      }}
                      className="text-indigo-400 hover:text-indigo-300 font-bold text-xs flex items-center gap-1"
                    >
                      <Plus className="w-3 h-3" />
                      <span>ربط مورد إضافي</span>
                    </button>
                  </div>

                  <div className="space-y-2">
                    {p.supplier_links && p.supplier_links.length > 0 ? (
                      p.supplier_links.map((link: any) => {
                        const isWinner = winnerLink && winnerLink.id === link.id;
                        return (
                          <div
                            key={link.id}
                            className={`p-3 rounded-xl border flex items-center justify-between text-xs transition ${
                              isWinner
                                ? "bg-emerald-500/[0.05] border-emerald-500/30 text-emerald-300"
                                : "bg-obsidian-950/60 border-white/[0.06] text-slate-300"
                            }`}
                          >
                            <div className="flex items-center space-x-2 space-x-reverse">
                              {isWinner ? (
                                <Trophy className="w-4 h-4 text-emerald-400 shrink-0" />
                              ) : (
                                <span className="w-2 h-2 rounded-full bg-slate-600 shrink-0" />
                              )}
                              <div>
                                <div className="font-semibold text-white flex items-center gap-2">
                                  <span>{link.supplier_name}</span>
                                  {isWinner && (
                                    <span className="text-[10px] font-bold px-1.5 py-0.2 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                                      الأرخص حالياً 🏆
                                    </span>
                                  )}
                                </div>
                                <div className="text-[11px] text-slate-400 font-mono">
                                  ID: {link.supplier_product_id} {link.supplier_product_name ? `• ${link.supplier_product_name}` : ""}
                                </div>
                              </div>
                            </div>

                            <div className="flex items-center space-x-3 space-x-reverse">
                              <span className="font-mono font-bold text-sm text-emerald-400">
                                ${link.last_known_cost?.toFixed(2) || "0.00"}
                              </span>
                              <button
                                onClick={() => handleDeleteMapping(p.id, link.id)}
                                className="p-1 rounded text-slate-500 hover:text-rose-400 transition"
                                title="إلغاء الربط"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        );
                      })
                    ) : (
                      <div className="p-4 rounded-xl bg-obsidian-950/40 border border-dashed border-white/[0.08] text-center text-xs text-amber-400">
                        لم يتم ربط هذا المنتج بأي مورد بعد! اضغط "ربط مورد إضافي" لتفعيل التوريد الفوري.
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {/* Card Footer: Quick Financial Summary */}
              <div className="pt-4 border-t border-white/[0.06] flex items-center justify-between text-xs">
                <span className="text-slate-400">أفضل سعر جملة متاح:</span>
                <span className="font-mono font-bold text-white text-sm">
                  {winnerLink ? `$${winnerLink.last_known_cost?.toFixed(2)} USD` : "—"}
                </span>
              </div>
            </div>
          );
        })}

        {products.length === 0 && !loading && (
          <div className="col-span-full p-12 text-center rounded-2xl glass-panel border border-white/[0.08] text-slate-500">
            لم يتم إنشاء أي منتجات حتى الآن.
          </div>
        )}
      </div>

      {/* Modal 1: Create Master Product with Headless CMS Display Editor */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md">
          <div className="w-full max-w-4xl bg-obsidian-950 border border-white/[0.1] rounded-3xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 text-xs">
            {/* Modal Header */}
            <div className="p-6 border-b border-white/[0.08] flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <span>محرر العرض والمنتج الموحد (Headless CMS Editor)</span>
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                    WYSIWYG
                  </span>
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  تحكم كامل في كيفية ظهور المنتج داخل بوت تيليجرام والقناة العامة، بمعزل تام عن أسماء وتنسيقات الموردين.
                </p>
              </div>
              <button 
                onClick={() => setShowCreateModal(false)}
                className="w-8 h-8 rounded-lg bg-white/[0.05] text-slate-400 hover:text-white flex items-center justify-center"
              >
                ✕
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-12 gap-0 divide-y md:divide-y-0 md:divide-x md:divide-x-reverse divide-white/[0.08]">
              {/* Form Controls Column (7 Cols) */}
              <form onSubmit={handleCreateProduct} className="p-6 md:col-span-7 space-y-4 max-h-[75vh] overflow-y-auto">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-slate-400 font-medium mb-1">رمز الـ SKU الداخلي</label>
                    <input
                      type="text"
                      required
                      placeholder="مثال: CHATGPT-PLUS-1M"
                      value={newProduct.internal_sku}
                      onChange={(e) => setNewProduct({ ...newProduct, internal_sku: e.target.value })}
                      className="w-full px-3.5 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white font-mono uppercase focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div>
                    <label className="block text-slate-400 font-medium mb-1">القسم / التصنيف</label>
                    <select
                      value={newProduct.category_id}
                      onChange={(e) => setNewProduct({ ...newProduct, category_id: e.target.value })}
                      className="w-full px-3.5 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
                    >
                      <option value="">بدون تصنيف</option>
                      {categories.map((c) => (
                        <option key={c.id} value={c.id}>{c.icon_emoji} {c.name_ar}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div>
                  <label className="block text-slate-400 font-medium mb-1">عنوان المنتج في البوت (عربي)</label>
                  <input
                    type="text"
                    required
                    placeholder="اشتراك ChatGPT Plus (شهر كامل)"
                    value={newProduct.title_ar}
                    onChange={(e) => setNewProduct({ ...newProduct, title_ar: e.target.value })}
                    className="w-full px-3.5 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 font-medium mb-1">الاسم الداخلي (إنجليزي)</label>
                  <input
                    type="text"
                    required
                    placeholder="ChatGPT Plus 1 Month"
                    value={newProduct.title_en}
                    onChange={(e) => setNewProduct({ ...newProduct, title_en: e.target.value })}
                    className="w-full px-3.5 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
                  />
                </div>

                {/* Headless Emoji Picker */}
                <div>
                  <label className="block text-slate-400 font-medium mb-1.5">أيقونة العرض (Emoji)</label>
                  <div className="flex flex-wrap gap-2">
                    {["✨", "🤖", "🔑", "⚡", "💎", "📦", "🚀", "👑", "🛡️", "🔥"].map((em) => (
                      <button
                        key={em}
                        type="button"
                        onClick={() => setNewProduct({ ...newProduct, custom_emoji: em })}
                        className={`w-9 h-9 rounded-xl border text-base flex items-center justify-center transition ${
                          newProduct.custom_emoji === em
                            ? "bg-indigo-600/30 border-indigo-500 scale-105"
                            : "bg-obsidian-900 border-white/[0.08] hover:border-white/[0.2]"
                        }`}
                      >
                        {em}
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="block text-slate-400 font-medium mb-1">نص زر الشراء في البوت (Telegram Inline Button)</label>
                  <input
                    type="text"
                    required
                    placeholder="🛒 شراء الآن (دفع فوري)"
                    value={newProduct.custom_button_label_ar}
                    onChange={(e) => setNewProduct({ ...newProduct, custom_button_label_ar: e.target.value })}
                    className="w-full px-3.5 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 font-medium mb-1">وصف المنتج المخصص (العرض في البوت)</label>
                  <textarea
                    rows={3}
                    placeholder="تسليم آلي وفوري للمفتاح مباشرة بعد الدفع..."
                    value={newProduct.custom_description_ar}
                    onChange={(e) => setNewProduct({ ...newProduct, custom_description_ar: e.target.value, description_ar: e.target.value })}
                    className="w-full px-3.5 py-2 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
                  />
                </div>

                <div className="pt-4 border-t border-white/[0.08] flex justify-end space-x-3 space-x-reverse">
                  <button
                    type="button"
                    onClick={() => setShowCreateModal(false)}
                    className="px-4 py-2 rounded-xl bg-white/[0.05] text-slate-300 font-medium"
                  >
                    إلغاء
                  </button>
                  <button
                    type="submit"
                    className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold shadow-md shadow-indigo-600/20"
                  >
                    حفظ ونشر في الكتالوج
                  </button>
                </div>
              </form>

              {/* Live Telegram Bot Preview Column (5 Cols) */}
              <div className="p-6 md:col-span-5 bg-obsidian-900/40 flex flex-col justify-center items-center">
                <div className="text-[11px] font-semibold text-slate-400 mb-3 flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                  <span>معاينة حية لبطاقة المنتج في تيليجرام</span>
                </div>

                <div className="w-full max-w-[280px] rounded-2xl bg-[#17212b] p-4 text-white shadow-xl border border-white/[0.05] space-y-3 font-sans">
                  <div className="text-sm font-bold flex items-center gap-1.5 border-b border-white/[0.1] pb-2">
                    <span className="text-lg">{newProduct.custom_emoji || "✨"}</span>
                    <span>{newProduct.title_ar || "اسم المنتج يظهر هنا"}</span>
                  </div>

                  <div className="text-[11px] text-slate-300 leading-relaxed whitespace-pre-wrap">
                    {newProduct.custom_description_ar || "هنا يظهر الوصف المخصص الذي تكتبه في لوحة التحكم..."}
                  </div>

                  <div className="pt-2 border-t border-white/[0.1] space-y-1 text-[11px]">
                    <div className="flex justify-between">
                      <span className="text-slate-400">السعر النهائي:</span>
                      <span className="font-mono font-bold text-emerald-400">$25.00</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">الحالة:</span>
                      <span className="text-emerald-400">متوفر للتسليم الفوري ✅</span>
                    </div>
                  </div>

                  {/* Mocked Inline Button */}
                  <div className="pt-2">
                    <div className="w-full py-2 rounded-xl bg-[#2b5278] text-white text-center font-bold text-xs shadow hover:bg-[#346290] transition flex items-center justify-center gap-1">
                      <span>{newProduct.custom_button_label_ar || "🛒 شراء الآن"}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Modal 2: Map Supplier Product */}
      {showMappingModal && selectedProduct && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-md">
          <div className="w-full max-w-lg bg-obsidian-950 border border-white/[0.1] rounded-3xl shadow-2xl p-6 space-y-4 animate-in zoom-in-95 duration-200 text-xs">
            <h3 className="text-base font-bold text-white border-b border-white/[0.08] pb-3">
              ربط مورد بالمنتج: {selectedProduct.title_ar}
            </h3>

            <form onSubmit={handleAddMapping} className="space-y-4">
              <div>
                <label className="block text-slate-400 font-medium mb-1">اختر المورد</label>
                <select
                  required
                  value={newMapping.supplier_id}
                  onChange={(e) => setNewMapping({ ...newMapping, supplier_id: e.target.value })}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
                >
                  <option value="">-- حدد المورد --</option>
                  {suppliers.map((s) => (
                    <option key={s.id} value={s.id}>{s.name} ({s.driver_type})</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-slate-400 font-medium mb-1">معرف المنتج لدى المورد (Supplier Product ID)</label>
                <input
                  type="text"
                  required
                  placeholder="مثال: 10 أو CHATGPT-1M"
                  value={newMapping.supplier_product_id}
                  onChange={(e) => setNewMapping({ ...newMapping, supplier_product_id: e.target.value })}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-slate-400 font-medium mb-1">تكلفة الجملة ($)</label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    placeholder="18.00"
                    value={newMapping.last_known_cost}
                    onChange={(e) => setNewMapping({ ...newMapping, last_known_cost: parseFloat(e.target.value) || 0 })}
                    className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                  />
                </div>

                <div>
                  <label className="block text-slate-400 font-medium mb-1">أولوية الترجيح</label>
                  <input
                    type="number"
                    value={newMapping.priority_override}
                    onChange={(e) => setNewMapping({ ...newMapping, priority_override: parseInt(e.target.value) || 100 })}
                    className="w-full px-3.5 py-2.5 rounded-xl bg-obsidian-900 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                  />
                </div>
              </div>

              <div className="pt-4 border-t border-white/[0.08] flex justify-end space-x-3 space-x-reverse">
                <button
                  type="button"
                  onClick={() => setShowMappingModal(false)}
                  className="px-4 py-2 rounded-xl bg-white/[0.05] text-slate-300 font-medium"
                >
                  إلغاء
                </button>
                <button
                  type="submit"
                  className="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold shadow-md shadow-emerald-600/20"
                >
                  ربط المورد
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
