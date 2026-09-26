from __future__ import annotations

import asyncio
import inspect
import json
import os
import queue
import threading
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.api.models import (
    ApiMessage,
    ApprovalRequest,
    RejectionRequest,
    WorkflowRunRequest,
    WorkflowRunResponse,
)
from app.api.trace_store import (
    append_trace_event,
    get_trace_events,
    is_finished,
    mark_finished,
)
from app.workflows.business_workflow import build_business_workflow

from app.core.logging import (
    log_event,
    new_run_id,
    set_run_id,
    subscribe,
    unsubscribe,
)

from app.tools.action_tools import (
    approve_action_request,
    get_action_request,
    reject_action_request,
)

from app.agents.action_agent import execute_action_request


APP_NAME = "SynapseOps AI API"
APP_VERSION = "0.9.0"

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="SynapseOps AI API with real-time SSE agent tracing.",
)

frontend_origins = [
    origin.strip()
    for origin in os.getenv(
        "SYNAPSEOPS_FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

workflow_graph = build_business_workflow()

_run_results: dict[str, dict[str, Any]] = {}
_run_errors: dict[str, str] = {}
_run_lock = threading.Lock()


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _invoke_with_supported_kwargs(
    fn: Callable[..., Any],
    **candidates: Any,
) -> Any:
    signature = inspect.signature(fn)
    supported = {}

    for name in signature.parameters:
        if name in candidates:
            supported[name] = candidates[name]

    aliases = {
        "approved_by": candidates.get("actor"),
        "rejected_by": candidates.get("actor"),
        "executed_by": candidates.get("actor"),
        "reason": candidates.get("reason"),
        "rejection_reason": candidates.get("reason"),
        "request_id": candidates.get("request_id"),
        "action_request_id": candidates.get("request_id"),
    }

    for name, value in aliases.items():
        if (
            name in signature.parameters
            and name not in supported
            and value is not None
        ):
            supported[name] = value

    return fn(**supported)


def _trace_collector(
    run_id: str,
    channel: queue.Queue,
) -> None:
    while True:
        event = channel.get()

        if event is None:
            return

        append_trace_event(
            run_id,
            event,
        )


def _execute_workflow_background(
    run_id: str,
    request: WorkflowRunRequest,
) -> None:

    # Context variables do not automatically propagate to a new
    # native thread, so explicitly bind this workflow thread.
    set_run_id(run_id)

    channel = subscribe(run_id)

    collector = threading.Thread(
        target=_trace_collector,
        args=(run_id, channel),
        daemon=True,
    )
    collector.start()

    try:
        log_event(
            "WORKFLOW",
            "Business workflow started",
        )

        workspace = dict(request.workspace or {})

        result = workflow_graph.invoke(
            {
                "goal": request.goal.strip(),
                "workspace": workspace,
                "workflow_steps": 0,
                "status": "running",
            },
            config={
                "recursion_limit": 30,
            },
        )

        with _run_lock:
            _run_results[run_id] = result

        log_event(
            "RUN",
            "Business workflow finished",
        )

    except Exception as exc:
        message = (
            f"{type(exc).__name__}: {exc}"
        )

        with _run_lock:
            _run_errors[run_id] = message

        log_event(
            "RUN",
            f"Business workflow failed | {message}",
        )

    finally:
        mark_finished(run_id)

        # Give the collector the terminal event before shutdown.
        channel.put(None)
        collector.join(timeout=1.0)
        unsubscribe(run_id, channel)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": APP_NAME,
        "version": APP_VERSION,
        "workflow": "ready",
        "live_trace": "sse",
    }


@app.post("/api/workflows/start")
def start_workflow(
    request: WorkflowRunRequest,
) -> dict[str, Any]:

    run_id = new_run_id()

    thread = threading.Thread(
        target=_execute_workflow_background,
        args=(run_id, request),
        daemon=True,
    )
    thread.start()

    return {
        "ok": True,
        "run_id": run_id,
        "status": "running",
        "trace_url": f"/api/workflows/{run_id}/events",
        "result_url": f"/api/workflows/{run_id}/result",
    }


@app.get("/api/workflows/{run_id}/events")
async def stream_events(run_id: str):

    async def event_generator():

        sent = 0

        while True:
            events = get_trace_events(run_id)

            while sent < len(events):
                event = events[sent]
                sent += 1

                yield (
                    "event: trace\n"
                    f"data: {json.dumps(event, default=str)}\n\n"
                )

            if is_finished(run_id):
                yield (
                    "event: complete\n"
                    f"data: {json.dumps({'run_id': run_id})}\n\n"
                )
                return

            # SSE heartbeat keeps proxies/browser connection alive.
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get(
    "/api/workflows/{run_id}/result",
    response_model=WorkflowRunResponse,
)
def workflow_result(
    run_id: str,
) -> WorkflowRunResponse:

    with _run_lock:
        result = _run_results.get(run_id)
        error = _run_errors.get(run_id)

    if error:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "WORKFLOW_EXECUTION_FAILED",
                "message": error,
            },
        )

    if result is None:
        if is_finished(run_id):
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "WORKFLOW_RESULT_MISSING",
                    "message": "Workflow finished without a result.",
                },
            )

        raise HTTPException(
            status_code=202,
            detail={
                "code": "WORKFLOW_STILL_RUNNING",
                "message": "Workflow is still running.",
            },
        )

    return WorkflowRunResponse(
        status=result.get("status", "unknown"),
        plan=result.get("plan"),
        task_results=result.get("task_results", []),
        critic_report=result.get("critic_report"),
        action_request=result.get("action_request"),
        action_status=result.get("action_status"),
        final_output=result.get("final_output", ""),
        workflow_steps=result.get("workflow_steps", 0),
    )


# Keep the synchronous Milestone-7 route for Swagger/testing.
@app.post(
    "/api/workflows/run",
    response_model=WorkflowRunResponse,
)
def run_workflow(
    request: WorkflowRunRequest,
) -> WorkflowRunResponse:

    run_id = new_run_id()
    set_run_id(run_id)

    channel = subscribe(run_id)

    collector = threading.Thread(
        target=_trace_collector,
        args=(run_id, channel),
        daemon=True,
    )
    collector.start()

    try:
        log_event(
            "WORKFLOW",
            "Business workflow started",
        )

        workspace = dict(request.workspace or {})

        result = workflow_graph.invoke(
            {
                "goal": request.goal.strip(),
                "workspace": workspace,
                "workflow_steps": 0,
                "status": "running",
            },
            config={"recursion_limit": 30},
        )

        log_event(
            "RUN",
            "Business workflow finished",
        )

        return WorkflowRunResponse(
            status=result.get("status", "unknown"),
            plan=result.get("plan"),
            task_results=result.get("task_results", []),
            critic_report=result.get("critic_report"),
            action_request=result.get("action_request"),
            action_status=result.get("action_status"),
            final_output=result.get("final_output", ""),
            workflow_steps=result.get("workflow_steps", 0),
        )

    finally:
        mark_finished(run_id)
        channel.put(None)
        collector.join(timeout=1.0)
        unsubscribe(run_id, channel)


@app.get(
    "/api/actions/{request_id}",
    response_model=ApiMessage,
)
def read_action(request_id: str) -> ApiMessage:
    action = _invoke_with_supported_kwargs(
        get_action_request,
        request_id=request_id,
    )

    if action is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ACTION_NOT_FOUND",
                "message": "Action request was not found.",
            },
        )

    return ApiMessage(
        message="Action request loaded.",
        data=_dump(action),
    )


@app.post(
    "/api/actions/{request_id}/approve",
    response_model=ApiMessage,
)
def approve_action(
    request_id: str,
    body: ApprovalRequest,
) -> ApiMessage:
    try:
        action = _invoke_with_supported_kwargs(
            approve_action_request,
            request_id=request_id,
            actor=body.actor,
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACTION_APPROVAL_REJECTED",
                "message": str(exc),
            },
        ) from exc

    return ApiMessage(
        message="Action approved.",
        data=_dump(action),
    )


@app.post(
    "/api/actions/{request_id}/reject",
    response_model=ApiMessage,
)
def reject_action(
    request_id: str,
    body: RejectionRequest,
) -> ApiMessage:
    try:
        action = _invoke_with_supported_kwargs(
            reject_action_request,
            request_id=request_id,
            actor=body.actor,
            reason=body.reason,
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACTION_REJECTION_REJECTED",
                "message": str(exc),
            },
        ) from exc

    return ApiMessage(
        message="Action rejected.",
        data=_dump(action),
    )


@app.post(
    "/api/actions/{request_id}/execute",
    response_model=ApiMessage,
)
def execute_approved_action(
    request_id: str,
) -> ApiMessage:
    try:
        result = _invoke_with_supported_kwargs(
            execute_action_request,
            request_id=request_id,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ACTION_EXECUTION_BLOCKED",
                "message": str(exc),
            },
        ) from exc

    return ApiMessage(
        message="Approved action executed.",
        data=_dump(result),
    )
