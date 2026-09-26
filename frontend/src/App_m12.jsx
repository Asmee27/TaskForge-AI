import {
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import {
  Activity,
  AlertTriangle,
  Bot,
  Database,
  Gauge,
  PlayCircle,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Settings2
} from "lucide-react";

import {
  createWorkspace,
  getWorkflowResult,
  listWorkspaces,
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
import WorkspaceManager from "./components/WorkspaceManager";
import DocumentsPanel from "./components/DocumentsPanel";
import DecisionIntelligencePanel from "./components/DecisionIntelligencePanel";
import AdminDashboard from "./components/AdminDashboard";

const defaultGoal =
  "Analyze inventory and recommend whether we should reorder 5000 units of low-stock products.";

function UsageMeter({ label, value = 0, limit = 0, suffix = "" }) {
  const safeLimit = Number(limit) || 0;
  const safeValue = Number(value) || 0;
  const percent = safeLimit > 0
    ? Math.min(100, (safeValue / safeLimit) * 100)
    : 0;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="font-medium text-slate-400">{label}</span>
        <span className="font-semibold text-slate-200">
          {suffix === "$" ? `$${safeValue.toFixed(4)} / $${safeLimit.toFixed(2)}` : `${safeValue.toLocaleString()} / ${safeLimit.toLocaleString()}${suffix}`}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div
          className="h-full rounded-full bg-indigo-400 transition-all"
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function RuntimeSafetyPanel({ result }) {
  const budget = result?.runtime_budget || {};
  const limits = budget.limits || {};
  const circuitData = result?.runtime_circuit || {};
  const circuits = circuitData.circuits || {};

  const circuitNames = [
    ["llm", "LLM Provider"],
    ["sql", "SQL"],
    ["research", "Research"]
  ];

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 font-semibold text-white">
            <Gauge size={18} className="text-indigo-300" />
            Runtime Safety
          </div>
          <div className="mt-1 text-xs text-slate-500">
            Hard budgets and workflow-scoped circuit breakers
          </div>
        </div>
        <Badge value={budget.exceeded ? "blocked" : "healthy"} />
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <UsageMeter label="Workflow steps" value={budget.workflow_steps ?? result?.workflow_steps ?? 0} limit={limits.max_workflow_steps} />
        <UsageMeter label="Tool calls" value={budget.tool_calls} limit={limits.max_tool_calls} />
        <UsageMeter label="LLM calls" value={budget.llm_calls} limit={limits.max_llm_calls} />
        <UsageMeter label="Tokens" value={budget.total_tokens} limit={limits.max_total_tokens} />
        <UsageMeter label="Estimated cost" value={budget.estimated_cost_usd} limit={limits.max_cost_usd} suffix="$" />
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        {circuitNames.map(([key, label]) => {
          const circuit = circuits[key] || {};
          const state = circuit.state || "closed";
          const open = state === "open";

          return (
            <div
              key={key}
              className={`rounded-xl border p-3 ${
                open
                  ? "border-rose-500/30 bg-rose-500/10"
                  : "border-emerald-500/20 bg-emerald-500/5"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-slate-300">{label}</span>
                <span className={`text-[11px] font-bold uppercase tracking-wider ${open ? "text-rose-300" : "text-emerald-300"}`}>
                  {state}
                </span>
              </div>
              <div className="mt-1 text-[11px] text-slate-500">
                Consecutive failures: {circuit.failure_count || 0}
              </div>
            </div>
          );
        })}
      </div>

      {budget.exceeded ? (
        <div className="mt-4 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle size={18} className="mt-0.5 shrink-0 text-rose-300" />
            <div>
              <div className="text-sm font-semibold text-rose-200">
                {budget.stop_code || "RUNTIME_BUDGET_EXCEEDED"}
              </div>
              <div className="mt-1 text-xs leading-5 text-rose-300/80">
                {budget.stop_reason || "The workflow was stopped by a runtime safety limit."}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function App() {
  const [goal, setGoal] =
    useState(defaultGoal);

  const [result, setResult] =
    useState(null);

  const [running, setRunning] =
    useState(false);

  const [error, setError] =
    useState("");

  const [traceEvents, setTraceEvents] =
    useState([]);

  const [runId, setRunId] =
    useState("");

  const [workspaces, setWorkspaces] =
    useState([]);

  const [
    selectedWorkspaceId,
    setSelectedWorkspaceId
  ] = useState("");

  const [
    creatingWorkspace,
    setCreatingWorkspace
  ] = useState(false);

  const [showAdmin, setShowAdmin] =
    useState(false);

  const closeTraceRef =
    useRef(null);

  const selectedWorkspace =
    useMemo(
      () =>
        workspaces.find(
          (workspace) =>
            workspace.workspace_id ===
            selectedWorkspaceId
        ) || workspaces[0] || null,
      [
        workspaces,
        selectedWorkspaceId
      ]
    );

  const totalDuration = useMemo(
    () =>
      (result?.task_results || []).reduce(
        (sum, item) =>
          sum + (item.duration_ms || 0),
        0
      ),
    [result]
  );

  useEffect(() => {
    loadWorkspaces();
  }, []);

  async function loadWorkspaces(
    preferredId = null
  ) {
    try {
      const items =
        await listWorkspaces();

      setWorkspaces(items);

      if (preferredId) {
        setSelectedWorkspaceId(
          preferredId
        );
      } else if (
        !selectedWorkspaceId &&
        items.length
      ) {
        setSelectedWorkspaceId(
          items[0].workspace_id
        );
      }
    } catch (err) {
      setError(
        err.response?.data?.detail
          ?.message ||
          err.message ||
          "Could not load workspaces."
      );
    }
  }

  async function handleCreateWorkspace(
    payload
  ) {
    setCreatingWorkspace(true);
    setError("");

    try {
      const workspace =
        await createWorkspace(payload);

      await loadWorkspaces(
        workspace.workspace_id
      );

      setGoal(
        "Analyze this business data and identify the most important operational insights and risks."
      );

      return workspace;
    } catch (err) {
      setError(
        err.response?.data?.detail
          ?.message ||
          err.message ||
          "Workspace could not be created."
      );
      return null;
    } finally {
      setCreatingWorkspace(false);
    }
  }

  async function handleRun() {
    if (
      !goal.trim() ||
      running ||
      !selectedWorkspace
    ) {
      return;
    }

    closeTraceRef.current?.();

    setRunning(true);
    setError("");
    setResult(null);
    setTraceEvents([]);
    setRunId("");

    try {
      const started =
        await startWorkflow({
          goal: goal.trim(),
          workspace: {
            workspace_id:
              selectedWorkspace.workspace_id,
            policies:
              selectedWorkspace.policies
          }
        });

      setRunId(started.run_id);

      closeTraceRef.current =
        openTrace(
          started.run_id,
          {
            onTrace: (event) => {
              setTraceEvents(
                (current) => [
                  ...current,
                  event
                ]
              );
            },

            onComplete: async () => {
              try {
                const finalResult =
                  await getWorkflowResult(
                    started.run_id
                  );

                setResult(
                  finalResult
                );
              } catch (err) {
                setError(
                  err.response?.data
                    ?.detail?.message ||
                    err.message ||
                    "Could not load workflow result."
                );
              } finally {
                setRunning(false);
              }
            },

            onError: () => {
              // Browser reconnects automatically
              // while SSE is still active.
            }
          }
        );
    } catch (err) {
      setRunning(false);
      setError(
        err.response?.data?.detail
          ?.message ||
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
        <div className="mx-auto flex max-w-[1550px] items-center justify-between px-5 py-4">
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

          <div className="flex items-center gap-3">
            <Badge
              value={
                running
                  ? "running"
                  : result?.status ||
                    "ready"
              }
            />
            <button
              type="button"
              onClick={() => setShowAdmin((value) => !value)}
              title="Open admin dashboard"
              className="rounded-lg border border-slate-700 p-2 text-slate-300 hover:border-cyan-500/50 hover:text-cyan-200"
            >
              <Settings2 size={16} />
            </button>
          </div>
        </div>
      </header>

      {showAdmin ? <AdminDashboard onBack={() => setShowAdmin(false)} /> : <main className="mx-auto grid max-w-[1550px] gap-5 px-5 py-6 lg:grid-cols-[285px_minmax(0,1fr)_350px]">
        <aside className="space-y-4">
          <WorkspaceManager
            workspaces={workspaces}
            selectedWorkspace={
              selectedWorkspace
            }
            onSelect={
              setSelectedWorkspaceId
            }
            onCreate={
              handleCreateWorkspace
            }
            creating={
              creatingWorkspace
            }
          />

          <DocumentsPanel workspace={selectedWorkspace} />

          <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">
              Agents
            </div>

            <div className="mt-4 space-y-3 text-sm text-slate-300">
              <div className="flex items-center gap-3">
                <Bot size={16} />
                Planner
              </div>

              <div className="flex items-center gap-3">
                <Database size={16} />
                Data Analyst
              </div>

              <div className="flex items-center gap-3">
                <Activity size={16} />
                Research
              </div>

              <div className="flex items-center gap-3">
                <ShieldCheck
                  size={16}
                />
                Safety / Critic
              </div>

              <div className="flex items-center gap-3">
                <PlayCircle
                  size={16}
                />
                Action
              </div>
            </div>
          </div>
        </aside>

        <section className="min-w-0 space-y-5">
          <div className="rounded-3xl border border-slate-800 bg-slate-900/75 p-6 shadow-2xl shadow-black/20">
            <div className="mb-1 flex items-center gap-2 text-sm font-medium text-indigo-300">
              <Sparkles size={16} />
              Business Goal
            </div>

            <div className="mb-3 text-xs text-slate-500">
              {selectedWorkspace
                ? `Analyzing ${selectedWorkspace.company_name || selectedWorkspace.workspace_id}`
                : "Create or select a data workspace first"}
            </div>

            <textarea
              value={goal}
              onChange={(event) =>
                setGoal(
                  event.target.value
                )
              }
              rows={4}
              disabled={
                !selectedWorkspace
              }
              className="w-full resize-none rounded-2xl border border-slate-800 bg-slate-950/70 p-4 text-base leading-7 text-white outline-none focus:border-indigo-500/60 disabled:opacity-50"
            />

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
              <span className="text-xs text-slate-500">
                Workspace → Planner → Workers → Critic → Action Gate
              </span>

              <div className="flex gap-2">
                {(result ||
                  traceEvents.length) ? (
                  <button
                    onClick={reset}
                    disabled={running}
                    className="inline-flex items-center gap-2 rounded-xl border border-slate-700 px-4 py-2.5 text-sm font-semibold text-slate-300 disabled:opacity-50"
                  >
                    <RotateCcw
                      size={16}
                    />
                    Reset
                  </button>
                ) : null}

                <button
                  onClick={handleRun}
                  disabled={
                    running ||
                    !goal.trim() ||
                    !selectedWorkspace
                  }
                  className="inline-flex items-center gap-2 rounded-xl bg-indigo-500 px-5 py-2.5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/20 disabled:opacity-50"
                >
                  <PlayCircle
                    size={17}
                  />

                  {running
                    ? "Agents running..."
                    : "Run Workflow"}
                </button>
              </div>
            </div>
          </div>

          {selectedWorkspace?.source ===
          "uploaded" ? (
            <div className="rounded-2xl border border-cyan-500/20 bg-cyan-500/5 p-4">
              <div className="text-sm font-semibold text-cyan-200">
                Uploaded data ready
              </div>

              <div className="mt-1 text-xs leading-5 text-slate-400">
                SynapseOps created a workspace-specific SQLite database, profiled the uploaded tables, detected likely semantic roles, and will route Analyst SQL only to this business workspace.
              </div>
            </div>
          ) : null}

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
                  value={
                    result.status
                      ?.replaceAll(
                        "_",
                        " "
                      )
                  }
                />

                <StatCard
                  label="Tasks"
                  value={
                    result.task_results
                      ?.length || 0
                  }
                />

                <StatCard
                  label="Worker time"
                  value={`${(
                    totalDuration / 1000
                  ).toFixed(1)}s`}
                />

                <StatCard
                  label="Confidence"
                  value={`${Math.round(
                    (
                      result
                        .critic_report
                        ?.overall_confidence ||
                      0
                    ) * 100
                  )}%`}
                />
              </div>

              <RuntimeSafetyPanel result={result} />

              <PlanPanel
                plan={result.plan}
                taskResults={
                  result.task_results
                }
              />

              <TaskResults
                results={
                  result.task_results
                }
              />

              <DecisionIntelligencePanel runId={runId} />

              <CriticPanel
                critic={
                  result.critic_report
                }
              />

              <ActionCenter
                actionRequest={
                  result.action_request
                }
                actionStatus={
                  result.action_status
                }
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
      </main>}
    </div>
  );
}
