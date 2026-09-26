from __future__ import annotations

import contextvars
import os
from pathlib import Path


_DEFAULT_DB = Path(
    os.getenv(
        "SYNAPSEOPS_DB",
        "data/processed/novamart.db",
    )
)

_workspace_db_path = contextvars.ContextVar(
    "workspace_db_path",
    default=str(_DEFAULT_DB),
)


def set_workspace_db_path(path: str | Path) -> None:
    _workspace_db_path.set(str(Path(path)))


def get_workspace_db_path() -> Path:
    return Path(_workspace_db_path.get())
