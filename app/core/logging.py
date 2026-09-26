from __future__ import annotations

import contextvars
import queue
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


_run_id = contextvars.ContextVar(
    "run_id",
    default="--------",
)

_subscribers: dict[str, list[queue.Queue]] = defaultdict(list)
_lock = threading.Lock()


def new_run_id() -> str:
    rid = uuid.uuid4().hex[:8]
    _run_id.set(rid)
    return rid


def set_run_id(run_id: str) -> None:
    _run_id.set(run_id)


def get_run_id() -> str:
    return _run_id.get()


def _classify_event(scope: str, message: str) -> tuple[str, str]:
    scope_upper = scope.upper()
    text = message.lower()

    if "failed" in text or "error" in text:
        status = "failed"
    elif "blocked" in text or "guard" in scope_upper:
        status = "blocked"
    elif "completed" in text or "success" in text or "finished" in text:
        status = "completed"
    elif "retry" in text or "rate limit" in text:
        status = "warning"
    else:
        status = "running"

    if scope_upper == "PLANNER":
        event_type = "planner"
    elif scope_upper in {"AGENT", "WORKER", "ROUTER"}:
        event_type = "agent"
    elif scope_upper in {"TOOL", "SQL", "CACHE"}:
        event_type = "tool"
    elif scope_upper == "CRITIC":
        event_type = "critic"
    elif scope_upper == "ACTION":
        event_type = "action"
    elif scope_upper in {"GUARD", "RETRY"}:
        event_type = "safety"
    else:
        event_type = "workflow"

    return event_type, status


def subscribe(run_id: str) -> queue.Queue:
    channel: queue.Queue = queue.Queue()

    with _lock:
        _subscribers[run_id].append(channel)

    return channel


def unsubscribe(run_id: str, channel: queue.Queue) -> None:
    with _lock:
        channels = _subscribers.get(run_id, [])

        if channel in channels:
            channels.remove(channel)

        if not channels:
            _subscribers.pop(run_id, None)


def publish_event(
    scope: str,
    message: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:

    run_id = get_run_id()
    event_type, status = _classify_event(scope, message)

    event = {
        "event_id": uuid.uuid4().hex,
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "timestamp_ms": int(time.time() * 1000),
        "scope": scope.upper(),
        "type": event_type,
        "status": status,
        "message": message,
        "metadata": metadata or {},
    }

    with _lock:
        channels = list(
            _subscribers.get(run_id, [])
        )

    for channel in channels:
        try:
            channel.put_nowait(event)
        except queue.Full:
            pass

    return event


def log_event(scope: str, message: str) -> None:
    print(
        f"[{scope}][{get_run_id()}] {message}",
        flush=True,
    )
    publish_event(scope, message)
