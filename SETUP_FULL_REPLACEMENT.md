# SynapseOps AI — Complete M9 Replacement Bundle

This bundle is intentionally complete for the `app/` and `frontend/` folders. It avoids the earlier incremental-update confusion.

## Keep from your existing project

Do NOT delete:
- `.env` (your API keys)
- `data/processed/novamart.db`
- any raw Olist CSVs you want to keep

## Replace

Replace your existing:
- `app/`
- `frontend/`
- `run_api.py`
- `requirements.txt`

with the versions in this bundle.

## Backend

From project root:

```powershell
venv\Scriptsctivate
python -m pip install -r requirements.txt
python -c "from app.api.server_m9 import app; print('BACKEND IMPORT OK')"
python run_api.py
```

Health check:
`http://127.0.0.1:8000/api/health`

## Frontend

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the URL printed by Vite, normally `http://localhost:5173/`.

## Expected live workflow

Planner -> Analyst/Research -> Critic -> Action Gate, with events appearing in the Live Agent Trace before the final result arrives.
