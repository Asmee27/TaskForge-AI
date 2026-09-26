from __future__ import annotations

import ast
import operator
import sqlite3
import time

from langchain_core.tools import tool

from app.core.workspace_runtime import (
    get_workspace_db_path,
)


MAX_ROWS_DEFAULT = 100
MAX_ROWS_ABSOLUTE = 500
QUERY_TIMEOUT_SECONDS = 5.0

_DENIED = {
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
}


def _result(
    ok,
    tool_name,
    data=None,
    error=None,
    started=None,
):
    return {
        "ok": ok,
        "tool": tool_name,
        "data": data,
        "error": error,
        "latency_ms": int(
            (
                time.perf_counter()
                - (started or time.perf_counter())
            )
            * 1000
        ),
    }


def _connect_ro():
    db_path = get_workspace_db_path()

    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found: {db_path}"
        )

    conn = sqlite3.connect(
        f"file:{db_path.resolve()}?mode=ro",
        uri=True,
    )
    conn.row_factory = sqlite3.Row

    def authorizer(
        action,
        arg1,
        arg2,
        dbname,
        source,
    ):
        return (
            sqlite3.SQLITE_DENY
            if action in _DENIED
            else sqlite3.SQLITE_OK
        )

    conn.set_authorizer(authorizer)
    return conn


@tool("get_database_schema")
def get_database_schema() -> dict:
    """Return the read-only SQLite database schema for the current workspace."""
    started = time.perf_counter()

    try:
        db_path = get_workspace_db_path()

        with _connect_ro() as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type='table'
                      AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                )
            ]

            schema = {}

            for table in tables:
                escaped = table.replace('"', '""')
                cols = []

                for row in conn.execute(
                    f'PRAGMA table_info("{escaped}")'
                ):
                    cols.append(
                        {
                            "name": row[1],
                            "type": row[2],
                            "not_null": bool(row[3]),
                            "primary_key": bool(row[5]),
                        }
                    )

                schema[table] = cols

        return _result(
            True,
            "get_database_schema",
            {
                "database": str(db_path),
                "tables": schema,
            },
            None,
            started,
        )

    except Exception as exc:
        return _result(
            False,
            "get_database_schema",
            None,
            {
                "type": "SCHEMA_ERROR",
                "message": str(exc),
                "retryable": False,
            },
            started,
        )


@tool("run_sql_query")
def run_sql_query(
    query: str,
    max_rows: int = MAX_ROWS_DEFAULT,
) -> dict:
    """Execute one read-only SELECT/WITH query against the current workspace SQLite database."""

    started = time.perf_counter()
    q = (query or "").strip()

    if not q:
        return _result(
            False,
            "run_sql_query",
            None,
            {
                "type": "VALIDATION_ERROR",
                "message": "SQL query is empty.",
                "retryable": False,
            },
            started,
        )

    head = q.lstrip().lower()

    if not (
        head.startswith("select")
        or head.startswith("with")
    ):
        return _result(
            False,
            "run_sql_query",
            None,
            {
                "type": "READ_ONLY_VIOLATION",
                "message": (
                    "Only SELECT/WITH queries are allowed."
                ),
                "retryable": False,
            },
            started,
        )

    if ";" in q.rstrip(";"):
        return _result(
            False,
            "run_sql_query",
            None,
            {
                "type": "MULTI_STATEMENT_BLOCKED",
                "message": (
                    "Multiple SQL statements are not allowed."
                ),
                "retryable": False,
            },
            started,
        )

    max_rows = max(
        1,
        min(
            int(max_rows or MAX_ROWS_DEFAULT),
            MAX_ROWS_ABSOLUTE,
        ),
    )

    try:
        with _connect_ro() as conn:
            deadline = (
                time.perf_counter()
                + QUERY_TIMEOUT_SECONDS
            )

            conn.set_progress_handler(
                lambda: (
                    1
                    if time.perf_counter() > deadline
                    else 0
                ),
                10000,
            )

            cur = conn.execute(q)
            rows = cur.fetchmany(max_rows + 1)
            columns = [
                d[0]
                for d in (cur.description or [])
            ]

            truncated = len(rows) > max_rows
            rows = rows[:max_rows]

            data_rows = [
                {
                    k: row[k]
                    for k in row.keys()
                }
                for row in rows
            ]

        return _result(
            True,
            "run_sql_query",
            {
                "columns": columns,
                "rows": data_rows,
                "row_count": len(data_rows),
                "truncated": truncated,
                "max_rows": max_rows,
            },
            None,
            started,
        )

    except sqlite3.OperationalError as exc:
        etype = (
            "TIMEOUT"
            if "interrupted" in str(exc).lower()
            else "SQL_ERROR"
        )

        return _result(
            False,
            "run_sql_query",
            None,
            {
                "type": etype,
                "message": str(exc),
                "retryable": False,
            },
            started,
        )

    except Exception as exc:
        return _result(
            False,
            "run_sql_query",
            None,
            {
                "type": "SQL_ERROR",
                "message": str(exc),
                "retryable": False,
            },
            started,
        )


_ALLOWED_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UN = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)

    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
    ):
        return node.value

    if (
        isinstance(node, ast.BinOp)
        and type(node.op) in _ALLOWED_BIN
    ):
        return _ALLOWED_BIN[type(node.op)](
            _eval(node.left),
            _eval(node.right),
        )

    if (
        isinstance(node, ast.UnaryOp)
        and type(node.op) in _ALLOWED_UN
    ):
        return _ALLOWED_UN[type(node.op)](
            _eval(node.operand)
        )

    raise ValueError(
        "Unsupported arithmetic expression."
    )


@tool("calculator")
def calculator(expression: str) -> dict:
    """Safely evaluate a basic arithmetic expression."""

    started = time.perf_counter()

    try:
        tree = ast.parse(
            expression,
            mode="eval",
        )
        value = _eval(tree)

        return _result(
            True,
            "calculator",
            {
                "expression": expression,
                "result": value,
            },
            None,
            started,
        )

    except Exception as exc:
        return _result(
            False,
            "calculator",
            None,
            {
                "type": "CALCULATION_ERROR",
                "message": str(exc),
                "retryable": False,
            },
            started,
        )
