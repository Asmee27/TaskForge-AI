from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.getenv(
    "SYNAPSEOPS_DATABASE_URL",
    "postgresql://synapseops:synapseops@127.0.0.1:5432/synapseops",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=5,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_persistence() -> None:
    with connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_runs (
                run_id TEXT PRIMARY KEY,
                goal TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                request_json JSONB NOT NULL,
                state_json JSONB NOT NULL,
                status TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                finished_at TIMESTAMPTZ
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
            ON workflow_runs(status)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_documents (
                document_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                page_count INTEGER NOT NULL DEFAULT 0,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workspace_documents_workspace
            ON workspace_documents(workspace_id, uploaded_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_document_chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES workspace_documents(document_id)
                    ON DELETE CASCADE,
                workspace_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                embedding JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workspace_document_chunks_scope
            ON workspace_document_chunks(workspace_id, document_id)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_audit_events (
                id BIGSERIAL PRIMARY KEY,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_audit_run
            ON workflow_audit_events(run_id, id)
            """
        )


def create_run(
    *,
    run_id: str,
    goal: str,
    workspace_id: str,
    request_payload: dict[str, Any],
    initial_state: dict[str, Any],
) -> None:
    initialize_persistence()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO workflow_runs (
                run_id, goal, workspace_id, request_json,
                state_json, status, current_stage
            )
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, 'running', 'created')
            ON CONFLICT (run_id) DO NOTHING
            """,
            (
                run_id,
                goal,
                workspace_id,
                json.dumps(request_payload, default=str),
                json.dumps(initial_state, default=str),
            ),
        )


def save_checkpoint(
    *,
    run_id: str,
    state: dict[str, Any],
    stage: str,
    status: str | None = None,
) -> None:
    effective_status = status or state.get("status", "running")
    terminal = effective_status in {
        "completed",
        "completed_degraded",
        "completed_with_errors",
        "failed",
        "blocked",
    }
    with connection() as conn:
        conn.execute(
            """
            UPDATE workflow_runs
            SET state_json = %s::jsonb,
                status = %s,
                current_stage = %s,
                updated_at = NOW(),
                finished_at = CASE WHEN %s THEN NOW() ELSE finished_at END
            WHERE run_id = %s
            """,
            (
                json.dumps(state, default=str),
                effective_status,
                stage,
                terminal,
                run_id,
            ),
        )


def mark_run_error(run_id: str, error: str) -> None:
    with connection() as conn:
        conn.execute(
            """
            UPDATE workflow_runs
            SET status = 'interrupted',
                error = %s,
                updated_at = NOW()
            WHERE run_id = %s
            """,
            (error, run_id),
        )


def load_run(run_id: str) -> dict[str, Any] | None:
    initialize_persistence()
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id = %s",
            (run_id,),
        ).fetchone()
    return dict(row) if row else None


def load_state(run_id: str) -> dict[str, Any] | None:
    row = load_run(run_id)
    if not row:
        return None
    value = row["state_json"]
    return value if isinstance(value, dict) else json.loads(value)


def list_incomplete_runs() -> list[dict[str, Any]]:
    """
    Return every workflow that has not reached a terminal state.

    A workflow may be checkpointed with intermediate statuses such as:
    planner_complete, task_complete, synthesis_complete, critic_complete,
    action_pending, etc.

    Therefore we exclude terminal runs instead of trying to enumerate
    every possible intermediate status.
    """
    initialize_persistence()

    with connection() as conn:
        rows = conn.execute(
            """
            SELECT run_id, goal, workspace_id, status, current_stage,
                   created_at, updated_at, error
            FROM workflow_runs
            WHERE finished_at IS NULL
              AND status NOT IN ('completed', 'failed', 'cancelled')
            ORDER BY updated_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def append_audit_event(
    run_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO workflow_audit_events(run_id, event_type, payload_json)
            VALUES (%s, %s, %s::jsonb)
            """,
            (run_id, event_type, json.dumps(payload, default=str)),
        )


def get_audit_events(run_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, run_id, event_type, payload_json, created_at
            FROM workflow_audit_events
            WHERE run_id = %s
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def persistence_health() -> dict[str, Any]:
    try:
        initialize_persistence()
        with connection() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ok": True, "backend": "postgresql"}
    except Exception as exc:
        return {
            "ok": False,
            "backend": "postgresql",
            "error": f"{type(exc).__name__}: {exc}",
        }
