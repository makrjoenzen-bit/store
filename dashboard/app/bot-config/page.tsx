"use client";

import React, { useState, useEffect } from "react";
import { 
  Bot, Save, CheckCircle2, Smartphone, Send, 
  Sparkles, Shield, ToggleLeft, ToggleRight, Radio,
  Lock, Share2, ExternalLink, Check, Bell, Award,
  Layers, AlertTriangle, ArrowRight
} from "lucide-react";

interface BotConfigState {
  welcome_message_ar: string;
  welcome_message_en: string;
  sham_cash_account_number: string;
  sham_cash_qr_url: string;
  sham_cash_recipient_name: string;
  sham_cash_instructions_ar: string;
  proof_channel_auto_post: boolean;
  sham_cash_usd_to_syp_rate: number;
  sham_cash_accept_syp: boolean;
  cashback_percent: number;
  min_order_value: number;
  payment_ingest_provider: string;
  force_subscribe_enabled: boolean;
  main_channel_id: string;
  main_channel_url: string;
  proof_channel_id: string;
  proof_channel_url: string;
  auto_post_proofs_enabled: boolean;
  maintenance_mode: boolean;
  pay_sham_cash_enabled: boolean;
  pay_usdt_enabled: boolean;
  pay_binance_enabled: boolean;
  escalation_telegram_ids: string;
  manual_delivery_sla_minutes: number;
}

export default function BotConfigPage() {
  const [configs, setConfigs] = useState<BotConfigState>({
    welcome_message_ar: "أهلاً بك في المتجر الرقمي الآلي! 🌟\nنوفر لك أفضل أسعار اشتراكات الذكاء الاصطناعي مع تسليم فوري.",
    welcome_message_en: "Welcome to the instant digital shop!",
    sham_cash_account_number: "0991234567",
    sham_cash_qr_url: "",
    sham_cash_recipient_name: "",
    sham_cash_instructions_ar: "قم بالتحويل عبر شام كاش للحساب: 0912345678\nثم أرسل صورة إشعار التحويل هنا للمراجعة الفورية.",
    proof_channel_auto_post: false,
    sham_cash_usd_to_syp_rate: 11500,
    sham_cash_accept_syp: true,
    cashback_percent: 2,
    min_order_value: 1.0,
    payment_ingest_provider: "direct_web3",
    force_subscribe_enabled: false,
    main_channel_id: "",
    main_channel_url: "",
    proof_channel_id: "",
    proof_channel_url: "",
    auto_post_proofs_enabled: true,
    maintenance_mode: false,
    pay_sham_cash_enabled: true,
    pay_usdt_enabled: true,
    pay_binance_enabled: true,
    escalation_telegram_ids: "",
    manual_delivery_sla_minutes: 60,
  });

  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [previewTab, setPreviewTab] = useState<"welcome" | "force_sub" | "proof_card" | "sham_cash">("welcome");

  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const res = await fetch("/api/bot-config");
        if (res.ok) {
          const data = await res.json();
          const truthy = (v: any, d = false) =>
            v === true || v === "true" || v === 1 || v === "1" || (v == null ? d : false);
          setConfigs({
            welcome_message_ar: data.welcome_message_ar || "أهلاً بك في المتجر الرقمي الآلي! 🌟\nنوفر لك أفضل أسعار اشتراكات الذكاء الاصطناعي مع تسليم فوري.",
            welcome_message_en: data.welcome_message_en || "Welcome to the instant digital shop!",
            sham_cash_account_number: data.sham_cash_account_number || "0991234567",
            sham_cash_qr_url: data.sham_cash_qr_url || "",
            sham_cash_recipient_name: data.sham_cash_recipient_name || "",
            sham_cash_instructions_ar: data.sham_cash_instructions_ar || "قم بالتحويل عبر شام كاش للحساب: 0912345678\nثم أرسل صورة إشعار التحويل هنا للمراجعة الفورية.",
            proof_channel_auto_post: truthy(data.proof_channel_auto_post, false),
            sham_cash_usd_to_syp_rate: Number(data.sham_cash_usd_to_syp_rate ?? 11500),
            sham_cash_accept_syp: data.sham_cash_accept_syp !== false && data.sham_cash_accept_syp !== "false",
            cashback_percent: Number(data.cashback_percent ?? 2),
            min_order_value: data.min_order_value ?? 1.0,
            payment_ingest_provider: data.payment_ingest_provider || "direct_web3",
            force_subscribe_enabled: truthy(data.force_subscribe_enabled, false),
            main_channel_id: data.main_channel_id || "",
            main_channel_url: data.main_channel_url || "",
            proof_channel_id: data.proof_channel_id || "",
            proof_channel_url: data.proof_channel_url || "",
            auto_post_proofs_enabled: data.auto_post_proofs_enabled !== false && data.auto_post_proofs_enabled !== "false",
            maintenance_mode: truthy(data.maintenance_mode, false),
            pay_sham_cash_enabled: data.pay_sham_cash_enabled !== false && data.pay_sham_cash_enabled !== "false",
            pay_usdt_enabled: data.pay_usdt_enabled !== false && data.pay_usdt_enabled !== "false",
            pay_binance_enabled: data.pay_binance_enabled !== false && data.pay_binance_enabled !== "false",
            escalation_telegram_ids: data.escalation_telegram_ids || "",
            manual_delivery_sla_minutes: Number(data.manual_delivery_sla_minutes ?? 60),
          });
        }
      } catch (e) {
        console.error("Failed to fetch bot config:", e);
      }
    };
    fetchConfig();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaved(false);
    try {
      const res = await fetch("/api/bot-config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ configs }),
      });
      if (res.ok) {
        setSaved(true);
        setTimeout(() => setSaved(false), 3500);
      }
    } catch (e) {
      console.error("Failed to save bot config:", e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-8 pb-12 font-sans">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
            <span>استوديو تخصيص البوت اللحظي</span>
            <span className="text-xs font-mono font-normal text-indigo-400 px-2 py-0.5 rounded-full bg-indigo-500/10 border border-indigo-500/20">
              LIVE BOT STUDIO
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            إدارة نصوص الترحيب، بوابات الاشتراك الإجباري، قناة المصداقية والإثباتات الحية مع محاكاة بصرية فورية لشاشة الهاتف.
          </p>
        </div>

        {saved && (
          <div className="flex items-center space-x-2 space-x-reverse px-4 py-2 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-xs font-semibold animate-in fade-in">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            <span>تم حفظ الإعدادات وبث التحديث لجميع العملاء فوراً!</span>
          </div>
        )}
      </div>

      {/* Split Screen Grid: Form (Right) vs Telegram Mockup (Left) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        
        {/* Right Column: Configuration Form (7 cols) */}
        <div className="lg:col-span-7 space-y-6">
          <form onSubmit={handleSave} className="space-y-6">
            
            {/* Section 1: Texts & Messages */}
            <div className="p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-5">
              <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                  <Bot className="w-4 h-4 text-indigo-400" />
                  <span>إعدادات النصوص والرسائل الآلية</span>
                </h3>
                <span className="text-[11px] font-mono text-slate-400">Core Messaging</span>
              </div>

              <div className="space-y-4 text-xs">
                {/* Arabic Welcome Message */}
                <div>
                  <label className="block text-slate-300 font-semibold mb-2">
                    رسالة الترحيب الرئيسية (/start) بالعربية:
                  </label>
                  <textarea
                    rows={4}
                    value={configs.welcome_message_ar}
                    onChange={(e) => setConfigs({ ...configs, welcome_message_ar: e.target.value })}
                    className="w-full px-4 py-3 rounded-2xl bg-obsidian-950 border border-white/[0.08] text-white text-xs leading-relaxed focus:outline-none focus:border-indigo-500 transition font-sans"
                    placeholder="أدخل رسالة الترحيب..."
                  />
                </div>

                <div>
                  <label className="block text-slate-300 font-semibold mb-2">
                    رقم / كود حساب شام كاش:
                  </label>
                  <input
                    type="text"
                    value={configs.sham_cash_account_number}
                    onChange={(e) => setConfigs({ ...configs, sham_cash_account_number: e.target.value })}
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                    placeholder="0991234567"
                  />
                </div>

                <div>
                  <label className="block text-slate-300 font-semibold mb-2">
                    اسم المستلم (تحت الـ QR):
                  </label>
                  <input
                    type="text"
                    value={configs.sham_cash_recipient_name}
                    onChange={(e) => setConfigs({ ...configs, sham_cash_recipient_name: e.target.value })}
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
                    placeholder="مثال: متجر الاشتراكات"
                  />
                </div>

                <div>
                  <label className="block text-slate-300 font-semibold mb-2">
                    رابط صورة QR شام كاش (`sham_cash_qr_url`):
                  </label>
                  <input
                    type="text"
                    value={configs.sham_cash_qr_url}
                    onChange={(e) => setConfigs({ ...configs, sham_cash_qr_url: e.target.value })}
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                    placeholder="/uploads/sham_cash/qr.jpg أو https://..."
                  />
                  {configs.sham_cash_qr_url ? (
                    <div className="mt-3 flex items-start gap-3">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={configs.sham_cash_qr_url}
                        alt="Sham Cash QR"
                        className="w-28 h-28 rounded-xl border border-white/10 object-contain bg-white"
                      />
                      <p className="text-[11px] text-slate-400 leading-relaxed pt-1">
                        معاينة مباشرة — تُعرض للعميل في البوت وMini App عند اختيار شام كاش.
                      </p>
                    </div>
                  ) : null}
                </div>

                {/* Sham Cash Instructions */}
                <div>
                  <label className="block text-slate-300 font-semibold mb-2">
                    تعليمات تحويل شام كاش (المحلية):
                  </label>
                  <textarea
                    rows={3}
                    value={configs.sham_cash_instructions_ar}
                    onChange={(e) => setConfigs({ ...configs, sham_cash_instructions_ar: e.target.value })}
                    className="w-full px-4 py-3 rounded-2xl bg-obsidian-950 border border-white/[0.08] text-white text-xs leading-relaxed focus:outline-none focus:border-indigo-500 transition font-sans"
                    placeholder="أدخل أرقام الحسابات وطريقة إرسال الإشعار..."
                  />
                </div>

                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 pt-1">
                  {([
                    ["pay_sham_cash_enabled", "شام كاش"],
                    ["pay_usdt_enabled", "USDT"],
                    ["pay_binance_enabled", "Binance Pay"],
                    ["maintenance_mode", "وضع الصيانة"],
                  ] as const).map(([key, label]) => (
                    <label key={key} className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer rounded-xl border border-white/[0.08] px-3 py-2">
                      <input
                        type="checkbox"
                        checked={Boolean(configs[key])}
                        onChange={(e) => setConfigs({ ...configs, [key]: e.target.checked })}
                        className="rounded border-white/20"
                      />
                      {label}
                    </label>
                  ))}
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div>
                    <label className="block text-slate-300 font-semibold mb-1.5">سعر USD → ل.س</label>
                    <input
                      type="number"
                      value={configs.sham_cash_usd_to_syp_rate}
                      onChange={(e) => setConfigs({ ...configs, sham_cash_usd_to_syp_rate: parseFloat(e.target.value) || 11500 })}
                      className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="block text-slate-300 font-semibold mb-1.5">كاش باك %</label>
                    <input
                      type="number"
                      step="0.1"
                      value={configs.cashback_percent}
                      onChange={(e) => setConfigs({ ...configs, cashback_percent: parseFloat(e.target.value) || 0 })}
                      className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div className="flex items-end pb-1">
                    <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={configs.sham_cash_accept_syp}
                        onChange={(e) => setConfigs({ ...configs, sham_cash_accept_syp: e.target.checked })}
                        className="rounded border-white/20"
                      />
                      عرض المبلغ بالليرة السورية
                    </label>
                  </div>
                </div>

                {/* Minimum Order Value & Ingest Provider */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
                  <div>
                    <label className="block text-slate-300 font-semibold mb-1.5">
                      الحد الأدنى لقيمة الطلب (USD $)
                    </label>
                    <input
                      type="number"
                      step="0.1"
                      value={configs.min_order_value}
                      onChange={(e) => setConfigs({ ...configs, min_order_value: parseFloat(e.target.value) || 1.0 })}
                      className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div>
                    <label className="block text-slate-300 font-semibold mb-1.5">
                      مزود استقبال الكريبتو
                    </label>
                    <select
                      value={configs.payment_ingest_provider}
                      onChange={(e) => setConfigs({ ...configs, payment_ingest_provider: e.target.value })}
                      className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white focus:outline-none focus:border-indigo-500"
                    >
                      <option value="direct_web3">مستمع الويب3 المباشر (0% عمولة - محفظة ذاتية)</option>
                      <option value="oxapay">بوابة OxaPay (0.4% عمولة)</option>
                      <option value="cryptomus">بوابة Cryptomus (0.4% عمولة)</option>
                    </select>
                  </div>
                </div>
              </div>
            </div>

            {/* Section 2: Phase 7 Force-Subscribe Gate */}
            <div className="p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-5">
              <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400">
                    <Lock className="w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-white">بوابة الاشتراك الإجباري للقناة</h3>
                    <p className="text-[11px] text-slate-400">Mandatory Force-Subscribe Gate</p>
                  </div>
                </div>
                
                {/* Toggle Switch */}
                <button
                  type="button"
                  onClick={() => setConfigs({ ...configs, force_subscribe_enabled: !configs.force_subscribe_enabled })}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${
                    configs.force_subscribe_enabled ? "bg-amber-500" : "bg-white/[0.1]"
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      configs.force_subscribe_enabled ? "translate-x-1" : "translate-x-6"
                    }`}
                  />
                </button>
              </div>

              <p className="text-xs text-slate-300 leading-relaxed">
                عند التفعيل، لن يتمكن أي عميل جديد أو قديم من تصفح المنتجات أو إنشاء طلبات قبل الانضمام للقناة الرسمية. يتم فحص العضوية لحظياً وكاشينج النتيجة لمدة دقيقتين.
              </p>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                <div>
                  <label className="block text-slate-300 font-semibold mb-1.5">
                    معرف القناة الرئيسية (@ أو ID سالب)
                  </label>
                  <input
                    type="text"
                    value={configs.main_channel_id}
                    onChange={(e) => setConfigs({ ...configs, main_channel_id: e.target.value })}
                    placeholder="@MyStoreChannel أو -100..."
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-amber-500 transition"
                  />
                </div>

                <div>
                  <label className="block text-slate-300 font-semibold mb-1.5">
                    رابط الدعوة للقناة (Invite URL)
                  </label>
                  <input
                    type="text"
                    value={configs.main_channel_url}
                    onChange={(e) => setConfigs({ ...configs, main_channel_url: e.target.value })}
                    placeholder="https://t.me/MyStoreChannel"
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-amber-500 transition"
                  />
                </div>
              </div>
            </div>

            {/* Manual delivery escalation */}
            <div className="p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-5">
              <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-lg bg-rose-500/10 border border-rose-500/20 flex items-center justify-center text-rose-400">
                    <Bell className="w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-white">تنبيهات التسليم اليدوي والتصعيد</h3>
                    <p className="text-[11px] text-slate-400">Multi-Admin Escalation (Telegram-only)</p>
                  </div>
                </div>
              </div>
              <p className="text-xs text-slate-300 leading-relaxed">
                أضف معرفات تيليجرام للمسؤول والمساعد (مفصولة بفاصلة). يستلمون تنبيه فوري عند الدفع، ثم تذكير عند 30 دقيقة، وتصعيد عاجل كل 10 دقائق بعد 50 دقيقة حتى التسليم.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                <div>
                  <label className="block text-slate-300 font-semibold mb-1.5">
                    escalation_telegram_ids
                  </label>
                  <input
                    type="text"
                    value={configs.escalation_telegram_ids}
                    onChange={(e) => setConfigs({ ...configs, escalation_telegram_ids: e.target.value })}
                    placeholder="111001,222003"
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-rose-500 transition"
                  />
                </div>
                <div>
                  <label className="block text-slate-300 font-semibold mb-1.5">
                    مهلة التجهيز (دقيقة)
                  </label>
                  <input
                    type="number"
                    min={5}
                    max={1440}
                    value={configs.manual_delivery_sla_minutes}
                    onChange={(e) => setConfigs({ ...configs, manual_delivery_sla_minutes: parseInt(e.target.value, 10) || 60 })}
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-rose-500 transition"
                  />
                </div>
              </div>
            </div>

            {/* Section 3: Phase 7 Live Social Proof Channel Engine */}
            <div className="p-6 rounded-3xl glass-panel border border-white/[0.08] space-y-5">
              <div className="flex items-center justify-between border-b border-white/[0.06] pb-3">
                <div className="flex items-center gap-2">
                  <div className="w-7 h-7 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center text-emerald-400">
                    <Share2 className="w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-white">محرك قناة إثباتات الشراء والمصداقية الحية</h3>
                    <p className="text-[11px] text-slate-400">Dual-Channel Social Proof Engine</p>
                  </div>
                </div>

                {/* Toggle Switch — legacy auto_post_proofs_enabled */}
                <button
                  type="button"
                  onClick={() => setConfigs({ ...configs, auto_post_proofs_enabled: !configs.auto_post_proofs_enabled })}
                  className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${
                    configs.auto_post_proofs_enabled ? "bg-emerald-500" : "bg-white/[0.1]"
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                      configs.auto_post_proofs_enabled ? "translate-x-1" : "translate-x-6"
                    }`}
                  />
                </button>
              </div>

              <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer rounded-xl border border-white/[0.08] px-3 py-2 w-fit">
                <input
                  type="checkbox"
                  checked={Boolean(configs.proof_channel_auto_post)}
                  onChange={(e) => setConfigs({ ...configs, proof_channel_auto_post: e.target.checked })}
                  className="rounded border-white/20"
                />
                نشر تلقائي في قناة التوثيق بعد قبول شام كاش / التسليم اليدوي (`proof_channel_auto_post`)
              </label>

              <p className="text-xs text-slate-300 leading-relaxed">
                نشر بطاقة إثبات فورية ومقنعة في قناة مخصصة فور اكتمال تسليم أي طلب أو شحن محفظة. يتم تشفير اسم المشتري ورقم الطلب لحماية الخصوصية، مع إرفاق زر شراء مباشر للمنتج لزيادة المبيعات.
              </p>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                <div>
                  <label className="block text-slate-300 font-semibold mb-1.5">
                    معرف قناة الإثباتات (@ أو ID سالب)
                  </label>
                  <input
                    type="text"
                    value={configs.proof_channel_id}
                    onChange={(e) => setConfigs({ ...configs, proof_channel_id: e.target.value })}
                    placeholder="@MyStoreProofs أو -100..."
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-emerald-500 transition"
                  />
                </div>

                <div>
                  <label className="block text-slate-300 font-semibold mb-1.5">
                    رابط قناة الإثباتات (Proof Channel URL)
                  </label>
                  <input
                    type="text"
                    value={configs.proof_channel_url}
                    onChange={(e) => setConfigs({ ...configs, proof_channel_url: e.target.value })}
                    placeholder="https://t.me/MyStoreProofs"
                    className="w-full px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-white font-mono focus:outline-none focus:border-emerald-500 transition"
                  />
                </div>
              </div>
            </div>

            {/* Save Button */}
            <div className="pt-2 flex justify-end">
              <button
                type="submit"
                disabled={saving}
                className="flex items-center space-x-2 space-x-reverse px-8 py-3.5 rounded-2xl bg-gradient-to-r from-indigo-600 via-indigo-500 to-indigo-600 hover:from-indigo-500 hover:to-indigo-400 text-white font-bold text-xs shadow-lg shadow-indigo-600/25 transition active:scale-95 disabled:opacity-50"
              >
                <Save className="w-4 h-4" />
                <span>{saving ? "جاري الحفظ والبث..." : "حفظ الإعدادات وبثها فوراً"}</span>
              </button>
            </div>
          </form>
        </div>

        {/* Left Column: Realistic Telegram Smartphone Chat Mockup Preview (5 cols) */}
        <div className="lg:col-span-5 flex flex-col items-center sticky top-6">
          <div className="w-full max-w-sm rounded-[42px] p-4 bg-obsidian-950 border-4 border-slate-800 shadow-2xl relative overflow-hidden">
            
            {/* Phone Notch / Dynamic Island */}
            <div className="w-28 h-4 rounded-full bg-slate-900 mx-auto mb-3 flex items-center justify-center">
              <div className="w-2.5 h-2.5 rounded-full bg-slate-950 ml-2"></div>
              <div className="w-1.5 h-1.5 rounded-full bg-slate-800"></div>
            </div>

            {/* Telegram Header */}
            <div className="px-3.5 py-2.5 rounded-2xl bg-[#17212b] border border-[#232e3c] flex items-center justify-between mb-3 text-xs">
              <div className="flex items-center space-x-2 space-x-reverse">
                <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-indigo-500 to-emerald-500 flex items-center justify-center font-bold text-white text-xs shadow">
                  ⚡
                </div>
                <div>
                  <div className="font-bold text-white leading-tight">متجر الاشتراكات الآلي</div>
                  <div className="text-[10px] text-emerald-400 flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block"></span>
                    <span>bot • متصل الآن</span>
                  </div>
                </div>
              </div>
              <span className="text-slate-500 font-mono text-[10px]">v2.4 Pro</span>
            </div>

            {/* Preview Multi-Tab Chips */}
            <div className="grid grid-cols-4 gap-1 p-1 rounded-xl bg-white/[0.03] border border-white/[0.06] mb-3 text-[10px]">
              <button
                type="button"
                onClick={() => setPreviewTab("welcome")}
                className={`py-1.5 rounded-lg font-medium transition text-center ${
                  previewTab === "welcome" ? "bg-indigo-600 text-white font-bold" : "text-slate-400 hover:text-white"
                }`}
              >
                الرئيسية
              </button>
              <button
                type="button"
                onClick={() => setPreviewTab("force_sub")}
                className={`py-1.5 rounded-lg font-medium transition text-center ${
                  previewTab === "force_sub" ? "bg-amber-600 text-white font-bold" : "text-slate-400 hover:text-white"
                }`}
              >
                الاشتراك
              </button>
              <button
                type="button"
                onClick={() => setPreviewTab("proof_card")}
                className={`py-1.5 rounded-lg font-medium transition text-center ${
                  previewTab === "proof_card" ? "bg-emerald-600 text-white font-bold" : "text-slate-400 hover:text-white"
                }`}
              >
                الإثباتات
              </button>
              <button
                type="button"
                onClick={() => setPreviewTab("sham_cash")}
                className={`py-1.5 rounded-lg font-medium transition text-center ${
                  previewTab === "sham_cash" ? "bg-sky-600 text-white font-bold" : "text-slate-400 hover:text-white"
                }`}
              >
                شام كاش
              </button>
            </div>

            {/* Telegram Chat Message Bubble (Realistic Design) */}
            <div className="min-h-[380px] p-3 rounded-2xl bg-[#0e1621] border border-[#1f2936] flex flex-col justify-between space-y-3 font-sans">
              
              {/* Message from Bot */}
              <div className="space-y-2">
                
                {/* 1. WELCOME HUB PREVIEW */}
                {previewTab === "welcome" && (
                  <>
                    <div className="p-3.5 rounded-2xl rounded-tr-none bg-[#182533] border border-[#2b3a4a] text-slate-100 text-xs leading-relaxed space-y-2.5">
                      {/* Rich User Header Card */}
                      <div className="p-2.5 rounded-xl bg-white/[0.04] border border-white/[0.08] space-y-1 text-[11px]">
                        <div className="flex items-center justify-between text-indigo-300 font-semibold">
                          <span>👤 الحساب: @w***sh</span>
                          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300">VIP</span>
                        </div>
                        <div className="flex items-center justify-between text-slate-300">
                          <span>💰 الرصيد الحالي:</span>
                          <span className="font-mono text-emerald-400 font-bold">$12.50 USDT</span>
                        </div>
                        <div className="flex items-center justify-between text-slate-400 text-[10px]">
                          <span>🛍️ إجمالي المشتريات:</span>
                          <span className="font-mono text-slate-300">$184.00 (7 طلبات)</span>
                        </div>
                      </div>

                      <p className="whitespace-pre-line text-slate-200">
                        {configs.welcome_message_ar || "أهلاً بك في المتجر الرقمي الآلي!"}
                      </p>
                      <div className="text-[9px] text-slate-500 text-left font-mono">12:45 PM ✓✓</div>
                    </div>

                    {/* 4-Row Inline Keyboard */}
                    <div className="space-y-1.5 pt-1">
                      <div className="py-2 px-3 rounded-xl bg-gradient-to-r from-indigo-600/90 to-indigo-700/90 border border-indigo-400/30 text-center font-bold text-xs text-white shadow-sm flex items-center justify-center gap-1.5">
                        <span>🛍️ تصفح الأقسام والمنتجات</span>
                      </div>
                      <div className="grid grid-cols-2 gap-1.5">
                        <div className="py-2 rounded-xl bg-[#242f3d] border border-[#2f3d4e] text-center text-[11px] font-medium text-emerald-300">
                          💳 شحن الرصيد
                        </div>
                        <div className="py-2 rounded-xl bg-[#242f3d] border border-[#2f3d4e] text-center text-[11px] font-medium text-slate-300">
                          📋 طلباتي السابقة
                        </div>
                      </div>
                      <div className="grid grid-cols-2 gap-1.5">
                        <div className="py-2 rounded-xl bg-[#242f3d] border border-[#2f3d4e] text-center text-[11px] font-medium text-indigo-300">
                          👤 حسابي والمحفظة
                        </div>
                        <div className="py-2 rounded-xl bg-[#242f3d] border border-[#2f3d4e] text-center text-[11px] font-medium text-slate-300">
                          💬 الدعم الفني
                        </div>
                      </div>
                      <div className="grid grid-cols-2 gap-1.5">
                        <div className="py-1.5 rounded-xl bg-[#1d2633] border border-[#2b3a4a] text-center text-[10px] text-slate-400">
                          📢 القناة الرسمية
                        </div>
                        <div className="py-1.5 rounded-xl bg-[#1d2633] border border-[#2b3a4a] text-center text-[10px] text-slate-400">
                          ⚡ إثباتات الشراء
                        </div>
                      </div>
                    </div>
                  </>
                )}

                {/* 2. FORCE SUBSCRIBE PREVIEW */}
                {previewTab === "force_sub" && (
                  <>
                    <div className="p-3.5 rounded-2xl rounded-tr-none bg-[#182533] border border-amber-500/30 text-slate-100 text-xs leading-relaxed space-y-2">
                      <div className="flex items-center gap-1.5 text-amber-400 font-bold text-xs">
                        <Lock className="w-3.5 h-3.5" />
                        <span>🔒 تنبيه: يلزم الاشتراك بالقناة للمتابعة!</span>
                      </div>
                      <p className="text-slate-300 text-[11px] leading-relaxed">
                        عزيزي العميل، للوصول إلى كافة خدمات المتجر واستعراض المنتجات وتلقي تنبيهات توفر الحسابات فوراً، يرجى الانضمام إلى قنواتنا الرسمية:
                      </p>
                      <div className="p-2 rounded-lg bg-black/30 border border-white/[0.04] space-y-1 font-mono text-[10px] text-slate-400">
                        <div>• {configs.main_channel_id || "@OfficialChannel"} (القناة الرسمية)</div>
                        {configs.proof_channel_id && (
                          <div>• {configs.proof_channel_id} (قناة الإثباتات)</div>
                        )}
                      </div>
                      <p className="text-[10px] text-slate-400">
                        بعد الانضمام، اضغط على زر التحقق للدخول المباشر.
                      </p>
                      <div className="text-[9px] text-slate-500 text-left font-mono">12:45 PM ✓✓</div>
                    </div>

                    {/* Subscription Buttons */}
                    <div className="space-y-1.5 pt-1">
                      <div className="py-2 rounded-xl bg-sky-600 border border-sky-400/30 text-center font-bold text-xs text-white flex items-center justify-center gap-1">
                        <span>📢 اشترك في القناة الرسمية</span>
                        <ExternalLink className="w-3 h-3" />
                      </div>
                      {configs.proof_channel_url && (
                        <div className="py-2 rounded-xl bg-[#242f3d] border border-[#2f3d4e] text-center text-[11px] font-medium text-slate-300 flex items-center justify-center gap-1">
                          <span>⚡ قناة إثباتات الشراء الحية</span>
                          <ExternalLink className="w-3 h-3" />
                        </div>
                      )}
                      <div className="py-2 rounded-xl bg-emerald-600 border border-emerald-400/30 text-center font-bold text-xs text-white shadow">
                        ✅ تم الاشتراك — تحقق وادخل المتجر
                      </div>
                    </div>
                  </>
                )}

                {/* 3. PROOF CARD PREVIEW */}
                {previewTab === "proof_card" && (
                  <>
                    <div className="p-3.5 rounded-2xl rounded-tr-none bg-[#182533] border border-emerald-500/30 text-slate-100 text-xs leading-relaxed space-y-2">
                      <div className="text-emerald-400 font-extrabold text-xs">
                        🎉 عملية شراء ناجحة! | ORDER COMPLETED
                      </div>
                      <div className="border-t border-dashed border-white/10 my-1"></div>
                      <div className="space-y-1 font-mono text-[11px]">
                        <div className="flex justify-between">
                          <span className="text-slate-400">📦 المنتج:</span>
                          <span className="text-white font-bold">Grok 2 AI Subscription</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-400">💰 القيمة:</span>
                          <span className="text-emerald-400 font-bold">$14.50 USDT</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-400">👤 المشتري:</span>
                          <span className="text-indigo-300">@a***9</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-400">🔖 رقم العملية:</span>
                          <span className="text-slate-300">ORD-88A1***4C</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-400">⏱️ سرعة التسليم:</span>
                          <span className="text-sky-300">1.4s (تسليم فوري)</span>
                        </div>
                      </div>
                      <div className="border-t border-dashed border-white/10 my-1"></div>
                      <div className="text-[10px] text-center text-slate-400 font-medium">
                        ⚡ تم التسليم بنجاح عبر النظام الآلي المستقل ⚡
                      </div>
                      <div className="text-[9px] text-slate-500 text-left font-mono">12:45 PM</div>
                    </div>

                    {/* Social Proof CTA Conversion Buttons */}
                    <div className="space-y-1.5 pt-1">
                      <div className="py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-600 text-center font-bold text-xs text-white shadow flex items-center justify-center gap-1.5">
                        <span>⚡ شراء نفس المنتج فوراً</span>
                      </div>
                      <div className="py-2 rounded-xl bg-[#242f3d] border border-[#2f3d4e] text-center text-[11px] font-medium text-slate-300">
                        🛒 الدخول للمتجر الرئيسي
                      </div>
                    </div>
                  </>
                )}

                {/* 4. SHAM CASH PREVIEW */}
                {previewTab === "sham_cash" && (
                  <>
                    <div className="p-3.5 rounded-2xl rounded-tr-none bg-[#182533] border border-[#2b3a4a] text-slate-100 text-xs leading-relaxed space-y-2">
                      <div className="p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 font-mono text-[11px] font-bold">
                        المبلغ المطلوب: 230,000 ل.س ($23.00)
                      </div>
                      <p className="whitespace-pre-line text-slate-300 text-[11px]">
                        📌 {configs.sham_cash_instructions_ar || "تعليمات التحويل تظهر هنا..."}
                      </p>
                      <div className="text-[9px] text-slate-500 text-left font-mono">12:45 PM ✓✓</div>
                    </div>

                    {/* Sham Cash Action Buttons */}
                    <div className="space-y-1.5 pt-1">
                      <div className="py-2 rounded-xl bg-emerald-600 text-center font-bold text-xs text-white shadow">
                        📷 اضغط لإرسال صورة الإشعار
                      </div>
                      <div className="py-2 rounded-xl bg-[#242f3d] text-center text-[11px] font-medium text-rose-400">
                        ❌ إلغاء الطلب
                      </div>
                    </div>
                  </>
                )}
              </div>

              {/* Chat Input Bar Mockup */}
              <div className="p-2 rounded-xl bg-[#17212b] border border-[#232e3c] flex items-center justify-between text-xs text-slate-500">
                <span>اكتب رسالة أو أرسل صورة...</span>
                <Send className="w-3.5 h-3.5 text-blue-400" />
              </div>
            </div>

            <div className="text-center text-[10px] text-slate-500 font-mono mt-3">
              LIVE TELEGRAM PREVIEW
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
