from __future__ import annotations

import json
from subprocess import call
from typing import Annotated, Literal, TypedDict
from app.core.runtime_budget import current_budget
from app.core.runtime_circuit import current_circuit
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from app.core.config import get_llm
from app.core.logging import log_event
from app.core.reliability import invoke_llm_with_retry
from app.tools.web_search import web_search


MAX_AGENT_STEPS = 4
MAX_SEARCHES = 1

WorkerStatus = Literal[
    "running",
    "completed",
    "degraded",
    "failed",
]


SYSTEM_PROMPT = """
You are the Research Agent for SynapseOps AI.

Your role is EXTERNAL research only.

GROUNDING RULES

1. Internal company facts may ONLY come from explicitly supplied upstream
   Analyst evidence. Never infer a company's category, price point,
   customer segment, product positioning, geography, or performance from
   a name or from your own assumptions.

2. When this task depends on Analyst evidence, research ONLY products,
   categories, regions, competitors, or metrics that are explicitly named
   in that upstream evidence.

3. A product ID alone does not establish its category.
   A product name alone does not prove "premium", "high-end", "budget",
   "luxury", or any other market positioning.

4. Do not compare competitor pricing with the company's pricing unless
   upstream evidence contains a comparable company price.

5. Use web_search only for externally verifiable information needed for
   the assigned task.

6. You have ONE focused search. Build one strong query rather than
   several broad searches.

7. Prefer current evidence when the task asks for current/latest/recent
   information. Report source dates/years when visible.

8. If the search does not provide enough evidence, say so. Do not fill
   gaps with model memory.

9. Never invent sources, URLs, current facts, or internal values.

10. Keep the final answer concise and evidence-oriented.

Finish with:

Finding:
Evidence:
Interpretation:
Confidence: High / Medium / Low
Limitations:
"""


class ResearchState(
    TypedDict,
    total=False,
):
    messages: Annotated[
        list[BaseMessage],
        add_messages,
    ]
    step_count: int
    search_count: int
    seen_tool_calls: list[str]
    successful_tool_calls: int
    failed_tool_calls: int
    guard_hit: bool
    status: WorkerStatus


def build_research_graph():
    base_llm = get_llm()
    llm = base_llm.bind_tools(
        [web_search]
    )

    def agent(
        state: ResearchState,
    ):
        step = state.get(
            "step_count",
            0,
        )

        log_event(
            "AGENT",
            (
                "Research Agent invoked "
                f"| step {step + 1}"
            ),
        )

        if step >= MAX_AGENT_STEPS:
            ok = (
                state.get(
                    "successful_tool_calls",
                    0,
                )
                > 0
            )

            return {
                "messages": [
                    AIMessage(
                        content=(
                            "Research stopped at the hard step limit. "
                            "Only collected evidence should be used."
                        )
                    )
                ],
                "status": (
                    "degraded"
                    if ok
                    else "failed"
                ),
                "guard_hit": True,
            }

        try:
            response = invoke_llm_with_retry(
                llm,
                [
                    SystemMessage(
                        content=SYSTEM_PROMPT
                    ),
                    *state["messages"],
                ],
                component="Research",
            )

        except Exception as exc:
            log_event(
                "AGENT",
                (
                    "Research LLM failed "
                    f"| {type(exc).__name__}: {exc}"
                ),
            )

            return {
                "messages": [
                    AIMessage(
                        content=(
                            "The Research Agent could not complete "
                            "this task."
                        )
                    )
                ],
                "status": "failed",
                "guard_hit": True,
                "step_count": step + 1,
            }

        calls = (
            getattr(
                response,
                "tool_calls",
                None,
            )
            or []
        )

        if not calls:
            return {
                "messages": [response],
                "status": (
                    "degraded"
                    if state.get(
                        "failed_tool_calls",
                        0,
                    )
                    else "completed"
                ),
                "step_count": step + 1,
            }

        return {
            "messages": [response],
            "status": "running",
            "step_count": step + 1,
        }

    def tools(
        state: ResearchState,
    ):
        last = state["messages"][-1]

        seen = list(
            state.get(
                "seen_tool_calls",
                [],
            )
        )

        searches = state.get(
            "search_count",
            0,
        )

        success = state.get(
            "successful_tool_calls",
            0,
        )

        failed = state.get(
            "failed_tool_calls",
            0,
        )

        

        outputs = []

        for call in (
            getattr(
                last,
                "tool_calls",
                [],
            )
            or []
        ):

            # ================================================
            # M12 — GLOBAL TOOL BUDGET
            # ================================================

            runtime_budget = current_budget()

            if runtime_budget is not None:

                allowed = runtime_budget.add_tool_calls(1)

                if not allowed:

                    log_event(
                        "BUDGET",
                        (
                            "Global workflow tool budget exceeded "
                            f"| tool={call['name']} "
                            f"| used={runtime_budget.tool_calls}"
                        ),
                    )

                    payload = {
                        "ok": False,
                        "tool": call["name"],
                        "data": None,
                        "error": {
                            "type": (
                                runtime_budget.stop_code
                                or "TOOL_CALL_BUDGET_EXCEEDED"
                            ),
                            "message": (
                                runtime_budget.stop_reason
                                or "Global workflow tool-call budget exceeded."
                            ),
                            "retryable": False,
                        },
                        "latency_ms": 0,
                    }

                    failed += 1

                    outputs.append(
                        ToolMessage(
                            content=json.dumps(
                                payload,
                                ensure_ascii=False,
                                default=str,
                            ),
                            tool_call_id=call["id"],
                        )
                    )

                    continue

                signature = json.dumps(
                {
                    "name": call["name"],
                    "args": call.get(
                        "args",
                        {},
                    ),
                },
                sort_keys=True,
                default=str,
            )

            if call["name"] != "web_search":
                payload = {
                    "ok": False,
                    "tool": call["name"],
                    "data": None,
                    "error": {
                        "type": "UNKNOWN_TOOL",
                        "message": "Unknown research tool.",
                        "retryable": False,
                    },
                    "latency_ms": 0,
                }
                failed += 1

            elif searches >= MAX_SEARCHES:
                payload = {
                    "ok": False,
                    "tool": "web_search",
                    "data": None,
                    "error": {
                        "type": "SEARCH_BUDGET_EXCEEDED",
                        "message": (
                            "The single focused research search "
                            "budget has been used."
                        ),
                        "retryable": False,
                    },
                    "latency_ms": 0,
                }
                failed += 1

            elif signature in seen:
                payload = {
                    "ok": False,
                    "tool": "web_search",
                    "data": None,
                    "error": {
                        "type": "DUPLICATE_TOOL_CALL",
                        "message": "Duplicate search blocked.",
                        "retryable": False,
                    },
                    "latency_ms": 0,
                }
                failed += 1

            else:
                runtime_circuit = current_circuit()

                if (
                    runtime_circuit is not None
                    and not runtime_circuit.allow_call("research")
                ):
                    reason = (
                        runtime_circuit.reason("research")
                        or "Research circuit is open for this workflow."
                    )

                    log_event(
                        "CIRCUIT_BREAKER",
                        (
                            "Research search blocked because circuit is OPEN "
                            f"| reason={reason}"
                        ),
                    )

                    payload = {
                        "ok": False,
                        "tool": "web_search",
                        "data": None,
                        "error": {
                            "type": "RESEARCH_CIRCUIT_OPEN",
                            "message": reason,
                            "retryable": False,
                        },
                        "latency_ms": 0,
                    }
                    failed += 1

                else:
                    seen.append(signature)
                    searches += 1

                    log_event(
                        "TOOL",
                        "Research requested focused web_search",
                    )

                    try:
                        payload = web_search.invoke(
                            call.get(
                                "args",
                                {},
                            )
                        )
                    except Exception as exc:
                        payload = {
                            "ok": False,
                            "tool": "web_search",
                            "data": None,
                            "error": {
                                "type": "WEB_SEARCH_EXECUTION_ERROR",
                                "message": str(exc),
                                "retryable": True,
                            },
                            "latency_ms": 0,
                        }

                    if payload.get("ok"):
                        success += 1

                        if runtime_circuit is not None:
                            runtime_circuit.record_success("research")

                    else:
                        failed += 1

                        error_data = payload.get("error") or {}
                        error_type = error_data.get("type")
                        error_message = error_data.get("message")

                        if (
                            runtime_circuit is not None
                            and error_type != "RESEARCH_CIRCUIT_OPEN"
                        ):
                            opened = runtime_circuit.record_failure(
                                "research",
                                (
                                    f"{error_type or 'RESEARCH_ERROR'}: "
                                    f"{error_message or 'Unknown research failure'}"
                                ),
                            )

                            if opened:
                                log_event(
                                    "CIRCUIT_BREAKER",
                                    (
                                        "Research circuit OPENED "
                                        f"| reason="
                                        f"{runtime_circuit.reason('research')}"
                                    ),
                                )

            outputs.append(
                ToolMessage(
                    content=json.dumps(
                        payload,
                        ensure_ascii=False,
                        default=str,
                    ),
                    tool_call_id=call["id"],
                )
            )

        return {
            "messages": outputs,
            "seen_tool_calls": seen,
            "search_count": searches,
            "successful_tool_calls": success,
            "failed_tool_calls": failed,
        }

    def route_agent(
        state: ResearchState,
    ):
        return (
            "tools"
            if getattr(
                state["messages"][-1],
                "tool_calls",
                None,
            )
            else END
        )

    graph = StateGraph(
        ResearchState
    )

    graph.add_node(
        "agent",
        agent,
    )
    graph.add_node(
        "tools",
        tools,
    )

    graph.set_entry_point(
        "agent"
    )

    graph.add_conditional_edges(
        "agent",
        route_agent,
        {
            "tools": "tools",
            END: END,
        },
    )

    graph.add_edge(
        "tools",
        "agent",
    )

    return graph.compile()
