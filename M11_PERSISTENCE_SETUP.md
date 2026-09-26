# SynapseOps M11 — PostgreSQL + Redis Persistence

This milestone is built directly on the working M11.2 project.

## What changed

- PostgreSQL is the durable source of truth for workflow runs and checkpoints.
- Every completed LangGraph node is checkpointed.
- Restart/resume skips Planner and worker tasks that already completed.
- Redis stores fast trace copies and a per-run execution lease.
- SSE traces can recover from Redis/PostgreSQL after an API restart.
- Duplicate start/resume attempts are protected by a Redis lease.
- Interrupted runs are discoverable and resumable.
- Existing SQLite workspace datasets remain unchanged. PostgreSQL is for
  orchestration state, not uploaded business data yet.
- Existing action approval store is intentionally left unchanged in this
  milestone to avoid destabilizing the already-tested approval path.

## 1. Keep your existing local data and secrets

Do NOT delete:
- `.env`
- `data/`
- your existing workspace uploads

Copy the new source files over the project.

## 2. Add these lines to `.env`

```env
SYNAPSEOPS_DATABASE_URL=postgresql://synapseops:synapseops@127.0.0.1:5432/synapseops
SYNAPSEOPS_REDIS_URL=redis://127.0.0.1:6379/0
SYNAPSEOPS_TRACE_TTL_SECONDS=86400
```

Keep your existing Groq/Tavily keys.

## 3. Start PostgreSQL + Redis

From the project root:

```powershell
docker compose -f docker-compose.persistence.yml up -d
```

Check:

```powershell
docker ps
```

You should see:
- `synapseops-postgres`
- `synapseops-redis`

## 4. Install new Python packages

```powershell
venv\Scripts\activate
python -m pip install -r requirements.txt
```

## 5. Start API

```powershell
python run_api.py
```

Open:

`http://127.0.0.1:8000/api/health`

Expected:
- version `0.12.0`
- persistence PostgreSQL `ok: true`
- Redis `ok: true`

## 6. Normal workflow test

Run the same Acme goal from the UI. Check backend logs for:

```text
[CHECKPOINT] Durable checkpoint saved | stage=planner
[CHECKPOINT] Durable checkpoint saved | stage=execute_task
...
[CHECKPOINT] Durable checkpoint saved | stage=finalize
```

## 7. Restart/resume test

Start a workflow with at least two worker tasks.

After Task 1 completes and you see its durable checkpoint:
1. Stop FastAPI with Ctrl+C.
2. Start it again with `python run_api.py`.
3. Open:

```text
GET /api/workflows/incomplete
```

Find the run ID.

Resume:

```text
POST /api/workflows/{run_id}/resume
```

Expected behavior:
- same run ID;
- Planner is NOT rerun;
- completed Task 1 is NOT rerun;
- workflow continues from the first unfinished stage;
- previous trace/result state remains available.

## Important reliability rule

PostgreSQL is durable state. Redis is an acceleration/coordination layer.
If Redis temporarily fails, durable checkpoints remain in PostgreSQL.
