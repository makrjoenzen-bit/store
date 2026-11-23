"use client";

import React, { useState, useEffect } from "react";
import { 
  CheckCircle2, XCircle, Clock, 
  ZoomIn, User, X, Factory, FileText
} from "lucide-react";

export default function ShamCashQueuePage() {
  const [invoices, setInvoices] = useState<any[]>([]);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<{ orderId: string; type: string } | null>(null);

  const fetchQueue = async () => {
    try {
      const res = await fetch("/api/orders/sham-cash/pending");
      if (res.ok) {
        const data = await res.json();
        setInvoices(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    fetchQueue();
    const interval = setInterval(fetchQueue, 8000);
    return () => clearInterval(interval);
  }, []);

  const handleApprove = async (orderId: string) => {
    setProcessingId(orderId);
    try {
      const res = await fetch(`/api/orders/${orderId}/sham-cash-approve`, { method: "POST" });
      if (res.ok) {
        fetchQueue();
      }
    } catch (e) {
      console.error(e);
    } finally {
      setProcessingId(null);
    }
  };

  const handleReject = async (orderId: string) => {
    const reason = prompt("يرجى كتابة سبب رفض الإشعار للعميل:", "الإشعار غير واضح أو لم يصل التحويل");
    if (!reason) return;

    setProcessingId(orderId);
    try {
      const res = await fetch(`/api/orders/${orderId}/sham-cash-reject?reason=${encodeURIComponent(reason)}`, {
        method: "POST",
      });
      if (res.ok) {
        fetchQueue();
      }
    } catch (e) {
      console.error(e);
    } finally {
      setProcessingId(null);
    }
  };

  const receiptUrl = (orderId: string) => `/api/orders/${orderId}/receipt`;

  return (
    <div className="space-y-8 pb-12">
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
            <span>مكتب تدقيق إيصالات شام كاش</span>
            <span className="text-xs font-mono font-normal text-rose-400 px-2 py-0.5 rounded-full bg-rose-500/10 border border-rose-500/20">
              SHAM CASH QUEUE
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            مراجعة فورية لإيصالات التحويل المحلي. الموافقة تطلق دورة التمويل الآلي (JIT) وتسليم الحساب للعميل مباشرة.
          </p>
        </div>

        <div className="flex items-center space-x-2 space-x-reverse text-xs font-mono">
          <span className="px-3 py-1.5 rounded-xl bg-white/[0.04] text-slate-300 border border-white/[0.08]">
            بانتظار التدقيق: <strong className="text-rose-400 font-bold">{invoices.length}</strong>
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {invoices.map((inv) => (
          <div 
            key={inv.invoice_id}
            className="p-6 rounded-2xl glass-panel glass-panel-hover border border-white/[0.08] flex flex-col justify-between space-y-5 glow-rose group"
          >
            <div>
              {inv.fraud_warning && (
                <div className="mb-3 p-3 rounded-xl bg-rose-500/15 border border-rose-500/40 text-rose-200 text-[11px] font-bold leading-relaxed">
                  {inv.fraud_warning}
                </div>
              )}
              {inv.pdf_forensics && (
                <div className="mb-3 p-3 rounded-xl bg-indigo-500/10 border border-indigo-500/30 text-indigo-100 text-[11px] leading-relaxed space-y-1">
                  <div className="font-bold">
                    {(inv.pdf_forensics as any).risk_badge_ar || "🔬 Forensics"}
                  </div>
                  <div>
                    المستخرج: {(inv.pdf_forensics as any).extracted_amount ?? "—"}{" "}
                    {(inv.pdf_forensics as any).extracted_currency || ""} ↔ المتوقع: $
                    {Number(inv.amount_expected || 0).toFixed(2)} →{" "}
                    {(inv.pdf_forensics as any).amount_match}
                  </div>
                  <div>
                    Tx: {(inv.pdf_forensics as any).extracted_tx_ref || inv.tx_ref || "—"}
                    {inv.receipt_sha256 || inv.receipt_file_sha256
                      ? ` · SHA ${String(inv.receipt_sha256 || inv.receipt_file_sha256).slice(0, 12)}…`
                      : ""}
                  </div>
                  <div className="text-slate-400">
                    Creator/Producer: {(inv.pdf_forensics as any).creator || "—"} /{" "}
                    {(inv.pdf_forensics as any).producer || "—"}
                  </div>
                  <div className="text-[10px] text-slate-500">مساعد فقط — لا اعتماد تلقائي</div>
                </div>
              )}
              <div className="flex items-center justify-between border-b border-white/[0.06] pb-3 mb-4">
                <div className="flex items-center space-x-2 space-x-reverse">
                  <span className="w-2 h-2 rounded-full bg-rose-400 animate-ping"></span>
                  <span className="font-mono font-bold text-sm text-white">{inv.order_number}</span>
                </div>
                <span className="flex items-center space-x-1 space-x-reverse text-[10px] font-mono text-amber-300 bg-amber-500/10 px-2 py-0.5 rounded-full border border-amber-500/20">
                  <Clock className="w-3 h-3" />
                  <span>بانتظار اعتمادك</span>
                </span>
              </div>

              <div className="space-y-3 text-xs">
                <div className="flex items-center justify-between p-3 rounded-xl bg-obsidian-950/70 border border-white/[0.05]">
                  <span className="text-slate-400">العميل:</span>
                  <span className="font-mono font-bold text-white flex items-center gap-1">
                    <User className="w-3 h-3 text-indigo-400" />
                    <span>{inv.customer_name || `TG-${inv.customer_telegram_id}`}</span>
                  </span>
                </div>

                <div className="flex items-center justify-between p-3 rounded-xl bg-obsidian-950/70 border border-white/[0.05]">
                  <span className="text-slate-400">المنتج المطلوب:</span>
                  <span className="font-bold text-slate-200">{inv.product_title}</span>
                </div>

                <div className="flex items-center justify-between p-3 rounded-xl bg-obsidian-950/70 border border-amber-500/20">
                  <span className="text-slate-400 flex items-center gap-1">
                    <Factory className="w-3 h-3 text-amber-400" />
                    المورد (التاجر):
                  </span>
                  <span className="font-bold text-amber-200">{inv.supplier_name || "—"}</span>
                </div>

                <div className="p-4 rounded-xl bg-gradient-to-r from-emerald-500/10 to-transparent border border-emerald-500/20 flex items-center justify-between">
                  <span className="text-slate-300 font-semibold">المبلغ المطلوب مطابقة إيصاله:</span>
                  <span className="text-2xl font-black font-mono text-emerald-400">
                    ${Number(inv.amount_expected || 0).toFixed(2)}
                  </span>
                </div>

                <div className="space-y-1.5 pt-1">
                  <span className="text-[11px] text-slate-400 font-medium">
                    إيصال العميل ({inv.receipt_type === "pdf" ? "PDF" : "صورة"}):
                  </span>
                  <div 
                    onClick={() => setLightbox({ orderId: inv.order_id, type: inv.receipt_type || "photo" })}
                    className="p-3 rounded-xl bg-obsidian-950 border border-white/[0.08] hover:border-indigo-500/50 cursor-pointer transition flex items-center justify-between group/receipt"
                  >
                    <div className="flex items-center space-x-2 space-x-reverse truncate">
                      {inv.receipt_type === "pdf" ? (
                        <FileText className="w-4 h-4 text-indigo-400" />
                      ) : (
                        <ZoomIn className="w-4 h-4 text-indigo-400 group-hover/receipt:scale-110 transition" />
                      )}
                      <span className="font-mono text-[11px] text-slate-300 truncate">
                        Tx: {inv.tx_ref || "—"}
                      </span>
                    </div>
                    <span className="text-[10px] text-indigo-400 font-bold shrink-0">معاينة / تنزيل 🔍</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="pt-4 border-t border-white/[0.06] grid grid-cols-2 gap-3">
              <button
                onClick={() => handleReject(inv.order_id)}
                disabled={processingId === inv.order_id}
                className="flex items-center justify-center space-x-1.5 space-x-reverse py-2.5 rounded-xl bg-rose-500/10 hover:bg-rose-500/20 text-rose-300 text-xs font-bold border border-rose-500/30 transition shadow-sm active:scale-95"
              >
                <XCircle className="w-4 h-4 text-rose-400" />
                <span>رفض الإيصال</span>
              </button>

              <button
                onClick={() => handleApprove(inv.order_id)}
                disabled={processingId === inv.order_id}
                className="flex items-center justify-center space-x-1.5 space-x-reverse py-2.5 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-500 hover:from-emerald-500 hover:to-teal-400 text-white text-xs font-bold shadow-lg shadow-emerald-500/25 transition active:scale-95"
              >
                <CheckCircle2 className="w-4 h-4" />
                <span>{processingId === inv.order_id ? "جاري التمويل..." : "موافقة وتمويل فوراً"}</span>
              </button>
            </div>
          </div>
        ))}

        {invoices.length === 0 && (
          <div className="col-span-full p-16 text-center rounded-3xl glass-panel border border-white/[0.08] space-y-3">
            <div className="w-12 h-12 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 flex items-center justify-center mx-auto text-xl font-bold">
              ✓
            </div>
            <h3 className="text-base font-bold text-white">لا توجد إيصالات معلقة بانتظار المراجعة!</h3>
            <p className="text-xs text-slate-500 max-w-md mx-auto">
              جميع طلبات شام كاش تم تدقيقها. بمجرد رفع إيصال جديد (صورة أو PDF) سيظهر هنا فوراً.
            </p>
          </div>
        )}
      </div>

      {lightbox && (
        <div 
          onClick={() => setLightbox(null)}
          className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/80 backdrop-blur-md cursor-pointer"
        >
          <div className="relative max-w-lg w-full p-6 rounded-3xl bg-obsidian-950 border border-white/[0.1] shadow-2xl space-y-4" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-white/[0.08] pb-3">
              <span className="font-bold text-sm text-white">معاينة إيصال التحويل</span>
              <button onClick={() => setLightbox(null)} className="w-7 h-7 rounded-lg bg-white/[0.05] text-slate-400 hover:text-white flex items-center justify-center">
                <X className="w-4 h-4" />
              </button>
            </div>

            {lightbox.type === "pdf" ? (
              <div className="p-6 rounded-2xl bg-obsidian-900 border border-white/[0.06] text-center space-y-3">
                <FileText className="w-12 h-12 text-indigo-400 mx-auto" />
                <a
                  href={receiptUrl(lightbox.orderId)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-block px-4 py-2 rounded-xl bg-indigo-500/20 text-indigo-300 text-xs font-bold border border-indigo-500/30"
                >
                  فتح / تنزيل PDF
                </a>
              </div>
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={receiptUrl(lightbox.orderId)}
                alt="Receipt"
                className="w-full max-h-[60vh] object-contain rounded-2xl border border-white/[0.06] bg-obsidian-900"
              />
            )}

            <button
              onClick={() => setLightbox(null)}
              className="w-full py-2.5 rounded-xl bg-white/[0.06] hover:bg-white/[0.1] text-white text-xs font-semibold"
            >
              إغلاق المعاينة
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
