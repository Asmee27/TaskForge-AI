from __future__ import annotations

import asyncio
import inspect
import json
import os
import queue
import threading
from typing import Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from app.api.models import ApiMessage, ApprovalRequest, DocumentQueryRequest, RejectionRequest, SimulationRequest, WorkflowRunRequest, WorkflowRunResponse
from app.api.trace_store import append_trace_event, get_trace_events, is_finished, mark_finished
from app.workflows.business_workflow import build_business_workflow
from app.core.logging import log_event, new_run_id, set_run_id, subscribe, unsubscribe
from app.core.workspace_runtime import set_workspace_db_path
from app.core.workspace_service import create_workspace_from_files, get_workspace, get_workspace_database_path, list_workspaces, workspace_agent_payload
from app.tools.action_tools import approve_action_request, get_action_request, reject_action_request
from app.agents.action_agent import execute_action_request
from app.core.persistence import create_run, load_run, load_state, list_incomplete_runs, mark_run_error, persistence_health, save_checkpoint
from app.core.redis_runtime import acquire_run_lease, create_lease_owner, force_release_run_lease, refresh_run_lease, release_run_lease, redis_health
from app.core.runtime_budget import RuntimeBudget, activate_budget, deactivate_budget
from app.core.runtime_circuit import RuntimeCircuitBreaker, activate_circuit, deactivate_circuit
from app.core.document_rag import ask_documents, list_documents, retrieve_passages, store_pdf_document
from app.core.decision_intelligence import build_intelligence, build_report_pdf, simulate_scenario
from app.core.admin_metrics import build_admin_overview

APP_NAME = "SynapseOps AI API"
APP_VERSION = "0.12.1"

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="SynapseOps AI API with multi-business workspace upload, workspace-aware analysis, human approval and live SSE tracing.",
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
_active_runs: set[str] = set()


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _invoke_with_supported_kwargs(fn: Callable[..., Any], **candidates: Any) -> Any:
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
        if name in signature.parameters and name not in supported and value is not None:
            supported[name] = value
    return fn(**supported)


def _trace_collector(run_id: str, channel: queue.Queue) -> None:
    while True:
        event = channel.get()
        if event is None:
            return
        append_trace_event(run_id, event)


def _resolve_request_workspace(request: WorkflowRunRequest) -> dict[str, Any]:
    incoming = dict(request.workspace or {})
    workspace_id = incoming.get("workspace_id") or "olist-demo"
    registered = workspace_agent_payload(workspace_id)
    if incoming.get("policies"):
        registered["policies"] = incoming["policies"]
    return registered


def _bind_workspace_database(workspace: dict[str, Any]) -> None:
    db_path = get_workspace_database_path(workspace["workspace_id"])
    set_workspace_db_path(db_path)


def _merge_update(state: dict[str, Any], update: dict[str, Any]) -> tuple[dict[str, Any], str]:
    merged = dict(state)
    stage = "unknown"
    for node_name, delta in update.items():
        stage = node_name
        if isinstance(delta, dict):
            merged.update(delta)
    return merged, stage


def _run_graph_with_checkpoints(*, run_id: str, initial_state: dict[str, Any]) -> dict[str, Any]:
    state = dict(initial_state)

    budget = RuntimeBudget.from_dict(state.get("runtime_budget"))
    circuit = RuntimeCircuitBreaker.from_dict(state.get("runtime_circuit"))

    budget.workflow_steps = max(
        budget.workflow_steps,
        int(state.get("workflow_steps", 0) or 0),
    )

    state["runtime_budget"] = budget.to_dict()
    state["runtime_circuit"] = circuit.to_dict()

    budget_context_token = activate_budget(budget)
    circuit_context_token = activate_circuit(circuit)

    try:
        for update in workflow_graph.stream(
            state,
            config={"recursion_limit": 30},
            stream_mode="updates",
        ):
            state, stage = _merge_update(state, update)

            current_workflow_steps = int(state.get("workflow_steps", 0) or 0)
            budget.workflow_steps = max(budget.workflow_steps, current_workflow_steps)

            # Persist the latest in-memory safety state at every node.
            state["runtime_budget"] = budget.to_dict()
            state["runtime_circuit"] = circuit.to_dict()

            save_checkpoint(run_id=run_id, state=state, stage=stage)

            budget_snapshot = budget.to_dict()
            circuit_snapshot = circuit.to_dict()
            open_circuits = [
                name
                for name, value in circuit_snapshot.get("circuits", {}).items()
                if value.get("state") == "open"
            ]

            log_event(
                "CHECKPOINT",
                "Durable checkpoint saved "
                f"| stage={stage} "
                f"| steps={budget.workflow_steps}/{budget_snapshot['limits']['max_workflow_steps']} "
                f"| llm_calls={budget.llm_calls} "
                f"| tokens={budget.total_tokens}/{budget_snapshot['limits']['max_total_tokens']} "
                f"| est_cost=${budget.estimated_cost_usd:.6f} "
                f"| open_circuits={','.join(open_circuits) if open_circuits else 'none'}",
            )

        budget.workflow_steps = max(
            budget.workflow_steps,
            int(state.get("workflow_steps", 0) or 0),
        )
        state["runtime_budget"] = budget.to_dict()
        state["runtime_circuit"] = circuit.to_dict()
        return state

    finally:
        deactivate_circuit(circuit_context_token)
        deactivate_budget(budget_context_token)


def _execute_workflow_background(
    run_id: str,
    request: WorkflowRunRequest,
    *,
    resume: bool = False,
) -> None:
    set_run_id(run_id)
    lease_owner = create_lease_owner()

    with _run_lock:
        if run_id in _active_runs:
            log_event("RUN", "Duplicate resume/start blocked by active local execution")
            return
        _active_runs.add(run_id)

    lease_acquired = False
    channel = None
    collector = None

    try:
        lease_acquired = acquire_run_lease(run_id, owner=lease_owner)
        if not lease_acquired:
            log_event("RUN", "Duplicate resume/start blocked by Redis lease")
            return

        channel = subscribe(run_id)
        collector = threading.Thread(target=_trace_collector, args=(run_id, channel), daemon=True)
        collector.start()

        try:
            if resume:
                persisted = load_state(run_id)
                if not persisted:
                    raise RuntimeError("No durable checkpoint exists for this run.")

                state = persisted
                workspace = state["workspace"]

                # Compatibility with checkpoints created before M12 circuit state.
                state.setdefault("runtime_budget", RuntimeBudget().to_dict())
                state.setdefault("runtime_circuit", RuntimeCircuitBreaker().to_dict())

                _bind_workspace_database(workspace)
                log_event("RESUME", "Workflow resumed from durable checkpoint")
            else:
                workspace = _resolve_request_workspace(request)
                _bind_workspace_database(workspace)

                initial_budget = RuntimeBudget()
                initial_circuit = RuntimeCircuitBreaker()

                state = {
                    "run_id": run_id,
                    "goal": request.goal.strip(),
                    "workspace": workspace,
                    "workflow_steps": 0,
                    "runtime_budget": initial_budget.to_dict(),
                    "runtime_circuit": initial_circuit.to_dict(),
                    "status": "running",
                }

                create_run(
                    run_id=run_id,
                    goal=request.goal.strip(),
                    workspace_id=workspace["workspace_id"],
                    request_payload=request.model_dump(),
                    initial_state=state,
                )

            log_event(
                "WORKSPACE",
                f"Workspace bound | {workspace['workspace_id']} | {workspace.get('company_name') or 'UNKNOWN'}",
            )
            log_event(
                "WORKFLOW",
                "Business workflow started" if not resume else "Business workflow resume started",
            )

            refresh_run_lease(run_id, lease_owner)

            result = _run_graph_with_checkpoints(run_id=run_id, initial_state=state)

            refresh_run_lease(run_id, lease_owner)

            save_checkpoint(
                run_id=run_id,
                state=result,
                stage="finished",
                status=result.get("status", "completed"),
            )

            with _run_lock:
                _run_results[run_id] = result

            log_event("RUN", "Business workflow finished")

        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            with _run_lock:
                _run_errors[run_id] = message
            try:
                mark_run_error(run_id, message)
            except Exception:
                pass
            log_event("RUN", f"Business workflow interrupted | {message}")

        finally:
            if lease_acquired:
                release_run_lease(run_id, owner=lease_owner)

            if channel is not None:
                channel.put(None)
            if collector is not None:
                collector.join(timeout=1.0)
            if channel is not None:
                unsubscribe(run_id, channel)

    finally:
        with _run_lock:
            _active_runs.discard(run_id)


@app.get("/api/health")
def health() -> dict[str, Any]:
    postgres = persistence_health()
    redis_status = redis_health()
    return {
        "ok": postgres["ok"] and redis_status["ok"],
        "service": APP_NAME,
        "version": APP_VERSION,
        "workflow": "ready",
        "live_trace": "sse",
        "workspace_upload": True,
        "runtime_budget": True,
        "runtime_circuit_breaker": True,
        "persistence": postgres,
        "redis": redis_status,
    }


@app.get("/api/ready")
def ready() -> dict[str, Any]:
    postgres = persistence_health()
    redis_status = redis_health()
    ready_status = postgres["ok"] and redis_status["ok"]
    payload = {
        "ok": ready_status,
        "persistence": postgres,
        "redis": redis_status,
    }
    if not ready_status:
        raise HTTPException(status_code=503, detail=payload)
    return payload


@app.get("/api/admin/overview")
def admin_overview() -> dict[str, Any]:
    try:
        return {"ok": True, "data": build_admin_overview()}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "ADMIN_DATA_UNAVAILABLE", "message": f"{type(exc).__name__}: {exc}"},
        ) from exc


@app.get("/health")
def root_health() -> dict[str, Any]:
    return health()


@app.get("/ready")
def root_ready() -> dict[str, Any]:
    return ready()


@app.get("/api/workspaces")
def read_workspaces() -> dict[str, Any]:
    return {"ok": True, "workspaces": list_workspaces()}


@app.get("/api/workspaces/{workspace_id}")
def read_workspace(workspace_id: str) -> dict[str, Any]:
    workspace = get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKSPACE_NOT_FOUND", "message": f"Workspace '{workspace_id}' was not found."},
        )
    return {"ok": True, "workspace": workspace}


def _require_workspace(workspace_id: str) -> dict[str, Any]:
    workspace = get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "WORKSPACE_NOT_FOUND",
                "message": f"Workspace '{workspace_id}' was not found.",
            },
        )
    return workspace


@app.post("/api/workspaces/{workspace_id}/documents")
async def upload_workspace_document(
    workspace_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    _require_workspace(workspace_id)
    try:
        raw = await file.read()
        document = store_pdf_document(
            workspace_id=workspace_id,
            filename=file.filename or "document.pdf",
            raw=raw,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "DOCUMENT_VALIDATION_ERROR", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "DOCUMENT_UPLOAD_FAILED", "message": f"{type(exc).__name__}: {exc}"},
        ) from exc
    return {"ok": True, "document": document}


@app.get("/api/workspaces/{workspace_id}/documents")
def read_workspace_documents(workspace_id: str) -> dict[str, Any]:
    _require_workspace(workspace_id)
    return {"ok": True, "documents": list_documents(workspace_id=workspace_id)}


@app.post("/api/workspaces/{workspace_id}/documents/retrieve")
def retrieve_workspace_documents(
    workspace_id: str,
    request: DocumentQueryRequest,
) -> dict[str, Any]:
    _require_workspace(workspace_id)
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "passages": retrieve_passages(
            workspace_id=workspace_id,
            query=request.query,
            top_k=request.top_k,
        ),
    }


@app.post("/api/workspaces/{workspace_id}/documents/ask")
def ask_workspace_documents(
    workspace_id: str,
    request: DocumentQueryRequest,
) -> dict[str, Any]:
    _require_workspace(workspace_id)
    return {"ok": True, **ask_documents(
        workspace_id=workspace_id,
        question=request.query,
        top_k=request.top_k,
    )}


@app.post("/api/workspaces")
async def create_workspace(
    company_name: str = Form(...),
    business_type: str | None = Form(None),
    currency: str | None = Form(None),
    timezone: str | None = Form(None),
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    file_payloads: list[tuple[str, bytes]] = []
    for uploaded in files:
        raw = await uploaded.read()
        file_payloads.append((uploaded.filename or "dataset.csv", raw))

    try:
        workspace = create_workspace_from_files(
            company_name=company_name,
            business_type=business_type,
            currency=currency,
            timezone=timezone,
            files=file_payloads,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "WORKSPACE_VALIDATION_ERROR", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "WORKSPACE_BUILD_FAILED", "message": f"{type(exc).__name__}: {exc}"},
        ) from exc

    return {"ok": True, "workspace": workspace}


@app.post("/api/workflows/start")
def start_workflow(request: WorkflowRunRequest) -> dict[str, Any]:
    try:
        _resolve_request_workspace(request)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKSPACE_NOT_FOUND", "message": str(exc)},
        ) from exc

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
                yield "event: trace\n" f"data: {json.dumps(event, default=str)}\n\n"

            if is_finished(run_id):
                yield "event: complete\n" f"data: {json.dumps({'run_id': run_id})}\n\n"
                return

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


@app.get("/api/workflows/{run_id}/result", response_model=WorkflowRunResponse)
def workflow_result(run_id: str) -> WorkflowRunResponse:
    with _run_lock:
        result = _run_results.get(run_id)
        error = _run_errors.get(run_id)

    if result is None and error is None:
        persisted = load_run(run_id)
        if persisted:
            result = persisted["state_json"]
            if not isinstance(result, dict):
                result = json.loads(result)
            if persisted.get("status") == "interrupted":
                error = persisted.get("error")

    if error:
        raise HTTPException(
            status_code=500,
            detail={"code": "WORKFLOW_EXECUTION_FAILED", "message": error},
        )

    if result is None:
        if is_finished(run_id):
            raise HTTPException(
                status_code=500,
                detail={"code": "WORKFLOW_RESULT_MISSING", "message": "Workflow finished without a result."},
            )
        raise HTTPException(
            status_code=202,
            detail={"code": "WORKFLOW_STILL_RUNNING", "message": "Workflow is still running."},
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
        runtime_budget=result.get("runtime_budget", {}),
        runtime_circuit=result.get("runtime_circuit", {}),
    )


def _load_completed_result(run_id: str) -> dict[str, Any]:
    with _run_lock:
        result = _run_results.get(run_id)
    if result is None:
        persisted = load_run(run_id)
        if persisted:
            result = persisted["state_json"]
            if not isinstance(result, dict):
                result = json.loads(result)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKFLOW_RESULT_NOT_FOUND", "message": "No workflow result exists for this run."},
        )
    return result


@app.get("/api/workflows/{run_id}/intelligence")
def workflow_intelligence(run_id: str) -> dict[str, Any]:
    result = _load_completed_result(run_id)
    workspace = result.get("workspace") or {}
    workspace_id = workspace.get("workspace_id")
    if not workspace_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "WORKSPACE_CONTEXT_MISSING", "message": "The workflow result has no workspace context."},
        )
    return {"ok": True, **build_intelligence(result=result, workspace_id=workspace_id)}


@app.post("/api/workflows/{run_id}/simulate")
def workflow_simulation(run_id: str, request: SimulationRequest) -> dict[str, Any]:
    result = _load_completed_result(run_id)
    return {"ok": True, **simulate_scenario(scenario=request.scenario, task_results=result.get("task_results", []))}


@app.get("/api/workflows/{run_id}/report")
def workflow_report(run_id: str) -> Response:
    result = _load_completed_result(run_id)
    workspace = result.get("workspace") or {}
    workspace_id = workspace.get("workspace_id")
    if not workspace_id:
        raise HTTPException(
            status_code=422,
            detail={"code": "WORKSPACE_CONTEXT_MISSING", "message": "The workflow result has no workspace context."},
        )
    intelligence = build_intelligence(result=result, workspace_id=workspace_id)
    pdf = build_report_pdf(result=result, workspace=workspace, intelligence=intelligence)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="synapseops-report-{run_id}.pdf"'},
    )


@app.get("/api/workflows/incomplete")
def incomplete_workflows() -> dict[str, Any]:
    return {"ok": True, "runs": list_incomplete_runs()}


@app.post("/api/workflows/{run_id}/resume")
def resume_workflow(run_id: str) -> dict[str, Any]:
    persisted = load_run(run_id)

    if persisted is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "WORKFLOW_NOT_FOUND",
                "message": "No durable workflow checkpoint exists for this run.",
            },
        )

    terminal_statuses = {
        "completed",
        "completed_degraded",
        "completed_with_errors",
        "failed",
        "blocked",
        "cancelled",
        "budget_stopped",
    }

    if persisted["status"] in terminal_statuses:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WORKFLOW_ALREADY_TERMINAL",
                "message": f"Workflow is already {persisted['status']}.",
            },
        )

    with _run_lock:
        locally_active = run_id in _active_runs

    if locally_active:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "WORKFLOW_ALREADY_RUNNING",
                "message": "Workflow is already executing in this API process.",
            },
        )

    force_release_run_lease(run_id)

    request_json = persisted["request_json"]
    if not isinstance(request_json, dict):
        request_json = json.loads(request_json)

    request = WorkflowRunRequest.model_validate(request_json)

    thread = threading.Thread(
        target=_execute_workflow_background,
        args=(run_id, request),
        kwargs={"resume": True},
        daemon=True,
    )
    thread.start()

    return {
        "ok": True,
        "run_id": run_id,
        "status": "resuming",
        "trace_url": f"/api/workflows/{run_id}/events",
        "result_url": f"/api/workflows/{run_id}/result",
    }


@app.post("/api/workflows/run", response_model=WorkflowRunResponse)
def run_workflow(request: WorkflowRunRequest) -> WorkflowRunResponse:
    """
    Synchronous compatibility endpoint.
    It now uses the same durable M12 execution wrapper as /start,
    so it cannot bypass runtime budgets or circuit state.
    """
    run_id = new_run_id()
    set_run_id(run_id)

    try:
        workspace = _resolve_request_workspace(request)
        _bind_workspace_database(workspace)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "WORKSPACE_NOT_FOUND", "message": str(exc)},
        ) from exc

    channel = subscribe(run_id)
    collector = threading.Thread(
        target=_trace_collector,
        args=(run_id, channel),
        daemon=True,
    )
    collector.start()

    try:
        initial_budget = RuntimeBudget()
        initial_circuit = RuntimeCircuitBreaker()

        state = {
            "run_id": run_id,
            "goal": request.goal.strip(),
            "workspace": workspace,
            "workflow_steps": 0,
            "runtime_budget": initial_budget.to_dict(),
            "runtime_circuit": initial_circuit.to_dict(),
            "status": "running",
        }

        create_run(
            run_id=run_id,
            goal=request.goal.strip(),
            workspace_id=workspace["workspace_id"],
            request_payload=request.model_dump(),
            initial_state=state,
        )

        log_event(
            "WORKSPACE",
            f"Workspace bound | {workspace['workspace_id']} | {workspace.get('company_name') or 'UNKNOWN'}",
        )
        log_event("WORKFLOW", "Business workflow started")

        result = _run_graph_with_checkpoints(
            run_id=run_id,
            initial_state=state,
        )

        save_checkpoint(
            run_id=run_id,
            state=result,
            stage="finished",
            status=result.get("status", "completed"),
        )

        with _run_lock:
            _run_results[run_id] = result

        log_event("RUN", "Business workflow finished")

        return WorkflowRunResponse(
            status=result.get("status", "unknown"),
            plan=result.get("plan"),
            task_results=result.get("task_results", []),
            critic_report=result.get("critic_report"),
            action_request=result.get("action_request"),
            action_status=result.get("action_status"),
            final_output=result.get("final_output", ""),
            workflow_steps=result.get("workflow_steps", 0),
            runtime_budget=result.get("runtime_budget", {}),
            runtime_circuit=result.get("runtime_circuit", {}),
        )

    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        try:
            mark_run_error(run_id, message)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail={"code": "WORKFLOW_EXECUTION_FAILED", "message": message},
        ) from exc

    finally:
        mark_finished(run_id)
        channel.put(None)
        collector.join(timeout=1.0)
        unsubscribe(run_id, channel)


@app.get("/api/actions/{request_id}", response_model=ApiMessage)
def read_action(request_id: str) -> ApiMessage:
    action = _invoke_with_supported_kwargs(
        get_action_request,
        request_id=request_id,
    )

    if action is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ACTION_NOT_FOUND", "message": "Action request was not found."},
        )

    return ApiMessage(message="Action request loaded.", data=_dump(action))


@app.post("/api/actions/{request_id}/approve", response_model=ApiMessage)
def approve_action(request_id: str, body: ApprovalRequest) -> ApiMessage:
    try:
        action = _invoke_with_supported_kwargs(
            approve_action_request,
            request_id=request_id,
            actor=body.actor,
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "ACTION_APPROVAL_REJECTED", "message": str(exc)},
        ) from exc

    return ApiMessage(message="Action approved.", data=_dump(action))


@app.post("/api/actions/{request_id}/reject", response_model=ApiMessage)
def reject_action(request_id: str, body: RejectionRequest) -> ApiMessage:
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
            detail={"code": "ACTION_REJECTION_REJECTED", "message": str(exc)},
        ) from exc

    return ApiMessage(message="Action rejected.", data=_dump(action))


@app.post("/api/actions/{request_id}/execute", response_model=ApiMessage)
def execute_approved_action(request_id: str) -> ApiMessage:
    try:
        result = _invoke_with_supported_kwargs(
            execute_action_request,
            request_id=request_id,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"code": "ACTION_EXECUTION_BLOCKED", "message": str(exc)},
        ) from exc

    return ApiMessage(message="Approved action executed.", data=_dump(result))
