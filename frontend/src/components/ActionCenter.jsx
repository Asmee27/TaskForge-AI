import { useState } from "react";
import {
  CheckCircle2,
  LockKeyhole,
  Play,
  XCircle
} from "lucide-react";

import Badge from "./Badge";
import {
  approveAction,
  rejectAction,
  executeAction
} from "../api/client";

export default function ActionCenter({
  actionRequest,
  actionStatus,
  onChanged
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  if (!actionRequest && !actionStatus) return null;

  const requestId = actionRequest?.request_id;

  async function runAction(fn) {
    try {
      setBusy(true);
      setMessage("");
      const result = await fn();
      setMessage(result.message || "Action updated.");
      onChanged?.();
    } catch (error) {
      setMessage(
        error.response?.data?.detail?.message ||
          error.message ||
          "Action operation failed."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-violet-500/30 bg-violet-500/10 text-violet-300">
            <LockKeyhole size={20} />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-white">
              Action Centre
            </h2>
            <p className="text-sm text-slate-500">
              Human-in-the-loop execution gate
            </p>
          </div>
        </div>

        <Badge value={actionStatus || "no_action"} />
      </div>

      {actionRequest ? (
        <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-4">
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <span className="text-slate-500">Request ID</span>
              <div className="mt-1 break-all text-slate-200">
                {requestId}
              </div>
            </div>

            <div>
              <span className="text-slate-500">Action</span>
              <div className="mt-1 text-slate-200">
                {actionRequest.action_type?.replaceAll("_", " ")}
              </div>
            </div>
          </div>

          <p className="mt-4 text-sm leading-6 text-slate-300">
            {actionRequest.description}
          </p>

          <pre className="mt-3 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-400">
            {JSON.stringify(actionRequest.payload, null, 2)}
          </pre>

          {actionStatus === "waiting_for_approval" ? (
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                disabled={busy}
                onClick={() =>
                  runAction(() =>
                    approveAction(requestId)
                  )
                }
                className="inline-flex items-center gap-2 rounded-xl bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:opacity-50"
              >
                <CheckCircle2 size={17} />
                Approve
              </button>

              <button
                disabled={busy}
                onClick={() => {
                  const reason = window.prompt(
                    "Reason for rejection:"
                  );
                  if (!reason) return;

                  runAction(() =>
                    rejectAction(requestId, reason)
                  );
                }}
                className="inline-flex items-center gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-2 text-sm font-semibold text-rose-300 transition hover:bg-rose-500/20 disabled:opacity-50"
              >
                <XCircle size={17} />
                Reject
              </button>
            </div>
          ) : null}

          {actionStatus === "ready_for_execution" ? (
            <button
              disabled={busy}
              onClick={() =>
                runAction(() =>
                  executeAction(requestId)
                )
              }
              className="mt-4 inline-flex items-center gap-2 rounded-xl bg-indigo-500 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-400 disabled:opacity-50"
            >
              <Play size={17} />
              Execute approved action
            </button>
          ) : null}
        </div>
      ) : (
        <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/50 p-4 text-sm leading-6 text-slate-400">
          {actionStatus === "blocked_insufficient_evidence"
            ? "No action request was created because the evidence was not strong enough for safe execution."
            : "No executable action was proposed for this workflow."}
        </div>
      )}

      {message ? (
        <div className="mt-3 rounded-xl border border-slate-800 bg-slate-950/50 p-3 text-sm text-slate-300">
          {message}
        </div>
      ) : null}
    </section>
  );
}
