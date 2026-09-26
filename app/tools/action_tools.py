from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.action_models import (
    ActionRequest,
    ProposedAction,
)


DEFAULT_ACTION_DB = Path(
    os.getenv(
        "SYNAPSEOPS_ACTION_DB",
        "data/processed/action_store.db",
    )
)


def _utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _connect() -> sqlite3.Connection:
    DEFAULT_ACTION_DB.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        DEFAULT_ACTION_DB
    )

    conn.row_factory = sqlite3.Row

    return conn


def initialize_action_store() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_requests (
                request_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                action_type TEXT NOT NULL,
                description TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                source TEXT NOT NULL,
                requires_human_approval INTEGER NOT NULL,
                status TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                approved_by TEXT,
                rejected_at TEXT,
                rejected_by TEXT,
                rejection_reason TEXT,
                executed_at TEXT,
                execution_result_json TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_action_requests_status
            ON action_requests(status)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_action_requests_workspace
            ON action_requests(workspace_id)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(request_id)
                    REFERENCES action_requests(request_id)
            )
            """
        )


def _fingerprint(
    workspace_id: str,
    proposal: ProposedAction,
) -> str:

    raw = json.dumps(
        {
            "workspace_id": workspace_id,
            "action_type": proposal.action_type,
            "payload": proposal.payload,
            "description": proposal.description,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def _row_to_action(
    row: sqlite3.Row,
) -> ActionRequest:

    execution_result = (
        json.loads(
            row["execution_result_json"]
        )
        if row["execution_result_json"]
        else None
    )

    return ActionRequest(
        request_id=row["request_id"],
        workspace_id=row["workspace_id"],
        action_type=row["action_type"],
        description=row["description"],
        payload=json.loads(
            row["payload_json"]
        ),
        source=row["source"],
        requires_human_approval=bool(
            row["requires_human_approval"]
        ),
        status=row["status"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
        approved_by=row["approved_by"],
        rejected_at=row["rejected_at"],
        rejected_by=row["rejected_by"],
        rejection_reason=row["rejection_reason"],
        executed_at=row["executed_at"],
        execution_result=execution_result,
    )


def _append_event(
    conn: sqlite3.Connection,
    request_id: str,
    event_type: str,
    actor: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:

    conn.execute(
        """
        INSERT INTO action_events (
            request_id,
            event_type,
            actor,
            details_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            request_id,
            event_type,
            actor,
            json.dumps(
                details or {},
                sort_keys=True,
                default=str,
            ),
            _utc_now(),
        ),
    )


def create_action_request(
    workspace_id: str,
    proposal: ProposedAction,
) -> ActionRequest:

    initialize_action_store()

    fingerprint = _fingerprint(
        workspace_id,
        proposal,
    )

    with _connect() as conn:

        existing = conn.execute(
            """
            SELECT *
            FROM action_requests
            WHERE workspace_id = ?
              AND fingerprint = ?
              AND status IN (
                  'pending_approval',
                  'approved'
              )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                workspace_id,
                fingerprint,
            ),
        ).fetchone()

        if existing:
            return _row_to_action(
                existing
            )

        request_id = (
            uuid.uuid4().hex
        )

        created_at = _utc_now()

        status = (
            "pending_approval"
            if proposal.requires_human_approval
            else "approved"
        )

        approved_at = (
            None
            if proposal.requires_human_approval
            else created_at
        )

        approved_by = (
            None
            if proposal.requires_human_approval
            else "policy:auto"
        )

        conn.execute(
            """
            INSERT INTO action_requests (
                request_id,
                workspace_id,
                action_type,
                description,
                payload_json,
                source,
                requires_human_approval,
                status,
                fingerprint,
                created_at,
                approved_at,
                approved_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                workspace_id,
                proposal.action_type,
                proposal.description,
                json.dumps(
                    proposal.payload,
                    sort_keys=True,
                    default=str,
                ),
                proposal.source,
                int(
                    proposal.requires_human_approval
                ),
                status,
                fingerprint,
                created_at,
                approved_at,
                approved_by,
            ),
        )

        _append_event(
            conn,
            request_id,
            "created",
            "workflow",
            {
                "status": status,
                "requires_human_approval":
                    proposal.requires_human_approval,
            },
        )

        row = conn.execute(
            """
            SELECT *
            FROM action_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

        return _row_to_action(
            row
        )


def get_action_request(
    request_id: str,
) -> ActionRequest | None:

    initialize_action_store()

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM action_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

    if row is None:
        return None

    return _row_to_action(row)


def get_latest_pending_action(
    workspace_id: str | None = None,
) -> ActionRequest | None:

    initialize_action_store()

    query = """
        SELECT *
        FROM action_requests
        WHERE status = 'pending_approval'
    """

    params: list[Any] = []

    if workspace_id:
        query += " AND workspace_id = ?"
        params.append(workspace_id)

    query += """
        ORDER BY created_at DESC
        LIMIT 1
    """

    with _connect() as conn:
        row = conn.execute(
            query,
            params,
        ).fetchone()

    if row is None:
        return None

    return _row_to_action(row)


def approve_action_request(
    request_id: str,
    approved_by: str,
) -> ActionRequest:

    initialize_action_store()

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM action_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

        if row is None:
            raise ValueError(
                "Action request not found."
            )

        if row["status"] == "rejected":
            raise ValueError(
                "Rejected action cannot be approved."
            )

        if row["status"] == "executed":
            return _row_to_action(row)

        if row["status"] == "approved":
            return _row_to_action(row)

        now = _utc_now()

        conn.execute(
            """
            UPDATE action_requests
            SET status = 'approved',
                approved_at = ?,
                approved_by = ?
            WHERE request_id = ?
              AND status = 'pending_approval'
            """,
            (
                now,
                approved_by,
                request_id,
            ),
        )

        _append_event(
            conn,
            request_id,
            "approved",
            approved_by,
        )

        updated = conn.execute(
            """
            SELECT *
            FROM action_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

        return _row_to_action(
            updated
        )


def reject_action_request(
    request_id: str,
    rejected_by: str,
    reason: str | None = None,
) -> ActionRequest:

    initialize_action_store()

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM action_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

        if row is None:
            raise ValueError(
                "Action request not found."
            )

        if row["status"] == "executed":
            raise ValueError(
                "Executed action cannot be rejected."
            )

        if row["status"] == "rejected":
            return _row_to_action(row)

        now = _utc_now()

        conn.execute(
            """
            UPDATE action_requests
            SET status = 'rejected',
                rejected_at = ?,
                rejected_by = ?,
                rejection_reason = ?
            WHERE request_id = ?
              AND status IN (
                  'pending_approval',
                  'approved'
              )
            """,
            (
                now,
                rejected_by,
                reason,
                request_id,
            ),
        )

        _append_event(
            conn,
            request_id,
            "rejected",
            rejected_by,
            {
                "reason": reason,
            },
        )

        updated = conn.execute(
            """
            SELECT *
            FROM action_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

        return _row_to_action(
            updated
        )


def mark_action_executed(
    request_id: str,
    result: dict[str, Any],
) -> ActionRequest:

    initialize_action_store()

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM action_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

        if row is None:
            raise ValueError(
                "Action request not found."
            )

        if row["status"] == "rejected":
            raise PermissionError(
                "Rejected action cannot be executed."
            )

        if row["status"] == "pending_approval":
            raise PermissionError(
                "Human approval is required before execution."
            )

        if row["status"] == "executed":
            return _row_to_action(row)

        now = _utc_now()

        conn.execute(
            """
            UPDATE action_requests
            SET status = 'executed',
                executed_at = ?,
                execution_result_json = ?
            WHERE request_id = ?
              AND status = 'approved'
            """,
            (
                now,
                json.dumps(
                    result,
                    sort_keys=True,
                    default=str,
                ),
                request_id,
            ),
        )

        _append_event(
            conn,
            request_id,
            "executed",
            "action_agent",
            result,
        )

        updated = conn.execute(
            """
            SELECT *
            FROM action_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

        return _row_to_action(
            updated
        )
