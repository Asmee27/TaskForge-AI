from __future__ import annotations

from app.core.logging import (
    new_run_id,
    log_event,
)

from app.workflows.business_workflow import (
    build_business_workflow,
)


def main():

    goal = input(
        "Enter business goal: "
    ).strip()

    if not goal:
        print(
            "Business goal cannot be empty."
        )
        return

    run_id = new_run_id()

    log_event(
        "RUN",
        "Business workflow started",
    )

    workflow = (
        build_business_workflow()
    )

    # --------------------------------------------------------
    # DEMO WORKSPACE
    # --------------------------------------------------------
    #
    # This is NOT hardcoded inside the agents.
    #
    # Later:
    #
    # uploaded file
    #     ↓
    # schema mapper
    #     ↓
    # workspace metadata
    #     ↓
    # this workflow
    #
    # --------------------------------------------------------

    workspace = {
        "workspace_id": "olist-demo",
        "company_name": "NovaMart",
        "business_type": "ecommerce",
        "currency": "BRL",
        "timezone": None,
        "dataset_name": (
            "Olist Brazilian "
            "E-Commerce Dataset"
        ),
        "metadata": {
            "source": "demo_workspace",
        },
        "policies": {
            "require_human_approval_for_irreversible_actions": True,

            "max_discount_percent_without_approval": 20,

            "max_reorder_quantity_without_approval": 1000,

            "minimum_margin_percent": 10,
        },
    }

    result = workflow.invoke(
        {
            "goal": goal,
            "workspace": workspace,
            "workflow_steps": 0,
            "status": "running",
        },
        config={
            "recursion_limit": 30
        },
    )

    print()
    print(
        result.get(
            "final_output",
            "No final output generated.",
        )
    )

    log_event(
        "RUN",
        "Business workflow finished",
    )


if __name__ == "__main__":
    main()