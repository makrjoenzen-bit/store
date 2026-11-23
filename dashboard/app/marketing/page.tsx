"use client";

import React, { useState, useEffect } from "react";
import { 
  Megaphone, Ticket, Gift, Send, Plus, Trash2, 
  CheckCircle2, AlertCircle, RefreshCw, ToggleLeft, 
  ToggleRight, Users, Sparkles, Percent, DollarSign,
  Calendar, Layers, Clock, ArrowRight, ShieldCheck, Image as ImageIcon
} from "lucide-react";

interface CouponItem {
  id: string;
  code: string;
  discount_type: "percent" | "fixed";
  value: number;
  min_order_usd: number;
  max_uses: number | null;
  used_count: number;
  is_active: boolean;
  expires_at: string | null;
  created_at: string;
}

interface BonusTier {
  id: string;
  min_deposit_usd: number;
  bonus_usd: number;
  is_active: boolean;
}

interface BroadcastItem {
  id: string;
  title: string;
  message_text: string;
  image_url: string | null;
  cta_button_text: string | null;
  cta_button_url: string | null;
  target_audience: string;
  status: string;
  total_targets: number;
  sent_count: number;
  failed_count: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export default function MarketingPage() {
  const [activeTab, setActiveTab] = useState<"coupons" | "loyalty" | "broadcast" | "ads" | "giveaway" | "radar">("coupons");

  // Coupons State
  const [coupons, setCoupons] = useState<CouponItem[]>([]);
  const [newCouponCode, setNewCouponCode] = useState("");
  const [newCouponType, setNewCouponType] = useState<"percent" | "fixed">("percent");
  const [newCouponValue, setNewCouponValue] = useState<number | "">("");
  const [newCouponMinOrder, setNewCouponMinOrder] = useState<number | "">(0);
  const [newCouponMaxUses, setNewCouponMaxUses] = useState<number | "">("");
  const [creatingCoupon, setCreatingCoupon] = useState(false);

  // Loyalty & Bonus Tiers State
  const [cashbackPercent, setCashbackPercent] = useState<number>(2.0);
  const [referralPercent, setReferralPercent] = useState<number>(5.0);
  const [bonusTiers, setBonusTiers] = useState<BonusTier[]>([]);
  const [savingLoyalty, setSavingLoyalty] = useState(false);
  const [savedLoyalty, setSavedLoyalty] = useState(false);
  const [newTierMin, setNewTierMin] = useState<number | "">("");
  const [newTierBonus, setNewTierBonus] = useState<number | "">("");
  const [addingTier, setAddingTier] = useState(false);

  // Broadcast State
  const [broadcasts, setBroadcasts] = useState<BroadcastItem[]>([]);
  const [bcTitle, setBcTitle] = useState("");
  const [bcAudience, setBcAudience] = useState("all");
  const [bcMessage, setBcMessage] = useState("");
  const [bcImageUrl, setBcImageUrl] = useState("");
  const [bcCtaText, setBcCtaText] = useState("");
  const [bcCtaUrl, setBcCtaUrl] = useState("");
  const [launchingBc, setLaunchingBc] = useState(false);
  const [bcSuccess, setBcSuccess] = useState(false);

  // Promo Ad Builder
  const [adCaption, setAdCaption] = useState("");
  const [adImageUrl, setAdImageUrl] = useState("");
  const [adProductId, setAdProductId] = useState("");
  const [adProof, setAdProof] = useState(true);
  const [adMain, setAdMain] = useState(true);
  const [adBroadcast, setAdBroadcast] = useState(false);
  const [adPin, setAdPin] = useState(false);
  const [adPublishing, setAdPublishing] = useState(false);
  const [adResult, setAdResult] = useState<string | null>(null);
  const [products, setProducts] = useState<{ id: string; title_ar: string }[]>([]);
  const [adTemplate, setAdTemplate] = useState<"promo_flash" | "just_restocked" | "referral_giveaway">("promo_flash");
  const [adGenerating, setAdGenerating] = useState(false);
  const [radarRows, setRadarRows] = useState<any[]>([]);
  const [radarBusy, setRadarBusy] = useState(false);

  // Referral giveaway
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [gwTitle, setGwTitle] = useState("حملة حسابات Gemini المجانية");
  const [gwReward, setGwReward] = useState("حساب Gemini Pro مجاني");
  const [gwInvites, setGwInvites] = useState(3);
  const [gwStockText, setGwStockText] = useState("");
  const [gwClaims, setGwClaims] = useState<any[]>([]);
  const [gwBusy, setGwBusy] = useState(false);

  const fetchCoupons = async () => {
    try {
      const res = await fetch("/api/marketing/coupons");
      if (res.ok) setCoupons(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchLoyalty = async () => {
    try {
      const res = await fetch("/api/marketing/loyalty-settings");
      if (res.ok) {
        const data = await res.json();
        setCashbackPercent(data.cashback_percent);
        setReferralPercent(data.referral_commission_percent);
        setBonusTiers(data.tiers || []);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const fetchBroadcasts = async () => {
    try {
      const res = await fetch("/api/marketing/broadcasts");
      if (res.ok) setBroadcasts(await res.json());
    } catch (e) {
      console.error(e);
    }
  };

  const fetchCampaigns = async () => {
    try {
      const res = await fetch("/api/marketing/campaigns");
      if (res.ok) {
        const rows = await res.json();
        setCampaigns(rows);
        const active = rows.find((c: any) => c.is_active) || rows[0];
        if (active?.id) {
          const cr = await fetch(`/api/marketing/campaigns/${active.id}/claims`);
          if (cr.ok) setGwClaims(await cr.json());
        }
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchCoupons();
    fetchLoyalty();
    fetchBroadcasts();
    fetchCampaigns();
    fetch("/api/products")
      .then((r) => (r.ok ? r.json() : []))
      .then((rows) => setProducts(Array.isArray(rows) ? rows.map((p: any) => ({ id: p.id, title_ar: p.title_ar })) : []))
      .catch(() => {});
    const interval = setInterval(() => {
      fetchBroadcasts();
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleGenerateAdImage = async () => {
    if (!adProductId && !adCaption.trim()) {
      setAdResult("اختر منتجاً أو اكتب عنواناً أولاً");
      return;
    }
    setAdGenerating(true);
    setAdResult(null);
    try {
      const res = await fetch("/api/marketing/ads/generate-image", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          product_id: adProductId || null,
          template: adTemplate,
          title: adCaption.split("\n")[0] || undefined,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.data_url) {
        setAdImageUrl(data.data_url);
        setAdResult(`🎨 تم توليد الصورة (${data.size_bytes} بايت) — جاهزة للنشر`);
      } else {
        setAdResult(`❌ ${data.detail || "فشل التوليد"}`);
      }
    } catch {
      setAdResult("❌ فشل الاتصال بمولّد الصور");
    } finally {
      setAdGenerating(false);
    }
  };

  const fetchRadar = async () => {
    setRadarBusy(true);
    try {
      const res = await fetch("/api/marketing/radar");
      const data = await res.json().catch(() => []);
      setRadarRows(Array.isArray(data) ? data : []);
    } catch {
      setRadarRows([]);
    } finally {
      setRadarBusy(false);
    }
  };

  const handlePublishPromoAd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!adCaption.trim()) return;
    setAdPublishing(true);
    setAdResult(null);
    try {
      const res = await fetch("/api/marketing/ads/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          caption: adCaption,
          image_url: adImageUrl || null,
          product_id: adProductId || null,
          publish_proof_channel: adProof,
          publish_main_channel: adMain,
          broadcast_customers: adBroadcast,
          pin_in_channel: adPin,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const d = data.delivery || {};
        setAdResult(`✅ نُشر — Proof:${d.proof_channel || 0} Main:${d.main_channel || 0}`);
        setAdCaption("");
        setAdImageUrl("");
      } else {
        setAdResult(`❌ ${data.detail || "فشل النشر"}`);
      }
    } catch (err) {
      setAdResult("❌ فشل الاتصال");
    } finally {
      setAdPublishing(false);
    }
  };

  const handleCreateCoupon = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCouponCode || !newCouponValue) return;
    setCreatingCoupon(true);
    try {
      const res = await fetch("/api/marketing/coupons", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: newCouponCode,
          discount_type: newCouponType,
          value: Number(newCouponValue),
          min_order_usd: Number(newCouponMinOrder || 0),
          max_uses: newCouponMaxUses ? Number(newCouponMaxUses) : null,
        }),
      });
      if (res.ok) {
        setNewCouponCode("");
        setNewCouponValue("");
        setNewCouponMinOrder(0);
        setNewCouponMaxUses("");
        fetchCoupons();
      }
    } catch (e) {
      console.error(e);
    } finally {
      setCreatingCoupon(false);
    }
  };

  const handleToggleCoupon = async (id: string) => {
    try {
      const res = await fetch(`/api/marketing/coupons/${id}/toggle`, { method: "PUT" });
      if (res.ok) fetchCoupons();
    } catch (e) {
      console.error(e);
    }
  };

  const handleDeleteCoupon = async (id: string) => {
    if (!confirm("هل أنت متأكد من حذف هذا الكوبون نهائياً؟")) return;
    try {
      const res = await fetch(`/api/marketing/coupons/${id}`, { method: "DELETE" });
      if (res.ok) fetchCoupons();
    } catch (e) {
      console.error(e);
    }
  };

  const handleSaveLoyalty = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingLoyalty(true);
    setSavedLoyalty(false);
    try {
      const res = await fetch("/api/marketing/loyalty-settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cashback_percent: cashbackPercent,
          referral_commission_percent: referralPercent,
        }),
      });
      if (res.ok) {
        setSavedLoyalty(true);
        setTimeout(() => setSavedLoyalty(false), 3500);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setSavingLoyalty(false);
    }
  };

  const handleAddBonusTier = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTierMin || !newTierBonus) return;
    setAddingTier(true);
    try {
      const res = await fetch("/api/marketing/topup-bonus-tiers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          min_deposit_usd: Number(newTierMin),
          bonus_usd: Number(newTierBonus),
        }),
      });
      if (res.ok) {
        setNewTierMin("");
        setNewTierBonus("");
        fetchLoyalty();
      }
    } catch (e) {
      console.error(e);
    } finally {
      setAddingTier(false);
    }
  };

  const handleDeleteTier = async (id: string) => {
    try {
      const res = await fetch(`/api/marketing/topup-bonus-tiers/${id}`, { method: "DELETE" });
      if (res.ok) fetchLoyalty();
    } catch (e) {
      console.error(e);
    }
  };

  const handleLaunchBroadcast = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!bcTitle || !bcMessage) return;
    setLaunchingBc(true);
    setBcSuccess(false);
    try {
      const res = await fetch("/api/marketing/broadcasts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: bcTitle,
          message_text: bcMessage,
          target_audience: bcAudience,
          image_url: bcImageUrl || null,
          cta_button_text: bcCtaText || null,
          cta_button_url: bcCtaUrl || null,
        }),
      });
      if (res.ok) {
        setBcSuccess(true);
        setBcTitle("");
        setBcMessage("");
        setBcImageUrl("");
        setBcCtaText("");
        setBcCtaUrl("");
        fetchBroadcasts();
        setTimeout(() => setBcSuccess(false), 5000);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLaunchingBc(false);
    }
  };

  return (
    <div className="space-y-8 pb-12 font-sans">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
            <span>مركز التسويق والبث الجماعي</span>
            <span className="text-xs font-mono font-normal text-pink-400 px-2 py-0.5 rounded-full bg-pink-500/10 border border-pink-500/20">
              GROWTH & MARKETING
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            إدارة كوبونات الخصم، نسب الكاش باك وعمولات الإحالة، بونص الشحن، وإرسال الرسائل الجماعية الآمنة بدون حظر.
          </p>
        </div>

        <div className="flex items-center gap-1.5 p-1 rounded-2xl bg-white/[0.03] border border-white/[0.06] text-xs">
          <button
            onClick={() => setActiveTab("coupons")}
            className={`px-4 py-2 rounded-xl font-bold transition flex items-center gap-2 ${
              activeTab === "coupons" ? "bg-gradient-to-r from-pink-600 to-rose-600 text-white shadow-lg shadow-pink-600/20" : "text-slate-400 hover:text-white"
            }`}
          >
            <Ticket className="w-4 h-4" />
            <span>كوبونات الخصم</span>
          </button>
          <button
            onClick={() => setActiveTab("loyalty")}
            className={`px-4 py-2 rounded-xl font-bold transition flex items-center gap-2 ${
              activeTab === "loyalty" ? "bg-gradient-to-r from-pink-600 to-rose-600 text-white shadow-lg shadow-pink-600/20" : "text-slate-400 hover:text-white"
            }`}
          >
            <Gift className="w-4 h-4" />
            <span>الكاش باك والبونص</span>
          </button>
          <button
            onClick={() => setActiveTab("giveaway")}
            className={`px-4 py-2 rounded-xl font-bold transition flex items-center gap-2 ${
              activeTab === "giveaway" ? "bg-gradient-to-r from-pink-600 to-rose-600 text-white shadow-lg shadow-pink-600/20" : "text-slate-400 hover:text-white"
            }`}
          >
            <Gift className="w-4 h-4" />
            <span>حملات الدعوة</span>
          </button>
          <button
            onClick={() => setActiveTab("ads")}
            className={`px-4 py-2 rounded-xl font-bold transition flex items-center gap-2 ${
              activeTab === "ads" ? "bg-gradient-to-r from-pink-600 to-rose-600 text-white shadow-lg shadow-pink-600/20" : "text-slate-400 hover:text-white"
            }`}
          >
            <ImageIcon className="w-4 h-4" />
            <span>إنشاء إعلان</span>
          </button>
          <button
            onClick={() => setActiveTab("broadcast")}
            className={`px-4 py-2 rounded-xl font-bold transition flex items-center gap-2 ${
              activeTab === "broadcast" ? "bg-gradient-to-r from-pink-600 to-rose-600 text-white shadow-lg shadow-pink-600/20" : "text-slate-400 hover:text-white"
            }`}
          >
            <Send className="w-4 h-4" />
            <span>البث الجماعي (Broadcast)</span>
          </button>
          <button
            onClick={() => { setActiveTab("radar"); fetchRadar(); }}
            className={`px-4 py-2 rounded-xl font-bold transition flex items-center gap-2 ${
              activeTab === "radar" ? "bg-gradient-to-r from-pink-600 to-rose-600 text-white shadow-lg shadow-pink-600/20" : "text-slate-400 hover:text-white"
            }`}
          >
            <Layers className="w-4 h-4" />
            <span>📡 رادار المنافسين</span>
          </button>
        </div>
      </div>

      {/* TAB 1: COUPONS */}
      {activeTab === "coupons" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          
          {/* Create Coupon Form (5 cols) */}
          <div className="lg:col-span-5">
            <form onSubmit={handleCreateCoupon} className="p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-4">
              <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                  <Ticket className="w-4 h-4 text-pink-400" />
                  <span>إنشاء كوبون خصم جديد</span>
                </h3>
                <span className="text-[11px] font-mono text-slate-400">Promo Code</span>
              </div>

              <div className="space-y-3 text-xs">
                <div>
                  <label className="block text-slate-300 font-semibold mb-1">كود الكوبون (رمز الخصم):</label>
                  <input
                    type="text"
                    value={newCouponCode}
                    onChange={(e) => setNewCouponCode(e.target.value.toUpperCase())}
                    placeholder="مثال: VIP20 أو WELCOME"
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono uppercase tracking-wider focus:outline-none focus:border-pink-500"
                    required
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-slate-300 font-semibold mb-1">نوع الخصم:</label>
                    <select
                      value={newCouponType}
                      onChange={(e) => setNewCouponType(e.target.value as any)}
                      className="w-full px-3 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white focus:outline-none focus:border-pink-500"
                    >
                      <option value="percent">نسبة مئوية (%)</option>
                      <option value="fixed">مبلغ ثابت ($)</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-slate-300 font-semibold mb-1">قيمة الخصم:</label>
                    <input
                      type="number"
                      step="0.1"
                      min="0.1"
                      value={newCouponValue}
                      onChange={(e) => setNewCouponValue(e.target.value === "" ? "" : parseFloat(e.target.value))}
                      placeholder={newCouponType === "percent" ? "مثال: 15 (يعني 15%)" : "مثال: 5 (يعني 5$)"}
                      className="w-full px-3 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-pink-500"
                      required
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-slate-300 font-semibold mb-1">الحد الأدنى للطلب ($):</label>
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      value={newCouponMinOrder}
                      onChange={(e) => setNewCouponMinOrder(e.target.value === "" ? "" : parseFloat(e.target.value))}
                      placeholder="0.00"
                      className="w-full px-3 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-pink-500"
                    />
                  </div>
                  <div>
                    <label className="block text-slate-300 font-semibold mb-1">الحد الأقصى للاستخدام:</label>
                    <input
                      type="number"
                      min="1"
                      value={newCouponMaxUses}
                      onChange={(e) => setNewCouponMaxUses(e.target.value === "" ? "" : parseInt(e.target.value))}
                      placeholder="فارغ = غير محدود"
                      className="w-full px-3 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-pink-500"
                    />
                  </div>
                </div>
              </div>

              <button
                type="submit"
                disabled={creatingCoupon || !newCouponCode || !newCouponValue}
                className="w-full py-3 rounded-xl bg-gradient-to-r from-pink-600 to-rose-600 hover:from-pink-500 hover:to-rose-500 text-white font-bold text-xs shadow-lg shadow-pink-600/20 transition active:scale-95 disabled:opacity-50"
              >
                {creatingCoupon ? "جاري الإنشاء..." : "حفظ ونشر الكوبون فوراً"}
              </button>
            </form>
          </div>

          {/* Coupons List (7 cols) */}
          <div className="lg:col-span-7 p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-4">
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
              <h3 className="text-sm font-bold text-white">الكوبونات النشطة والسابقة ({coupons.length})</h3>
              <button onClick={fetchCoupons} className="text-slate-400 hover:text-white transition">
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            </div>

            <div className="space-y-2.5">
              {coupons.map((c) => (
                <div key={c.id} className="p-4 rounded-2xl bg-white/[0.02] border border-white/[0.06] flex items-center justify-between text-xs">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-black text-sm px-2.5 py-0.5 rounded-lg bg-pink-500/20 text-pink-300 border border-pink-500/30">
                        {c.code}
                      </span>
                      <span className="font-semibold text-emerald-400 font-mono">
                        {c.discount_type === "percent" ? `${c.value}% خصم` : `$${c.value} خصم`}
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-400">
                      الحد الأدنى: ${c.min_order_usd.toFixed(2)} • مرات الاستخدام: {c.used_count} {c.max_uses ? `/ ${c.max_uses}` : "(غير محدود)"}
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => handleToggleCoupon(c.id)}
                      className={`px-3 py-1 rounded-xl text-[11px] font-bold border transition ${
                        c.is_active 
                          ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" 
                          : "bg-slate-500/10 text-slate-400 border-slate-500/30"
                      }`}
                    >
                      {c.is_active ? "مفعل" : "معطل"}
                    </button>
                    <button
                      onClick={() => handleDeleteCoupon(c.id)}
                      className="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 transition"
                      title="حذف"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              ))}
              {coupons.length === 0 && (
                <p className="text-center text-xs text-slate-500 py-8">لم يتم إنشاء أي كوبونات خصم بعد.</p>
              )}
            </div>
          </div>

        </div>
      )}

      {/* TAB 2: LOYALTY & BONUSES */}
      {activeTab === "loyalty" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          
          {/* Cashback & Referral Form (6 cols) */}
          <div className="lg:col-span-6 p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-6">
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Gift className="w-4 h-4 text-pink-400" />
                <span>إعدادات الكاش باك وعمولات الإحالة</span>
              </h3>
              {savedLoyalty && (
                <span className="text-[11px] text-emerald-400 flex items-center gap-1 font-semibold">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>تم الحفظ والتطبيق فوراً!</span>
                </span>
              )}
            </div>

            <form onSubmit={handleSaveLoyalty} className="space-y-5 text-xs">
              <div>
                <label className="block text-slate-300 font-semibold mb-1.5">
                  نسبة الكاش باك للمشتري (%):
                </label>
                <div className="relative">
                  <input
                    type="number"
                    step="0.5"
                    min="0"
                    max="50"
                    value={cashbackPercent}
                    onChange={(e) => setCashbackPercent(parseFloat(e.target.value) || 0)}
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-pink-500"
                  />
                  <Percent className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
                </div>
                <p className="text-[11px] text-slate-500 mt-1">
                  تُعاد تلقائياً إلى محفظة المشتري داخل البوت فور اكتمال الطلب بنجاح.
                </p>
              </div>

              <div>
                <label className="block text-slate-300 font-semibold mb-1.5">
                  عمولة دعوة الأصدقاء والإحالة (%):
                </label>
                <div className="relative">
                  <input
                    type="number"
                    step="0.5"
                    min="0"
                    max="50"
                    value={referralPercent}
                    onChange={(e) => setReferralPercent(parseFloat(e.target.value) || 0)}
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-pink-500"
                  />
                  <Percent className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
                </div>
                <p className="text-[11px] text-slate-500 mt-1">
                  تُضاف لمحفظة الشخص الذي قام بدعوة العميل عند كل عملية شراء مكتملة مع إشعار تلغرام لحظي.
                </p>
              </div>

              <div className="pt-2 flex justify-end">
                <button
                  type="submit"
                  disabled={savingLoyalty}
                  className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-pink-600 to-rose-600 hover:from-pink-500 hover:to-rose-500 text-white font-bold text-xs shadow transition active:scale-95 disabled:opacity-50"
                >
                  {savingLoyalty ? "جاري الحفظ..." : "حفظ الإعدادات"}
                </button>
              </div>
            </form>
          </div>

          {/* Top-Up Bonus Tiers (6 cols) */}
          <div className="lg:col-span-6 p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-6">
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-amber-400" />
                <span>حوافز وبونص شحن الرصيد (Top-Up Bonuses)</span>
              </h3>
              <span className="text-[11px] font-mono text-slate-400">Auto-Credit</span>
            </div>

            {/* Add Tier Form */}
            <form onSubmit={handleAddBonusTier} className="p-4 rounded-2xl bg-white/[0.02] border border-white/[0.06] flex items-end gap-3 text-xs">
              <div className="flex-1">
                <label className="block text-slate-300 font-semibold mb-1 text-[11px]">عند شحن مبلغ أكبر من ($):</label>
                <input
                  type="number"
                  step="1"
                  min="1"
                  value={newTierMin}
                  onChange={(e) => setNewTierMin(e.target.value === "" ? "" : parseFloat(e.target.value))}
                  placeholder="مثال: 50"
                  className="w-full px-3 py-2 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-amber-500"
                  required
                />
              </div>
              <div className="flex-1">
                <label className="block text-slate-300 font-semibold mb-1 text-[11px]">احصل على بونص إضافي ($):</label>
                <input
                  type="number"
                  step="0.5"
                  min="0.5"
                  value={newTierBonus}
                  onChange={(e) => setNewTierBonus(e.target.value === "" ? "" : parseFloat(e.target.value))}
                  placeholder="مثال: 5.0"
                  className="w-full px-3 py-2 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-amber-500"
                  required
                />
              </div>
              <button
                type="submit"
                disabled={addingTier || !newTierMin || !newTierBonus}
                className="px-4 py-2 rounded-xl bg-amber-600 hover:bg-amber-500 text-white font-bold text-xs transition active:scale-95 disabled:opacity-50"
              >
                إضافة
              </button>
            </form>

            {/* Existing Tiers List */}
            <div className="space-y-2 text-xs">
              {bonusTiers.map((t) => (
                <div key={t.id} className="p-3.5 rounded-xl bg-white/[0.02] border border-white/[0.05] flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400 font-mono font-bold text-xs">
                      +${t.bonus_usd}
                    </div>
                    <div>
                      <div className="font-bold text-white">إيداع ${t.min_deposit_usd.toFixed(2)} أو أكثر</div>
                      <div className="text-[11px] text-slate-400">يُضاف تلقائياً للمحفظة فور تأكيد الدفعة</div>
                    </div>
                  </div>
                  <button
                    onClick={() => handleDeleteTier(t.id)}
                    className="p-1.5 rounded-lg text-slate-500 hover:text-rose-400 transition"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ))}
              {bonusTiers.length === 0 && (
                <p className="text-center text-xs text-slate-500 py-4">لم يتم تحديد أي شرائح بونص بعد.</p>
              )}
            </div>
          </div>

        </div>
      )}

      {/* TAB 3: SAFE BROADCAST CENTER */}
      {activeTab === "giveaway" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="space-y-4 p-6 rounded-2xl glass-panel border border-white/[0.08]">
            <h2 className="text-lg font-bold text-white">🎁 حملة الدعوة المجانية</h2>
            {campaigns[0] && (
              <div className="text-sm text-slate-300 space-y-1 p-3 rounded-xl bg-white/[0.03] border border-white/[0.06]">
                <div className="font-bold text-white">{campaigns.find((c) => c.is_active)?.title || campaigns[0].title}</div>
                <div>الجائزة: {campaigns.find((c) => c.is_active)?.reward_name || campaigns[0].reward_name}</div>
                <div>المطلوب: {campaigns.find((c) => c.is_active)?.required_invites || campaigns[0].required_invites} دعوات</div>
                <div>
                  المخزون: متاح {(campaigns.find((c) => c.is_active) || campaigns[0]).stock?.available ?? 0}
                  {" / "}
                  مُستلم {(campaigns.find((c) => c.is_active) || campaigns[0]).stock?.claimed ?? 0}
                </div>
                <div>{(campaigns.find((c) => c.is_active) || campaigns[0]).is_active ? "🟢 نشطة" : "⏸ متوقفة"}</div>
              </div>
            )}
            <div className="space-y-2">
              <input className="w-full px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white" value={gwTitle} onChange={(e) => setGwTitle(e.target.value)} placeholder="عنوان الحملة" />
              <input className="w-full px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white" value={gwReward} onChange={(e) => setGwReward(e.target.value)} placeholder="اسم الجائزة" />
              <input type="number" min={1} className="w-full px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white" value={gwInvites} onChange={(e) => setGwInvites(Number(e.target.value))} placeholder="عدد الدعوات المطلوبة" />
              <button
                type="button"
                disabled={gwBusy}
                onClick={async () => {
                  setGwBusy(true);
                  try {
                    const active = campaigns.find((c) => c.is_active) || campaigns[0];
                    if (active) {
                      await fetch(`/api/marketing/campaigns/${active.id}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ title: gwTitle, reward_name: gwReward, required_invites: gwInvites }),
                      });
                    } else {
                      await fetch("/api/marketing/campaigns", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ title: gwTitle, reward_name: gwReward, required_invites: gwInvites }),
                      });
                    }
                    await fetchCampaigns();
                  } finally {
                    setGwBusy(false);
                  }
                }}
                className="w-full py-2.5 rounded-xl bg-pink-600 text-white text-sm font-bold"
              >
                {campaigns.length ? "💾 حفظ التعديلات" : "➕ إنشاء حملة"}
              </button>
              {campaigns[0] && (
                <button
                  type="button"
                  className="w-full py-2.5 rounded-xl bg-white/[0.06] text-slate-200 text-sm font-bold"
                  onClick={async () => {
                    const active = campaigns.find((c) => c.is_active) || campaigns[0];
                    await fetch(`/api/marketing/campaigns/${active.id}`, {
                      method: "PUT",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ is_active: !active.is_active }),
                    });
                    await fetchCampaigns();
                  }}
                >
                  {(campaigns.find((c) => c.is_active) || campaigns[0]).is_active ? "⏸ إيقاف الحملة" : "▶️ تفعيل الحملة"}
                </button>
              )}
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">رفع مخزون الجوائز (سطر لكل حساب)</label>
              <textarea
                rows={6}
                value={gwStockText}
                onChange={(e) => setGwStockText(e.target.value)}
                className="w-full px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white font-mono"
                placeholder={"user:pass1\nuser:pass2\nKEY-XXXX"}
              />
              <button
                type="button"
                disabled={gwBusy || !gwStockText.trim() || !campaigns.length}
                onClick={async () => {
                  const active = campaigns.find((c) => c.is_active) || campaigns[0];
                  if (!active) return;
                  setGwBusy(true);
                  try {
                    const payloads = gwStockText.split("\n").map((l) => l.trim()).filter(Boolean);
                    await fetch(`/api/marketing/campaigns/${active.id}/stock`, {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ payloads }),
                    });
                    setGwStockText("");
                    await fetchCampaigns();
                  } finally {
                    setGwBusy(false);
                  }
                }}
                className="mt-2 w-full py-2.5 rounded-xl bg-emerald-600 text-white text-sm font-bold disabled:opacity-50"
              >
                📥 رفع للحوض المشفر
              </button>
            </div>
          </div>
          <div className="p-6 rounded-2xl glass-panel border border-white/[0.08] space-y-3">
            <h3 className="text-sm font-bold text-white">من استلم الجوائز ({gwClaims.length})</h3>
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {gwClaims.map((cl) => (
                <div key={cl.item_id} className="text-xs text-slate-300 p-2 rounded-lg bg-white/[0.03] border border-white/[0.05]">
                  <div>👤 {cl.first_name || "—"} @{cl.username || "—"} · ID {cl.telegram_id}</div>
                  <div className="text-slate-500">{cl.claimed_at}</div>
                </div>
              ))}
              {gwClaims.length === 0 && <p className="text-xs text-slate-500">لا يوجد مستلمون بعد.</p>}
            </div>
          </div>
        </div>
      )}

      {activeTab === "radar" && (
        <div className="p-6 rounded-2xl glass-panel border border-white/[0.08] space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-bold text-white">📡 رادار المنافسين</h2>
            <button
              type="button"
              onClick={fetchRadar}
              disabled={radarBusy}
              className="px-3 py-1.5 rounded-xl bg-white/[0.06] text-xs text-slate-200"
            >
              {radarBusy ? "…" : "تحديث"}
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs text-right">
              <thead className="text-slate-400 border-b border-white/[0.06]">
                <tr>
                  <th className="py-2 px-2">المنتج</th>
                  <th className="py-2 px-2">سعرنا</th>
                  <th className="py-2 px-2">أقل سعر منافس</th>
                  <th className="py-2 px-2">الفرق</th>
                  <th className="py-2 px-2">المنافس</th>
                </tr>
              </thead>
              <tbody>
                {radarRows.map((r) => (
                  <tr key={r.product_id} className="border-b border-white/[0.04] text-slate-200">
                    <td className="py-2 px-2 font-medium">{r.title_ar}</td>
                    <td className="py-2 px-2 font-mono">${Number(r.our_price ?? 0).toFixed(2)}</td>
                    <td className="py-2 px-2 font-mono text-amber-300">${Number(r.competitor_price ?? 0).toFixed(2)}</td>
                    <td className={`py-2 px-2 font-mono ${(r.diff ?? 0) > 0 ? "text-rose-300" : "text-emerald-300"}`}>
                      {r.diff == null ? "—" : `$${Number(r.diff).toFixed(2)}`}
                    </td>
                    <td className="py-2 px-2">{r.competitor || "—"}</td>
                  </tr>
                ))}
                {radarRows.length === 0 && (
                  <tr><td colSpan={5} className="py-6 text-center text-slate-500">لا توجد لقطات أسعار بعد — أضف مصدراً وشغّل المسح.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === "ads" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <form onSubmit={handlePublishPromoAd} className="space-y-4 p-6 rounded-2xl glass-panel border border-white/[0.08]">
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Megaphone className="w-5 h-5 text-pink-400" />
              إنشاء إعلان مرئي (Promo Ad Builder)
            </h2>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">رابط صورة الإعلان (اختياري)</label>
              <input
                value={adImageUrl}
                onChange={(e) => setAdImageUrl(e.target.value)}
                placeholder="https://…/promo.jpg"
                className="w-full px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white"
              />
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">نص الإعلان / Caption</label>
              <textarea
                value={adCaption}
                onChange={(e) => setAdCaption(e.target.value)}
                rows={8}
                placeholder={"🤖 CHATGPT PLUS — 1 MONTH!\n✨ …\n🛡 25 Days Warranty\n💎 ONLY $7!\n#ChatGPT"}
                className="w-full px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white font-mono"
                required
              />
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">منتج مستهدف (اختياري — زر الشراء عبر البوت)</label>
              <select
                value={adProductId}
                onChange={(e) => setAdProductId(e.target.value)}
                className="w-full px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white"
              >
                <option value="">— المتجر العام —</option>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>{p.title_ar}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-400 mb-1 block">قالب الصورة التلقائية</label>
              <select
                value={adTemplate}
                onChange={(e) => setAdTemplate(e.target.value as typeof adTemplate)}
                className="w-full px-3 py-2 rounded-xl bg-white/[0.04] border border-white/[0.08] text-sm text-white"
              >
                <option value="promo_flash">promo_flash — نيون داكن</option>
                <option value="just_restocked">just_restocked — إعادة تخزين</option>
                <option value="referral_giveaway">referral_giveaway — حملة دعوة</option>
              </select>
            </div>
            <button
              type="button"
              onClick={handleGenerateAdImage}
              disabled={adGenerating}
              className="w-full py-2.5 rounded-xl bg-indigo-600/80 hover:bg-indigo-500 text-white text-sm font-bold disabled:opacity-50"
            >
              {adGenerating ? "جاري التوليد…" : "🎨 توليد صورة إعلان تلقائياً"}
            </button>
            <div className="space-y-2 text-sm text-slate-300">
              <label className="flex items-center gap-2"><input type="checkbox" checked={adProof} onChange={(e) => setAdProof(e.target.checked)} /> نشر في قناة المصداقية والتحديثات</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={adMain} onChange={(e) => setAdMain(e.target.checked)} /> نشر في القناة الرئيسية</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={adBroadcast} onChange={(e) => setAdBroadcast(e.target.checked)} /> بث لجميع مستخدمي بوت الزبائن (~25 ر/ث)</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={adPin} onChange={(e) => setAdPin(e.target.checked)} /> 📌 تثبيت المنشور في القناة</label>
            </div>
            <button
              type="submit"
              disabled={adPublishing || !adCaption.trim()}
              className="w-full py-3 rounded-xl bg-gradient-to-r from-pink-600 to-rose-500 text-white font-bold text-sm disabled:opacity-50"
            >
              {adPublishing ? "جاري النشر…" : "🚀 نشر الإعلان"}
            </button>
            {adResult && <p className="text-xs text-slate-300">{adResult}</p>}
          </form>

          <div className="p-6 rounded-2xl bg-[#1c1c1e] border border-white/[0.08] space-y-3">
            <p className="text-xs text-slate-500 uppercase tracking-wider">معاينة نمط تيليجرام</p>
            {adImageUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={adImageUrl} alt="promo" className="w-full rounded-xl object-cover max-h-56" />
            ) : (
              <div className="h-40 rounded-xl bg-white/[0.04] flex items-center justify-center text-slate-500 text-sm">بدون صورة</div>
            )}
            <pre className="whitespace-pre-wrap text-sm text-slate-200 font-sans leading-relaxed">{adCaption || "نص الإعلان…"}</pre>
            <div className="space-y-2 pt-2">
              <div className="w-full py-2.5 rounded-xl bg-[#2c2c2e] text-center text-sm text-sky-300">🏪 فتح المتجر (الموقع / Mini App)</div>
              <div className="w-full py-2.5 rounded-xl bg-[#2c2c2e] text-center text-sm text-sky-300">🤖 الشراء عبر البوت</div>
            </div>
          </div>
        </div>
      )}

      {activeTab === "broadcast" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          
          {/* Broadcast Composer (6 cols) */}
          <div className="lg:col-span-6 p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-5">
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                <Send className="w-4 h-4 text-pink-400" />
                <span>إنشاء رسالة بث جماعي آمنة (Anti-FloodWait)</span>
              </h3>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                25 MSGS / SEC SAFE
              </span>
            </div>

            {bcSuccess && (
              <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs font-semibold flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                <span>تم إطلاق حملة البث الجماعي بنجاح وجاري إرسالها بمعدل آمن!</span>
              </div>
            )}

            <form onSubmit={handleLaunchBroadcast} className="space-y-4 text-xs">
              <div>
                <label className="block text-slate-300 font-semibold mb-1.5">عنوان الحملة (داخلي للأدمن):</label>
                <input
                  type="text"
                  value={bcTitle}
                  onChange={(e) => setBcTitle(e.target.value)}
                  placeholder="مثال: إعلان تخفيضات نهاية الأسبوع"
                  className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white focus:outline-none focus:border-pink-500 font-sans"
                  required
                />
              </div>

              <div>
                <label className="block text-slate-300 font-semibold mb-1.5">الجمهور المستهدف:</label>
                <select
                  value={bcAudience}
                  onChange={(e) => setBcAudience(e.target.value)}
                  className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white focus:outline-none focus:border-pink-500"
                >
                  <option value="all">جميع المستخدمين في البوت (All Users)</option>
                  <option value="buyers_only">العملاء المشترين فقط (Buyers Only)</option>
                  <option value="balance_holders">العملاء ذوي الرصيد في المحفظة (Balance Holders)</option>
                </select>
              </div>

              <div>
                <label className="block text-slate-300 font-semibold mb-1.5">نص الرسالة (يدعم تنسيق Markdown):</label>
                <textarea
                  rows={5}
                  value={bcMessage}
                  onChange={(e) => setBcMessage(e.target.value)}
                  placeholder="اكتب نص الإعلان هنا... يدعم التنسيقات *عريض* أو _مائل_ وقوائم نقطية."
                  className="w-full px-4 py-3 rounded-2xl bg-obsidian-950 border border-white/[0.08] text-white text-xs leading-relaxed focus:outline-none focus:border-pink-500 font-sans"
                  required
                />
              </div>

              <div>
                <label className="block text-slate-300 font-semibold mb-1.5">رابط صورة الإعلان (اختياري):</label>
                <input
                  type="url"
                  value={bcImageUrl}
                  onChange={(e) => setBcImageUrl(e.target.value)}
                  placeholder="https://example.com/banner.jpg"
                  className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-pink-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-300 font-semibold mb-1.5">نص الزر التفاعلي (اختياري):</label>
                  <input
                    type="text"
                    value={bcCtaText}
                    onChange={(e) => setBcCtaText(e.target.value)}
                    placeholder="مثال: 🛍️ تصفح العروض الآن"
                    className="w-full px-3 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white focus:outline-none focus:border-pink-500 font-sans"
                  />
                </div>
                <div>
                  <label className="block text-slate-300 font-semibold mb-1.5">رابط الزر (URL):</label>
                  <input
                    type="url"
                    value={bcCtaUrl}
                    onChange={(e) => setBcCtaUrl(e.target.value)}
                    placeholder="https://t.me/..."
                    className="w-full px-3 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-pink-500"
                  />
                </div>
              </div>

              <div className="pt-2 flex justify-end">
                <button
                  type="submit"
                  disabled={launchingBc || !bcTitle || !bcMessage}
                  className="px-8 py-3 rounded-xl bg-gradient-to-r from-pink-600 via-rose-500 to-pink-600 hover:from-pink-500 hover:to-rose-400 text-white font-bold text-xs shadow-lg shadow-pink-600/25 transition active:scale-95 disabled:opacity-50"
                >
                  {launchingBc ? "جاري الإطلاق..." : "🚀 إطلاق البث الجماعي الآمن"}
                </button>
              </div>
            </form>
          </div>

          {/* Past Broadcasts & Live Progress Monitor (6 cols) */}
          <div className="lg:col-span-6 p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-4">
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
              <h3 className="text-sm font-bold text-white">حملات البث السابقة وحالتها الحية</h3>
              <button onClick={fetchBroadcasts} className="text-slate-400 hover:text-white transition">
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            </div>

            <div className="space-y-3">
              {broadcasts.map((b) => {
                const percent = b.total_targets > 0 ? Math.round(((b.sent_count + b.failed_count) / b.total_targets) * 100) : 0;
                const isRunning = b.status === "running";
                const isDone = b.status === "completed";

                return (
                  <div key={b.id} className="p-4 rounded-2xl bg-white/[0.02] border border-white/[0.06] space-y-3 text-xs">
                    <div className="flex items-center justify-between">
                      <div className="font-bold text-white text-sm">{b.title}</div>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                        isRunning 
                          ? "bg-amber-500/10 text-amber-400 border-amber-500/30 animate-pulse" 
                          : isDone 
                          ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" 
                          : "bg-rose-500/10 text-rose-400 border-rose-500/30"
                      }`}>
                        {isRunning ? "جاري الإرسال..." : isDone ? "مكتمل" : "فشل"}
                      </span>
                    </div>

                    <p className="text-slate-400 text-[11px] line-clamp-2 leading-relaxed">
                      {b.message_text}
                    </p>

                    {/* Progress Bar */}
                    <div className="space-y-1">
                      <div className="flex justify-between text-[10px] text-slate-400 font-mono">
                        <span>المرسل: {b.sent_count} / {b.total_targets}</span>
                        <span>فشل/حظر: {b.failed_count}</span>
                        <span className="text-white font-bold">{percent}%</span>
                      </div>
                      <div className="w-full h-2 rounded-full bg-white/[0.06] overflow-hidden">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-pink-500 to-rose-500 transition-all duration-300"
                          style={{ width: `${percent}%` }}
                        />
                      </div>
                    </div>

                    <div className="text-[10px] text-slate-500 font-mono flex items-center justify-between pt-1 border-t border-white/[0.04]">
                      <span>الجمهور: {b.target_audience}</span>
                      <span>{b.started_at ? new Date(b.started_at).toLocaleString("ar-EG") : ""}</span>
                    </div>
                  </div>
                );
              })}
              {broadcasts.length === 0 && (
                <p className="text-center text-xs text-slate-500 py-8">لم يتم إطلاق أي حملات بث حتى الآن.</p>
              )}
            </div>
          </div>

        </div>
      )}
    </div>
  );
}
