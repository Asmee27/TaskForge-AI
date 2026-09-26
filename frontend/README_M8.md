# SynapseOps AI — Milestone 8 Frontend

Professional React + Tailwind dashboard connected to the Milestone 7 FastAPI API.

## Features

- Business goal input
- NovaMart workspace card
- Planner execution-plan visualization
- Analyst / Research task results
- Worker status + latency
- Critic verdict, evidence quality, confidence and findings
- Action Centre
- Approve / reject / execute API integration
- Workflow summary
- Initial trace panel
- Handles completed, degraded, failed and blocked runs

## Install

Place this entire folder as:

`<project-root>/frontend`

Then:

```powershell
cd frontend
npm install
npm run dev
```

Keep the FastAPI terminal running separately:

```powershell
python run_api.py
```

Open:

`http://127.0.0.1:5173`

## Expected first run

The default goal is already filled in:

`Analyze inventory and recommend whether we should reorder 5000 units of low-stock products.`

Click `Run Workflow`.

Your current backend result should render as:

- Workflow: `completed degraded`
- Task 1 Analyst: `DEGRADED`
- Analyst finding/evidence text
- Critic: `REVIEW REQUIRED`
- Evidence quality: `LOW`
- Confidence: `20%`
- Action Centre: `BLOCKED INSUFFICIENT EVIDENCE`

This is correct safe-failure behavior.

## Next milestone

Milestone 9 replaces the static trace summary with real-time server-sent events or WebSocket trace events:

Planner → Agent → Tool → Result → Critic → Action Gate

It will also add token/tool-call/retry/cost telemetry.
