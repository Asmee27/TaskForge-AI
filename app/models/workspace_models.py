from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field
from app.models.policy_models import BusinessPolicyConfig

class WorkspaceContext(BaseModel):
    workspace_id: str = "default"
    company_name: str | None = None
    business_type: str | None = None
    currency: str | None = None
    timezone: str | None = None
    dataset_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    policies: BusinessPolicyConfig = Field(default_factory=BusinessPolicyConfig)

    def to_agent_context(self) -> str:
        def value(v):
            return v if v not in (None, "") else "UNKNOWN"
        return f"""CURRENT BUSINESS WORKSPACE
Workspace ID: {value(self.workspace_id)}
Company: {value(self.company_name)}
Business type: {value(self.business_type)}
Currency: {value(self.currency)}
Timezone: {value(self.timezone)}
Dataset: {value(self.dataset_name)}
Metadata: {self.metadata or {}}

Do not guess values marked UNKNOWN. Use 'monetary units' when currency is unknown.
""".strip()
