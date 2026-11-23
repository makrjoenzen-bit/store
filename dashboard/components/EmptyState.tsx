export default function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="py-12 text-center text-slate-400">
      <p className="text-sm font-medium text-slate-300">{title}</p>
      {hint && <p className="text-xs mt-1 text-slate-500">{hint}</p>}
    </div>
  );
}
