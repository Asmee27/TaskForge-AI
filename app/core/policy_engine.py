from __future__ import annotations

import re

from app.models.policy_models import (
    BusinessPolicyConfig,
    PolicyEvaluation,
    PolicyFinding,
)


IRREVERSIBLE_ACTION_PATTERNS = [
    r"\bdelete\b",
    r"\bremove permanently\b",
    r"\bterminate\b",
    r"\bcancel permanently\b",
    r"\bissue refund\b",
    r"\bplace order\b",
    r"\bexecute purchase\b",
    r"\bchange price\b",
    r"\bupdate price\b",
    r"\bsend payment\b",
]


def _normalize_number_text(value: str) -> str:
    return value.replace(",", "").strip()


def _contains_irreversible_action(text: str) -> bool:
    lowered = text.lower()

    return any(
        re.search(pattern, lowered)
        for pattern in IRREVERSIBLE_ACTION_PATTERNS
    )


def _extract_discount_percentages(text: str) -> list[float]:
    patterns = [
        r"(\d+(?:\.\d+)?)\s*%\s*discount",
        r"discount(?:ed)?\s*(?:by|of)?\s*(\d+(?:\.\d+)?)\s*%",
    ]

    values: list[float] = []

    for pattern in patterns:
        for match in re.findall(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            try:
                values.append(float(match))
            except ValueError:
                pass

    return values


def _extract_reorder_quantities(text: str) -> list[int]:
    number = r"(\d{1,3}(?:,\d{3})+|\d+)"

    patterns = [
        rf"\breorder(?:ing)?\s+{number}",
        rf"\border(?:ing)?\s+{number}\s+(?:units|items|pieces|products|skus)\b",
        rf"\bconsider\s+ordering\s+{number}\s+(?:units|items|pieces|products|skus)\b",
        rf"\brecommend(?:ed|ing)?\s+(?:a\s+)?(?:reorder|order)\s+(?:of\s+)?{number}",
        rf"\b(?:reorder|order)\s+quantity\s+(?:of\s+)?{number}",
    ]

    values: list[int] = []

    for pattern in patterns:
        for match in re.findall(
            pattern,
            text,
            flags=re.IGNORECASE,
        ):
            try:
                values.append(
                    int(_normalize_number_text(match))
                )
            except ValueError:
                pass

    deduped: list[int] = []

    for value in values:
        if value not in deduped:
            deduped.append(value)

    return deduped


def _extract_quantity_unit_phrases(text: str) -> list[int]:
    number = r"(\d{1,3}(?:,\d{3})+|\d+)"

    pattern = (
        rf"{number}\s+"
        r"(?:units|items|pieces|products|skus)\b"
    )

    values: list[int] = []

    for match in re.findall(
        pattern,
        text,
        flags=re.IGNORECASE,
    ):
        try:
            values.append(
                int(_normalize_number_text(match))
            )
        except ValueError:
            pass

    deduped: list[int] = []

    for value in values:
        if value not in deduped:
            deduped.append(value)

    return deduped


def _contains_reorder_context(text: str) -> bool:
    return bool(
        re.search(
            r"\b(reorder|re-order|restock|replenish|ordering|purchase order)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def _looks_like_execution_request(text: str) -> bool:
    execution_patterns = [
        r"\bplace\s+(?:the\s+)?order\b",
        r"\bexecute\s+(?:the\s+)?purchase\b",
        r"\bproceed\s+with\s+(?:the\s+)?order\b",
        r"\bsubmit\s+(?:the\s+)?purchase\s+order\b",
        r"\bconfirm\s+(?:the\s+)?order\b",
        r"\bbuy\s+\d",
        r"\bpurchase\s+\d",
        r"\border\s+\d",
        r"\breorder\s+\d",
    ]

    recommendation_patterns = [
        r"\banaly[sz]e\b",
        r"\brecommend\b",
        r"\bshould\s+we\b",
        r"\bconsider\b",
        r"\bwhether\s+we\s+should\b",
        r"\bwhat\s+should\s+we\s+do\b",
    ]

    has_execution = any(
        re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )
        for pattern in execution_patterns
    )

    has_recommendation = any(
        re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )
        for pattern in recommendation_patterns
    )

    return has_execution and not has_recommendation


def evaluate_business_policies(
    text: str,
    config: BusinessPolicyConfig,
    *,
    business_goal: str | None = None,
) -> PolicyEvaluation:

    findings: list[PolicyFinding] = []
    requires_approval = False

    goal_text = business_goal or ""

    combined_text = "\n\n".join(
        part
        for part in [
            goal_text,
            text,
        ]
        if part
    )

    # ========================================================
    # Irreversible actions
    # ========================================================

    if (
        config.require_human_approval_for_irreversible_actions
        and _contains_irreversible_action(combined_text)
    ):

        requires_approval = True

        findings.append(
            PolicyFinding(
                rule="human_approval_for_irreversible_actions",
                passed=False,
                severity="critical",
                message=(
                    "Potential irreversible or externally impacting action detected. "
                    "Analysis may continue, but execution requires human approval."
                ),
                requires_human_approval=True,
            )
        )

    else:

        findings.append(
            PolicyFinding(
                rule="human_approval_for_irreversible_actions",
                passed=True,
                severity="info",
                message=(
                    "No irreversible action requiring approval was detected."
                ),
            )
        )

    # ========================================================
    # Discount threshold
    # ========================================================

    if (
        config.max_discount_percent_without_approval
        is not None
    ):

        discounts = _extract_discount_percentages(
            combined_text
        )

        for discount in discounts:

            if (
                discount
                > config.max_discount_percent_without_approval
            ):

                requires_approval = True

                findings.append(
                    PolicyFinding(
                        rule="max_discount_without_approval",
                        passed=False,
                        severity="critical",
                        message=(
                            f"Proposed discount of {discount:.2f}% exceeds "
                            f"workspace limit of "
                            f"{config.max_discount_percent_without_approval:.2f}%. "
                            "Analysis/recommendation is allowed, "
                            "but execution requires human approval."
                        ),
                        requires_human_approval=True,
                    )
                )

    # ========================================================
    # Reorder threshold
    # ========================================================

    if (
        config.max_reorder_quantity_without_approval
        is not None
    ):

        quantities = _extract_reorder_quantities(
            combined_text
        )

        if _contains_reorder_context(
            combined_text
        ):
            for quantity in _extract_quantity_unit_phrases(
                combined_text
            ):
                if quantity not in quantities:
                    quantities.append(quantity)

        for quantity in quantities:

            if (
                quantity
                > config.max_reorder_quantity_without_approval
            ):

                requires_approval = True

                findings.append(
                    PolicyFinding(
                        rule="max_reorder_without_approval",
                        passed=False,
                        severity="critical",
                        message=(
                            f"Proposed reorder quantity {quantity} exceeds "
                            f"workspace limit of "
                            f"{config.max_reorder_quantity_without_approval}. "
                            "Analysis/recommendation is allowed, "
                            "but execution must wait for human approval."
                        ),
                        requires_human_approval=True,
                    )
                )

    # ========================================================
    # Execution intent
    # ========================================================

    if (
        _looks_like_execution_request(
            goal_text
        )
        and config.require_human_approval_for_irreversible_actions
    ):

        requires_approval = True

        findings.append(
            PolicyFinding(
                rule="execution_intent_requires_approval",
                passed=False,
                severity="critical",
                message=(
                    "The business goal appears to request execution rather than "
                    "analysis only. Human approval is required before any external "
                    "action is performed."
                ),
                requires_human_approval=True,
            )
        )

    passed = not any(
        (
            not finding.passed
            and finding.severity == "critical"
            and not finding.requires_human_approval
        )
        for finding in findings
    )

    return PolicyEvaluation(
        passed=passed,
        requires_human_approval=requires_approval,
        findings=findings,
    )
