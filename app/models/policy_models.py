from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

class BusinessPolicyConfig(BaseModel):
    require_human_approval_for_irreversible_actions: bool = True
    max_discount_percent_without_approval: float | None = Field(default=20.0, ge=0, le=100)
    max_reorder_quantity_without_approval: int | None = Field(default=1000, ge=0)
    minimum_margin_percent: float | None = Field(default=10.0, ge=-100, le=100)

class PolicyFinding(BaseModel):
    rule: str
    passed: bool
    severity: Literal["info", "warning", "critical"] = "info"
    message: str
    requires_human_approval: bool = False

class PolicyEvaluation(BaseModel):
    passed: bool
    requires_human_approval: bool
    findings: list[PolicyFinding] = Field(default_factory=list)

class CriticFinding(BaseModel):
    category: Literal["evidence", "consistency", "confidence", "policy", "dependency", "freshness", "safety"] | str
    severity: Literal["info", "warning", "critical"]
    message: str

class CriticReport(BaseModel):
    verdict: Literal["approved", "review_required", "blocked"]
    overall_confidence: float = Field(ge=0, le=1)
    evidence_quality: Literal["high", "medium", "low"]
    requires_human_approval: bool = False
    findings: list[CriticFinding] = Field(default_factory=list)
    summary: str
