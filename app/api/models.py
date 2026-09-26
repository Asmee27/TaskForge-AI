from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

class WorkflowRunRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=5000)
    workspace: dict[str, Any] | None = None

class DocumentQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=3000)
    top_k: int = Field(default=5, ge=1, le=20)

class SimulationRequest(BaseModel):
    scenario: str = Field(..., min_length=1, max_length=1000)

class WorkflowRunResponse(BaseModel):
    status: str
    plan: Any | None = None
    task_results: list[Any] = Field(default_factory=list)
    critic_report: Any | None = None
    action_request: Any | None = None
    action_status: str | None = None
    final_output: str = ""
    workflow_steps: int = 0
    runtime_budget: dict[str, Any] = Field(default_factory=dict)
    runtime_circuit: dict[str, Any] = Field(default_factory=dict)

class ApprovalRequest(BaseModel):
    actor: str = Field(default="dashboard-user", min_length=1, max_length=200)

class RejectionRequest(BaseModel):
    actor: str = Field(default="dashboard-user", min_length=1, max_length=200)
    reason: str = Field(..., min_length=1, max_length=1000)

class ApiMessage(BaseModel):
    ok: bool = True
    message: str
    data: Any | None = None
