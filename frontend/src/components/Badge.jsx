const styles = {
  completed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  approved: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  degraded: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  review_required: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  waiting_for_approval: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  failed: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  blocked: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  blocked_insufficient_evidence:
    "bg-rose-500/15 text-rose-300 border-rose-500/30",
  default: "bg-slate-700/60 text-slate-300 border-slate-600"
};

export default function Badge({ value }) {
  const key = String(value || "unknown").toLowerCase();
  const style = styles[key] || styles.default;

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold uppercase tracking-wide ${style}`}
    >
      {String(value || "unknown").replaceAll("_", " ")}
    </span>
  );
}
