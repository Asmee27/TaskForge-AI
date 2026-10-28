import {
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";

import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bot,
  ChevronDown,
  ChevronUp,
  Database,
  FileText,
  Gauge,
  LayoutDashboard,
  PanelRightClose,
  PanelRightOpen,
  PlayCircle,
  RotateCcw,
  Settings2,
  ShieldCheck,
  Sparkles,
  Zap
} from "lucide-react";

import {
  createWorkspace,
  getWorkflowResult,
  listWorkspaces,
  openTrace,
  startWorkflow
} from "./api/client";

import ActionCenter from "./components/ActionCenter";
import AdminDashboard from "./components/AdminDashboard";
import Badge from "./components/Badge";
import CriticPanel from "./components/CriticPanel";
import DecisionIntelligencePanel from "./components/DecisionIntelligencePanel";
import DocumentsPanel from "./components/DocumentsPanel";
import LiveTrace from "./components/LiveTrace";
import PlanPanel from "./components/PlanPanel";
import StatCard from "./components/StatCard";
import TaskResults from "./components/TaskResults";
import WorkspaceManager from "./components/WorkspaceManager";

const defaultGoal =
  "Analyze inventory and recommend whether we should reorder 5000 units of low-stock products.";

function UsageMeter({ label, value = 0, limit = 0, suffix = "" }) {
  const safeLimit = Number(limit) || 0;
  const safeValue = Number(value) || 0;
  const percent = safeLimit > 0
    ? Math.min(100, (safeValue / safeLimit) * 100)
    : 0;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="font-medium text-slate-400">{label}</span>
        <span className="font-semibold text-slate-200">
          {suffix === "$" ? `$${safeValue.toFixed(4)} / $${safeLimit.toFixed(2)}` : `${safeValue.toLocaleString()} / ${safeLimit.toLocaleString()}${suffix}`}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div
          className={`h-full rounded-full transition-all ${percent > 90 ? "bg-rose-400" : percent > 70 ? "bg-amber-400" : "bg-indigo-400"}`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

function RuntimeSafetyPanel({ result, defaultExpanded = false }) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const budget = result?.runtime_budget || {};
  const limits = budget.limits || {};
  const circuitData = result?.runtime_circuit || {};
  const circuits = circuitData.circuits || {};

  const circuitNames = [
    ["llm", "LLM Provider"],
    ["sql", "SQL Engine"],
    ["research", "Research Engine"]
  ];

  return (
    <div className={`rounded-2xl border transition-all ${budget.exceeded ? "border-rose-500/40 bg-rose-500/10" : "border-slate-800/80 bg-slate-900/70"} p-4`}>
      <div className="flex items-center justify-between gap-3 cursor-pointer" onClick={() => setExpanded((v) => !v)}>
        <div className="flex items-center gap-3">
          <div className={`flex h-8 w-8 items-center justify-center rounded-xl ${budget.exceeded ? "bg-rose-500/20 text-rose-300" : "bg-indigo-500/20 text-indigo-300"}`}>
            <Gauge size={17} />
          </div>
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-white">
              Runtime Safety & Governance
              <Badge value={budget.exceeded ? "blocked" : "healthy"} />
            </div>
            <div className="mt-0.5 text-xs text-slate-400">
              Steps: {budget.workflow_steps ?? result?.workflow_steps ?? 0}/{limits.max_workflow_steps || 12} · Tools: {budget.tool_calls || 0}/{limits.max_tool_calls || 20} · Cost: ${Number(budget.estimated_cost_usd || 0).toFixed(4)}
            </div>
          </div>
        </div>

        <button type="button" className="flex items-center gap-1 text-xs font-medium text-slate-400 hover:text-white">
          {expanded ? "Collapse" : "Expand"}
          {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>

      {expanded ? (
        <div className="mt-4 pt-4 border-t border-slate-800/80 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <UsageMeter label="Workflow steps" value={budget.workflow_steps ?? result?.workflow_steps ?? 0} limit={limits.max_workflow_steps} />
            <UsageMeter label="Tool calls" value={budget.tool_calls} limit={limits.max_tool_calls} />
            <UsageMeter label="LLM calls" value={budget.llm_calls} limit={limits.max_llm_calls} />
            <UsageMeter label="Tokens" value={budget.total_tokens} limit={limits.max_total_tokens} />
            <UsageMeter label="Cost (USD)" value={budget.estimated_cost_usd} limit={limits.max_cost_usd} suffix="$" />
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
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
            <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-4">
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
      ) : null}
    </div>
  );
}

export default function App() {
  const [goal, setGoal] = useState(defaultGoal);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [traceEvents, setTraceEvents] = useState([]);
  const [runId, setRunId] = useState("");
  const [workspaces, setWorkspaces] = useState([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const [creatingWorkspace, setCreatingWorkspace] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [traceCollapsed, setTraceCollapsed] = useState(false);
  const [activeTab, setActiveTab] = useState("overview");

  const closeTraceRef = useRef(null);

  const selectedWorkspace = useMemo(
    () =>
      workspaces.find(
        (workspace) => workspace.workspace_id === selectedWorkspaceId
      ) || workspaces[0] || null,
    [workspaces, selectedWorkspaceId]
  );

  const totalDuration = useMemo(
    () =>
      (result?.task_results || []).reduce(
        (sum, item) => sum + (item.duration_ms || 0),
        0
      ),
    [result]
  );

  useEffect(() => {
    loadWorkspaces();
  }, []);

  async function loadWorkspaces(preferredId = null) {
    try {
      const items = await listWorkspaces();
      setWorkspaces(items);

      if (preferredId) {
        setSelectedWorkspaceId(preferredId);
      } else if (!selectedWorkspaceId && items.length) {
        setSelectedWorkspaceId(items[0].workspace_id);
      }
    } catch (err) {
      setError(
        err.response?.data?.detail?.message ||
          err.message ||
          "Could not load workspaces."
      );
    }
  }

  async function handleCreateWorkspace(payload) {
    setCreatingWorkspace(true);
    setError("");

    try {
      const workspace = await createWorkspace(payload);
      await loadWorkspaces(workspace.workspace_id);
      setGoal(
        "Analyze this business data and identify the most important operational insights and risks."
      );
      return workspace;
    } catch (err) {
      setError(
        err.response?.data?.detail?.message ||
          err.message ||
          "Workspace could not be created."
      );
      return null;
    } finally {
      setCreatingWorkspace(false);
    }
  }

  async function handleRun() {
    if (!goal.trim() || running || !selectedWorkspace) {
      return;
    }

    closeTraceRef.current?.();

    setRunning(true);
    setError("");
    setResult(null);
    setTraceEvents([]);
    setRunId("");
    setActiveTab("overview");

    try {
      const started = await startWorkflow({
        goal: goal.trim(),
        workspace: {
          workspace_id: selectedWorkspace.workspace_id,
          policies: selectedWorkspace.policies
        }
      });

      setRunId(started.run_id);

      closeTraceRef.current = openTrace(started.run_id, {
        onTrace: (event) => {
          setTraceEvents((current) => [...current, event]);
        },

        onComplete: async () => {
          try {
            const finalResult = await getWorkflowResult(started.run_id);
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

        onError: () => {}
      });
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
    setActiveTab("overview");
  }

  const isBlockedOrFailed = useMemo(() => {
    if (!result) return false;
    const status = String(result.status || "").toLowerCase();
    const verdict = String(result.critic_report?.verdict || "").toLowerCase();
    return (
      status === "blocked" ||
      status === "failed" ||
      status === "blocked_insufficient_evidence" ||
      verdict === "blocked"
    );
  }, [result]);

  const tabs = [
    { id: "overview", label: "Overview", icon: LayoutDashboard },
    { id: "analysis", label: "Analysis", icon: Database },
    { id: "safety", label: "Safety", icon: ShieldCheck },
    { id: "decision_intelligence", label: "Decision Intelligence", icon: BarChart3 },
    { id: "action", label: "Action", icon: Zap }
  ];

  return (
    <div className="min-h-screen text-slate-100 bg-slate-950 font-sans">
      <header className="border-b border-slate-800/80 bg-slate-950/80 backdrop-blur sticky top-0 z-30">
        <div className="mx-auto flex max-w-[1650px] items-center justify-between px-5 py-3.5">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-500 shadow-md shadow-indigo-500/20">
              <Sparkles size={19} />
            </div>

            <div>
              <div className="text-base font-bold tracking-tight text-white flex items-center gap-2">
                SynapseOps AI
                <span className="text-[10px] uppercase font-semibold px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
                  v2.0
                </span>
              </div>
              <div className="text-[11px] text-slate-400">
                Multi-Agent Autonomous Business Intelligence & Decision Governance
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Badge
              value={
                running
                  ? "running"
                  : result?.status || "ready"
              }
            />

            <button
              type="button"
              onClick={() => setTraceCollapsed((v) => !v)}
              title={traceCollapsed ? "Expand Live Trace" : "Collapse Live Trace"}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700/80 bg-slate-900/60 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-slate-600 hover:text-white transition-colors"
            >
              {traceCollapsed ? <PanelRightOpen size={15} /> : <PanelRightClose size={15} />}
              <span>{traceCollapsed ? "Show Trace" : "Hide Trace"}</span>
            </button>

            <button
              type="button"
              onClick={() => setShowAdmin((v) => !v)}
              title="Open admin dashboard"
              className="rounded-lg border border-slate-700 p-2 text-slate-300 hover:border-cyan-500/50 hover:text-cyan-200 transition-colors"
            >
              <Settings2 size={16} />
            </button>
          </div>
        </div>
      </header>

      {showAdmin ? (
        <AdminDashboard onBack={() => setShowAdmin(false)} />
      ) : (
        <main className={`mx-auto grid max-w-[1650px] gap-5 px-5 py-5 transition-all ${
          traceCollapsed
            ? "lg:grid-cols-[280px_minmax(0,1fr)]"
            : "lg:grid-cols-[280px_minmax(0,1fr)_340px]"
        }`}>
          {/* LEFT SIDEBAR: Workspace, Knowledge/RAG, Agents */}
          <aside className="space-y-4">
            <WorkspaceManager
              workspaces={workspaces}
              selectedWorkspace={selectedWorkspace}
              onSelect={setSelectedWorkspaceId}
              onCreate={handleCreateWorkspace}
              creating={creatingWorkspace}
            />

            <DocumentsPanel workspace={selectedWorkspace} />

            <div className="rounded-2xl border border-slate-800/80 bg-slate-900/60 p-4">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Agent Roster
              </div>

              <div className="mt-3 space-y-2.5 text-xs text-slate-300">
                <div className="flex items-center gap-2.5 rounded-lg bg-slate-950/40 p-2 border border-slate-800/50">
                  <Bot size={15} className="text-indigo-400" />
                  <div>
                    <div className="font-semibold text-slate-200">Planner</div>
                    <div className="text-[10px] text-slate-400">Goal decomposition</div>
                  </div>
                </div>

                <div className="flex items-center gap-2.5 rounded-lg bg-slate-950/40 p-2 border border-slate-800/50">
                  <Database size={15} className="text-cyan-400" />
                  <div>
                    <div className="font-semibold text-slate-200">Data Analyst</div>
                    <div className="text-[10px] text-slate-400">SQL & quantitative proof</div>
                  </div>
                </div>

                <div className="flex items-center gap-2.5 rounded-lg bg-slate-950/40 p-2 border border-slate-800/50">
                  <Activity size={15} className="text-emerald-400" />
                  <div>
                    <div className="font-semibold text-slate-200">Research</div>
                    <div className="text-[10px] text-slate-400">Market & web evidence</div>
                  </div>
                </div>

                <div className="flex items-center gap-2.5 rounded-lg bg-slate-950/40 p-2 border border-slate-800/50">
                  <ShieldCheck size={15} className="text-amber-400" />
                  <div>
                    <div className="font-semibold text-slate-200">Safety / Critic</div>
                    <div className="text-[10px] text-slate-400">Governance & verification</div>
                  </div>
                </div>

                <div className="flex items-center gap-2.5 rounded-lg bg-slate-950/40 p-2 border border-slate-800/50">
                  <PlayCircle size={15} className="text-rose-400" />
                  <div>
                    <div className="font-semibold text-slate-200">Action Gate</div>
                    <div className="text-[10px] text-slate-400">Human approval gate</div>
                  </div>
                </div>
              </div>
            </div>
          </aside>

          {/* MAIN CENTER CONTENT */}
          <section className="min-w-0 space-y-4">
            {/* Top Area: Business Goal */}
            <div className="rounded-2xl border border-slate-800/80 bg-slate-900/75 p-5 shadow-xl shadow-black/20">
              <div className="mb-1.5 flex items-center justify-between">
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-300">
                  <Sparkles size={15} />
                  Business Goal
                </div>
                <div className="text-[11px] text-slate-400">
                  {selectedWorkspace
                    ? `Workspace: ${selectedWorkspace.company_name || selectedWorkspace.workspace_id}`
                    : "Select workspace"}
                </div>
              </div>

              <textarea
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                rows={3}
                disabled={!selectedWorkspace}
                placeholder="Describe your business objective or decision question..."
                className="w-full resize-none rounded-xl border border-slate-800 bg-slate-950/80 p-3.5 text-sm leading-6 text-white outline-none focus:border-indigo-500/60 disabled:opacity-50"
              />

              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <span className="text-[11px] text-slate-400">
                  Pipeline: Goal → Planner → Workers → Critic → Action Gate
                </span>

                <div className="flex gap-2">
                  {result || traceEvents.length ? (
                    <button
                      onClick={reset}
                      disabled={running}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700/80 px-3.5 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800/60 disabled:opacity-50 transition-colors"
                    >
                      <RotateCcw size={14} />
                      Reset
                    </button>
                  ) : null}

                  <button
                    onClick={handleRun}
                    disabled={running || !goal.trim() || !selectedWorkspace}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-2 text-xs font-semibold text-white shadow-md shadow-indigo-500/20 hover:bg-indigo-400 disabled:opacity-50 transition-all"
                  >
                    <PlayCircle size={15} />
                    {running ? "Agents Executing..." : "Run Workflow"}
                  </button>
                </div>
              </div>
            </div>

            {selectedWorkspace?.source === "uploaded" ? (
              <div className="rounded-xl border border-cyan-500/30 bg-cyan-500/10 p-3.5">
                <div className="text-xs font-semibold text-cyan-200">
                  Uploaded workspace ready
                </div>
                <div className="mt-0.5 text-xs text-slate-300">
                  Isolated SQLite database configured. Analyst SQL will operate strictly on this business workspace.
                </div>
              </div>
            ) : null}

            {error ? (
              <div className="rounded-xl border border-rose-500/30 bg-rose-500/15 p-4 text-xs font-medium text-rose-300">
                {error}
              </div>
            ) : null}

            {/* RESULTS VIEW */}
            {result ? (
              <div className="space-y-4">
                {/* PROMINENT BLOCKED / FAILED STATE BANNER */}
                {isBlockedOrFailed ? (
                  <div className="rounded-2xl border-2 border-rose-500/60 bg-rose-500/15 p-4 shadow-xl shadow-rose-950/40">
                    <div className="flex items-start gap-3">
                      <AlertTriangle size={22} className="mt-0.5 shrink-0 text-rose-300" />
                      <div>
                        <div className="text-sm font-bold text-rose-100 uppercase tracking-wider flex items-center gap-2">
                          WORKFLOW ACTION BLOCKED / FAILED
                          <Badge value={result.status} />
                        </div>
                        <p className="mt-1 text-xs leading-5 text-rose-200/90">
                          {result.final_output || result.critic_report?.summary || "Workflow execution halted due to policy restrictions, safety findings, or failed worker tasks."}
                        </p>
                      </div>
                    </div>
                  </div>
                ) : null}

                {/* Compact KPI Cards */}
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  <StatCard
                    label="Status"
                    value={result.status?.replaceAll("_", " ")}
                  />
                  <StatCard
                    label="Tasks Executed"
                    value={result.task_results?.length || 0}
                  />
                  <StatCard
                    label="Worker Time"
                    value={`${(totalDuration / 1000).toFixed(1)}s`}
                  />
                  <StatCard
                    label="Overall Confidence"
                    value={`${Math.round((result.critic_report?.overall_confidence || 0) * 100)}%`}
                  />
                </div>

                {/* TABBED NAVIGATION */}
                <div className="border-b border-slate-800">
                  <nav className="flex space-x-1" aria-label="Tabs">
                    {tabs.map((tab) => {
                      const Icon = tab.icon;
                      const isActive = activeTab === tab.id;
                      return (
                        <button
                          key={tab.id}
                          onClick={() => setActiveTab(tab.id)}
                          className={`flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-semibold transition-all ${
                            isActive
                              ? "border-indigo-400 text-indigo-300 bg-indigo-500/10 rounded-t-lg"
                              : "border-transparent text-slate-400 hover:border-slate-700 hover:text-slate-200"
                          }`}
                        >
                          <Icon size={15} />
                          {tab.label}
                        </button>
                      );
                    })}
                  </nav>
                </div>

                {/* TAB CONTENTS */}
                {activeTab === "overview" && (
                  <div className="space-y-4">
                    {/* Key Recommendation / Final Output */}
                    <div className="rounded-2xl border border-slate-800/80 bg-slate-900/70 p-5">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-indigo-300">
                          <FileText size={15} />
                          Executive Recommendation
                        </div>
                        <Badge value={result.action_status || "completed"} />
                      </div>
                      <div className="whitespace-pre-wrap text-sm leading-6 text-slate-200 rounded-xl bg-slate-950/60 p-4 border border-slate-800/60">
                        {result.final_output || "No final recommendation generated."}
                      </div>
                    </div>

                    {/* Execution Plan */}
                    <PlanPanel plan={result.plan} taskResults={result.task_results} />
                  </div>
                )}

                {activeTab === "analysis" && (
                  <div className="space-y-4">
                    {/* Task Results Breakdown */}
                    <TaskResults results={result.task_results} />
                  </div>
                )}

                {activeTab === "safety" && (
                  <div className="space-y-4">
                    {/* Critic Panel */}
                    <CriticPanel critic={result.critic_report} />

                    {/* Compact / Expandable Runtime Safety Panel */}
                    <RuntimeSafetyPanel result={result} defaultExpanded={true} />
                  </div>
                )}

                {activeTab === "decision_intelligence" && (
                  <div className="space-y-4">
                    <DecisionIntelligencePanel runId={runId} />
                  </div>
                )}

                {activeTab === "action" && (
                  <div className="space-y-4">
                    <ActionCenter
                      actionRequest={result.action_request}
                      actionStatus={result.action_status}
                    />
                  </div>
                )}
              </div>
            ) : running ? (
              <div className="rounded-2xl border border-indigo-500/30 bg-indigo-500/10 p-6 text-center">
                <div className="flex flex-col items-center justify-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-indigo-500/20 text-indigo-300 animate-spin">
                    <Activity size={22} />
                  </div>
                  <div>
                    <div className="font-semibold text-white">
                      SynapseOps Multi-Agent Workflow Executing...
                    </div>
                    <div className="mt-1 text-xs text-slate-400">
                      Planner, Data Analyst, Research, Safety Critic, and Action Gate are collaborating.
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-2xl border border-dashed border-slate-800 bg-slate-900/30 p-8 text-center text-slate-500">
                <Bot size={32} className="mx-auto text-slate-600 mb-2" />
                <div className="text-sm font-medium text-slate-400">No active workflow run</div>
                <div className="text-xs text-slate-500 mt-1">Select a workspace and enter a business goal to begin analysis.</div>
              </div>
            )}
          </section>

          {/* RIGHT SIDEBAR: Collapsible Live Agent Trace */}
          {!traceCollapsed && (
            <aside className="transition-all">
              <LiveTrace
                events={traceEvents}
                running={running}
                runId={runId}
              />
            </aside>
          )}
        </main>
      )}
    </div>
  );
}
