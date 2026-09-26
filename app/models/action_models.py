from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ActionType = Literal[
    "reorder_inventory",
    "apply_discount",
    "external_action",
]

ActionStatus = Literal[
    "pending_approval",
    "approved",
    "rejected",
    "executed",
    "failed",
]


class ProposedAction(BaseModel):
    action_type: ActionType
    description: str = Field(min_length=1, max_length=1000)
    payload: dict[str, Any] = Field(default_factory=dict)
    source: Literal["user_goal", "worker_recommendation"] = "user_goal"
    requires_human_approval: bool = True


class ActionRequest(BaseModel):
    request_id: str
    workspace_id: str
    action_type: ActionType
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str
    requires_human_approval: bool
    status: ActionStatus
    created_at: str
    approved_at: str | None = None
    approved_by: str | None = None
    rejected_at: str | None = None
    rejected_by: str | None = None
    rejection_reason: str | None = None
    executed_at: str | None = None
    execution_result: dict[str, Any] | None = None
