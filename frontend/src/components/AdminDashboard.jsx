import { Activity, BarChart3, CircuitBoard, FileText, Gauge, RefreshCw, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { getAdminOverview } from "../api/client";

function valueOrDash(value) {
  return value === null || value === undefined ? "—" : value;
}

function Metric({ label, value }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-white">{valueOrDash(value)}</div>
    </div>
  );
}

export default function AdminDashboard({ onBack }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      setData(await getAdminOverview());
    } catch (err) {
      setError(err.response?.data?.detail?.message || err.message || "Admin data unavailable.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const averages = data?.averages || {};
  const runData = data?.workflow_runs || {};
  const m13 = data?.m13_summary;

  return (
    <main className="mx-auto max-w-[1550px] px-5 py-6">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-cyan-300"><Gauge size={17} />Admin Dashboard</div>
          <h1 className="mt-1 text-2xl font-semibold text-white">Persisted platform operations</h1>
          <p className="mt-1 text-sm text-slate-500">Read-only metrics from PostgreSQL, workspace metadata, RAG documents, audit events, and M13 artifacts.</p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={refresh} title="Refresh admin data" className="rounded-lg border border-slate-700 p-2 text-slate-300 hover:border-cyan-500/50"><RefreshCw size={16} /></button>
          <button type="button" onClick={onBack} className="rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-300 hover:border-cyan-500/50">Back to workspace</button>
        </div>
      </div>

      {loading ? <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-5 text-sm text-slate-400">Loading persisted metrics...</div> : null}
      {error ? <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-300">{error}</div> : null}

      {data ? (
        <div className="space-y-5">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
            <Metric label="Workspaces" value={data.workspaces?.length} />
            <Metric label="Workflow runs" value={runData.total} />
            <Metric label="Avg steps" value={averages.workflow_steps} />
            <Metric label="Avg tool calls" value={averages.tool_calls} />
            <Metric label="Avg LLM calls" value={averages.llm_calls} />
            <Metric label="Avg tokens" value={averages.tokens} />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
              <div className="flex items-center gap-2 font-semibold text-white"><Activity size={17} className="text-indigo-300" />Run status</div>
              <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
                {Object.entries(runData.status_counts || {}).map(([status, count]) => <div key={status} className="flex justify-between rounded-lg bg-slate-950/60 px-3 py-2"><span className="text-slate-400">{status}</span><span className="font-semibold text-slate-200">{count}</span></div>)}
              </div>
              <div className="mt-4 text-sm text-slate-400">Average cost: <span className="text-slate-200">{valueOrDash(averages.estimated_cost_usd)}</span></div>
            </section>

            <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
              <div className="flex items-center gap-2 font-semibold text-white"><ShieldCheck size={17} className="text-amber-300" />Budget stops</div>
              {Object.keys(data.budget_stops || {}).length ? <div className="mt-4 space-y-2 text-sm">{Object.entries(data.budget_stops).map(([code, count]) => <div key={code} className="flex justify-between rounded-lg bg-amber-500/5 px-3 py-2"><span className="text-amber-200">{code}</span><span>{count}</span></div>)}</div> : <div className="mt-4 text-sm text-slate-500">No persisted budget stops.</div>}
              <div className="mt-4 flex items-center gap-2 font-semibold text-white"><CircuitBoard size={17} className="text-rose-300" />Open circuit events</div>
              <div className="mt-2 text-sm text-slate-400">{data.circuit_events?.length || 0} persisted open-circuit snapshots</div>
            </section>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5"><div className="flex items-center gap-2 font-semibold text-white"><FileText size={17} className="text-cyan-300" />RAG documents</div><div className="mt-4 max-h-56 space-y-2 overflow-auto text-sm">{data.rag_documents?.length ? data.rag_documents.map((document) => <div key={document.document_id} className="flex justify-between gap-3 rounded-lg bg-slate-950/60 px-3 py-2"><span className="truncate text-slate-300">{document.filename}</span><span className="shrink-0 text-slate-500">{document.chunk_count} chunks</span></div>) : <div className="text-slate-500">No persisted documents.</div>}</div></section>
            <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5"><div className="flex items-center gap-2 font-semibold text-white"><BarChart3 size={17} className="text-emerald-300" />M13 evaluation</div>{m13 ? <div className="mt-4 grid grid-cols-2 gap-2 text-sm">{Object.entries(m13.aggregate_metrics || {}).map(([key, value]) => <div key={key} className="rounded-lg bg-slate-950/60 px-3 py-2"><div className="text-xs text-slate-500">{key}</div><div className="mt-1 text-slate-200">{valueOrDash(value)}</div></div>)}</div> : <div className="mt-4 text-sm text-slate-500">No M13 artifact is available.</div>}</section>
          </div>

          <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5"><div className="font-semibold text-white">Recent audit events</div><div className="mt-4 max-h-72 space-y-2 overflow-auto">{data.recent_audit_events?.length ? data.recent_audit_events.map((event) => <div key={event.id} className="rounded-lg bg-slate-950/60 px-3 py-2 text-xs"><div className="flex justify-between gap-3"><span className="font-semibold text-slate-300">{event.event_type}</span><span className="text-slate-600">{event.created_at}</span></div><div className="mt-1 text-slate-500">{event.run_id} · {event.message}</div></div>) : <div className="text-sm text-slate-500">No audit events persisted.</div>}</div></section>
        </div>
      ) : null}
    </main>
  );
}
