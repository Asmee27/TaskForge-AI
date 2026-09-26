from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, model_validator

AgentName = Literal["analyst", "research"]
TaskStatus = Literal["pending", "running", "completed", "degraded", "failed", "blocked"]

class PlanTask(BaseModel):
    id: int = Field(ge=1, le=5)
    order: int = Field(ge=1, le=5)
    description: str = Field(min_length=3, max_length=1200)
    agent: AgentName
    reason: str = Field(default="Required to answer the business goal.", max_length=1000)
    confidence: float = Field(default=0.8, ge=0, le=1)
    depends_on: list[int] = Field(default_factory=list)

class ExecutionPlan(BaseModel):
    goal: str = Field(min_length=1, max_length=5000)
    tasks: list[PlanTask] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_plan(self):
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("Task IDs must be unique.")
        ordered = sorted(self.tasks, key=lambda t: t.order)
        for expected, task in enumerate(ordered, start=1):
            if task.order != expected:
                raise ValueError("Task order must be contiguous starting at 1.")
            if task.id != task.order:
                raise ValueError("Task id and order must match.")
            for dep in task.depends_on:
                if dep >= task.id:
                    raise ValueError("Dependencies must reference earlier tasks.")
        self.tasks = ordered
        return self

class TaskResult(BaseModel):
    task_id: int
    agent: AgentName
    status: TaskStatus
    description: str
    output: str | None = None
    error: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    structured_evidence: list[dict] = Field(default_factory=list)
