# SynapseOps AI — Milestone 9 Live Trace

Adds real-time agent observability using Server-Sent Events (SSE).

## Backend additions

- run-scoped trace event bus
- structured trace events
- asynchronous workflow start endpoint
- SSE event stream
- final result endpoint
- existing synchronous `/api/workflows/run` preserved
- run IDs propagated into background workflow threads

## Frontend additions

- real-time Live Agent Trace panel
- Planner / Agent / Tool / SQL / Retry / Guard / Critic / Action events
- UTC event timestamp rendered in browser local time
- run ID
- live status indicator
- result automatically loads when the SSE stream completes

## Replace / add

Project root:

- replace `app/core/logging.py`
- add `app/api/trace_store.py`
- add `app/api/server_m9.py`
- replace `run_api.py`

Frontend:

- replace `frontend/src/api/client.js`
- add `frontend/src/components/LiveTrace.jsx`
- add `frontend/src/App_m9.jsx`
- replace `frontend/src/App.jsx`

## Run

Restart backend:

```powershell
Ctrl+C
python run_api.py
```

Frontend can remain running; Vite should hot reload. If needed:

```powershell
cd frontend
npm run dev
```

Then run the same inventory goal from the dashboard.

Expected live trace:

- WORKFLOW started
- PLANNER creating plan
- PLANNER plan created
- ROUTER task → analyst
- AGENT analyst invoked
- TOOL / CACHE schema
- SQL attempts/results
- WORKER completed/degraded
- CRITIC review
- ACTION gate
- RUN finished

After `RUN finished`, the final structured workflow result loads automatically.
