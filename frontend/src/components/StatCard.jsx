export default function StatCard({ label, value, subtext }) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 shadow-xl shadow-black/10">
      <div className="text-xs font-medium uppercase tracking-[0.18em] text-slate-500">
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold text-white">
        {value}
      </div>
      {subtext ? (
        <div className="mt-1 text-xs text-slate-500">{subtext}</div>
      ) : null}
    </div>
  );
}
