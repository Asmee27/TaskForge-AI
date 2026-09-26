import { ShieldCheck, AlertTriangle } from "lucide-react";
import Badge from "./Badge";

export default function CriticPanel({ critic }) {
  if (!critic) return null;

  const confidence = Math.round(
    (critic.overall_confidence || 0) * 100
  );

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-cyan-500/30 bg-cyan-500/10 text-cyan-300">
            <ShieldCheck size={20} />
          </div>

          <div>
            <h2 className="text-lg font-semibold text-white">
              Safety / Critic Review
            </h2>
            <p className="text-sm text-slate-500">
              Evidence, confidence and policy validation
            </p>
          </div>
        </div>

        <Badge value={critic.verdict} />
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            Evidence
          </div>
          <div className="mt-1 font-semibold text-white">
            {critic.evidence_quality?.toUpperCase()}
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            Confidence
          </div>
          <div className="mt-1 font-semibold text-white">
            {confidence}%
          </div>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
          <div className="text-xs uppercase tracking-wide text-slate-500">
            Approval
          </div>
          <div className="mt-1 font-semibold text-white">
            {critic.requires_human_approval ? "Required" : "Not required"}
          </div>
        </div>
      </div>

      <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/50 p-4 text-sm leading-6 text-slate-300">
        {critic.summary}
      </div>

      {critic.findings?.length ? (
        <div className="mt-4 space-y-2">
          {critic.findings.map((finding, index) => (
            <div
              key={`${finding.category}-${index}`}
              className="flex gap-3 rounded-xl border border-slate-800 bg-slate-950/40 p-3"
            >
              <AlertTriangle
                size={17}
                className={
                  finding.severity === "critical"
                    ? "mt-0.5 shrink-0 text-rose-400"
                    : "mt-0.5 shrink-0 text-amber-400"
                }
              />

              <div>
                <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                  {finding.severity} · {finding.category}
                </div>
                <p className="mt-1 text-sm leading-6 text-slate-300">
                  {finding.message}
                </p>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
