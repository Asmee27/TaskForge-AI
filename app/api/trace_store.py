from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from app.core.persistence import append_audit_event, get_audit_events, load_run
from app.core.redis_runtime import cache_trace_event, get_cached_trace_events


_lock = threading.Lock()
_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
_finished: set[str] = set()


def append_trace_event(run_id: str, event: dict[str, Any]) -> None:
    with _lock:
        _events[run_id].append(event)
    cache_trace_event(run_id, event)
    try:
        append_audit_event(run_id, "trace", event)
    except Exception:
        pass


def get_trace_events(run_id: str) -> list[dict[str, Any]]:
    with _lock:
        memory = list(_events.get(run_id, []))
    if memory:
        return memory

    cached = get_cached_trace_events(run_id)
    if cached:
        return cached

    try:
        rows = get_audit_events(run_id)
        return [
            row["payload_json"]
            for row in rows
            if row.get("event_type") == "trace"
        ]
    except Exception:
        return []


def mark_finished(run_id: str) -> None:
    with _lock:
        _finished.add(run_id)


def is_finished(run_id: str) -> bool:
    with _lock:
        if run_id in _finished:
            return True
    try:
        row = load_run(run_id)
        return bool(
            row
            and row.get("status")
            in {
                "completed",
                "completed_degraded",
                "completed_with_errors",
                "failed",
                "blocked",
            }
        )
    except Exception:
        return False
