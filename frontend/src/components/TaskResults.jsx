import { Clock3 } from "lucide-react";
import Badge from "./Badge";

export default function TaskResults({ results }) {
  if (!results?.length) return null;

  return (
    <section className="space-y-4">
      {results.map((item) => (
        <article
          key={item.task_id}
          className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <h3 className="font-semibold text-white">
                  Task {item.task_id} · {item.agent?.toUpperCase()}
                </h3>
                <Badge value={item.status} />
              </div>

              <p className="mt-1 text-sm text-slate-500">
                {item.description}
              </p>
            </div>

            <div className="flex items-center gap-1.5 text-xs text-slate-500">
              <Clock3 size={14} />
              {(item.duration_ms / 1000).toFixed(2)}s
            </div>
          </div>

          {item.output ? (
            <pre className="mt-4 whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-950/70 p-4 text-sm leading-6 text-slate-300">
              {item.output}
            </pre>
          ) : null}

          {item.error ? (
            <div className="mt-4 rounded-xl border border-rose-500/25 bg-rose-500/10 p-3 text-sm text-rose-300">
              {item.error}
            </div>
          ) : null}
        </article>
      ))}
    </section>
  );
}
