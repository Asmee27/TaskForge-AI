import { BrainCircuit, Database, Search } from "lucide-react";
import Badge from "./Badge";

function AgentIcon({ agent }) {
  if (agent === "analyst") return <Database size={18} />;
  if (agent === "research") return <Search size={18} />;
  return <BrainCircuit size={18} />;
}

export default function PlanPanel({ plan, taskResults }) {
  if (!plan?.tasks?.length) return null;

  const resultMap = new Map(
    (taskResults || []).map((item) => [item.task_id, item])
  );

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-white">
            Execution Plan
          </h2>
          <p className="text-sm text-slate-500">
            Planner-generated task graph
          </p>
        </div>
        <Badge value="planned" />
      </div>

      <div className="space-y-3">
        {plan.tasks.map((task, index) => {
          const result = resultMap.get(task.id);

          return (
            <div
              key={task.id}
              className="relative rounded-xl border border-slate-800 bg-slate-950/50 p-4"
            >
              {index < plan.tasks.length - 1 ? (
                <div className="absolute left-[29px] top-[55px] h-[calc(100%-20px)] w-px bg-slate-800" />
              ) : null}

              <div className="flex gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-indigo-500/30 bg-indigo-500/10 text-indigo-300">
                  <AgentIcon agent={task.agent} />
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-white">
                      {task.agent?.toUpperCase()}
                    </span>
                    {result ? <Badge value={result.status} /> : <Badge value="pending" />}
                    <span className="text-xs text-slate-600">
                      Confidence {Math.round((task.confidence || 0) * 100)}%
                    </span>
                  </div>

                  <p className="mt-2 text-sm leading-6 text-slate-300">
                    {task.description}
                  </p>

                  {task.depends_on?.length ? (
                    <p className="mt-2 text-xs text-slate-500">
                      Depends on tasks: {task.depends_on.join(", ")}
                    </p>
                  ) : null}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
