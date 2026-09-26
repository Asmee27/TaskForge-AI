from __future__ import annotations

from app.agents.action_agent import (
    execute_action_request,
)
from app.tools.action_tools import (
    approve_action_request,
    get_action_request,
    get_latest_pending_action,
    reject_action_request,
)


def main() -> None:

    request_id = input(
        "Action request ID "
        "(press Enter for latest pending): "
    ).strip()

    if request_id:
        request = get_action_request(
            request_id
        )
    else:
        request = get_latest_pending_action()

    if request is None:
        print(
            "No pending action request found."
        )
        return

    print()
    print("ACTION REQUEST")
    print("=" * 60)
    print(
        f"ID: {request.request_id}"
    )
    print(
        f"Workspace: {request.workspace_id}"
    )
    print(
        f"Type: {request.action_type}"
    )
    print(
        f"Status: {request.status}"
    )
    print(
        f"Description: {request.description}"
    )
    print(
        f"Payload: {request.payload}"
    )
    print(
        "Human approval required: "
        f"{request.requires_human_approval}"
    )
    print("=" * 60)

    decision = input(
        "Approve this action? (yes/no): "
    ).strip().lower()

    if decision in {
        "yes",
        "y",
        "approve",
    }:

        actor = (
            input(
                "Approver name/role "
                "(default: human_reviewer): "
            ).strip()
            or "human_reviewer"
        )

        approved = (
            approve_action_request(
                request.request_id,
                actor,
            )
        )

        print(
            f"Approved: {approved.request_id}"
        )

        executed = (
            execute_action_request(
                approved.request_id
            )
        )

        print(
            f"Execution status: {executed.status}"
        )
        print(
            "Execution result:",
            executed.execution_result,
        )

    else:

        reason = input(
            "Rejection reason (optional): "
        ).strip()

        rejected = (
            reject_action_request(
                request.request_id,
                "human_reviewer",
                reason or None,
            )
        )

        print(
            f"Action status: {rejected.status}"
        )


if __name__ == "__main__":
    main()
