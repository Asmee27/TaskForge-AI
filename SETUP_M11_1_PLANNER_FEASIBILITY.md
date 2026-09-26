# SynapseOps M11.1 — Planner Feasibility Guard

This fixes the failure where Planner generated one giant Analyst task with
revenue, profit, returns, cancellations, brands, suppliers, ratings, CLV,
repeat customer rate, shipping, discounts, etc.

## New deterministic guard

The generated plan is now checked AFTER the LLM response.

- Analyst metric checklists are capped at 5 related metrics per task.
- Oversized checklists are split into at most 2 bounded Analyst tasks.
- Detected workspace columns are used to filter obviously unsupported metrics.
- The guard is generic; it contains no Acme/NovaMart/Olist-specific schema.
- Invented proxy formulas and thresholds are explicitly forbidden.
- Dependencies are rebuilt after plan repair.
- Total workflow tasks remain capped at 5.

## Replace

Replace the complete `app/` folder from this package.

`frontend/` can also be replaced safely, but there is no required UI change
for M11.1.

Keep:
- `.env`
- `data/`
- `venv/`

## Run

```powershell
venv\Scripts\activate
python run_api.py
```

Health version: 0.11.1

Then rerun the exact same Acme Retail goal that previously failed.

Expected difference:
The Planner must NOT hand the Analyst one ~20-metric task. You should see a
small bounded Analyst task (or at most two) based on schema-supported evidence.
