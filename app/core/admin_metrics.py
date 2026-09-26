from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.persistence import connection, initialize_persistence
from app.core.workspace_service import list_workspaces


REPORT_PATH = Path(__file__).resolve().parents[2] / "reports" / "m13" / "evaluation_results.json"


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def build_admin_overview() -> dict[str, Any]:
    initialize_persistence()
    workspaces = list_workspaces()

    with connection() as conn:
        runs = [dict(row) for row in conn.execute(
            """
            SELECT run_id, goal, workspace_id, status, current_stage,
                   created_at, updated_at, finished_at, state_json
            FROM workflow_runs
            ORDER BY updated_at DESC
            LIMIT 1000
            """
        ).fetchall()]
        audits = [dict(row) for row in conn.execute(
            """
            SELECT id, run_id, event_type, payload_json, created_at
            FROM workflow_audit_events
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()]
        documents = [dict(row) for row in conn.execute(
            """
            SELECT document_id, workspace_id, filename, uploaded_at,
                   page_count, chunk_count, status, error
            FROM workspace_documents
            ORDER BY uploaded_at DESC
            LIMIT 1000
            """
        ).fetchall()]

    status_counts: dict[str, int] = {}
    steps: list[float] = []
    tools: list[float] = []
    llm_calls: list[float] = []
    tokens: list[float] = []
    costs: list[float] = []
    budget_stops: dict[str, int] = {}
    circuit_events = []

    for run in runs:
        status = str(run.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        state = _json(run.get("state_json"))
        budget = _json(state.get("runtime_budget"))
        if budget:
            for values, key in (
                (steps, "workflow_steps"),
                (tools, "tool_calls"),
                (llm_calls, "llm_calls"),
                (tokens, "total_tokens"),
                (costs, "estimated_cost_usd"),
            ):
                value = budget.get(key)
                if isinstance(value, (int, float)):
                    values.append(float(value))
            stop_code = budget.get("stop_code")
            if budget.get("exceeded") and stop_code:
                budget_stops[stop_code] = budget_stops.get(stop_code, 0) + 1

        circuits = _json(state.get("runtime_circuit")).get("circuits", {})
        for name, circuit in circuits.items():
            if circuit.get("state") == "open":
                circuit_events.append({
                    "run_id": run["run_id"],
                    "component": name,
                    "event": "open",
                    "reason": circuit.get("opened_reason"),
                    "updated_at": run.get("updated_at"),
                })

    recent_audit_events = []
    for event in audits[:50]:
        payload = _json(event.get("payload_json"))
        recent_audit_events.append({
            "id": event["id"],
            "run_id": event["run_id"],
            "event_type": event["event_type"],
            "scope": payload.get("scope"),
            "status": payload.get("status"),
            "message": payload.get("message") or str(payload),
            "created_at": event["created_at"].isoformat() if hasattr(event["created_at"], "isoformat") else str(event["created_at"]),
        })

    rag_documents = [
        {
            **document,
            "uploaded_at": document["uploaded_at"].isoformat() if hasattr(document["uploaded_at"], "isoformat") else str(document["uploaded_at"]),
        }
        for document in documents
    ]

    m13_summary: dict[str, Any] | None = None
    if REPORT_PATH.exists():
        try:
            report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
            m13_summary = {
                "evaluation_id": report.get("evaluation_id"),
                "provider_mode": report.get("provider_mode"),
                "aggregate_metrics": report.get("aggregate_metrics", {}),
                "failure_breakdown_by_category": report.get("failure_breakdown_by_category", {}),
            }
        except (OSError, json.JSONDecodeError):
            m13_summary = None

    return {
        "workspaces": workspaces,
        "workflow_runs": {
            "total": len(runs),
            "status_counts": status_counts,
            "recent": [
                {
                    "run_id": run["run_id"],
                    "goal": run["goal"],
                    "workspace_id": run["workspace_id"],
                    "status": run["status"],
                    "current_stage": run["current_stage"],
                    "updated_at": run["updated_at"].isoformat() if hasattr(run["updated_at"], "isoformat") else str(run["updated_at"]),
                }
                for run in runs[:20]
            ],
        },
        "averages": {
            "workflow_steps": _average(steps),
            "tool_calls": _average(tools),
            "llm_calls": _average(llm_calls),
            "tokens": _average(tokens),
            "estimated_cost_usd": _average(costs),
        },
        "budget_stops": budget_stops,
        "circuit_events": circuit_events,
        "rag_documents": rag_documents,
        "recent_audit_events": recent_audit_events,
        "m13_summary": m13_summary,
    }
