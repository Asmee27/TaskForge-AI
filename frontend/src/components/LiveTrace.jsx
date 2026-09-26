import {
  Activity,
  Bot,
  Database,
  Search,
  ShieldCheck,
  Wrench,
  AlertTriangle,
  CheckCircle2,
  XCircle
} from "lucide-react";

function iconFor(event) {
  const scope = event.scope;

  if (scope === "PLANNER") return Bot;
  if (scope === "SQL") return Database;
  if (scope === "TOOL" || scope === "CACHE") return Wrench;
  if (scope === "CRITIC") return ShieldCheck;
  if (scope === "RESEARCH") return Search;
  if (scope === "GUARD" || scope === "RETRY") return AlertTriangle;
  if (event.status === "failed") return XCircle;
  if (event.status === "completed") return CheckCircle2;

  return Activity;
}

function statusClass(status) {
  if (status === "failed") return "text-rose-400";
  if (status === "blocked") return "text-rose-400";
  if (status === "warning") return "text-amber-400";
  if (status === "completed") return "text-emerald-400";
  return "text-indigo-300";
}

export default function LiveTrace({
  events,
  running,
  runId
}) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <Activity size={17} className="text-cyan-300" />
            <h2 className="font-semibold text-white">
              Live Agent Trace
            </h2>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {runId ? `Run ${runId}` : "Waiting for workflow"}
          </p>
        </div>

        {running ? (
          <span className="flex items-center gap-2 text-xs text-indigo-300">
            <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
            LIVE
          </span>
        ) : null}
      </div>

      <div className="mt-4 max-h-[650px] space-y-2 overflow-y-auto pr-1">
        {!events.length ? (
          <p className="text-sm leading-6 text-slate-500">
            Start a workflow and Planner, tools, workers, Critic and Action Gate events will appear here in real time.
          </p>
        ) : (
          events.map((event) => {
            const Icon = iconFor(event);

            return (
              <div
                key={event.event_id}
                className="rounded-xl border border-slate-800 bg-slate-950/55 p-3"
              >
                <div className="flex gap-3">
                  <div className={`mt-0.5 ${statusClass(event.status)}`}>
                    <Icon size={16} />
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold tracking-wide text-slate-300">
                        {event.scope}
                      </span>
                      <span className="text-[10px] text-slate-600">
                        {new Date(event.timestamp).toLocaleTimeString()}
                      </span>
                    </div>

                    <p className="mt-1 break-words text-xs leading-5 text-slate-400">
                      {event.message}
                    </p>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
