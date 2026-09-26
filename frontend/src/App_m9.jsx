import { useMemo, useRef, useState } from "react";
import {
  Activity,
  Bot,
  Database,
  PlayCircle,
  RotateCcw,
  ShieldCheck,
  Sparkles
} from "lucide-react";

import {
  getWorkflowResult,
  openTrace,
  startWorkflow
} from "./api/client";

import Badge from "./components/Badge";
import StatCard from "./components/StatCard";
import PlanPanel from "./components/PlanPanel";
import TaskResults from "./components/TaskResults";
import CriticPanel from "./components/CriticPanel";
import ActionCenter from "./components/ActionCenter";
import LiveTrace from "./components/LiveTrace";

const defaultGoal =
  "Analyze inventory and recommend whether we should reorder 5000 units of low-stock products.";

const defaultWorkspace = {
  workspace_id: "olist-demo",
  company_name: "NovaMart",
  business_type: "ecommerce",
  dataset_name: "Olist Brazilian E-Commerce Dataset",
  metadata: { source: "demo_workspace" },
  policies: {
    require_human_approval_for_irreversible_actions: true,
    max_discount_percent_without_approval: 20,
    max_reorder_quantity_without_approval: 1000,
    minimum_margin_percent: 10
  }
};

export default function App() {
  const [goal, setGoal] = useState(defaultGoal);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [traceEvents, setTraceEvents] = useState([]);
  const [runId, setRunId] = useState("");
  const closeTraceRef = useRef(null);

  const totalDuration = useMemo(
    () =>
      (result?.task_results || []).reduce(
        (sum, item) => sum + (item.duration_ms || 0),
        0
      ),
    [result]
  );

  async function handleRun() {
    if (!goal.trim() || running) return;

    closeTraceRef.current?.();

    setRunning(true);
    setError("");
    setResult(null);
    setTraceEvents([]);
    setRunId("");

    try {
      const started = await startWorkflow({
        goal: goal.trim(),
        workspace: defaultWorkspace
      });

      setRunId(started.run_id);

      closeTraceRef.current = openTrace(
        started.run_id,
        {
          onTrace: (event) => {
            setTraceEvents((current) => [
              ...current,
              event
            ]);
          },

          onComplete: async () => {
            try {
              const finalResult =
                await getWorkflowResult(
                  started.run_id
                );

              setResult(finalResult);
            } catch (err) {
              setError(
                err.response?.data?.detail?.message ||
                  err.message ||
                  "Could not load workflow result."
              );
            } finally {
              setRunning(false);
            }
          },

          onError: () => {
            // EventSource reconnects automatically while the run
            // is active. Final completion closes it explicitly.
          }
        }
      );
    } catch (err) {
      setRunning(false);
      setError(
        err.response?.data?.detail?.message ||
          err.message ||
          "Workflow could not be started."
      );
    }
  }

  function reset() {
    closeTraceRef.current?.();
    setResult(null);
    setError("");
    setTraceEvents([]);
    setRunId("");
    setRunning(false);
  }

  return (
    <div className="min-h-screen text-slate-100">
      <header className="border-b border-slate-800/80 bg-slate-950/70 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] items-center justify-between px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-indigo-500 shadow-lg shadow-indigo-500/20">
              <Sparkles size={20} />
            </div>
            <div>
              <div className="text-lg font-semibold text-white">
                SynapseOps AI
              </div>
              <div className="text-xs text-slate-500">
                Multi-Agent Business Decision Platform
              </div>
            </div>
          </div>

          <Badge
            value={
              running
                ? "running"
                : result?.status || "ready"
            }
          />
        </div>
      </header>

      <main className="mx-auto grid max-w-[1500px] gap-5 px-5 py-6 lg:grid-cols-[260px_minmax(0,1fr)_350px]">
        <aside className="space-y-4">
          <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
              Workspace
            </div>

            <div className="mt-4 flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-indigo-500/30 bg-indigo-500/10 text-indigo-300">
                <Database size={18} />
              </div>
              <div>
                <div className="font-semibold text-white">
                  NovaMart
                </div>
                <div className="text-xs text-slate-500">
                  Olist demo workspace
                </div>
              </div>
            </div>

            <div className="mt-4 space-y-2 text-sm">
              <div className="flex justify-between">
                <span className="text-slate-500">Business</span>
                <span>E-commerce</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Currency</span>
                <span>BRL</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Reorder limit</span>
                <span>1,000</span>
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
              Agents
            </div>

            <div className="mt-4 space-y-3 text-sm text-slate-300">
              <div className="flex items-center gap-3"><Bot size={16}/>Planner</div>
              <div className="flex items-center gap-3"><Database size={16}/>Data Analyst</div>
              <div className="flex items-center gap-3"><Activity size={16}/>Research</div>
              <div className="flex items-center gap-3"><ShieldCheck size={16}/>Safety / Critic</div>
              <div className="flex items-center gap-3"><PlayCircle size={16}/>Action</div>
            </div>
          </div>
        </aside>

        <section className="min-w-0 space-y-5">
          <div className="rounded-3xl border border-slate-800 bg-slate-900/75 p-6 shadow-2xl shadow-black/20">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-indigo-300">
              <Sparkles size={16}/>
              Business Goal
            </div>

            <textarea
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              rows={4}
              className="w-full resize-none rounded-2xl border border-slate-800 bg-slate-950/70 p-4 text-base leading-7 text-white outline-none focus:border-indigo-500/60"
            />

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <span className="text-xs text-slate-500">
                Planner → Workers → Critic → Action Gate
              </span>

              <div className="flex gap-2">
                {(result || traceEvents.length) ? (
                  <button
                    onClick={reset}
                    disabled={running}
                    className="inline-flex items-center gap-2 rounded-xl border border-slate-700 px-4 py-2.5 text-sm font-semibold text-slate-300 disabled:opacity-50"
                  >
                    <RotateCcw size={16}/>
                    Reset
                  </button>
                ) : null}

                <button
                  onClick={handleRun}
                  disabled={running || !goal.trim()}
                  className="inline-flex items-center gap-2 rounded-xl bg-indigo-500 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/20 disabled:opacity-50"
                >
                  <PlayCircle size={17}/>
                  {running ? "Agents running..." : "Run Workflow"}
                </button>
              </div>
            </div>
          </div>

          {error ? (
            <div className="rounded-2xl border border-rose-500/25 bg-rose-500/10 p-4 text-sm text-rose-300">
              {error}
            </div>
          ) : null}

          {result ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard
                  label="Workflow"
                  value={result.status?.replaceAll("_", " ")}
                />
                <StatCard
                  label="Tasks"
                  value={result.task_results?.length || 0}
                />
                <StatCard
                  label="Worker time"
                  value={`${(totalDuration / 1000).toFixed(1)}s`}
                />
                <StatCard
                  label="Confidence"
                  value={`${Math.round(
                    (result.critic_report?.overall_confidence || 0) * 100
                  )}%`}
                />
              </div>

              <PlanPanel
                plan={result.plan}
                taskResults={result.task_results}
              />

              <TaskResults
                results={result.task_results}
              />

              <CriticPanel
                critic={result.critic_report}
              />

              <ActionCenter
                actionRequest={result.action_request}
                actionStatus={result.action_status}
              />
            </>
          ) : running ? (
            <div className="rounded-2xl border border-indigo-500/20 bg-indigo-500/5 p-6">
              <div className="flex items-center gap-3">
                <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-indigo-400" />
                <div>
                  <div className="font-semibold text-white">
                    Workflow executing
                  </div>
                  <div className="mt-1 text-sm text-slate-500">
                    Follow the live trace to watch SynapseOps progress.
                  </div>
                </div>
              </div>
            </div>
          ) : null}
        </section>

        <aside>
          <LiveTrace
            events={traceEvents}
            running={running}
            runId={runId}
          />
        </aside>
      </main>
    </div>
  );
}
