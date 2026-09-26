from __future__ import annotations

import json
from typing import Annotated, Literal, TypedDict
from unittest.mock import call
from app.core.runtime_budget import current_budget
from app.core.runtime_circuit import current_circuit
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from app.core.config import get_llm
from app.core.logging import log_event
from app.core.reliability import invoke_llm_with_retry
from app.core.workspace_runtime import get_workspace_db_path

from app.tools.database_tools import (
    calculator,
    get_database_schema,
    run_sql_query,
)


# ============================================================
# SAFEGUARDS
# ============================================================

MAX_AGENT_STEPS = 6
MAX_TOOL_CALLS = 4
MAX_SQL_CALLS = 2

WorkerStatus = Literal[
    "running",
    "completed",
    "degraded",
    "failed",
]


# ============================================================
# PROMPTS
# ============================================================

SYSTEM_PROMPT = """
You are the Data Analyst Agent for SynapseOps AI.

You analyze INTERNAL data for the CURRENT BUSINESS WORKSPACE.

The workspace/company/currency/schema may vary between runs.
Never assume NovaMart, BRL, ecommerce, or any fixed schema unless
that context is explicitly supplied in the user/task message.

TOOLS

1. get_database_schema
2. run_sql_query
3. calculator


EFFICIENCY RULES

Use the SMALLEST sufficient number of tool calls.

Preferred flow:

schema if necessary
→ one strong SQL query
→ final answer

For genuinely more complex requests:

schema if necessary
→ first SQL query
→ optional second SQL query
→ final answer

Never use more than 2 SQL queries.

If a SQL result already provides enough evidence to answer the
assigned task, STOP querying and write the final answer.

Do not keep querying merely to make the answer more detailed.

If requested information is not available in the schema, state the
limitation instead of repeatedly searching for nonexistent data.


STRICT DATA RULES

1. Never invent database values.

2. Query the database before making internal business claims.

3. If schema is unknown, call get_database_schema.

4. SQL is READ ONLY.

5. Never request INSERT, UPDATE, DELETE, DROP, ALTER,
   CREATE or any database modification.

6. Do not request arbitrary Python execution.

7. Use calculator only when arithmetic is genuinely needed.

8. Do not confuse:
   - order_items.price
   - payments.payment_value
   - freight_value

9. Be careful with one-to-many joins.

An order may have multiple order_items and multiple payments.
Joining those tables directly may duplicate monetary values.

10. Product sales revenue normally uses order_items.price.

11. Payment totals use payments.payment_value.

12. Explicitly state important assumptions about cancelled or
    unavailable orders.

13. Do not silently interpret NULL dates.

14. Insufficient evidence must be stated clearly.

15. Correlation does not prove causation.

16. Respect SQL result truncation.

17. If a business goal asks for information unavailable in the
    current database, answer with the strongest supported analysis
    and clearly identify what could not be calculated.

18. IMPORTANT SQL RULE:
    Use only columns that actually exist in the database schema.
    Do not invent columns such as avg_daily_sales, sales_velocity,
    safety_stock, or reorder_qty unless the schema explicitly contains
    them or they can be safely derived from available columns.

19. For low-stock inventory analysis, prefer a simple reliable query
    against the inventory table first. Example shape:

    SELECT product_id, stock_quantity, reorder_level, supplier_lead_days
    FROM inventory
    WHERE stock_quantity <= reorder_level
    ORDER BY (reorder_level - stock_quantity) DESC
    LIMIT 20;

    Only join sales/order tables if the schema clearly supports the join
    and the extra evidence is genuinely needed.

20. If an SQL query fails, inspect the returned SQL error and correct the
    query once. Do not repeat the same invalid SQL.

21. Preserve quantity semantics exactly.
    "Should we reorder 5000 units?" means evaluate a proposed 5000-unit order.
    It does NOT mean "make total stock equal to 5000" unless the user explicitly
    states that 5000 is the desired target stock level.

22. Never create a derived metric whose formula is not justified by the goal,
    schema, or clearly stated business rule. If target stock or safety stock is
    unknown, do not fabricate a target-stock formula.

23. Before presenting an aggregate, sanity-check its sign and scale.
    Reorder quantities should not become negative unless negative inventory is
    explicitly meaningful in the source data. If a result is implausible,
    correct the SQL or mark the metric unsupported.

24. EVIDENCE DISCIPLINE:
    Every material interpretation must be traceable to a successful SQL or
    calculator result from this run. Do not infer product category, premium/
    budget positioning, customer segment, region, profitability, returns,
    cancellations, or operational quality unless the queried columns/results
    directly establish it.

25. If the assigned task asks for several metrics but the schema does not
    support some of them, compute the supported metrics and list unsupported
    metrics under Limitations. Never manufacture a proxy silently.

26. Product IDs/names are not proof of category or market positioning.
    If category claims matter, query a category field or explicitly state that
    category evidence is unavailable.

27. Currency may come ONLY from CURRENT BUSINESS WORKSPACE metadata.
    If Currency is UNKNOWN, call values monetary units.
28. Never infer currency from dataset values, company name, dataset name,
    geography, language, timezone, or formatting. Never say a currency is
    assumed. Never use INR, USD, BRL, or a currency symbol unless that exact
    currency is supplied in workspace metadata.

29. Never label a metric high, low, healthy, poor, good, bad, unusual, or
    concerning unless evidence includes a target, policy threshold, comparison,
    or external benchmark.
30. Average discount amount is not discount usage frequency. Do not infer causes
    for repeat-customer or other aggregates without evidence.


ANSWER FORMAT

Finding:
...

Evidence:
...

Interpretation:
...

Confidence:
High / Medium / Low

Limitations:
...

Do not reveal chain-of-thought.
"""


FINALIZER_PROMPT = """
You are the final response stage of the SynapseOps Data Analyst.

You MUST NOT call tools.

Using only the evidence already present in the conversation,
produce the best supported final answer for the assigned task.

Do not invent missing values.

If requested metrics are unavailable from the retrieved evidence,
state that clearly as a limitation rather than refusing to answer.

Use this exact structure:

Finding:
...

Evidence:
...

Interpretation:
...

Confidence:
High / Medium / Low

Limitations:
...

Be concise and decision-useful.
Do not reveal chain-of-thought.
"""


# ============================================================
# STATE
# ============================================================

class AnalystState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    step_count: int
    tool_call_count: int
    sql_call_count: int
    seen_tool_calls: list[str]
    status: WorkerStatus
    guard_hit: bool
    successful_tool_calls: int
    failed_tool_calls: int
    force_finalize: bool


TOOLS = [
    get_database_schema,
    run_sql_query,
    calculator,
]


# ============================================================
# HELPERS
# ============================================================

def _tool_payload_to_text(payload: dict) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        default=str,
    )


# ============================================================
# GRAPH
# ============================================================

def build_data_analyst_graph():

    base_llm = get_llm()
    llm_with_tools = base_llm.bind_tools(TOOLS)

    tool_map = {
        tool.name: tool
        for tool in TOOLS
    }

    # Cached schema is stored as DATA ONLY.
    # It is never injected into the transcript as an orphan ToolMessage.
    schema_cache: dict[str, dict] = {}

    # ========================================================
    # AGENT
    # ========================================================

    def agent_node(state: AnalystState):

        step_count = state.get("step_count", 0)

        log_event(
            "AGENT",
            f"Data Analyst Agent invoked | step {step_count + 1}",
        )

        if step_count >= MAX_AGENT_STEPS:
            successful = state.get("successful_tool_calls", 0)

            status: WorkerStatus = (
                "degraded"
                if successful > 0
                else "failed"
            )

            log_event(
                "GUARD",
                f"Analyst hard step limit reached | status={status}",
            )

            return {
                "messages": [
                    AIMessage(
                        content=(
                            "Analysis stopped because the maximum "
                            "agent step limit was reached. Available "
                            "evidence may be incomplete."
                        )
                    )
                ],
                "status": status,
                "guard_hit": True,
                "step_count": step_count,
            }

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            *state["messages"],
        ]

        try:
            response = invoke_llm_with_retry(
                llm_with_tools,
                messages,
                component="Data Analyst",
            )

        except Exception as exc:
            log_event(
                "AGENT",
                (
                    "Data Analyst LLM failed "
                    f"| {type(exc).__name__}: {exc}"
                ),
            )

            successful = state.get(
                "successful_tool_calls",
                0,
            )

            return {
                "messages": [
                    AIMessage(
                        content=(
                            "The Data Analyst model could not "
                            "complete this task."
                        )
                    )
                ],
                "status": (
                    "degraded"
                    if successful > 0
                    else "failed"
                ),
                "guard_hit": True,
                "step_count": step_count + 1,
            }

        tool_calls = getattr(
            response,
            "tool_calls",
            None,
        ) or []

        if not tool_calls:
            previous_failures = state.get(
                "failed_tool_calls",
                0,
            )

            final_status: WorkerStatus = (
                "degraded"
                if previous_failures > 0
                else "completed"
            )

            return {
                "messages": [response],
                "step_count": step_count + 1,
                "status": final_status,
            }

        return {
            "messages": [response],
            "step_count": step_count + 1,
            "status": "running",
        }

    # ========================================================
    # TOOLS
    # ========================================================

    def tool_node(state: AnalystState):

        nonlocal schema_cache

        last_message = state["messages"][-1]

        seen = list(
            state.get(
                "seen_tool_calls",
                [],
            )
        )

        tool_call_count = state.get(
            "tool_call_count",
            0,
        )

        sql_call_count = state.get(
            "sql_call_count",
            0,
        )

        successful = state.get(
            "successful_tool_calls",
            0,
        )

        failed = state.get(
            "failed_tool_calls",
            0,
        )

        force_finalize = state.get(
            "force_finalize",
            False,
        )

        output_messages = []

        for call in getattr(
            last_message,
            "tool_calls",
            [],
        ) or []:

            tool_name = call["name"]
            args = call.get("args", {})
            # ============================================================
# M12 — GLOBAL TOOL BUDGET
# ============================================================

            runtime_budget = current_budget()

            if runtime_budget is not None:

                allowed = runtime_budget.add_tool_calls(1)

                if not allowed:

                    log_event(
                        "BUDGET",
                        (
                            "Global workflow tool budget exceeded "
                            f"| tool={tool_name} "
                            f"| used={runtime_budget.tool_calls}"
                        ),
                    )

                    payload = {
                        "ok": False,
                        "tool": tool_name,
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
                    force_finalize = True

                    output_messages.append(
                        ToolMessage(
                            content=_tool_payload_to_text(
                                payload
                            ),
                            tool_call_id=call["id"],
                        )
                    )

                    continue

            if tool_call_count >= MAX_TOOL_CALLS:
                log_event(
                    "GUARD",
                    (
                        "Analyst tool-call budget reached "
                        f"| max={MAX_TOOL_CALLS}"
                    ),
                )

                payload = {
                    "ok": False,
                    "tool": tool_name,
                    "data": None,
                    "error": {
                        "type": "TOOL_BUDGET_EXCEEDED",
                        "message": (
                            "Maximum Analyst tool-call budget reached."
                        ),
                        "retryable": False,
                    },
                    "latency_ms": 0,
                }

                failed += 1
                force_finalize = True

                output_messages.append(
                    ToolMessage(
                        content=_tool_payload_to_text(payload),
                        tool_call_id=call["id"],
                    )
                )
                continue

            if (
                tool_name == "run_sql_query"
                and sql_call_count >= MAX_SQL_CALLS
            ):
                log_event(
                    "GUARD",
                    (
                        "Analyst SQL-call budget reached "
                        f"| max={MAX_SQL_CALLS}"
                    ),
                )

                payload = {
                    "ok": False,
                    "tool": tool_name,
                    "data": None,
                    "error": {
                        "type": "SQL_BUDGET_EXCEEDED",
                        "message": (
                            "Maximum SQL query budget reached. "
                            "Use the evidence already collected "
                            "and produce the final answer."
                        ),
                        "retryable": False,
                    },
                    "latency_ms": 0,
                }

                failed += 1
                force_finalize = True

                output_messages.append(
                    ToolMessage(
                        content=_tool_payload_to_text(payload),
                        tool_call_id=call["id"],
                    )
                )
                continue

            signature = json.dumps(
                {
                    "tool": tool_name,
                    "args": args,
                },
                sort_keys=True,
                ensure_ascii=False,
            )

            # -------------------------------------------------
            # Schema cache — protocol-safe
            # -------------------------------------------------
            #
            # IMPORTANT:
            # We still return a ToolMessage because the CURRENT
            # AI message really did request get_database_schema.
            # Therefore the tool_call_id is valid and provider-safe.
            #
            # The bug to avoid is inserting cached schema into the
            # transcript later without a matching tool call.
            # -------------------------------------------------

            if tool_name == "get_database_schema":
                cache_key = str(
                    get_workspace_db_path().resolve()
                )

                if cache_key in schema_cache:
                    log_event(
                        "CACHE",
                        "Database schema cache hit",
                    )

                    payload = schema_cache[cache_key]
                    successful += 1
                    tool_call_count += 1

                else:
                    seen.append(signature)
                    tool_call_count += 1

                    log_event(
                        "TOOL",
                        f"Analyst requested {tool_name}",
                    )

                    try:
                        payload = tool_map[
                            tool_name
                        ].invoke(args)

                    except Exception as exc:
                        payload = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": {
                                "type": "TOOL_EXECUTION_ERROR",
                                "message": str(exc),
                                "retryable": True,
                            },
                            "latency_ms": 0,
                        }

                    if payload.get("ok", False):
                        successful += 1
                        schema_cache[cache_key] = payload
                    else:
                        failed += 1
                        error_data = payload.get("error") or {}
                        if not error_data.get("retryable", False):
                            force_finalize = True

            elif signature in seen:
                log_event(
                    "GUARD",
                    (
                        "Duplicate Analyst tool call blocked "
                        f"| {tool_name}"
                    ),
                )

                payload = {
                    "ok": False,
                    "tool": tool_name,
                    "data": None,
                    "error": {
                        "type": "DUPLICATE_TOOL_CALL",
                        "message": (
                            "The exact same tool call was "
                            "already executed."
                        ),
                        "retryable": False,
                    },
                    "latency_ms": 0,
                }

                failed += 1
                tool_call_count += 1
                force_finalize = True

            elif tool_name not in tool_map:
                log_event(
                    "GUARD",
                    (
                        "Unknown Analyst tool blocked "
                        f"| {tool_name}"
                    ),
                )

                payload = {
                    "ok": False,
                    "tool": tool_name,
                    "data": None,
                    "error": {
                        "type": "UNKNOWN_TOOL",
                        "message": (
                            "Tool unavailable to "
                            "Data Analyst Agent."
                        ),
                        "retryable": False,
                    },
                    "latency_ms": 0,
                }

                failed += 1
                tool_call_count += 1
                force_finalize = True

            else:
                seen.append(signature)
                tool_call_count += 1

                if tool_name == "run_sql_query":
                    sql_call_count += 1

                    query = (
                        args.get("query")
                        or args.get("sql")
                        or ""
                    )

                    log_event(
                        "SQL",
                        (
                            "Analyst SQL attempt "
                            f"{sql_call_count}/{MAX_SQL_CALLS} "
                            f"| {query[:500]}"
                        ),
                    )

                log_event(
                    "TOOL",
                    f"Analyst requested {tool_name}",
                )

                # ============================================================
                # M12.2 — SQL CIRCUIT BREAKER
                # ============================================================
                #
                # The circuit is workflow-scoped and persisted by server_m10.
                # Once SQL has failed repeatedly, further SQL execution is
                # blocked before the database tool is invoked.
                # ============================================================

                runtime_circuit = current_circuit()

                if (
                    tool_name == "run_sql_query"
                    and runtime_circuit is not None
                    and not runtime_circuit.allow_call("sql")
                ):
                    reason = (
                        runtime_circuit.reason("sql")
                        or "SQL circuit is open for this workflow."
                    )

                    log_event(
                        "CIRCUIT_BREAKER",
                        (
                            "SQL call blocked because circuit is OPEN "
                            f"| reason={reason}"
                        ),
                    )

                    payload = {
                        "ok": False,
                        "tool": tool_name,
                        "data": None,
                        "error": {
                            "type": "SQL_CIRCUIT_OPEN",
                            "message": reason,
                            "retryable": False,
                        },
                        "latency_ms": 0,
                    }

                else:
                    try:
                        payload = tool_map[
                            tool_name
                        ].invoke(args)

                    except Exception as exc:
                        payload = {
                            "ok": False,
                            "tool": tool_name,
                            "data": None,
                            "error": {
                                "type": "TOOL_EXECUTION_ERROR",
                                "message": str(exc),
                                "retryable": True,
                            },
                            "latency_ms": 0,
                        }

                if payload.get("ok", False):
                    successful += 1

                    if tool_name == "run_sql_query":
                        if runtime_circuit is not None:
                            runtime_circuit.record_success("sql")

                        if sql_call_count >= MAX_SQL_CALLS:
                            force_finalize = True

                else:
                    failed += 1

                    error_data = (
                        payload.get("error")
                        or {}
                    )

                    if tool_name == "run_sql_query":
                        error_type = error_data.get("type")
                        error_message = error_data.get("message")

                        # Do not count an already-open circuit as another
                        # database failure. Only real attempted SQL failures
                        # advance the consecutive-failure streak.
                        if (
                            runtime_circuit is not None
                            and error_type != "SQL_CIRCUIT_OPEN"
                        ):
                            opened = runtime_circuit.record_failure(
                                "sql",
                                (
                                    f"{error_type or 'SQL_ERROR'}: "
                                    f"{error_message or 'Unknown SQL failure'}"
                                ),
                            )

                            if opened:
                                log_event(
                                    "CIRCUIT_BREAKER",
                                    (
                                        "SQL circuit OPENED "
                                        f"| reason={runtime_circuit.reason('sql')}"
                                    ),
                                )

                        log_event(
                            "SQL",
                            (
                                "SQL failed "
                                f"| type={error_type} "
                                f"| message={error_message}"
                            ),
                        )

                    # Let the model correct ONE failed SQL if we still
                    # have SQL budget. This is critical for reliability.
                    #
                    # Previously, any non-retryable SQL failure could
                    # immediately force finalization. SQL syntax/schema
                    # mistakes are often recoverable by generating one
                    # corrected query.
                    if tool_name == "run_sql_query":
                        if error_data.get("type") == "SQL_CIRCUIT_OPEN":
                            force_finalize = True
                        elif (
                            runtime_circuit is not None
                            and runtime_circuit.is_open("sql")
                        ):
                            force_finalize = True
                        elif sql_call_count >= MAX_SQL_CALLS:
                            force_finalize = True
                        else:
                            force_finalize = False
                    elif not error_data.get(
                        "retryable",
                        False,
                    ):
                        force_finalize = True

            log_event(
                "TOOL",
                (
                    f"Analyst tool completed | tool={tool_name} "
                    f"| ok={payload.get('ok', False)} "
                    f"| latency_ms={payload.get('latency_ms', 0)}"
                ),
            )

            output_messages.append(
                ToolMessage(
                    content=_tool_payload_to_text(payload),
                    tool_call_id=call["id"],
                )
            )

        return {
            "messages": output_messages,
            "seen_tool_calls": seen,
            "tool_call_count": tool_call_count,
            "sql_call_count": sql_call_count,
            "successful_tool_calls": successful,
            "failed_tool_calls": failed,
            "force_finalize": force_finalize,
        }

    # ========================================================
    # FORCED FINALIZER
    # ========================================================

    def finalizer_node(state: AnalystState):

        log_event(
            "AGENT",
            "Data Analyst finalizer invoked",
        )

        original_task = ""

        for message in state.get(
            "messages",
            [],
        ):
            if message.type == "human":
                content = getattr(
                    message,
                    "content",
                    "",
                )
                if isinstance(content, str):
                    original_task = content

        evidence_blocks = []

        for message in state.get(
            "messages",
            [],
        ):
            if not isinstance(
                message,
                ToolMessage,
            ):
                continue

            try:
                payload = json.loads(
                    message.content
                )
            except Exception:
                continue

            tool_name = payload.get("tool")

            # Keep SQL/calculator evidence and errors.
            # Exclude large schema payload from finalizer context.
            if tool_name not in {
                "run_sql_query",
                "calculator",
            }:
                continue

            compact_payload = {
                "ok": payload.get(
                    "ok",
                    False,
                ),
                "tool": tool_name,
                "data": payload.get("data"),
                "error": payload.get("error"),
            }

            evidence_blocks.append(
                json.dumps(
                    compact_payload,
                    ensure_ascii=False,
                    default=str,
                )
            )

        evidence_text = (
            "\n\n".join(evidence_blocks)
            if evidence_blocks
            else (
                "No usable SQL or calculator evidence "
                "was collected."
            )
        )

        finalizer_user_message = (
            "Assigned task:\n"
            f"{original_task}\n\n"
            "Collected tool evidence:\n"
            f"{evidence_text}\n\n"
            "Produce the final supported analysis now."
        )

        messages = [
            SystemMessage(
                content=FINALIZER_PROMPT
            ),
            HumanMessage(
                content=finalizer_user_message
            ),
        ]

        try:
            response = invoke_llm_with_retry(
                base_llm,
                messages,
                component="Data Analyst Finalizer",
            )

        except Exception as exc:
            log_event(
                "AGENT",
                (
                    "Data Analyst finalizer failed "
                    f"| {type(exc).__name__}: {exc}"
                ),
            )

            successful = state.get(
                "successful_tool_calls",
                0,
            )

            return {
                "messages": [
                    AIMessage(
                        content=(
                            "The Data Analyst collected evidence "
                            "but could not synthesize the final answer."
                        )
                    )
                ],
                "status": (
                    "degraded"
                    if successful > 0
                    else "failed"
                ),
                "guard_hit": True,
            }

        failed_tools = state.get(
            "failed_tool_calls",
            0,
        )

        return {
            "messages": [response],
            "status": (
                "degraded"
                if failed_tools > 0
                else "completed"
            ),
        }

    # ========================================================
    # ROUTING
    # ========================================================

    def route_after_agent(state: AnalystState):

        last_message = state[
            "messages"
        ][-1]

        tool_calls = getattr(
            last_message,
            "tool_calls",
            None,
        ) or []

        if not tool_calls:
            return END

        if state.get(
            "step_count",
            0,
        ) >= MAX_AGENT_STEPS:
            log_event(
                "GUARD",
                "Analyst hard step limit reached",
            )
            return "finalizer"

        return "tools"

    def route_after_tools(state: AnalystState):

        if state.get(
            "force_finalize",
            False,
        ):
            return "finalizer"

        return "agent"

    # ========================================================
    # GRAPH DEFINITION
    # ========================================================

    graph = StateGraph(
        AnalystState
    )

    graph.add_node(
        "agent",
        agent_node,
    )

    graph.add_node(
        "tools",
        tool_node,
    )

    graph.add_node(
        "finalizer",
        finalizer_node,
    )

    graph.set_entry_point(
        "agent"
    )

    graph.add_conditional_edges(
        "agent",
        route_after_agent,
        {
            "tools": "tools",
            "finalizer": "finalizer",
            END: END,
        },
    )

    graph.add_conditional_edges(
        "tools",
        route_after_tools,
        {
            "agent": "agent",
            "finalizer": "finalizer",
        },
    )

    graph.add_edge(
        "finalizer",
        END,
    )

    return graph.compile()
