from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from app.models.policy_models import BusinessPolicyConfig


WORKSPACE_ROOT = Path("data/workspaces")
DEMO_DB = Path("data/processed/novamart.db")
SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_ROWS_PER_FILE = 500_000


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    value = re.sub(r"-+", "-", value).strip("-").lower()
    return value or "workspace"


def _safe_table_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        value = "dataset"
    if value[0].isdigit():
        value = f"t_{value}"
    return value[:60]


def _workspace_dir(workspace_id: str) -> Path:
    return WORKSPACE_ROOT / _slug(workspace_id)


def _metadata_path(workspace_id: str) -> Path:
    return _workspace_dir(workspace_id) / "metadata.json"


def _database_path(workspace_id: str) -> Path:
    return _workspace_dir(workspace_id) / "workspace.db"


def _semantic_role(column_name: str) -> dict[str, Any]:
    c = column_name.strip().lower()
    compact = re.sub(r"[^a-z0-9]+", "_", c).strip("_")

    rules = [
        ("product_id", {"product_id", "productid", "sku", "sku_id", "item_id"}),
        ("order_id", {"order_id", "orderid", "transaction_id", "invoice_id"}),
        ("customer_id", {"customer_id", "customerid", "client_id", "buyer_id"}),
        ("stock_quantity", {
            "stock", "stock_qty", "stock_quantity", "inventory",
            "inventory_qty", "inventory_quantity", "on_hand", "onhand"
        }),
        ("reorder_level", {
            "reorder_level", "reorder_point", "minimum_stock",
            "min_stock", "safety_level", "restock_level"
        }),
        ("quantity", {"qty", "quantity", "units", "units_sold", "order_quantity"}),
        ("unit_price", {"price", "unit_price", "selling_price", "item_price"}),
        ("revenue", {"revenue", "sales_amount", "sales_value", "net_sales"}),
        ("payment_value", {"payment_value", "payment_amount", "paid_amount"}),
        ("cost", {"cost", "unit_cost", "cogs", "cost_price"}),
        ("date", {
            "date", "order_date", "created_at", "timestamp",
            "purchase_date", "transaction_date", "sale_date"
        }),
        ("category", {"category", "product_category", "category_name"}),
        ("supplier", {"supplier", "supplier_id", "vendor", "vendor_id"}),
        ("lead_time", {
            "lead_time", "supplier_lead_days", "lead_days", "delivery_lead_days"
        }),
    ]

    for role, names in rules:
        if compact in names:
            return {
                "role": role,
                "confidence": 0.98,
                "status": "detected",
            }

    # Fuzzy lexical hints for common schemas.
    if "stock" in compact or "inventory" in compact:
        return {"role": "stock_quantity", "confidence": 0.72, "status": "review"}
    if compact.endswith("_id"):
        return {"role": "identifier", "confidence": 0.65, "status": "review"}
    if "date" in compact or "time" in compact:
        return {"role": "date", "confidence": 0.70, "status": "review"}
    if "price" in compact:
        return {"role": "unit_price", "confidence": 0.70, "status": "review"}
    if "amount" in compact or "value" in compact:
        return {"role": "monetary_value", "confidence": 0.55, "status": "review"}

    return {
        "role": "unknown",
        "confidence": 0.0,
        "status": "unknown",
    }


def _profile_dataframe(
    df: pd.DataFrame,
    *,
    table_name: str,
    source_file: str,
) -> dict[str, Any]:

    sample = (
        df.head(5)
        .where(pd.notna(df.head(5)), None)
        .to_dict(orient="records")
    )

    columns = []
    for col in df.columns:
        series = df[col]
        columns.append(
            {
                "name": str(col),
                "dtype": str(series.dtype),
                "missing_count": int(series.isna().sum()),
                "missing_percent": round(
                    float(series.isna().mean() * 100),
                    2,
                ) if len(series) else 0.0,
                "unique_count": int(series.nunique(dropna=True)),
                "semantic": _semantic_role(str(col)),
            }
        )

    return {
        "table_name": table_name,
        "source_file": source_file,
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "duplicate_rows": int(df.duplicated().sum()),
        "columns": columns,
        "sample_rows": sample,
    }


def _read_upload(path: Path) -> list[tuple[str, pd.DataFrame]]:
    suffix = path.suffix.lower()

    if suffix == ".csv":
        df = pd.read_csv(path, low_memory=False)
        return [(_safe_table_name(path.stem), df)]

    if suffix in {".xlsx", ".xls"}:
        book = pd.ExcelFile(path)
        frames = []
        for sheet in book.sheet_names:
            df = pd.read_excel(path, sheet_name=sheet)
            table = _safe_table_name(
                f"{path.stem}_{sheet}"
                if len(book.sheet_names) > 1
                else path.stem
            )
            frames.append((table, df))
        return frames

    raise ValueError(
        f"Unsupported file type '{suffix}'. "
        "Only CSV and Excel files are supported."
    )


def _dedupe_table_name(
    desired: str,
    used: set[str],
) -> str:
    candidate = desired
    i = 2
    while candidate in used:
        candidate = f"{desired}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def _find_relationship_hints(
    datasets: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    occurrences: dict[str, list[str]] = {}

    for dataset in datasets:
        table = dataset["table_name"]
        for column in dataset["columns"]:
            name = column["name"]
            if name.lower().endswith("_id") or column["semantic"]["role"] in {
                "product_id",
                "order_id",
                "customer_id",
                "identifier",
            }:
                occurrences.setdefault(name.lower(), []).append(table)

    hints = []
    for column, tables in occurrences.items():
        unique_tables = sorted(set(tables))
        if len(unique_tables) >= 2:
            hints.append(
                {
                    "column": column,
                    "tables": unique_tables,
                    "type": "possible_join_key",
                    "confidence": 0.70,
                }
            )

    return hints


def _default_policies() -> dict[str, Any]:
    return BusinessPolicyConfig().model_dump()


def ensure_demo_workspace() -> None:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

    meta = _metadata_path("olist-demo")
    if meta.exists():
        return

    if not DEMO_DB.exists():
        return

    d = _workspace_dir("olist-demo")
    d.mkdir(parents=True, exist_ok=True)

    payload = {
        "workspace_id": "olist-demo",
        "company_name": "NovaMart",
        "business_type": "ecommerce",
        "currency": "BRL",
        "timezone": "America/Sao_Paulo",
        "dataset_name": "Olist Brazilian E-Commerce Dataset",
        "database_path": str(DEMO_DB),
        "source": "demo",
        "status": "ready",
        "datasets": [],
        "relationship_hints": [],
        "policies": {
            **_default_policies(),
            "max_reorder_quantity_without_approval": 1000,
        },
    }

    meta.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def list_workspaces() -> list[dict[str, Any]]:
    ensure_demo_workspace()
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

    results = []

    for metadata_file in WORKSPACE_ROOT.glob("*/metadata.json"):
        try:
            data = json.loads(
                metadata_file.read_text(encoding="utf-8")
            )
            results.append(data)
        except Exception:
            continue

    return sorted(
        results,
        key=lambda item: (
            item.get("source") != "demo",
            item.get("company_name") or "",
        ),
    )


def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    ensure_demo_workspace()
    p = _metadata_path(workspace_id)

    if not p.exists():
        return None

    return json.loads(p.read_text(encoding="utf-8"))


def workspace_agent_payload(
    workspace_id: str,
) -> dict[str, Any]:
    workspace = get_workspace(workspace_id)

    if workspace is None:
        raise FileNotFoundError(
            f"Workspace '{workspace_id}' was not found."
        )

    return {
        "workspace_id": workspace["workspace_id"],
        "company_name": workspace.get("company_name"),
        "business_type": workspace.get("business_type"),
        "currency": workspace.get("currency"),
        "timezone": workspace.get("timezone"),
        "dataset_name": workspace.get("dataset_name"),
        "metadata": {
            "source": workspace.get("source"),
            "datasets": [
                {
                    "table_name": item["table_name"],
                    "row_count": item["row_count"],
                    "columns": [
                        {
                            "name": col["name"],
                            "semantic_role": col["semantic"]["role"],
                        }
                        for col in item.get("columns", [])
                    ],
                }
                for item in workspace.get("datasets", [])
            ],
            "relationship_hints": workspace.get(
                "relationship_hints",
                [],
            ),
        },
        "policies": workspace.get("policies") or _default_policies(),
    }


def get_workspace_database_path(
    workspace_id: str,
) -> Path:
    workspace = get_workspace(workspace_id)

    if workspace is None:
        raise FileNotFoundError(
            f"Workspace '{workspace_id}' was not found."
        )

    path = Path(workspace["database_path"])

    if not path.exists():
        raise FileNotFoundError(
            f"Workspace database is missing: {path}"
        )

    return path


def create_workspace_from_files(
    *,
    company_name: str,
    business_type: str | None,
    currency: str | None,
    timezone: str | None,
    files: list[tuple[str, bytes]],
) -> dict[str, Any]:

    if not company_name.strip():
        raise ValueError("Company name is required.")

    if not files:
        raise ValueError("Upload at least one CSV or Excel file.")

    workspace_id = (
        f"{_slug(company_name)}-"
        f"{uuid.uuid4().hex[:6]}"
    )

    directory = _workspace_dir(workspace_id)
    uploads_dir = directory / "uploads"
    db_path = _database_path(workspace_id)

    directory.mkdir(parents=True, exist_ok=False)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    datasets: list[dict[str, Any]] = []
    used_tables: set[str] = set()

    try:
        with sqlite3.connect(db_path) as conn:
            for original_name, raw in files:
                suffix = Path(original_name).suffix.lower()

                if suffix not in SUPPORTED_EXTENSIONS:
                    raise ValueError(
                        f"{original_name}: only CSV/XLSX/XLS files are supported."
                    )

                if len(raw) > MAX_FILE_BYTES:
                    raise ValueError(
                        f"{original_name}: file exceeds the 50 MB limit."
                    )

                stored_name = (
                    f"{uuid.uuid4().hex[:8]}_"
                    f"{Path(original_name).name}"
                )
                stored_path = uploads_dir / stored_name
                stored_path.write_bytes(raw)

                frames = _read_upload(stored_path)

                for desired_table, df in frames:
                    if len(df) > MAX_TOTAL_ROWS_PER_FILE:
                        raise ValueError(
                            f"{original_name}: table '{desired_table}' "
                            f"has {len(df):,} rows; current safety limit "
                            f"is {MAX_TOTAL_ROWS_PER_FILE:,}."
                        )

                    table_name = _dedupe_table_name(
                        desired_table,
                        used_tables,
                    )

                    # Normalize column labels to strings and keep
                    # names unique for SQLite.
                    renamed = []
                    seen_cols: dict[str, int] = {}
                    for col in df.columns:
                        base = str(col).strip() or "column"
                        count = seen_cols.get(base, 0)
                        seen_cols[base] = count + 1
                        renamed.append(
                            base if count == 0 else f"{base}_{count + 1}"
                        )
                    df.columns = renamed

                    df.to_sql(
                        table_name,
                        conn,
                        if_exists="replace",
                        index=False,
                    )

                    datasets.append(
                        _profile_dataframe(
                            df,
                            table_name=table_name,
                            source_file=original_name,
                        )
                    )

        total_rows = sum(
            item["row_count"]
            for item in datasets
        )

        profile = {
            "workspace_id": workspace_id,
            "company_name": company_name.strip(),
            "business_type": (
                business_type.strip()
                if business_type
                else None
            ),
            "currency": (
                currency.strip().upper()
                if currency
                else None
            ),
            "timezone": (
                timezone.strip()
                if timezone
                else None
            ),
            "dataset_name": (
                ", ".join(
                    sorted(
                        {
                            item["source_file"]
                            for item in datasets
                        }
                    )
                )
            ),
            "database_path": str(db_path),
            "source": "uploaded",
            "status": "ready",
            "summary": {
                "files_uploaded": len(files),
                "tables_created": len(datasets),
                "total_rows": total_rows,
                "warnings": sum(
                    1
                    for dataset in datasets
                    for col in dataset["columns"]
                    if col["semantic"]["status"] == "review"
                ),
            },
            "datasets": datasets,
            "relationship_hints": _find_relationship_hints(
                datasets
            ),
            "policies": _default_policies(),
        }

        _metadata_path(workspace_id).write_text(
            json.dumps(
                profile,
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )

        return profile

    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
