from __future__ import annotations

from langgraph import graph
from app.core.runtime_budget import current_budget
from app.agents.critic_agent import (
    review_workflow,
)

from app.agents.action_agent import (
    create_proposed_action_request,
)
import time
from typing import TypedDict

from langchain_core.messages import (
    HumanMessage,
)

from langgraph.graph import (
    END,
    StateGraph,
)

from app.agents.data_analyst_agent import (
    build_data_analyst_graph,
)

from app.agents.planner_agent import (
    create_plan,
)

from app.agents.research_agent import (
    build_research_graph,
)

from app.agents.synthesis_agent import (
    synthesize_evidence,
)

from app.core.logging import log_event
from app.core.document_rag import retrieve_passages

from app.models.plan_models import (
    ExecutionPlan,
    TaskResult,
)

from app.models.workspace_models import (
    WorkspaceContext,
)


MAX_WORKFLOW_STEPS = 12
MAX_TASKS = 5

# Prevent giant upstream outputs from exploding
# LLM context/token usage.
MAX_DEPENDENCY_CHARS = 3500


class BusinessWorkflowState(
    TypedDict,
    total=False,
):
    run_id: str

    goal: str

    workspace: dict

    plan: dict

    current_task_index: int

    task_results: list[dict]

    workflow_steps: int

    runtime_budget: dict

    runtime_circuit: dict

    synthesis_result: dict

    critic_report: dict

    action_request: dict

    action_status: str

    status: str

    final_output: str


def build_business_workflow():

    analyst_graph = (
        build_data_analyst_graph()
    )

    research_graph = (
        build_research_graph()
    )

    def next_step(
        state: BusinessWorkflowState,
    ):
        current_step = state.get(
            "workflow_steps",
            0,
        )

        runtime_budget = current_budget()

        # A different hard budget (tool/token/cost/LLM) may already have
        # stopped the workflow. Do not consume or falsely report another
        # workflow step in that case.
        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            return current_step

        attempted_step = current_step + 1

        if runtime_budget is not None:
            allowed = runtime_budget.consume_workflow_step()

            if not allowed:
                # consume_workflow_step() rejected the attempted step.
                # Keep durable workflow_steps at the last accepted value.
                limit = (
                    runtime_budget.to_dict()
                    .get("limits", {})
                    .get("max_workflow_steps", MAX_WORKFLOW_STEPS)
                )

                log_event(
                    "CIRCUIT_BREAKER",
                    (
                        "Workflow step budget exceeded "
                        f"| attempted_step={attempted_step} "
                        f"| limit={limit} "
                        f"| code={runtime_budget.stop_code}"
                    ),
                )

                return current_step

        return attempted_step
    # ========================================================
    # WORKSPACE
    # ========================================================

    def get_workspace(
        state: BusinessWorkflowState,
    ) -> WorkspaceContext:

        raw = state.get(
            "workspace"
        )

        if not raw:
            return WorkspaceContext()

        return (
            WorkspaceContext
            .model_validate(raw)
        )

    # ========================================================
    # DEPENDENCY EVIDENCE
    # ========================================================

    def get_dependency_context(
        task,
        task_results: list[dict],
    ) -> tuple[str, bool]:

        if not task.depends_on:
            return (
                "No upstream dependencies.",
                False,
            )

        results_by_id = {
            item["task_id"]: item
            for item in task_results
        }

        sections = []

        has_blocking_dependency = False

        for dependency_id in (
            task.depends_on
        ):

            result = results_by_id.get(
                dependency_id
            )

            if result is None:

                sections.append(
                    f"""
Dependency Task {dependency_id}
Status: MISSING
Evidence: No upstream result was produced.
""".strip()
                )

                has_blocking_dependency = True

                continue

            status = result.get(
                "status",
                "failed",
            )

            output = (
                result.get(
                    "output"
                )
                or "No usable output."
            )

            # Context-size protection
            if len(output) > MAX_DEPENDENCY_CHARS:
                output = (
                    output[
                        :MAX_DEPENDENCY_CHARS
                    ]
                    + "\n...[evidence truncated]"
                )

            sections.append(
                f"""
Dependency Task {dependency_id}
Agent: {result.get("agent")}
Status: {status.upper()}

Evidence:
{output}
""".strip()
            )

            if status in {
                "failed",
                "blocked",
            }:
                has_blocking_dependency = True

        return (
            "\n\n".join(
                sections
            ),
            has_blocking_dependency,
        )

    # ========================================================
    # PLANNER
    # ========================================================

    def planner_node(
        state: BusinessWorkflowState,
    ):

        step = next_step(
            state
        )
        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            return {
                "workflow_steps": step,
                "runtime_budget": runtime_budget.to_dict(),
                "status": "budget_exceeded",
            }

        log_event(
            "WORKFLOW",
            (
                "Planner node entered "
                f"| step {step}"
            ),
        )

        if step > MAX_WORKFLOW_STEPS:

            return {
                "workflow_steps": step,
                "status": "blocked",
                "final_output": (
                    "Workflow stopped by "
                    "hard workflow step limit."
                ),
            }

        goal = state.get(
            "goal",
            "",
        ).strip()

        if not goal:

            return {
                "workflow_steps": step,
                "status": "failed",
                "final_output": (
                    "No business goal provided."
                ),
            }

        workspace = get_workspace(
            state
        )

        try:

            plan = create_plan(
                goal,
                workspace=workspace,
            )

        except Exception as exc:

            log_event(
                "WORKFLOW",
                (
                    "Planner failed "
                    f"| {type(exc).__name__}"
                ),
            )

            return {
                "workflow_steps": step,
                "status": "failed",
                "final_output": (
                    "Planner could not create "
                    "a valid execution plan."
                ),
            }

        if len(plan.tasks) > MAX_TASKS:

            return {
                "workflow_steps": step,
                "status": "blocked",
                "final_output": (
                    "Planner generated too "
                    "many tasks."
                ),
            }

        return {
            "plan": plan.model_dump(),
            "current_task_index": 0,
            "task_results": [],
            "workflow_steps": step,
            "status": "running",
        }

    # ========================================================
    # EXECUTOR
    # ========================================================

    def execute_task_node(
        state: BusinessWorkflowState,
    ):

        step = next_step(
            state
        )

        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            return {
                "workflow_steps": step,
                "runtime_budget": runtime_budget.to_dict(),
                "status": "budget_exceeded",
            }

        if step > MAX_WORKFLOW_STEPS:

            return {
                "workflow_steps": step,
                "status": "blocked",
                "final_output": (
                    "Workflow stopped by "
                    "hard workflow step limit."
                ),
            }

        plan = (
            ExecutionPlan
            .model_validate(
                state["plan"]
            )
        )

        index = state.get(
            "current_task_index",
            0,
        )

        if index >= len(plan.tasks):

            return {
                "workflow_steps": step,
                "status": (
                    "ready_to_finalize"
                ),
            }

        task = plan.tasks[
            index
        ]

        previous_results = list(
            state.get(
                "task_results",
                [],
            )
        )
        workspace = get_workspace(
            state
        )

        dependency_context, dependency_blocked = (
            get_dependency_context(
                task,
                previous_results,
            )
        )

        # ----------------------------------------------------
        # Hard dependency failure
        # ----------------------------------------------------

        if dependency_blocked:

            log_event(
                "GUARD",
                (
                    f"Task {task.id} blocked "
                    "because required upstream "
                    "evidence failed or is missing"
                ),
            )

            blocked_result = TaskResult(
                task_id=task.id,
                agent=task.agent,
                status="blocked",
                description=task.description,
                output=(
                    "Task was not executed because "
                    "required upstream evidence was "
                    "missing or failed."
                ),
                error=(
                    "DEPENDENCY_FAILURE"
                ),
                duration_ms=0,
            ).model_dump()

            previous_results.append(
                blocked_result
            )

        # If dependency handling itself caused a runtime budget stop,
        # do not start another worker. Otherwise continue into the real
        # Analyst/Research execution block below.
        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            log_event(
                "BUDGET",
                (
                    "Workflow budget exceeded before worker execution "
                    f"| code={runtime_budget.stop_code} "
                    f"| reason={runtime_budget.stop_reason}"
                ),
            )

            return {
                "task_results": previous_results,
                "current_task_index": index,
                "workflow_steps": step,
                "runtime_budget": runtime_budget.to_dict(),
                "status": "budget_exceeded",
            }

        # ----------------------------------------------------
        # Execute worker
        # ----------------------------------------------------

        log_event(
            "ROUTER",
            (
                f"Task {task.id} "
                f"→ {task.agent}"
            ),
        )

        started = time.perf_counter()

        output = None
        error = None
        worker_status = "failed"
        structured_evidence = []

        workspace_context = (
            workspace.to_agent_context()
        )

        try:

            # =================================================
            # ANALYST
            # =================================================

            if task.agent == "analyst":

                prompt = f"""
{workspace_context}

BUSINESS GOAL

{plan.goal}


ASSIGNED ANALYST TASK

{task.description}


TASK REASON

{task.reason}


UPSTREAM DEPENDENCY EVIDENCE

{dependency_context}


DEPENDENCY RULE

Use upstream evidence only as supplied.

If an upstream dependency is DEGRADED,
explicitly treat its evidence as incomplete.

Do not recreate missing Research Agent evidence
from memory.

Complete only the assigned task.

Use the smallest sufficient number of SQL/tool calls.
"""

                result = (
                    analyst_graph.invoke(
                        {
                            "messages": [
                                HumanMessage(
                                    content=prompt
                                )
                            ],
                            "step_count": 0,
                            "tool_call_count": 0,
                            "seen_tool_calls": [],
                            "successful_tool_calls": 0,
                            "failed_tool_calls": 0,
                            "guard_hit": False,
                            "status": "running",
                        },
                        config={
                            "recursion_limit": 15
                        },
                    )
                )

            # =================================================
            # RESEARCH
            # =================================================

            elif task.agent == "research":

                prompt = f"""
{workspace_context}

BUSINESS GOAL

{plan.goal}


ASSIGNED RESEARCH TASK

{task.description}


TASK REASON

{task.reason}


UPSTREAM DEPENDENCY EVIDENCE

{dependency_context}


Complete only this external research task.

Use supplied upstream internal evidence when
the research task depends on it.

GROUNDING CONTRACT:
- Research ONLY entities/categories/regions/products that are explicitly
  established in the upstream evidence.
- Do not infer category or premium/budget positioning from product IDs or
  names unless the upstream evidence explicitly establishes it.
- Do not compare competitor prices against this business unless upstream
  evidence contains a comparable internal price.
- If upstream evidence is insufficient for the requested comparison, state
  that limitation rather than broadening the claim.

Use live web evidence when current market
information is required.

Do not invent internal business values.
"""

                result = (
                    research_graph.invoke(
                        {
                            "messages": [
                                HumanMessage(
                                    content=prompt
                                )
                            ],
                            "step_count": 0,
                            "search_count": 0,
                            "seen_tool_calls": [],
                            "successful_tool_calls": 0,
                            "failed_tool_calls": 0,
                            "guard_hit": False,
                            "status": "running",
                        },
                        config={
                            "recursion_limit": 12
                        },
                    )
                )

            # =================================================
            # UNKNOWN WORKER
            # =================================================

            else:

                result = None

                worker_status = "failed"

                error = (
                    "Planner attempted to use "
                    "an unavailable worker."
                )

                log_event(
                    "GUARD",
                    (
                        "Unknown worker blocked "
                        f"| {task.agent}"
                    ),
                )

            # -------------------------------------------------
            # Worker output
            # -------------------------------------------------

            if result is not None:

                worker_status = (
                    result.get(
                        "status",
                        "failed",
                    )
                )

                guard_hit = (
                    result.get(
                        "guard_hit",
                        False,
                    )
                )

                if result.get(
                    "messages"
                ):

                    for message in result["messages"]:
                        if getattr(message, "type", None) != "tool":
                            continue
                        try:
                            payload = json.loads(
                                getattr(message, "content", "")
                            )
                        except (TypeError, ValueError):
                            continue
                        if not payload.get("ok"):
                            continue
                        if payload.get("tool") not in {
                            "run_sql_query",
                            "calculator",
                        }:
                            continue
                        structured_evidence.append({
                            "tool": payload.get("tool"),
                            "data": payload.get("data"),
                            "observed": True,
                        })

                    # -------------------------------------------------
                    # Robust worker-output extraction
                    # -------------------------------------------------
                    #
                    # Do not assume the final LangGraph message always
                    # contains the worker's answer. A run can end with
                    # an empty AI/tool message even though an earlier
                    # assistant message contains the actual analysis.
                    #
                    # Scan backwards and take the most recent non-empty
                    # assistant text response.
                    # -------------------------------------------------

                    def _message_text(
                        message,
                    ) -> str:

                        content = getattr(
                            message,
                            "content",
                            "",
                        )

                        if isinstance(
                            content,
                            str,
                        ):
                            return content.strip()

                        if isinstance(
                            content,
                            list,
                        ):
                            parts = []

                            for item in content:

                                if isinstance(
                                    item,
                                    str,
                                ):
                                    parts.append(
                                        item
                                    )

                                elif isinstance(
                                    item,
                                    dict,
                                ):

                                    text_part = (
                                        item.get("text")
                                        or item.get("content")
                                    )

                                    if isinstance(
                                        text_part,
                                        str,
                                    ):
                                        parts.append(
                                            text_part
                                        )

                            return (
                                "\n".join(parts)
                                .strip()
                            )

                        return ""

                    for message in reversed(
                        result["messages"]
                    ):

                        # Only use assistant/AI messages as the
                        # final business-facing worker output.
                        if getattr(
                            message,
                            "type",
                            None,
                        ) != "ai":
                            continue

                        candidate = (
                            _message_text(
                                message
                            )
                        )

                        if candidate:
                            output = candidate
                            break

                # -------------------------------------------------
                # Output integrity guard
                # -------------------------------------------------
                #
                # A worker must never be marked COMPLETED without a
                # usable business-facing answer. This prevents the
                # Critic and Action Gate from treating an empty result
                # as trustworthy evidence.
                # -------------------------------------------------

                if (
                    worker_status
                    == "completed"
                    and not (
                        output
                        and output.strip()
                    )
                ):

                    log_event(
                        "GUARD",
                        (
                            "Worker reported completed "
                            "without usable output "
                            f"| task {task.id}"
                        ),
                    )

                    worker_status = (
                        "degraded"
                    )

                    error = (
                        "EMPTY_WORKER_OUTPUT"
                    )

                    output = (
                        "The worker completed its internal "
                        "processing but did not produce a "
                        "usable final response."
                    )

                if (
                    guard_hit
                    and worker_status
                    == "completed"
                ):
                    # Defensive protection:
                    # guard + completed is inconsistent.
                    worker_status = "degraded"

                if guard_hit:

                    log_event(
                        "GUARD",
                        (
                            "Worker guard propagated "
                            f"| task {task.id} "
                            f"| {worker_status}"
                        ),
                    )

        except Exception as exc:

            worker_status = "failed"

            error = (
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            log_event(
                "WORKER",
                (
                    f"{task.agent} failed "
                    f"| task {task.id}"
                ),
            )

        duration_ms = int(
            (
                time.perf_counter()
                - started
            )
            * 1000
        )

        valid_statuses = {
            "completed",
            "degraded",
            "failed",
            "blocked",
        }

        if worker_status not in valid_statuses:
            worker_status = "failed"

        task_result = TaskResult(
            task_id=task.id,
            agent=task.agent,
            status=worker_status,
            description=task.description,
            output=output,
            error=error,
            duration_ms=duration_ms,
            structured_evidence=structured_evidence if result is not None else [],
        ).model_dump()

        previous_results.append(
            task_result
        )

        log_event(
            "WORKER",
            (
                f"Task {task.id} "
                f"{worker_status.upper()} "
                f"| {duration_ms} ms"
            ),
        )

        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            log_event(
                "BUDGET",
                (
                    "Workflow budget exceeded after worker task "
                    f"| code={runtime_budget.stop_code} "
                    f"| reason={runtime_budget.stop_reason}"
                ),
            )

            return {
                "task_results": previous_results,
                "current_task_index": index + 1,
                "workflow_steps": step,
                "runtime_budget": runtime_budget.to_dict(),
                "status": "budget_exceeded",
            }

        return {
            "task_results": previous_results,
            "current_task_index": index + 1,
            "workflow_steps": step,
            "runtime_budget": (
                runtime_budget.to_dict()
                if runtime_budget is not None
                else state.get("runtime_budget", {})
            ),
            "status": "running",
        }
        # ========================================================
    # SAFETY / CRITIC
    # ========================================================

    def synthesis_node(
        state: BusinessWorkflowState,
    ):
        step = next_step(state)
        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            return {
                "workflow_steps": step,
                "runtime_budget": runtime_budget.to_dict(),
                "status": "budget_exceeded",
            }
        log_event("WORKFLOW", f"Evidence synthesis node entered | step {step}")
        workspace = get_workspace(state)
        chunks = []
        for result in state.get("task_results", []):
            if result.get("status") in {"completed", "degraded"}:
                chunks.append(
                    f"Task {result.get('task_id')} ({result.get('agent')}):\n"
                    f"{result.get('output', '')}"
                )
        evidence = "\n\n".join(chunks)
        result = (
            synthesize_evidence(
                goal=state["goal"],
                workspace_context=workspace.to_agent_context(),
                evidence=evidence,
            )
            if evidence.strip()
            else {"status":"skipped","content":"No completed worker evidence was available."}
        )
        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            log_event(
                "CIRCUIT_BREAKER",
                (
                    "Budget exceeded during Evidence Synthesis "
                    f"| code={runtime_budget.stop_code} "
                    f"| reason={runtime_budget.stop_reason}"
                ),
            )

            return {
                "synthesis_result": result,
                "workflow_steps": step,
                "runtime_budget": runtime_budget.to_dict(),
                "status": "budget_exceeded",
            }

        return {
            "synthesis_result": result,
            "workflow_steps": step,
            "runtime_budget": (
                runtime_budget.to_dict()
                if runtime_budget is not None
                else state.get("runtime_budget", {})
            ),
            "status": "synthesis_complete",
        }

    def critic_node(
        state: BusinessWorkflowState,
    ):

        step = next_step(
            state
        )

        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            return {
                "workflow_steps": step,
                "runtime_budget": runtime_budget.to_dict(),
                "status": "budget_exceeded",
            }

        log_event(
            "WORKFLOW",
            (
                "Safety/Critic node entered "
                f"| step {step}"
            ),
        )

        if step > MAX_WORKFLOW_STEPS:

            return {
                "workflow_steps": step,
                "status": "blocked",
                "final_output": (
                    "Workflow stopped by hard "
                    "workflow step limit."
                ),
            }

        workspace = get_workspace(
            state
        )

        try:

            try:
                document_evidence = retrieve_passages(
                    workspace_id=workspace.workspace_id,
                    query=(
                        f"{state['goal']} workspace policy approval "
                        "restrictions requirements"
                    ),
                    top_k=4,
                )
            except Exception as exc:
                log_event(
                    "CRITIC",
                    (
                        "Workspace document retrieval degraded "
                        f"| {type(exc).__name__}: {exc}"
                    ),
                )
                document_evidence = []

            critic_results = list(state.get("task_results", []))
            synthesis = state.get("synthesis_result", {})
            if synthesis.get("content"):
                critic_results.append({
                    "task_id": 0,
                    "agent": "analyst",
                    "status": "completed" if synthesis.get("status") == "completed" else "degraded",
                    "description": "Evidence synthesis (review-only; not a worker execution task)",
                    "output": synthesis.get("content", ""),
                    "error": None,
                    "duration_ms": 0,
                })

            report = review_workflow(
                goal=state["goal"],
                workspace=workspace,
                task_results=critic_results,
                document_evidence=document_evidence,
            )

            return {
                "critic_report": (
                    report.model_dump()
                ),
                "workflow_steps": step,
                "status": (
                    "critic_complete"
                ),
            }

        except Exception as exc:

            log_event(
                "CRITIC",
                (
                    "Critic failed unexpectedly "
                    f"| {type(exc).__name__}"
                ),
            )

            return {
                "critic_report": {
                    "verdict": (
                        "review_required"
                    ),
                    "overall_confidence": 0.0,
                    "evidence_quality": "low",
                    "requires_human_approval": True,
                    "findings": [
                        {
                            "category": "evidence",
                            "severity": "critical",
                            "message": (
                                "Safety/Critic evaluation "
                                "could not be completed."
                            ),
                        }
                    ],
                    "summary": (
                        "Manual review required because "
                        "the Critic Agent failed."
                    ),
                },
                "workflow_steps": step,
                "status": (
                    "critic_complete"
                ),
            }
    # ========================================================
    # ACTION GATE
    # ========================================================

    def action_gate_node(
        state: BusinessWorkflowState,
    ):

        step = next_step(
            state
        )
        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            return {
                "workflow_steps": step,
                "runtime_budget": runtime_budget.to_dict(),
                "status": "budget_exceeded",
                "action_status": "blocked_insufficient_evidence",
            }

        log_event(
            "WORKFLOW",
            (
                "Action gate entered "
                f"| step {step}"
            ),
        )

        if step > MAX_WORKFLOW_STEPS:

            return {
                "workflow_steps": step,
                "status": "blocked",
                "final_output": (
                    "Workflow stopped by hard "
                    "workflow step limit."
                ),
            }

        critic = state.get(
            "critic_report",
            {},
        )

        verdict = critic.get(
            "verdict",
            "review_required",
        )

        task_results = state.get(
            "task_results",
            [],
        )

        # ----------------------------------------------------
        # Hard evidence safety gate
        # ----------------------------------------------------
        #
        # Human approval may authorize a POLICY-RESTRICTED
        # action, but it must never override missing, failed,
        # contradictory, or critically unsafe evidence.
        #
        # Examples:
        # - Analyst failed -> BLOCK action creation
        # - Dependency failed -> BLOCK action creation
        # - Critical evidence/consistency finding -> BLOCK
        # - Policy threshold exceeded -> approval is allowed
        #   because analysis may be valid; execution simply
        #   waits for a human.
        # ----------------------------------------------------

        has_failed_worker = any(
            item.get("status") in {
                "failed",
                "blocked",
            }
            for item in task_results
        )

        critical_non_policy_findings = [
            finding
            for finding in critic.get(
                "findings",
                [],
            )
            if (
                finding.get("severity")
                == "critical"
                and finding.get("category")
                != "policy"
            )
        ]

        evidence_quality = critic.get(
            "evidence_quality",
            "low",
        )

        if (
            verdict == "blocked"
            or has_failed_worker
            or critical_non_policy_findings
            or evidence_quality == "low"
        ):

            reasons = []

            if verdict == "blocked":
                reasons.append(
                    "critic verdict is BLOCKED"
                )

            if has_failed_worker:
                reasons.append(
                    "one or more required worker tasks failed or were blocked"
                )

            if critical_non_policy_findings:
                reasons.append(
                    "critical non-policy safety/evidence findings exist"
                )

            if evidence_quality == "low":
                reasons.append(
                    "critic evidence quality is LOW"
                )

            log_event(
                "ACTION",
                (
                    "Action creation blocked "
                    "| "
                    + "; ".join(reasons)
                ),
            )

            return {
                "workflow_steps": step,
                "action_status": (
                    "blocked_insufficient_evidence"
                ),
                "status": "action_gate_complete",
            }

        workspace = get_workspace(
            state
        )

        try:

            request = (
                create_proposed_action_request(
                    workspace_id=(
                        workspace.workspace_id
                    ),
                    goal=state.get(
                        "goal",
                        "",
                    ),
                    critic_report=critic,
                )
            )

        except Exception as exc:

            log_event(
                "ACTION",
                (
                    "Action proposal failed "
                    f"| {type(exc).__name__}"
                ),
            )

            return {
                "workflow_steps": step,
                "action_status": "proposal_failed",
                "status": "action_gate_complete",
            }

        if request is None:

            return {
                "workflow_steps": step,
                "action_status": "no_action_proposed",
                "status": "action_gate_complete",
            }

        if request.status == "pending_approval":
            action_status = (
                "waiting_for_approval"
            )

        elif request.status == "approved":
            action_status = (
                "ready_for_execution"
            )

        else:
            action_status = request.status

        return {
            "action_request": (
                request.model_dump()
            ),
            "action_status": action_status,
            "workflow_steps": step,
            "status": "action_gate_complete",
        }


    # ========================================================
    # FINALIZER
    # ========================================================

    def finalize_node(
        state: BusinessWorkflowState,
    ):

        step = next_step(
            state
        )

        log_event(
            "WORKFLOW",
            "Combining worker evidence",
        )

        plan = (
            ExecutionPlan
            .model_validate(
                state["plan"]
            )
        )

        results = state.get(
            "task_results",
            [],
        )

        critic = state.get(
            "critic_report",
            {},
        )

        lines = [
            "SYNAPSEOPS WORKFLOW RESULT",
            "=" * 60,
            "",
            "Goal:",
            plan.goal,
            "",
            "Execution Plan:",
        ]

        for task in plan.tasks:

            dependency_text = (
                (
                    f" | depends_on="
                    f"{task.depends_on}"
                )
                if task.depends_on
                else ""
            )

            lines.append(
                (
                    f"{task.order}. "
                    f"[{task.agent.upper()}] "
                    f"{task.description}"
                    f"{dependency_text}"
                )
            )

        lines.extend(
            [
                "",
                "Worker Evidence:",
            ]
        )

        counts = {
            "completed": 0,
            "degraded": 0,
            "failed": 0,
            "blocked": 0,
        }

        for item in results:

            status = item.get(
                "status",
                "failed",
            )

            if status in counts:
                counts[
                    status
                ] += 1

            lines.extend(
                [
                    "",
                    "-" * 60,
                    (
                        f"Task "
                        f"{item['task_id']} "
                        f"| "
                        f"{item['agent'].upper()} "
                        f"| "
                        f"{status.upper()}"
                    ),
                    (
                        "Duration: "
                        f"{item['duration_ms']} ms"
                    ),
                ]
            )

            if item.get("output"):

                lines.extend(
                    [
                        "",
                        "Result:",
                        item["output"],
                    ]
                )

            if item.get("error"):

                lines.extend(
                    [
                        "",
                        "Error:",
                        item["error"],
                    ]
                )

        if critic:
            lines.extend(
                [
                    "",
                    "=" * 60,
                    "",
                    "SAFETY / CRITIC REVIEW",
                    "",
                    (
                        "Verdict: "
                        f"{critic.get('verdict', 'unknown').upper()}"
                    ),
                    (
                        "Evidence Quality: "
                        f"{critic.get('evidence_quality', 'unknown').upper()}"
                    ),
                    (
                        "Overall Confidence: "
                        f"{critic.get('overall_confidence', 0):.2f}"
                    ),
                    (
                        "Human Approval Required: "
                        f"{critic.get('requires_human_approval', False)}"
                    ),
                    "",
                    "Critic Summary:",
                    critic.get(
                        "summary",
                        "No critic summary.",
                    ),
                ]
            )

            findings = critic.get(
                "findings",
                [],
            )

            if findings:
                lines.extend(
                    [
                        "",
                        "Critic Findings:",
                    ]
                )

                for finding in findings:
                    lines.append(
                        (
                            "- "
                            f"[{finding.get('severity', 'info').upper()}] "
                            f"{finding.get('category', 'unknown')}: "
                            f"{finding.get('message', '')}"
                        )
                    )

        action_request = state.get(
            "action_request",
            {},
        )

        action_status = state.get(
            "action_status",
            "no_action_proposed",
        )

        if action_request:

            lines.extend(
                [
                    "",
                    "=" * 60,
                    "",
                    "ACTION GATE",
                    "",
                    (
                        "Request ID: "
                        f"{action_request.get('request_id')}"
                    ),
                    (
                        "Type: "
                        f"{action_request.get('action_type', 'unknown').upper()}"
                    ),
                    (
                        "Status: "
                        f"{action_status.upper()}"
                    ),
                    (
                        "Human Approval Required: "
                        f"{action_request.get('requires_human_approval', False)}"
                    ),
                    (
                        "Description: "
                        f"{action_request.get('description', '')}"
                    ),
                ]
            )

            if action_status == "waiting_for_approval":
                lines.append(
                    (
                        "Execution is locked until this "
                        "request is explicitly approved."
                    )
                )

        elif action_status in {
            "blocked_by_critic",
            "blocked_insufficient_evidence",
        }:

            lines.extend(
                [
                    "",
                    "=" * 60,
                    "",
                    "ACTION GATE",
                    "",
                    (
                        "Status: "
                        f"{action_status.upper()}"
                    ),
                    (
                        "No executable action request was created. "
                        "Human approval cannot override failed, "
                        "blocked, or critically insufficient evidence."
                    ),
                ]
            )

        lines.extend(
            [
                "",
                "=" * 60,
                "",
                (
                    "Workflow Summary: "
                    f"{counts['completed']} completed, "
                    f"{counts['degraded']} degraded, "
                    f"{counts['failed']} failed, "
                    f"{counts['blocked']} blocked."
                ),
            ]
        )

        if (
            counts["failed"] > 0
            or counts["blocked"] > 0
        ):
            final_status = "completed_with_errors"
            lines.append(
                "Some required tasks could not be completed."
            )

        elif counts["degraded"] > 0:
            final_status = "completed_degraded"
            lines.append(
                (
                    "Some evidence is incomplete "
                    "or reliability safeguards fired."
                )
            )

        else:
            final_status = "completed"

        return {
            "workflow_steps": step,
            "status": final_status,
            "final_output": "\n".join(lines),
        }


    # ========================================================
    # RESUME ROUTER
    # ========================================================

    def resume_router_node(state: BusinessWorkflowState):
        # No work here. The conditional edge below chooses the first
        # unfinished stage from the durable checkpoint.
        return {}

    def resume_target(state: BusinessWorkflowState):
        status = state.get("status", "running")

        if status in {
            "completed",
            "completed_degraded",
            "completed_with_errors",
            "failed",
            "blocked",
        }:
            return END

        if not state.get("plan"):
            return "planner"

        plan = ExecutionPlan.model_validate(state["plan"])
        index = state.get("current_task_index", 0)

        if index < len(plan.tasks):
            return "execute_task"

        if not state.get("synthesis_result"):
            return "synthesis"

        if not state.get("critic_report"):
            return "critic"

        if state.get("action_status") is None:
            return "action_gate"

        if not state.get("final_output"):
            return "finalize"

        return END
        # ========================================================
    # M12 — HARD BUDGET STOP
    # ========================================================

    def budget_stop_node(
        state: BusinessWorkflowState,
    ):
        runtime_budget = current_budget()

        if runtime_budget is not None:
            budget_data = runtime_budget.to_dict()
            stop_code = (
                runtime_budget.stop_code
                or "WORKFLOW_BUDGET_EXCEEDED"
            )
            stop_reason = (
                runtime_budget.stop_reason
                or "A workflow runtime budget was exceeded."
            )
        else:
            budget_data = state.get(
                "runtime_budget",
                {},
            )
            stop_code = budget_data.get(
                "stop_code",
                "WORKFLOW_BUDGET_EXCEEDED",
            )
            stop_reason = budget_data.get(
                "stop_reason",
                "A workflow runtime budget was exceeded.",
            )

        log_event(
            "CIRCUIT_BREAKER",
            (
                "Workflow execution stopped safely "
                f"| code={stop_code} "
                f"| reason={stop_reason}"
            ),
        )

        critic_report = {
            "verdict": "blocked",
            "evidence_quality": "low",
            "overall_confidence": 0.0,
            "requires_human_approval": False,
            "summary": (
                "The workflow was stopped by a runtime "
                "safety budget. No executable action may "
                "be created from this incomplete run."
            ),
            "findings": [
                {
                    "severity": "critical",
                    "category": "runtime_budget",
                    "message": stop_reason,
                }
            ],
        }

        synthesis_result = state.get(
            "synthesis_result"
        ) or {
            "status": "skipped",
            "content": (
                "Evidence synthesis was skipped because "
                "a hard workflow runtime budget was exceeded."
            ),
        }

        return {
            "runtime_budget": budget_data,
            "synthesis_result": synthesis_result,
            "critic_report": critic_report,
            "action_status": (
                "blocked_insufficient_evidence"
            ),
            "status": "budget_stopped",
        }

    # ========================================================
    # ROUTING
    # ========================================================
    # ========================================================
    # ROUTING
    # ========================================================

    def after_planner(
        state: BusinessWorkflowState,
    ):

        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            log_event(
                "CIRCUIT_BREAKER",
                (
                    "Budget exceeded after Planner "
                    f"| code={runtime_budget.stop_code}"
                ),
            )

            return "budget_stop"

        if state.get("status") != "running":
            return END

        return "execute_task"

    def after_task(
        state: BusinessWorkflowState,
    ):

        if state.get("status") == "budget_exceeded":
            return "budget_stop"

        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            log_event(
                "CIRCUIT_BREAKER",
                (
                    "Budget exceeded during worker execution "
                    f"| code={runtime_budget.stop_code}"
                ),
            )
            return "budget_stop"

        if state.get("status") != "running":
            return END

        plan = ExecutionPlan.model_validate(
            state["plan"]
        )

        if state.get("current_task_index", 0) < len(plan.tasks):
            return "execute_task"

        return "critic"

    def after_synthesis(
        state: BusinessWorkflowState,
    ):
        if state.get("status") == "budget_exceeded":
            return "budget_stop"

        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            log_event(
                "CIRCUIT_BREAKER",
                (
                    "Budget exceeded during Evidence Synthesis "
                    f"| code={runtime_budget.stop_code}"
                ),
            )
            return "budget_stop"

        return "critic"


    def after_critic(
        state: BusinessWorkflowState,
    ):
        if state.get("status") == "budget_exceeded":
            return "budget_stop"

        runtime_budget = current_budget()

        if (
            runtime_budget is not None
            and runtime_budget.exceeded
        ):
            log_event(
                "CIRCUIT_BREAKER",
                (
                    "Budget exceeded during Safety/Critic "
                    f"| code={runtime_budget.stop_code}"
                ),
            )
            return "budget_stop"

        return "action_gate"

    # ========================================================
    # GRAPH
    # ========================================================
    graph = StateGraph(
    BusinessWorkflowState
    )

    graph.add_node(
        "resume_router",
        resume_router_node,
    )

    graph.add_node(
        "planner",
        planner_node,
    )

    graph.add_node(
        "execute_task",
        execute_task_node,
    )

    graph.add_node(
        "synthesis",
        synthesis_node,
    )

    graph.add_node(
        "critic",
        critic_node,
    )

    graph.add_node(
        "budget_stop",
        budget_stop_node,
    )

    graph.add_node(
        "action_gate",
        action_gate_node,
    )

    graph.add_node(
        "finalize",
        finalize_node,
    )

    graph.set_entry_point(
        "resume_router"
    )

    # ========================================================
    # RESUME ROUTING
    # ========================================================

    graph.add_conditional_edges(
        "resume_router",
        resume_target,
        {
            "planner": "planner",
            "execute_task": "execute_task",
            "synthesis": "synthesis",
            "critic": "critic",
            "budget_stop": "budget_stop",
            "action_gate": "action_gate",
            "finalize": "finalize",
            END: END,
        },
    )

    # ========================================================
    # PLANNER ROUTING
    # ========================================================

    graph.add_conditional_edges(
        "planner",
        after_planner,
        {
            "execute_task": "execute_task",
            "budget_stop": "budget_stop",
            END: END,
        },
    )

    # ========================================================
    # WORKER ROUTING
    # ========================================================

    graph.add_conditional_edges(
        "execute_task",
        after_task,
        {
            "execute_task": "execute_task",
            "critic": "synthesis",
            "budget_stop": "budget_stop",
            END: END,
        },
    )

    # ========================================================
    # SYNTHESIS ROUTING
    # ========================================================

    graph.add_conditional_edges(
        "synthesis",
        after_synthesis,
        {
            "critic": "critic",
            "budget_stop": "budget_stop",
        },
    )

    # ========================================================
    # CRITIC ROUTING
    # ========================================================

    graph.add_conditional_edges(
        "critic",
         after_critic,
        {
            "action_gate": "action_gate",
            "budget_stop": "budget_stop",
        },
    )

    # ========================================================
    # BUDGET STOP
    # ========================================================

    graph.add_edge(
        "budget_stop",
        "finalize",
    )

    # ========================================================
    # NORMAL COMPLETION
    # ========================================================

    graph.add_edge(
        "action_gate",
        "finalize",
    )

    graph.add_edge(
        "finalize",
        END,
    )

    return graph.compile()