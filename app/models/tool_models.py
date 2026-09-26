from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field

class ToolError(BaseModel):
    type: str
    message: str
    retryable: bool = False

class ToolResult(BaseModel):
    ok: bool
    tool: str
    data: Any = None
    error: ToolError | None = None
    latency_ms: int = 0

class SearchInput(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    max_results: int = Field(default=5, ge=1, le=10)
