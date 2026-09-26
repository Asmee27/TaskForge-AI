from __future__ import annotations

import re

from app.core.logging import log_event
from app.models.action_models import (
    ActionRequest,
    ProposedAction,
)
from app.tools.action_tools import (
    create_action_request,
    get_action_request,
    mark_action_executed,
)


_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+)"


def _to_int(value: str) -> int:
    return int(
        value.replace(",", "")
    )


def _extract_goal_reorder_quantity(
    goal: str,
) -> int | None:

    patterns = [
        rf"\breorder\s+{_NUMBER}",
        rf"\border\s+{_NUMBER}\s+(?:units|items|pieces|products|skus)\b",
        rf"\breorder(?:ing)?\s+(?:of\s+)?{_NUMBER}\s+(?:units|items|pieces|products|skus)\b",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            goal,
            flags=re.IGNORECASE,
        )

        if match:
            return _to_int(
                match.group(1)
            )

    return None


def _extract_goal_discount(
    goal: str,
) -> float | None:

    patterns = [
        r"(\d+(?:\.\d+)?)\s*%\s*discount",
        r"discount(?:ed)?\s*(?:by|of)?\s*(\d+(?:\.\d+)?)\s*%",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            goal,
            flags=re.IGNORECASE,
        )

        if match:
            return float(
                match.group(1)
            )

    return None


def build_proposed_action(
    *,
    goal: str,
    critic_report: dict,
) -> ProposedAction | None:
    """
    Build a structured proposed action from the user's explicit goal.

    Important safety rule:
    worker text is NOT used to silently invent a new action quantity.
    The user goal is the source of truth for the requested action.
    """

    requires_approval = bool(
        critic_report.get(
            "requires_human_approval",
            False,
        )
    )

    reorder_quantity = (
        _extract_goal_reorder_quantity(
            goal
        )
    )

    if reorder_quantity is not None:

        return ProposedAction(
            action_type="reorder_inventory",
            description=(
                f"Create a reorder request for "
                f"{reorder_quantity} inventory units."
            ),
            payload={
                "quantity": reorder_quantity,
                "mode": "record_only",
            },
            source="user_goal",
            requires_human_approval=(
                requires_approval
            ),
        )

    discount = _extract_goal_discount(
        goal
    )

    if discount is not None:

        return ProposedAction(
            action_type="apply_discount",
            description=(
                f"Create a request for a "
                f"{discount:.2f}% discount."
            ),
            payload={
                "discount_percent": discount,
                "mode": "record_only",
            },
            source="user_goal",
            requires_human_approval=(
                requires_approval
            ),
        )

    return None


def create_proposed_action_request(
    *,
    workspace_id: str,
    goal: str,
    critic_report: dict,
) -> ActionRequest | None:

    proposal = build_proposed_action(
        goal=goal,
        critic_report=critic_report,
    )

    if proposal is None:
        return None

    log_event(
        "ACTION",
        (
            "Structured action proposal created "
            f"| {proposal.action_type}"
        ),
    )

    request = create_action_request(
        workspace_id,
        proposal,
    )

    log_event(
        "ACTION",
        (
            f"Action request {request.request_id} "
            f"| status={request.status}"
        ),
    )

    return request


def execute_action_request(
    request_id: str,
) -> ActionRequest:
    """
    Milestone 6 safe execution.

    This deliberately does NOT mutate the operational business
    dataset yet. It records the approved action as executed,
    producing an auditable result. Real mutation tools can be
    added behind this gate in the next milestone.
    """

    request = get_action_request(
        request_id
    )

    if request is None:
        raise ValueError(
            "Action request not found."
        )

    if request.status == "pending_approval":
        raise PermissionError(
            "Human approval is required before execution."
        )

    if request.status == "rejected":
        raise PermissionError(
            "Rejected action cannot be executed."
        )

    if request.status == "executed":
        return request

    if request.status != "approved":
        raise PermissionError(
            f"Action status '{request.status}' "
            "is not executable."
        )

    result = {
        "mode": "record_only",
        "business_mutation_performed": False,
        "message": (
            "Approved action passed the Action Agent gate "
            "and was recorded as executed. No operational "
            "business data was mutated in Milestone 6."
        ),
        "action_type": request.action_type,
        "payload": request.payload,
    }

    log_event(
        "ACTION",
        (
            f"Executing approved action "
            f"| {request.request_id}"
        ),
    )

    return mark_action_executed(
        request.request_id,
        result,
    )
