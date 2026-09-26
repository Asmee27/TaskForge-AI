# SynapseOps AI M11 — Agent Reliability Upgrade

Complete replacement package.

## What changed

### Planner
- Schema-aware planning.
- No automatic retail metric checklist.
- External Research is optional, not automatic.
- Unsupported metrics are identified rather than invented.

### Analyst
- Strong evidence-to-claim discipline.
- Product IDs/names cannot be used as proof of category/positioning.
- Unsupported metrics go into Limitations.
- Currency must come from workspace metadata.

### Research
- One focused web search maximum.
- Research may use only entities/categories explicitly grounded upstream.
- No competitor-price comparison without comparable internal price evidence.
- Smaller result budget.

### Web search timeout
- Fixed a concurrency bug where a timed-out Tavily request could still keep
  the workflow waiting because ThreadPoolExecutor shutdown waited for it.
- Timeout now returns control without waiting for the timed-out worker.

### Critic
- REVIEW_REQUIRED means analysis quality needs review.
- It no longer semantically implies human execution approval.
- Human approval is reserved for policy/action execution conditions.

## Replace

Replace:
- app/
- frontend/
- run_api.py
- requirements.txt

Keep:
- .env
- data/
- venv/

## Run

Backend:

```powershell
venv\Scripts\activate
python -m pip install -r requirements.txt
python run_api.py
```

Health should report version 0.11.0.

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

## Validation test

Use the SAME Acme Retail workspace and SAME goal from the previous test.

Expected behavior:
- Planner asks only for metrics supported by the uploaded schema.
- Analyst does not label products high-end/premium without direct evidence.
- Research is narrower and faster.
- Critic may still return REVIEW_REQUIRED for evidence quality, but
  Approval should remain "Not required" unless an executable controlled
  action/policy actually requires approval.
- Action Centre should remain NO ACTION PROPOSED for an insight-only goal.
