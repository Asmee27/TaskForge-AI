# SynapseOps Docker and CI

## Local Compose

1. Copy `.env.example` to `.env` and set `GROQ_API_KEY` only when running a real workflow.
2. Start the complete local stack:

```powershell
docker compose up --build
```

The dashboard is available at `http://localhost:5173`, the API at `http://localhost:8000`, and the API readiness check is `http://localhost:8000/api/ready`.

Compose starts FastAPI, React/Nginx, PostgreSQL, and Redis. PostgreSQL and Redis use named persistent volumes. Workspace files and M13 reports remain mounted from the repository. The default PostgreSQL host port is `55516` because the previous Windows environment reserved the `55416-55515` range; override it with `POSTGRES_HOST_PORT` when needed.

The browser-facing `VITE_API_BASE_URL` is compiled into the frontend image. It should point to the API address reachable by the browser, not the internal Compose service name.

## Health

- `/api/health` reports application and dependency status.
- `/api/ready` returns HTTP 200 only when PostgreSQL and Redis are ready; otherwise it returns HTTP 503.

## Admin Dashboard

The settings button in the M12 dashboard opens the read-only admin view. It reads persisted workflow runs, workspace metadata, RAG documents, audit events, runtime budget snapshots, circuit snapshots, and the generated M13 report artifact. Missing data is displayed as unavailable rather than synthesized.

## CI

`.github/workflows/ci.yml` installs Python dependencies, compiles the backend, runs deterministic tests and the 29-scenario M13 evaluation, runs `npm ci` and `npm run build`, validates Compose configuration, and builds both images. CI has empty Groq/Tavily keys and makes no provider calls.

No cloud deployment is configured by this milestone.
