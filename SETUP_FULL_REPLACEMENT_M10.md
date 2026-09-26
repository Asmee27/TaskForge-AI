# SynapseOps AI — Milestone 10
## Multi-Business Workspace Upload + Workspace-Aware Agents

This is a COMPLETE replacement package for the `app/` and `frontend/`
folders. Do not mix individual files from earlier milestones.

### New capability

A new business can:

1. Create a workspace.
2. Upload one or more CSV/XLSX/XLS files.
3. SynapseOps creates `data/workspaces/<workspace-id>/workspace.db`.
4. It profiles rows, columns, missing values and duplicates.
5. It detects likely semantic roles such as product_id, stock_quantity,
   revenue, order_date, category and cost.
6. It detects possible join keys across uploaded tables.
7. The dashboard switches to the new business workspace.
8. Planner, Analyst, Research and Critic receive that workspace context.
9. Analyst SQL is executed only against the selected workspace database.

### Important architecture fix

The Analyst's schema cache is now keyed by database path. This prevents
schema leakage when switching from NovaMart to another company.

The SQL tool also resolves the database from run-scoped workspace context,
instead of using one global hardcoded NovaMart database.

### Planner/Analyst correctness improvement

A proposed quantity such as:

"Should we reorder 5000 units?"

is treated as an action quantity to evaluate, NOT as a target total inventory
of 5000 units. The agents are explicitly forbidden from inventing target-stock
formulas when the source data does not define them.

## Replace

Replace the old project folders/files with:

- `app/`
- `frontend/`
- `run_api.py`
- `requirements.txt`

KEEP your existing:

- `.env`
- `data/`
- `venv/`

The existing NovaMart database at `data/processed/novamart.db` is still used
for the built-in demo workspace.

## Backend

```powershell
venv\Scripts\activate
python -m pip install -r requirements.txt
python run_api.py
```

Expected API version: `0.10.0`

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

## First test

Use NovaMart first and verify the existing workflow.

Then click `+` in Data Workspace and upload a small CSV/XLSX from another
business. After workspace creation, select it and ask:

"Analyze this business data and identify the most important operational insights."

Do not expect inventory-specific conclusions unless the uploaded schema
actually contains inventory-related evidence.
