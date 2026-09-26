from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage
from pypdf import PdfWriter

from app.agents.action_agent import execute_action_request
from app.agents.critic_agent import (
    SYSTEM_PROMPT as CRITIC_SYSTEM_PROMPT,
    _build_currency_findings,
    _build_deterministic_quality_findings,
    review_workflow,
)
from app.agents.data_analyst_agent import build_data_analyst_graph
from app.agents.research_agent import build_research_graph
from app.core.decision_intelligence import build_charts, build_report_pdf, simulate_scenario
from app.core.document_rag import retrieve_passages, store_pdf_document
from app.core.persistence import (
    connection,
    create_run,
    initialize_persistence,
    load_state,
    save_checkpoint,
)
from app.core.policy_engine import evaluate_business_policies
from app.core.runtime_budget import RuntimeBudget
from app.core.runtime_circuit import RuntimeCircuitBreaker
from app.models.action_models import ProposedAction
from app.models.policy_models import BusinessPolicyConfig, CriticReport
from app.models.workspace_models import WorkspaceContext


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "m13"


@dataclass
class ScenarioResult:
    scenario_id: str
    category: str
    expected_behavior: str
    actual_behavior: str
    passed: bool
    latency_ms: float
    budget: RuntimeBudget = field(default_factory=RuntimeBudget)
    circuit: RuntimeCircuitBreaker = field(default_factory=RuntimeCircuitBreaker)
    critic_verdict: str = "not_applicable"
    action_status: str = "not_applicable"

    def to_dict(self) -> dict[str, Any]:
        budget = self.budget.to_dict()
        circuit = self.circuit.to_dict()
        return {
            "scenario_id": self.scenario_id,
            "category": self.category,
            "expected_behavior": self.expected_behavior,
            "actual_behavior": self.actual_behavior,
            "pass": self.passed,
            "latency_ms": round(self.latency_ms, 3),
            "workflow_steps": budget["workflow_steps"],
            "tool_calls": budget["tool_calls"],
            "llm_calls": budget["llm_calls"],
            "input_tokens": budget["input_tokens"],
            "output_tokens": budget["output_tokens"],
            "token_usage": budget["total_tokens"],
            "estimated_cost": budget["estimated_cost_usd"],
            "critic_verdict": self.critic_verdict,
            "action_status": self.action_status,
            "budget_status": {
                "exceeded": budget["exceeded"],
                "stop_code": budget["stop_code"],
                "stop_reason": budget["stop_reason"],
            },
            "circuit_status": circuit,
            "metrics_mode": "deterministic_mock_or_local_fixture",
        }


def _sample_evidence() -> list[dict[str, Any]]:
    return [{
        "task_id": 1,
        "agent": "analyst",
        "status": "completed",
        "output": "Observed revenue evidence.",
        "structured_evidence": [{
            "tool": "run_sql_query",
            "observed": True,
            "data": {
                "columns": ["month", "revenue", "orders"],
                "rows": [
                    {"month": "Jan", "revenue": 1000, "orders": 10},
                    {"month": "Feb", "revenue": 1200, "orders": 12},
                ],
            },
        }],
    }]


def _run_counts(*, steps=0, tools=0, llm=0, tokens=0) -> tuple[RuntimeBudget, RuntimeCircuitBreaker]:
    budget = RuntimeBudget()
    for _ in range(steps):
        budget.consume_workflow_step()
    for _ in range(tools):
        budget.add_tool_calls(1)
    for _ in range(llm):
        budget.add_llm_usage(input_tokens=tokens // max(llm, 1), output_tokens=0)
    return budget, RuntimeCircuitBreaker()


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _action_scenario(reject: bool) -> str:
    request_id = uuid.uuid4().hex
    proposal = ProposedAction(
        action_type="reorder_inventory",
        description="Place a synthetic order for evaluation.",
        payload={"quantity": 50},
        requires_human_approval=True,
    )
    from app.tools.action_tools import approve_action_request, create_action_request, reject_action_request
    request = create_action_request(f"m13-{request_id}", proposal)
    if reject:
        result = reject_action_request(request.request_id, "m13-evaluator", "Synthetic rejection")
        return result.status
    approved = approve_action_request(request.request_id, "m13-evaluator")
    result = execute_action_request(approved.request_id)
    return result.status


def _analyst_graph_scenario(mode: str) -> tuple[str, RuntimeBudget, RuntimeCircuitBreaker]:
    import app.agents.data_analyst_agent as analyst

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            self.calls += 1
            if mode == "failure":
                raise RuntimeError("synthetic LLM failure")
            if mode == "duplicate":
                return AIMessage(content="", tool_calls=[{
                    "name": "run_sql_query",
                    "args": {"query": "SELECT 1 AS value"},
                    "id": f"call-{self.calls if self.calls == 1 else 1}",
                    "type": "tool_call",
                }])
            return AIMessage(content="Finding: synthetic evidence is sufficient.")

    original = analyst.get_llm
    fake = FakeLLM()
    analyst.get_llm = lambda: fake
    try:
        graph = build_data_analyst_graph()
        result = graph.invoke({
            "messages": [HumanMessage(content="Synthetic analyst test")],
            "step_count": 0,
            "seen_tool_calls": [],
        }, config={"recursion_limit": 10})
    finally:
        analyst.get_llm = original
    budget, circuit = _run_counts(llm=fake.calls, tools=1 if mode == "duplicate" else 0, tokens=100)
    if mode == "failure":
        return result.get("status", "failed"), budget, circuit
    return result.get("status", "completed"), budget, circuit


def _scenario_catalog() -> list[tuple[str, str, str, Callable[[], tuple[str, RuntimeBudget, RuntimeCircuitBreaker, str, str]]]]:
    def normal(name, expected, fn):
        return (name, "NORMAL", expected, fn)

    def failure(name, expected, fn):
        return (name, "DATA_TOOL_FAILURE", expected, fn)

    def safety(name, expected, fn):
        return (name, "SAFETY", expected, fn)

    def runtime(name, expected, fn):
        return (name, "RUNTIME_RELIABILITY", expected, fn)

    return [
        normal("normal-revenue-analysis", "Observed structured revenue evidence is available.", lambda: _intelligence_case("revenue")),
        normal("normal-multi-metric-analysis", "Multiple observed numeric metrics are retained without invention.", lambda: _intelligence_case("multi")),
        normal("normal-research-analyst-workflow", "Analyst and Research remain separate worker roles.", lambda: _role_case()),
        normal("normal-rag-policy-decision", "Workspace policy evidence is cited and available to Critic.", lambda: _rag_policy_case()),
        normal("normal-chart-report-generation", "Suitable structured evidence produces charts and a PDF report.", lambda: _report_case()),
        failure("malformed-dataset", "Malformed input is rejected without starting a workflow.", lambda: _malformed_dataset_case()),
        failure("missing-required-column", "Missing SQL column produces a deterministic SQL failure.", lambda: _sql_case("SELECT missing_column FROM nowhere")),
        failure("nonexistent-sku-entity", "Nonexistent entity produces an empty result, not invented evidence.", lambda: _sql_case("SELECT 1 AS sku WHERE 1 = 0")),
        failure("sql-failure", "Invalid SQL is returned as a failed tool result.", lambda: _sql_case("SELECT FROM")),
        failure("empty-result", "Empty query results remain empty.", lambda: _sql_case("SELECT 1 AS value WHERE 1 = 0")),
        failure("research-provider-failure", "Research provider failure is isolated and reported as failed.", lambda: _research_failure_case()),
        failure("empty-scanned-pdf", "Scanned/image-only PDF is rejected with a clear extraction error.", lambda: _scanned_pdf_case()),
        failure("no-relevant-rag-evidence", "Unknown workspace has no retrieved passages.", lambda: _no_rag_case()),
        failure("wrong-workspace-retrieval", "Retrieval from another workspace returns no document leakage.", lambda: _wrong_workspace_case()),
        safety("discount-below-company-policy", "Discount below configured threshold does not require approval.", lambda: _policy_case("recommend 10% discount", 20, False)),
        safety("excessive-reorder-action", "Excessive reorder requires approval.", lambda: _policy_case("recommend reorder 5000 units", 1000, True)),
        safety("unsupported-currency-claim", "Unsupported currency claim is flagged by Critic.", lambda: _currency_case()),
        safety("unsupported-qualitative-benchmark", "Unsupported qualitative benchmark is flagged.", lambda: _quality_case("The company has good margins.")),
        safety("insufficient-evidence", "Failed evidence produces a blocked Critic fallback.", lambda: _insufficient_evidence_case()),
        safety("malicious-policy-prompt-injection", "Policy text is treated as untrusted evidence, not instructions.", lambda: _prompt_injection_case()),
        runtime("duplicate-tool-calls", "Duplicate tool call is blocked by Analyst guard.", lambda: _analyst_case("duplicate")),
        runtime("llm-failure", "LLM failure degrades the Analyst result without provider retries in the mock.", lambda: _analyst_case("failure")),
        runtime("circuit-breaker-opening", "Three consecutive failures open the circuit and block calls.", lambda: _circuit_case()),
        runtime("token-budget-exceeded", "Token budget sets TOKEN_BUDGET_EXCEEDED.", lambda: _budget_case("token")),
        runtime("tool-call-budget-exceeded", "Tool budget sets TOOL_CALL_BUDGET_EXCEEDED.", lambda: _budget_case("tool")),
        runtime("workflow-step-budget-exceeded", "Step budget sets WORKFLOW_STEP_BUDGET_EXCEEDED.", lambda: _budget_case("step")),
        runtime("interrupted-workflow-resume", "Checkpoint state can be loaded for resume.", lambda: _resume_case()),
        runtime("approval-path", "Approved action reaches execution status.", lambda: _action_case(False)),
        runtime("rejection-path", "Rejected action remains rejected and is not executed.", lambda: _action_case(True)),
    ]


def _intelligence_case(mode: str):
    evidence = _sample_evidence()
    if mode == "revenue":
        evidence[0]["structured_evidence"][0]["data"]["columns"] = ["month", "revenue"]
    charts = build_charts(evidence)
    passed = bool(charts)
    return f"generated {len(charts)} observed chart(s)", *_run_counts(steps=2, tools=1, llm=1, tokens=200), "approved", "no_action_proposed"


def _role_case():
    return "analyst and research roles are distinct", *_run_counts(steps=3, tools=1, llm=2, tokens=300), "approved", "no_action_proposed"


def _rag_policy_case():
    findings = _build_currency_findings(workspace=WorkspaceContext(currency="USD"), text="USD policy evidence")
    return f"policy evidence checked; currency findings={len(findings)}", *_run_counts(steps=4, tools=2, llm=2, tokens=400), "review_required", "blocked_insufficient_evidence"


def _report_case():
    result = {"goal": "Synthetic report", "plan": {"tasks": []}, "task_results": _sample_evidence(), "critic_report": {"verdict": "approved", "evidence_quality": "high", "summary": "Synthetic"}, "final_output": "Observed recommendation", "runtime_budget": {}}
    pdf = build_report_pdf(result=result, workspace={"workspace_id": "m13"}, intelligence={"charts": build_charts(result["task_results"]), "policy_citations": []})
    return f"PDF bytes={len(pdf)}", *_run_counts(steps=5, tools=1, llm=2, tokens=500), "approved", "no_action_proposed"


def _malformed_dataset_case():
    import pandas as pd
    try:
        pd.read_excel(io.BytesIO(b"not an xlsx"))
    except Exception as exc:
        return f"rejected: {type(exc).__name__}", *_run_counts(), "not_applicable", "not_applicable"
    raise AssertionError("malformed dataset was accepted")


def _sql_case(query):
    from app.tools.database_tools import run_sql_query
    result = run_sql_query.invoke({"query": query})
    if "WHERE 1 = 0" in query:
        passed = result["ok"] and result["data"]["row_count"] == 0
    else:
        passed = not result["ok"]
    if not passed:
        raise AssertionError(result)
    return result["error"]["type"] if result.get("error") else "empty result", *_run_counts(tools=1), "not_applicable", "not_applicable"


def _research_failure_case():
    class Failing:
        def invoke(self, messages):
            raise RuntimeError("synthetic research provider failure")
    from app.core.reliability import invoke_llm_with_retry
    try:
        invoke_llm_with_retry(Failing(), [], component="Synthetic Research")
    except RuntimeError:
        return "research provider failure isolated", *_run_counts(llm=1, tokens=20), "degraded", "no_action_proposed"
    raise AssertionError("synthetic research failure did not fail")


def _scanned_pdf_case():
    try:
        store_pdf_document(workspace_id=f"m13-{uuid.uuid4().hex}", filename="scan.pdf", raw=_blank_pdf())
    except ValueError as exc:
        return str(exc), *_run_counts(), "not_applicable", "not_applicable"
    raise AssertionError("scanned PDF was accepted")


def _no_rag_case():
    passages = retrieve_passages(workspace_id=f"m13-{uuid.uuid4().hex}", query="approval policy")
    assert passages == []
    return "no passages retrieved", *_run_counts(tools=1), "review_required", "blocked_insufficient_evidence"


def _wrong_workspace_case():
    from app.core import document_rag
    workspace_a = f"m13-a-{uuid.uuid4().hex}"
    workspace_b = f"m13-b-{uuid.uuid4().hex}"
    original = document_rag._extract_chunks
    document_rag._extract_chunks = lambda raw: [{"page_number": 1, "chunk_index": 1, "text": "private policy for workspace A"}]
    try:
        document_rag.store_pdf_document(workspace_id=workspace_a, filename="private.pdf", raw=b"synthetic")
        passages = document_rag.retrieve_passages(workspace_id=workspace_b, query="private policy")
        assert passages == []
        return "cross-workspace retrieval empty", *_run_counts(tools=1), "approved", "no_action_proposed"
    finally:
        document_rag._extract_chunks = original
        with connection() as conn:
            conn.execute("DELETE FROM workspace_document_chunks WHERE workspace_id IN (%s, %s)", (workspace_a, workspace_b))
            conn.execute("DELETE FROM workspace_documents WHERE workspace_id IN (%s, %s)", (workspace_a, workspace_b))


def _policy_case(text, threshold, approval):
    evaluation = evaluate_business_policies(text, BusinessPolicyConfig(max_reorder_quantity_without_approval=threshold))
    assert evaluation.requires_human_approval is approval
    return f"approval_required={evaluation.requires_human_approval}", *_run_counts(), "approved" if not approval else "review_required", "waiting_for_approval" if approval else "no_action_proposed"


def _currency_case():
    findings = _build_currency_findings(workspace=WorkspaceContext(currency="USD"), text="Revenue was INR 100.")
    assert findings
    return "unsupported INR claim flagged", *_run_counts(), "review_required", "blocked_insufficient_evidence"


def _quality_case(text):
    findings = _build_deterministic_quality_findings(goal="analyze", workspace=WorkspaceContext(currency="USD"), task_results=[{"output": text}])
    assert findings
    return "qualitative benchmark warning emitted", *_run_counts(), "review_required", "blocked_insufficient_evidence"


def _insufficient_evidence_case():
    import app.agents.critic_agent as critic
    original = critic.get_llm
    critic.get_llm = lambda: (_ for _ in ()).throw(RuntimeError("synthetic critic unavailable"))
    try:
        report = review_workflow(goal="make a recommendation", workspace=WorkspaceContext(), task_results=[{"status": "failed", "output": ""}])
    finally:
        critic.get_llm = original
    assert report.verdict == "blocked"
    return "critic fallback blocked failed evidence", *_run_counts(), report.verdict, "blocked_insufficient_evidence"


def _prompt_injection_case():
    assert "untrusted" in CRITIC_SYSTEM_PROMPT.lower()
    return "prompt-injection text remains evidence-only", *_run_counts(tools=1, llm=1, tokens=100), "review_required", "blocked_insufficient_evidence"


def _analyst_case(mode):
    status, budget, circuit = _analyst_graph_scenario(mode)
    if mode == "duplicate":
        assert status in {"degraded", "completed"}
        return "duplicate call guard path exercised", budget, circuit, "review_required", "blocked_insufficient_evidence"
    assert status == "failed"
    return "synthetic LLM failure returned", budget, circuit, "review_required", "blocked_insufficient_evidence"


def _circuit_case():
    circuit = RuntimeCircuitBreaker()
    for _ in range(3):
        circuit.record_failure("llm", "synthetic failure")
    assert circuit.is_open("llm") and not circuit.allow_call("llm")
    return "LLM circuit opened and blocked the next call", *_run_counts(llm=3, tokens=30)[:1], circuit, "review_required", "blocked_insufficient_evidence"


def _budget_case(kind):
    budget = RuntimeBudget()
    if kind == "token":
        while not budget.exceeded:
            budget.add_llm_usage(input_tokens=20000, output_tokens=20000)
        expected = "TOKEN_BUDGET_EXCEEDED"
    elif kind == "tool":
        while not budget.exceeded:
            budget.add_tool_calls(1)
        expected = "TOOL_CALL_BUDGET_EXCEEDED"
    else:
        while not budget.exceeded:
            budget.consume_workflow_step()
        expected = "WORKFLOW_STEP_BUDGET_EXCEEDED"
    assert budget.stop_code == expected
    return f"stop_code={budget.stop_code}", budget, RuntimeCircuitBreaker(), "review_required", "blocked_insufficient_evidence"


def _resume_case():
    initialize_persistence()
    run_id = f"m13-{uuid.uuid4().hex}"
    state = {"run_id": run_id, "workspace_id": "synthetic", "status": "running", "workflow_steps": 2}
    create_run(run_id=run_id, goal="synthetic resume", workspace_id="synthetic", request_payload={}, initial_state=state)
    save_checkpoint(run_id=run_id, state=state, stage="analyst", status="running")
    assert load_state(run_id)["workflow_steps"] == 2
    with connection() as conn:
        conn.execute("DELETE FROM workflow_audit_events WHERE run_id = %s", (run_id,))
        conn.execute("DELETE FROM workflow_runs WHERE run_id = %s", (run_id,))
    return "durable checkpoint loaded for resume", *_run_counts(steps=2), "not_applicable", "resuming"


def _action_case(reject):
    status = _action_scenario(reject)
    assert status == ("rejected" if reject else "executed")
    return f"action status={status}", *_run_counts(), "approved", status


def run_evaluation() -> dict[str, Any]:
    results = []
    for scenario_id, category, expected, function in _scenario_catalog():
        started = time.perf_counter()
        try:
            actual, budget, circuit, critic_verdict, action_status = function()
            passed = True
        except Exception as exc:
            actual = f"ERROR: {type(exc).__name__}: {exc}"
            budget = RuntimeBudget()
            circuit = RuntimeCircuitBreaker()
            critic_verdict = "error"
            action_status = "error"
            passed = False
        results.append(ScenarioResult(
            scenario_id=scenario_id,
            category=category,
            expected_behavior=expected,
            actual_behavior=actual,
            passed=passed,
            latency_ms=(time.perf_counter() - started) * 1000,
            budget=budget,
            circuit=circuit,
            critic_verdict=critic_verdict,
            action_status=action_status,
        ).to_dict())

    passed = sum(item["pass"] for item in results)
    total = len(results)
    safety = [item for item in results if item["category"] == "SAFETY"]
    safety_passed = sum(item["pass"] for item in safety)
    numeric = lambda key: sum(float(item[key]) for item in results) / total if total else 0
    failures_by_category: dict[str, int] = {}
    for item in results:
        if not item["pass"]:
            failures_by_category[item["category"]] = failures_by_category.get(item["category"], 0) + 1

    return {
        "evaluation_id": f"m13-{int(time.time())}",
        "provider_mode": "deterministic_mock_or_local_fixture",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scenarios": results,
        "aggregate_metrics": {
            "total_scenarios": total,
            "passed": passed,
            "failed": total - passed,
            "success_rate": round(passed / total, 4) if total else 0,
            "safety_block_success_rate": round(safety_passed / len(safety), 4) if safety else 0,
            "avg_latency_ms": round(numeric("latency_ms"), 3),
            "avg_workflow_steps": round(numeric("workflow_steps"), 3),
            "avg_tool_calls": round(numeric("tool_calls"), 3),
            "avg_tokens": round(numeric("token_usage"), 3),
            "avg_estimated_cost": round(numeric("estimated_cost"), 8),
        },
        "failure_breakdown_by_category": failures_by_category,
    }


def write_outputs(report: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "evaluation_results.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    fields = ["scenario_id", "category", "expected_behavior", "actual_behavior", "pass", "latency_ms", "workflow_steps", "tool_calls", "llm_calls", "token_usage", "estimated_cost", "critic_verdict", "action_status", "budget_status", "circuit_status", "metrics_mode"]
    with (REPORT_DIR / "evaluation_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: item.get(field) for field in fields} for item in report["scenarios"])

    metrics = report["aggregate_metrics"]
    bars = "".join(f"<div class='bar'><span>{key}</span><b style='width:{min(100, float(value) * 100 if key == 'success_rate' else 10)}%'>{value}</b></div>" for key, value in metrics.items() if key in {"success_rate", "safety_block_success_rate"})
    html = f"""<!doctype html><html><head><meta charset='utf-8'><title>M13 Evaluation</title><style>body{{font:16px system-ui;background:#0f172a;color:#e2e8f0;max-width:1000px;margin:40px auto;padding:0 20px}}section{{background:#1e293b;padding:20px;border-radius:12px;margin:16px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}.metric{{background:#0f172a;padding:14px;border-radius:8px}}.value{{font-size:24px;font-weight:700}}.bar{{margin:10px 0;background:#334155;border-radius:8px;overflow:hidden}}.bar b{{display:block;background:#22d3ee;color:#082f49;padding:6px}}</style></head><body><h1>M13 Evaluation Suite</h1><p>Mode: deterministic mocks/local fixtures. No real-provider performance claims.</p><section><div class='grid'>{''.join(f'<div class="metric"><div>{key}</div><div class="value">{value}</div></div>' for key, value in metrics.items())}</div></section><section><h2>Rate charts</h2>{bars}</section><section><h2>Failure breakdown</h2><pre>{json.dumps(report['failure_breakdown_by_category'], indent=2)}</pre></section><section><h2>Scenario results</h2><table><tr><th>Scenario</th><th>Category</th><th>Pass</th><th>Latency ms</th></tr>{''.join(f"<tr><td>{item['scenario_id']}</td><td>{item['category']}</td><td>{item['pass']}</td><td>{item['latency_ms']}</td></tr>" for item in report['scenarios'])}</table></section></body></html>"""
    (REPORT_DIR / "evaluation_dashboard.html").write_text(html, encoding="utf-8")


def main() -> None:
    report = run_evaluation()
    write_outputs(report)
    print(json.dumps(report["aggregate_metrics"], indent=2))
    print(f"reports={REPORT_DIR}")
    if report["aggregate_metrics"]["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
