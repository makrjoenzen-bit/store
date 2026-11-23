export default function KpiCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="kpi-card p-5 space-y-2">
      <div className="text-xs text-slate-400 font-medium">{label}</div>
      <div className="text-2xl font-bold text-white tracking-tight tabular-nums">{value}</div>
      {hint && <div className="text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}
