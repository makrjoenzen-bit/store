"use client";

import React, { useState, useEffect } from "react";
import { 
  LifeBuoy, MessageSquare, CheckCircle2, Clock, AlertTriangle, 
  Send, RefreshCw, Key, User, DollarSign, ArrowLeft, ShieldAlert,
  Search, Check, Sparkles
} from "lucide-react";

export default function SupportDeskPage() {
  const [tickets, setTickets] = useState<any[]>([]);
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null);
  const [ticketDetail, setTicketDetail] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [replyText, setReplyText] = useState("");
  const [sendingReply, setSendingReply] = useState(false);
  const [refunding, setRefunding] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");

  const fetchTickets = async () => {
    try {
      const url = statusFilter === "all" ? "/api/support/tickets" : `/api/support/tickets?status_filter=${statusFilter}`;
      const res = await fetch(url);
      if (res.ok) {
        const data = await res.json();
        setTickets(Array.isArray(data) ? data : []);
        if (data.length > 0 && !selectedTicketId) {
          setSelectedTicketId(data[0].id);
        }
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  const fetchTicketDetail = async (id: string) => {
    setDetailLoading(true);
    try {
      const res = await fetch(`/api/support/tickets/${id}`);
      if (res.ok) {
        setTicketDetail(await res.json());
      }
    } catch (e) {
      console.error(e);
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    fetchTickets();
  }, [statusFilter]);

  useEffect(() => {
    if (selectedTicketId) {
      fetchTicketDetail(selectedTicketId);
    }
  }, [selectedTicketId]);

  const handleSendReply = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!replyText.trim() || !selectedTicketId) return;
    setSendingReply(true);
    try {
      const res = await fetch(`/api/support/tickets/${selectedTicketId}/reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_text: replyText, mark_as_resolved: false }),
      });
      if (res.ok) {
        setReplyText("");
        fetchTicketDetail(selectedTicketId);
        fetchTickets();
      }
    } catch (e) {
      console.error(e);
    } finally {
      setSendingReply(false);
    }
  };

  const handle1ClickRefundAndResolve = async () => {
    if (!selectedTicketId) return;
    if (!confirm("هل أنت متأكد من تعويض العميل وإرجاع كامل المبلغ لرصيد محفظته وإغلاق التذكرة؟")) return;
    setRefunding(true);
    try {
      const res = await fetch(`/api/support/tickets/${selectedTicketId}/resolve-refund`, {
        method: "POST",
      });
      if (res.ok) {
        fetchTicketDetail(selectedTicketId);
        fetchTickets();
      }
    } catch (e) {
      console.error(e);
    } finally {
      setRefunding(false);
    }
  };

  const filteredTickets = tickets.filter(
    (t) =>
      t.subject.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (t.order_number && t.order_number.toLowerCase().includes(searchQuery.toLowerCase())) ||
      t.customer_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      t.ticket_short_id.includes(searchQuery)
  );

  return (
    <div className="space-y-6 pb-12">
      {/* Header */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 border-b border-white/[0.06] pb-6">
        <div>
          <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-white flex items-center gap-2">
            <span>مركز الدعم الفني والنزاعات</span>
            <span className="text-xs font-mono font-normal text-rose-400 px-2 py-0.5 rounded-full bg-rose-500/10 border border-rose-500/20">
              DISPUTE DESK
            </span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            إدارة شكاوى العملاء ونزاعات المفاتيح غير الصالحة، الرد المباشر في تيليجرام، والتعويض المالي بضغطة واحدة.
          </p>
        </div>

        <button 
          onClick={() => { fetchTickets(); if (selectedTicketId) fetchTicketDetail(selectedTicketId); }}
          className="flex items-center space-x-2 space-x-reverse px-4 py-2.5 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] text-slate-200 font-semibold text-xs border border-white/[0.08] transition"
        >
          <RefreshCw className="w-4 h-4 text-indigo-400" />
          <span>تحديث التذاكر</span>
        </button>
      </div>

      {/* Main Split Interface */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 min-h-[680px]">
        
        {/* Left Column: Tickets Queue (4 Cols) */}
        <div className="lg:col-span-5 flex flex-col space-y-4">
          {/* Filter Pills & Search */}
          <div className="p-3.5 rounded-2xl glass-panel border border-white/[0.08] space-y-3">
            <div className="relative">
              <Search className="w-4 h-4 absolute right-3.5 top-3 text-slate-500" />
              <input
                type="text"
                placeholder="ابحث برقم الطلب أو العميل أو التذكرة..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-4 pr-10 py-2 rounded-xl bg-obsidian-950 border border-white/[0.08] text-xs text-white focus:outline-none focus:border-indigo-500 transition"
              />
            </div>

            <div className="flex gap-1.5 text-xs">
              {[
                { id: "all", label: "الكل" },
                { id: "open", label: "مفتوحة" },
                { id: "in_progress", label: "قيد المتابعة" },
                { id: "resolved", label: "تم الحل" },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setStatusFilter(tab.id)}
                  className={`flex-1 py-1.5 rounded-lg font-medium transition ${
                    statusFilter === tab.id
                      ? "bg-indigo-600/30 border border-indigo-500 text-white"
                      : "bg-white/[0.03] text-slate-400 hover:text-white"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          {/* Tickets List */}
          <div className="flex-1 overflow-y-auto space-y-2.5 max-h-[600px] pr-1">
            {loading ? (
              <div className="p-12 text-center text-xs text-slate-500">جاري تحميل تذاكر الدعم...</div>
            ) : filteredTickets.length > 0 ? (
              filteredTickets.map((t) => {
                const isSelected = selectedTicketId === t.id;
                const isOpen = t.status === "open";
                const isResolved = t.status === "resolved";

                return (
                  <div
                    key={t.id}
                    onClick={() => setSelectedTicketId(t.id)}
                    className={`p-4 rounded-2xl border cursor-pointer transition text-xs space-y-2 ${
                      isSelected
                        ? "bg-indigo-600/10 border-indigo-500/50 shadow-lg shadow-indigo-600/10"
                        : "bg-obsidian-950/70 border-white/[0.06] hover:border-white/[0.14]"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-[10px] text-slate-400 bg-white/[0.04] px-2 py-0.5 rounded">
                        #T-{t.ticket_short_id}
                      </span>
                      <div className="flex items-center gap-1.5">
                        {t.is_warranty_expired && (
                          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30">
                            ⚠️ خارج فترة الضمان
                          </span>
                        )}
                        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                          isOpen 
                            ? "bg-rose-500/10 text-rose-400 border border-rose-500/20 animate-pulse"
                            : isResolved
                            ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                            : "bg-amber-500/10 text-amber-400 border border-amber-500/20"
                        }`}>
                          {isOpen ? "جديدة" : isResolved ? "تم الحل" : "قيد المتابعة"}
                        </span>
                      </div>
                    </div>

                    <div className="font-semibold text-white line-clamp-1">{t.subject}</div>

                    {t.last_message_text && (
                      <div className="text-slate-400 text-[11px] line-clamp-2 leading-relaxed">
                        {t.last_message_text}
                      </div>
                    )}

                    <div className="pt-2 border-t border-white/[0.04] flex items-center justify-between text-[10px] text-slate-500">
                      <span>العميل: {t.customer_name}</span>
                      {t.order_number && (
                        <span className="font-mono text-indigo-400">#{t.order_number}</span>
                      )}
                    </div>
                  </div>
                );
              })
            ) : (
              <div className="p-12 text-center text-xs text-slate-500 rounded-2xl glass-panel border border-white/[0.06]">
                لا توجد أي تذاكر مطابقة حالياً.
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Ticket Conversation & Resolution Hub (7 Cols) */}
        <div className="lg:col-span-7 flex flex-col justify-between rounded-3xl glass-panel border border-white/[0.08] overflow-hidden">
          {ticketDetail ? (
            <div className="flex flex-col h-full justify-between">
              
              {/* Ticket Top Meta Banner */}
              <div className="p-6 border-b border-white/[0.08] bg-obsidian-900/60 space-y-4">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-bold text-indigo-400">#T-{ticketDetail.ticket_short_id}</span>
                      <h2 className="text-base font-bold text-white">{ticketDetail.subject}</h2>
                      {ticketDetail.is_warranty_expired && (
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30">
                          ⚠️ خارج فترة الضمان
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-400">
                      <span>العميل: <strong className="text-white">{ticketDetail.customer?.name}</strong> (@{ticketDetail.customer?.telegram_username || ticketDetail.customer?.telegram_id})</span>
                      <span>•</span>
                      <span>رصيد المحفظة: <strong className="text-emerald-400 font-mono">${ticketDetail.customer?.wallet_balance?.toFixed(2)}</strong></span>
                    </div>
                  </div>

                  {ticketDetail.order && ticketDetail.status !== "resolved" && (
                    <button
                      onClick={handle1ClickRefundAndResolve}
                      disabled={refunding}
                      className="px-3.5 py-2 rounded-xl bg-gradient-to-r from-emerald-600 to-teal-500 hover:from-emerald-500 hover:to-teal-400 text-white font-bold text-xs shadow-lg shadow-emerald-600/20 flex items-center gap-1.5 transition"
                    >
                      <Sparkles className="w-3.5 h-3.5" />
                      <span>{refunding ? "جاري التعويض..." : `تعويض فوري ($${ticketDetail.order.our_price}) وإغلاق`}</span>
                    </button>
                  )}
                </div>

                {/* Delivered Product Payload Inspector Card */}
                {ticketDetail.order && (
                  <div className="p-3.5 rounded-xl bg-obsidian-950/80 border border-white/[0.06] text-xs space-y-2">
                    <div className="flex items-center justify-between text-slate-400 text-[11px]">
                      <span>المنتج المطلوب: <strong className="text-white">{ticketDetail.order.product_title}</strong> (طلب #{ticketDetail.order.order_number})</span>
                      <span className="font-mono text-emerald-400 font-bold">${ticketDetail.order.our_price.toFixed(2)} USD</span>
                    </div>

                    <div className="pt-1.5 border-t border-white/[0.05]">
                      <div className="text-[10px] text-slate-500 mb-1 flex items-center gap-1 font-mono">
                        <Key className="w-3 h-3 text-amber-400" />
                        <span>البيانات المسلمة للعميل من المورد (Delivered Payload):</span>
                      </div>
                      <div className="p-2.5 rounded-lg bg-black/60 font-mono text-[11px] text-amber-200 break-all select-all border border-amber-500/20">
                        {ticketDetail.order.delivered_product || "لم يتم تسليم بيانات بعد"}
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Chat Thread Messages */}
              <div className="flex-1 overflow-y-auto p-6 space-y-4 max-h-[420px]">
                {ticketDetail.messages?.map((msg: any) => {
                  const isAdmin = msg.sender_type === "admin";
                  const isSystem = msg.sender_type === "system";

                  if (isSystem) {
                    return (
                      <div key={msg.id} className="p-2.5 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-center text-[11px] font-medium">
                        {msg.message_text}
                      </div>
                    );
                  }

                  return (
                    <div
                      key={msg.id}
                      className={`flex flex-col ${isAdmin ? "items-end" : "items-start"}`}
                    >
                      <div className="flex items-center gap-1.5 text-[10px] text-slate-500 mb-1 font-mono">
                        <span>{msg.sender_name}</span>
                        <span>•</span>
                        <span>{new Date(msg.created_at).toLocaleTimeString("ar-EG", { hour: "2-digit", minute: "2-digit" })}</span>
                      </div>

                      <div className={`p-4 rounded-2xl max-w-[85%] text-xs leading-relaxed ${
                        isAdmin
                          ? "bg-indigo-600 text-white rounded-br-none shadow-md shadow-indigo-600/20"
                          : "bg-obsidian-900 border border-white/[0.08] text-slate-200 rounded-bl-none"
                      }`}>
                        {msg.message_text}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Bottom Reply Form */}
              <form onSubmit={handleSendReply} className="p-4 border-t border-white/[0.08] bg-obsidian-900/40 flex items-center gap-3">
                <input
                  type="text"
                  required
                  placeholder="اكتب ردك هنا... سيصل مباشرة كرسالة تيليجرام للعميل"
                  value={replyText}
                  onChange={(e) => setReplyText(e.target.value)}
                  className="flex-1 px-4 py-2.5 rounded-xl bg-obsidian-950 border border-white/[0.08] text-xs text-white focus:outline-none focus:border-indigo-500"
                />
                <button
                  type="submit"
                  disabled={sendingReply}
                  className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs shadow-md shadow-indigo-600/20 flex items-center gap-1.5 transition"
                >
                  <Send className="w-3.5 h-3.5" />
                  <span>{sendingReply ? "جاري الإرسال..." : "إرسال إلى تيليجرام"}</span>
                </button>
              </form>

            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full p-12 text-center text-slate-500 space-y-3">
              <LifeBuoy className="w-12 h-12 text-slate-600 stroke-[1.5]" />
              <div className="text-sm font-medium">اختر تذكرة من القائمة الجانبية لعرض النزاع والمحادثة</div>
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
