const styles = {
  completed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  approved: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  degraded: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  review_required: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  waiting_for_approval: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  failed: "bg-rose-500/25 text-rose-200 border-rose-500/60 font-bold shadow-sm shadow-rose-950",
  blocked: "bg-rose-500/25 text-rose-200 border-rose-500/60 font-bold shadow-sm shadow-rose-950",
  blocked_insufficient_evidence:
    "bg-rose-500/25 text-rose-200 border-rose-500/60 font-bold shadow-sm shadow-rose-950",
  running: "bg-indigo-500/20 text-indigo-200 border-indigo-500/50 animate-pulse",
  ready: "bg-cyan-500/15 text-cyan-300 border-cyan-500/40",
  default: "bg-slate-800/80 text-slate-300 border-slate-700"
};

export default function Badge({ value }) {
  const key = String(value || "unknown").toLowerCase();
  const style = styles[key] || styles.default;
  const isFailedOrBlocked = key.includes("failed") || key.includes("blocked");

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wider ${style}`}
    >
      {isFailedOrBlocked && (
        <span className="h-1.5 w-1.5 rounded-full bg-rose-400 animate-ping" />
      )}
      {String(value || "unknown").replaceAll("_", " ")}
    </span>
  );
}
