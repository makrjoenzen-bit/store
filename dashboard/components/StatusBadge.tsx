const MAP: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  order_fulfilled: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  delivered: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  sham_cash_review: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  awaiting_payment: "bg-sky-500/15 text-sky-300 border-sky-500/25",
  failed_manual_review: "bg-rose-500/15 text-rose-300 border-rose-500/25",
  rejected: "bg-rose-500/15 text-rose-300 border-rose-500/25",
  cancelled: "bg-slate-500/15 text-slate-300 border-slate-500/25",
};

export default function StatusBadge({ state }: { state?: string | null }) {
  const key = (state || "").toLowerCase();
  const cls = MAP[key] || "bg-slate-500/15 text-slate-300 border-slate-500/25";
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-md text-[11px] font-medium border ${cls}`}>
      {state || "—"}
    </span>
  );
}
