import { BarChart3, Download, FlaskConical, LineChart, PieChart, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { API_BASE, getWorkflowIntelligence, simulateWorkflow } from "../api/client";

function formatValue(value) {
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function Chart({ chart }) {
  const width = 520;
  const height = 220;
  const padding = 32;
  const max = Math.max(...chart.values, 1);

  if (chart.type === "pie") {
    const total = chart.values.reduce((sum, value) => sum + value, 0) || 1;
    let offset = 0;
    const segments = chart.values.map((value, index) => {
      const start = offset;
      offset += (value / total) * 360;
      return `${["#67e8f9", "#818cf8", "#fbbf24", "#fb7185", "#4ade80"][index % 5]} ${start}deg ${offset}deg`;
    });
    return (
      <div className="flex flex-wrap items-center gap-5">
        <div className="h-32 w-32 rounded-full" style={{ background: `conic-gradient(${segments.join(", ")})` }} />
        <div className="space-y-1 text-xs text-slate-400">
          {chart.labels.map((label, index) => (
            <div key={label} className="flex gap-2">
              <span className="text-slate-200">{label}</span>
              <span>{formatValue(chart.values[index])}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const points = chart.values.map((value, index) => ({
    x: padding + (index * (width - padding * 2)) / Math.max(chart.values.length - 1, 1),
    y: height - padding - ((value / max) * (height - padding * 2))
  }));
  const path = points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-52 w-full" role="img" aria-label={chart.title}>
      <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} stroke="#334155" />
      {chart.type === "line" ? (
        <path d={path} fill="none" stroke="#67e8f9" strokeWidth="3" />
      ) : null}
      {points.map((point, index) => (
        <g key={`${chart.labels[index]}-${index}`}>
          {chart.type === "bar" ? (
            <rect x={point.x - 12} y={point.y} width="24" height={height - padding - point.y} rx="3" fill="#818cf8" />
          ) : (
            <circle cx={point.x} cy={point.y} r="4" fill="#67e8f9" />
          )}
          <text x={point.x} y={height - 10} textAnchor="middle" fill="#94a3b8" fontSize="10">{String(chart.labels[index]).slice(0, 12)}</text>
        </g>
      ))}
    </svg>
  );
}

export default function DecisionIntelligencePanel({ runId }) {
  const [intelligence, setIntelligence] = useState(null);
  const [scenario, setScenario] = useState("");
  const [simulation, setSimulation] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!runId) {
      setIntelligence(null);
      setSimulation(null);
      return;
    }
    getWorkflowIntelligence(runId)
      .then(setIntelligence)
      .catch((err) => setError(err.response?.data?.detail?.message || err.message || "Decision intelligence unavailable."));
  }, [runId]);

  async function runSimulation(event) {
    event.preventDefault();
    if (!scenario.trim()) return;
    setBusy(true);
    setError("");
    try {
      setSimulation(await simulateWorkflow(runId, scenario.trim()));
    } catch (err) {
      setError(err.response?.data?.detail?.message || err.message || "Simulation failed.");
    } finally {
      setBusy(false);
    }
  }

  if (!runId) return null;

  const charts = intelligence?.charts || [];
  const citations = intelligence?.policy_citations || [];

  return (
    <section className="space-y-5 rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 font-semibold text-white"><BarChart3 size={18} className="text-cyan-300" />Decision Intelligence</div>
          <div className="mt-1 text-xs text-slate-500">Charts use observed structured Analyst evidence only.</div>
        </div>
        <a href={`${API_BASE}/api/workflows/${runId}/report`} download className="inline-flex items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-300 hover:border-cyan-500/50 hover:text-cyan-200"><Download size={14} />Download report</a>
      </div>

      {charts.length ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {charts.map((chart, index) => (
            <div key={`${chart.title}-${index}`} className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-200">
                {chart.type === "line" ? <LineChart size={15} /> : chart.type === "pie" ? <PieChart size={15} /> : <BarChart3 size={15} />}
                {chart.title}
              </div>
              <Chart chart={chart} />
              <div className="text-[11px] text-slate-600">Observed, not simulated · {chart.source}</div>
            </div>
          ))}
        </div>
      ) : <div className="rounded-xl border border-dashed border-slate-700 p-4 text-xs text-slate-500">No suitable structured evidence was available for a chart.</div>}

      <div className="border-t border-slate-800 pt-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-white"><FlaskConical size={16} className="text-amber-300" />What-if simulation</div>
        <form onSubmit={runSimulation} className="flex flex-wrap gap-2">
          <input value={scenario} onChange={(event) => setScenario(event.target.value)} placeholder="What if discount increases from 10% to 15%?" className="min-w-[240px] flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white outline-none focus:border-amber-400" />
          <button type="submit" disabled={busy || !scenario.trim()} className="inline-flex items-center gap-2 rounded-lg bg-amber-400 px-3 py-2 text-xs font-semibold text-slate-950 disabled:opacity-50"><Play size={13} />Simulate</button>
        </form>
        {simulation ? (
          <div className="mt-3 rounded-xl border border-amber-500/20 bg-amber-500/5 p-3 text-xs">
            <div className="font-semibold uppercase tracking-wide text-amber-200">{simulation.status}</div>
            <div className="mt-1 text-slate-300">{simulation.message || simulation.assumption}</div>
            {(simulation.simulated || []).map((item) => <div key={item.metric} className="mt-2 flex justify-between gap-3 text-slate-300"><span>{item.metric} · simulated</span><span>{formatValue(item.value)}</span></div>)}
            {(simulation.observed || []).map((item) => <div key={`observed-${item.metric}`} className="mt-1 flex justify-between gap-3 text-slate-500"><span>{item.metric} · observed</span><span>{formatValue(item.value)}</span></div>)}
          </div>
        ) : null}
      </div>

      {citations.length ? <div className="border-t border-slate-800 pt-4"><div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Policy / RAG citations</div><div className="space-y-1 text-xs text-slate-400">{citations.slice(0, 4).map((item) => <div key={`${item.document_id}-${item.page}-${item.chunk}`}>{item.citation}</div>)}</div></div> : null}
      {error ? <div className="text-xs text-rose-300">{error}</div> : null}
    </section>
  );
}
