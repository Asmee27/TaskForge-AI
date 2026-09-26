import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler


# Load project .env BEFORE reading any runtime-budget settings.
# This makes budget configuration independent of module import order.
load_dotenv()

# ============================================================
# CONFIG
# ============================================================

MAX_WORKFLOW_STEPS = int(
    os.getenv("SYNAPSEOPS_MAX_WORKFLOW_STEPS", "12")
)

MAX_TOOL_CALLS = int(
    os.getenv("SYNAPSEOPS_MAX_TOOL_CALLS", "12")
)

MAX_LLM_CALLS = int(
    os.getenv("SYNAPSEOPS_MAX_LLM_CALLS", "15")
)

MAX_TOTAL_TOKENS = int(
    os.getenv("SYNAPSEOPS_MAX_TOTAL_TOKENS", "30000")
)

MAX_COST_USD = float(
    os.getenv("SYNAPSEOPS_MAX_COST_USD", "0.10")
)

# Current Groq GPT-OSS-20B pricing.
# These can always be overridden in .env later.
INPUT_COST_PER_1M = float(
    os.getenv(
        "SYNAPSEOPS_INPUT_COST_PER_1M",
        "0.075",
    )
)

OUTPUT_COST_PER_1M = float(
    os.getenv(
        "SYNAPSEOPS_OUTPUT_COST_PER_1M",
        "0.30",
    )
)


# ============================================================
# TRACKER
# ============================================================

@dataclass
class RuntimeBudget:
    workflow_steps: int = 0

    tool_calls: int = 0

    llm_calls: int = 0

    input_tokens: int = 0

    output_tokens: int = 0

    total_tokens: int = 0

    estimated_cost_usd: float = 0.0

    exceeded: bool = False

    stop_reason: str | None = None

    stop_code: str | None = None

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any] | None,
    ) -> "RuntimeBudget":

        if not raw:
            return cls()

        allowed = {
            field_name
            for field_name
            in cls.__dataclass_fields__
        }

        clean = {
            key: value
            for key, value in raw.items()
            if key in allowed
        }

        return cls(**clean)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_steps": self.workflow_steps,
            "tool_calls": self.tool_calls,
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(
                self.estimated_cost_usd,
                8,
            ),
            "exceeded": self.exceeded,
            "stop_reason": self.stop_reason,
            "stop_code": self.stop_code,
            "limits": {
                "max_workflow_steps": MAX_WORKFLOW_STEPS,
                "max_tool_calls": MAX_TOOL_CALLS,
                "max_llm_calls": MAX_LLM_CALLS,
                "max_total_tokens": MAX_TOTAL_TOKENS,
                "max_cost_usd": MAX_COST_USD,
            },
        }

    # --------------------------------------------------------
    # STOP
    # --------------------------------------------------------

    def block(
        self,
        code: str,
        reason: str,
    ) -> None:

        if not self.exceeded:
            self.exceeded = True
            self.stop_code = code
            self.stop_reason = reason

    # --------------------------------------------------------
    # STEP BUDGET
    # --------------------------------------------------------

    def consume_workflow_step(self) -> bool:

        if self.exceeded:
            return False

        if (
            self.workflow_steps + 1
            > MAX_WORKFLOW_STEPS
        ):
            self.block(
                "WORKFLOW_STEP_BUDGET_EXCEEDED",
                (
                    "Workflow exceeded the maximum "
                    f"of {MAX_WORKFLOW_STEPS} steps."
                ),
            )

            return False

        self.workflow_steps += 1

        return True

    # --------------------------------------------------------
    # TOOL BUDGET
    # --------------------------------------------------------

    def add_tool_calls(
        self,
        count: int,
    ) -> bool:

        if count <= 0:
            return not self.exceeded

        self.tool_calls += count

        if self.tool_calls > MAX_TOOL_CALLS:

            self.block(
                "TOOL_CALL_BUDGET_EXCEEDED",
                (
                    "Workflow exceeded the maximum "
                    f"of {MAX_TOOL_CALLS} tool calls."
                ),
            )

            return False

        return not self.exceeded

    # --------------------------------------------------------
    # LLM / TOKEN / COST BUDGET
    # --------------------------------------------------------

    def add_llm_usage(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> bool:

        input_tokens = max(
            int(input_tokens or 0),
            0,
        )

        output_tokens = max(
            int(output_tokens or 0),
            0,
        )

        self.llm_calls += 1

        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

        self.total_tokens = (
            self.input_tokens
            + self.output_tokens
        )

        input_cost = (
            self.input_tokens
            / 1_000_000
        ) * INPUT_COST_PER_1M

        output_cost = (
            self.output_tokens
            / 1_000_000
        ) * OUTPUT_COST_PER_1M

        self.estimated_cost_usd = (
            input_cost
            + output_cost
        )

        if self.llm_calls > MAX_LLM_CALLS:

            self.block(
                "LLM_CALL_BUDGET_EXCEEDED",
                (
                    "Workflow exceeded the maximum "
                    f"of {MAX_LLM_CALLS} LLM calls."
                ),
            )

            return False

        if self.total_tokens > MAX_TOTAL_TOKENS:

            self.block(
                "TOKEN_BUDGET_EXCEEDED",
                (
                    "Workflow exceeded the token "
                    f"budget of {MAX_TOTAL_TOKENS} tokens."
                ),
            )

            return False

        if (
            self.estimated_cost_usd
            > MAX_COST_USD
        ):

            self.block(
                "COST_BUDGET_EXCEEDED",
                (
                    "Workflow exceeded the estimated "
                    f"cost budget of ${MAX_COST_USD:.4f}."
                ),
            )

            return False

        return not self.exceeded


# ============================================================
# CURRENT WORKFLOW BUDGET
# ============================================================

_CURRENT_BUDGET: ContextVar[
    RuntimeBudget | None
] = ContextVar(
    "synapseops_runtime_budget",
    default=None,
)


def activate_budget(
    budget: RuntimeBudget,
) -> Token:

    return _CURRENT_BUDGET.set(
        budget
    )


def deactivate_budget(
    token: Token,
) -> None:

    _CURRENT_BUDGET.reset(
        token
    )


def current_budget() -> RuntimeBudget | None:
    return _CURRENT_BUDGET.get()


# ============================================================
# LANGCHAIN CALLBACK
# ============================================================

class BudgetUsageCallback(
    BaseCallbackHandler,
):
    """
    Captures token usage for every LLM response.

    The callback itself is shared by all agents, but ContextVar
    routes the usage to the currently executing workflow.
    """

    def on_llm_end(
        self,
        response,
        **kwargs,
    ) -> None:

        budget = current_budget()

        if budget is None:
            return

        input_tokens = 0
        output_tokens = 0

        # ----------------------------------------------------
        # LangChain llm_output metadata
        # ----------------------------------------------------

        llm_output = getattr(
            response,
            "llm_output",
            None,
        )

        if isinstance(
            llm_output,
            dict,
        ):

            usage = (
                llm_output.get("token_usage")
                or llm_output.get("usage")
                or {}
            )

            if isinstance(
                usage,
                dict,
            ):

                input_tokens = int(
                    usage.get("prompt_tokens")
                    or usage.get("input_tokens")
                    or 0
                )

                output_tokens = int(
                    usage.get("completion_tokens")
                    or usage.get("output_tokens")
                    or 0
                )

        # ----------------------------------------------------
        # AIMessage usage_metadata fallback
        # ----------------------------------------------------

        if (
            input_tokens == 0
            and output_tokens == 0
        ):

            try:

                generations = getattr(
                    response,
                    "generations",
                    [],
                )

                if generations:

                    message = (
                        generations[0][0]
                        .message
                    )

                    metadata = getattr(
                        message,
                        "usage_metadata",
                        None,
                    )

                    if isinstance(
                        metadata,
                        dict,
                    ):

                        input_tokens = int(
                            metadata.get(
                                "input_tokens",
                                0,
                            )
                            or 0
                        )

                        output_tokens = int(
                            metadata.get(
                                "output_tokens",
                                0,
                            )
                            or 0
                        )

            except Exception:
                pass

        budget.add_llm_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


BUDGET_USAGE_CALLBACK = (
    BudgetUsageCallback()
)