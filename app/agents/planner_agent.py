from __future__ import annotations

from app.core.config import get_llm
from app.core.logging import log_event
from app.core.reliability import invoke_llm_with_retry
from app.models.plan_models import ExecutionPlan, PlanTask
from app.models.workspace_models import WorkspaceContext


SYSTEM_PROMPT = """
You are the Planner Agent for SynapseOps AI.

Create the SMALLEST useful execution plan for the business goal.

Workers:
- analyst: internal database evidence, SQL, calculations
- research: current/external web evidence

SCHEMA-AWARE PLANNING RULES

1. Treat WORKSPACE METADATA as the source of truth for what internal
   evidence is available.

2. Before requesting a metric, check whether the supplied tables/columns
   can reasonably support it.

3. NEVER invent a standard business checklist.
   Do not automatically request revenue, profit, margin, returns,
   cancellations, demographics, regions, inventory, customer spend,
   or any other metric merely because it would be useful.

4. If the schema cannot support a requested metric:
   - do not pretend it can;
   - ask the Analyst to compute the closest supported evidence only when
     that evidence is useful;
   - explicitly note the missing field/relationship in the task.

5. Semantic mappings are hints, not proof. Prefer actual column names
   and supplied table metadata.

6. Prefer 1-2 tasks. Never exceed 5.

7. Combine related work instead of fragmenting it.

8. Dependencies may refer only to earlier task IDs.

9. Internal facts belong to Analyst.

10. Research is OPTIONAL. Create a Research task only when the USER GOAL
    actually requires external/current/competitor/market/regulatory
    evidence, or external evidence is necessary to answer the goal.
    Do not add web research to a purely internal-data request.

11. If Research needs internal categories/products/metrics first, make
    it depend on Analyst.

12. When Research depends on Analyst, tell Research to investigate ONLY
    entities/categories explicitly established by upstream evidence.

13. Never assign destructive execution to a worker. Convert it to
    analysis/recommendation. Execution is handled by the Action Gate.

14. Preserve quantity semantics exactly.
    "Should we reorder 5000 units?" means evaluate a proposed 5000-unit
    order. It does NOT mean "make total inventory equal to 5000."

15. Never invent target-stock, margin, return, demand, or other formulas
    unless the business goal, workspace policy, or supplied schema/data
    actually defines the required inputs.

16. Do not invent business data.

17. Do NOT create an Analyst task whose main purpose is to synthesize already-computed metrics, identify insights from previous tasks, or produce recommendations from previous worker output. Final synthesis happens after workers in a dedicated stage.

18. Analyst tasks must primarily require database evidence. Research tasks must primarily require external evidence.
"""


def _fallback(
    goal: str,
    workspace: WorkspaceContext,
) -> ExecutionPlan:
    lower = goal.lower()

    external_terms = (
        "current market",
        "latest market",
        "competitor",
        "industry",
        "regulation",
        "external",
        "web research",
        "market trend",
    )

    wants_external = any(
        term in lower
        for term in external_terms
    )

    tasks = [
        PlanTask(
            id=1,
            order=1,
            agent="analyst",
            description=(
                "Use only the internal metrics and dimensions that are "
                "actually supported by the supplied workspace schema to "
                f"answer this goal: {goal}"
            ),
            reason=(
                "The goal should first be grounded in available internal "
                "business evidence."
            ),
            confidence=0.75,
        )
    ]

    if wants_external:
        tasks.append(
            PlanTask(
                id=2,
                order=2,
                agent="research",
                description=(
                    "Research only the external entities, categories, or "
                    "market questions that are explicitly grounded in Task 1 "
                    "and required by the business goal."
                ),
                reason=(
                    "The goal explicitly requires external/current evidence."
                ),
                confidence=0.70,
                depends_on=[1],
            )
        )

    return ExecutionPlan(
        goal=goal,
        tasks=tasks,
    )



# Deterministic planner guard.
# The LLM may propose useful metrics, but it is not allowed to hand one
# oversized "business checklist" to a worker. This layer is intentionally
# generic: it does not hardcode NovaMart, Acme, Olist, or retail columns.

MAX_METRICS_PER_ANALYST_TASK = 5
MAX_TASK_DESCRIPTION_CHARS = 950

_METRIC_HINTS = {
    "revenue": (("revenue", "sales", "amount", "price", "value"),),
    "profit": (("profit", "margin", "cost", "revenue", "sales"),),
    "return": (("return", "refund"),),
    "cancellation": (("cancel", "status"),),
    "rating": (("rating", "review", "score"),),
    "discount": (("discount", "coupon", "promotion", "promo"),),
    "shipping": (("shipping", "freight", "delivery", "ship"),),
    "supplier": (("supplier", "vendor"),),
    "brand": (("brand", "manufacturer"),),
    "category": (("category", "department", "segment"),),
    "customer": (("customer", "client", "buyer"),),
    "order": (("order", "invoice", "transaction"),),
    "inventory": (("stock", "inventory", "quantity", "qty"),),
}

_METRIC_PHRASES = (
    "total revenue",
    "total profit",
    "average order value",
    "return rate",
    "cancellation rate",
    "top product categories",
    "top categories",
    "top brands",
    "top suppliers",
    "average product rating",
    "discount usage",
    "shipping cost per order",
    "profit margin",
    "repeat customer rate",
    "customer lifetime value",
    "top customers",
    "inventory turnover",
    "stock level",
)


def _workspace_column_names(
    workspace: WorkspaceContext,
) -> set[str]:
    names: set[str] = set()

    metadata = (
        workspace.metadata
        if isinstance(workspace.metadata, dict)
        else {}
    )

    for dataset in metadata.get("datasets", []) or []:
        for column in dataset.get("columns", []) or []:
            if isinstance(column, dict):
                value = (
                    column.get("name")
                    or column.get("column_name")
                    or column.get("semantic_role")
                )
            else:
                value = str(column)

            if value:
                names.add(str(value).lower())

    return names


def _phrase_supported(
    phrase: str,
    columns: set[str],
) -> bool:
    p = phrase.lower()

    # No column metadata means we cannot prove feasibility here.
    # Keep the task, but the size guard still applies.
    if not columns:
        return True

    relevant = []
    for concept, groups in _METRIC_HINTS.items():
        if concept in p:
            relevant.extend(groups)

    if not relevant:
        return True

    flattened = " ".join(sorted(columns))

    # Require at least one schema clue for each concept family referenced.
    for alternatives in relevant:
        if not any(
            token in flattened
            for token in alternatives
        ):
            return False

    return True


def _extract_metric_phrases(
    description: str,
) -> list[str]:
    lower = description.lower()
    return [
        phrase
        for phrase in _METRIC_PHRASES
        if phrase in lower
    ]


def _repair_plan(
    plan: ExecutionPlan,
    *,
    goal: str,
    workspace: WorkspaceContext,
) -> ExecutionPlan:
    columns = _workspace_column_names(
        workspace
    )

    repaired: list[PlanTask] = []
    next_id = 1
    old_to_new: dict[int, list[int]] = {}

    for original in sorted(
        plan.tasks,
        key=lambda item: item.order,
    ):
        if original.agent != "analyst":
            new_task = original.model_copy(
                update={
                    "id": next_id,
                    "order": next_id,
                    "depends_on": [],
                }
            )
            repaired.append(new_task)
            old_to_new[original.id] = [
                next_id
            ]
            next_id += 1
            continue

        metrics = _extract_metric_phrases(
            original.description
        )

        supported = [
            metric
            for metric in metrics
            if _phrase_supported(
                metric,
                columns,
            )
        ]

        # If the task is not a checklist, preserve its original intent.
        if len(metrics) <= MAX_METRICS_PER_ANALYST_TASK:
            description = original.description[
                :MAX_TASK_DESCRIPTION_CHARS
            ]

            new_task = original.model_copy(
                update={
                    "id": next_id,
                    "order": next_id,
                    "description": description,
                    "depends_on": [],
                }
            )

            repaired.append(new_task)
            old_to_new[original.id] = [
                next_id
            ]
            next_id += 1
            continue

        # Oversized metric checklist: split only schema-supported metrics.
        if not supported:
            supported = metrics[
                :MAX_METRICS_PER_ANALYST_TASK
            ]

        groups = [
            supported[i:i + MAX_METRICS_PER_ANALYST_TASK]
            for i in range(
                0,
                len(supported),
                MAX_METRICS_PER_ANALYST_TASK,
            )
        ]

        # Hard bound prevents one goal from exploding into many worker calls.
        groups = groups[:2]

        created_ids = []

        for group in groups:
            description = (
                "Using only the current workspace database, compute these "
                "schema-supported metrics when their required fields and "
                "relationships actually exist: "
                + ", ".join(group)
                + ". Return queried evidence, interpretation, confidence, "
                  "and explicitly list any requested metric that cannot be "
                  "supported by the available columns. Do not invent proxy "
                  "formulas or thresholds."
            )

            repaired.append(
                PlanTask(
                    id=next_id,
                    order=next_id,
                    agent="analyst",
                    description=description,
                    reason=(
                        "Deterministic feasibility guard split an oversized "
                        "Analyst checklist into a bounded evidence task."
                    ),
                    confidence=min(
                        original.confidence,
                        0.80,
                    ),
                    depends_on=[],
                )
            )

            created_ids.append(
                next_id
            )
            next_id += 1

        old_to_new[original.id] = (
            created_ids
        )

    # Restore dependencies after IDs changed. A dependent task waits for the
    # final repaired task generated from each original dependency.
    original_by_id = {
        task.id: task
        for task in plan.tasks
    }

    final_tasks = []

    for task in repaired:
        # Find which original task produced this task.
        source_id = None
        for old_id, new_ids in old_to_new.items():
            if task.id in new_ids:
                source_id = old_id
                break

        deps = []

        if source_id is not None:
            source = original_by_id.get(
                source_id
            )
            for old_dep in (
                source.depends_on
                if source
                else []
            ):
                mapped = old_to_new.get(
                    old_dep,
                    [],
                )
                if mapped:
                    deps.append(mapped[-1])

        final_tasks.append(
            task.model_copy(
                update={
                    "depends_on": sorted(
                        set(deps)
                    )
                }
            )
        )

    # Remove synthesis-only Analyst tasks; final interpretation is a separate stage.
    synthesis_markers = (
        "analyze the computed metrics", "synthesize the findings",
        "synthesize findings", "based on the computed metrics",
        "using the computed metrics", "provide actionable recommendations",
    )
    kept = []
    for task in final_tasks:
        lower = task.description.lower()
        synthesis_only = (
            task.agent == "analyst"
            and any(m in lower for m in synthesis_markers)
            and not any(w in lower for w in ("compute", "query", "calculate", "extract", "database", "sql"))
        )
        if synthesis_only:
            log_event("GUARD", f"Removed synthesis-only Analyst task | task {task.id}")
        else:
            kept.append(task)
    final_tasks = kept[:5]

    # Re-number once more so order/id are always contiguous.
    id_map = {
        task.id: i + 1
        for i, task in enumerate(
            final_tasks
        )
    }

    normalized = []
    for i, task in enumerate(
        final_tasks,
        start=1,
    ):
        normalized.append(
            task.model_copy(
                update={
                    "id": i,
                    "order": i,
                    "depends_on": [
                        id_map[d]
                        for d in task.depends_on
                        if d in id_map
                        and id_map[d] < i
                    ],
                }
            )
        )

    if not normalized:
        return _fallback(
            goal,
            workspace,
        )

    return ExecutionPlan(
        goal=goal,
        tasks=normalized,
    )

def create_plan(
    goal: str,
    *,
    workspace: WorkspaceContext,
) -> ExecutionPlan:
    log_event(
        "PLANNER",
        "Creating schema-aware execution plan",
    )

    structured = (
        get_llm()
        .with_structured_output(
            ExecutionPlan
        )
    )

    prompt = f"""
{SYSTEM_PROMPT}

WORKSPACE / DETECTED SCHEMA

{workspace.to_agent_context()}

BUSINESS GOAL

{goal}

Create a plan based on the ACTUAL supplied schema.
Return only the structured execution plan.
"""

    for attempt in range(2):
        try:
            log_event(
                "PLANNER",
                f"Structured planning attempt {attempt + 1}",
            )

            plan = invoke_llm_with_retry(
                structured,
                prompt,
                component="Planner",
            )

            if not isinstance(
                plan,
                ExecutionPlan,
            ):
                plan = (
                    ExecutionPlan
                    .model_validate(plan)
                )

            plan.goal = goal

            log_event(
                "PLANNER",
                f"Plan created successfully | {len(plan.tasks)} tasks",
            )

            repaired = _repair_plan(
                plan,
                goal=goal,
                workspace=workspace,
            )

            if len(repaired.tasks) != len(plan.tasks):
                log_event(
                    "GUARD",
                    (
                        "Planner feasibility guard repaired plan "
                        f"| {len(plan.tasks)} -> {len(repaired.tasks)} tasks"
                    ),
                )

            return repaired

        except Exception as exc:
            log_event(
                "PLANNER",
                (
                    "Planning attempt failed "
                    f"| {type(exc).__name__}"
                ),
            )

    plan = _repair_plan(
        _fallback(
            goal,
            workspace,
        ),
        goal=goal,
        workspace=workspace,
    )

    log_event(
        "PLANNER",
        f"Fallback plan created | {len(plan.tasks)} tasks",
    )

    return plan
