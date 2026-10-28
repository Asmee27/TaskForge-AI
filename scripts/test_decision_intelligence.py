from app.core.decision_intelligence import (
    build_charts,
    build_report_pdf,
    simulate_scenario,
)


def sample_result():
    return {
        "goal": "Review observed sales by month.",
        "workspace": {
            "workspace_id": "synthetic-workspace",
            "company_name": "Synthetic Co",
            "currency": "USD",
        },
        "plan": {"tasks": [{"id": 1, "description": "Analyze sales."}]},
        "task_results": [{
            "task_id": 1,
            "agent": "analyst",
            "status": "completed",
            "output": "Observed sales evidence.",
            "structured_evidence": [{
                "tool": "run_sql_query",
                "observed": True,
                "data": {
                    "columns": ["month", "revenue"],
                    "rows": [
                        {"month": "Jan", "revenue": 100},
                        {"month": "Feb", "revenue": 120},
                    ],
                },
            }],
        }],
        "synthesis_result": {"content": "Observed synthesis."},
        "critic_report": {"verdict": "approved", "evidence_quality": "high", "summary": "Approved."},
        "final_output": "Recommendation based on observed evidence.",
        "action_status": "no_action_proposed",
        "runtime_budget": {"workflow_steps": 4},
    }


def main():
    result = sample_result()
    charts = build_charts(result["task_results"])
    assert len(charts) == 1
    assert charts[0]["type"] == "line"
    assert charts[0]["observed"] is True

    simulation = simulate_scenario(
        scenario="What if discount increases from 10% to 15%?",
        task_results=result["task_results"],
    )
    assert simulation["status"] == "simulated"
    assert simulation["simulated"][0]["simulated"] is True
    assert simulation["observed"][0]["observed"] is True

    unsupported = simulate_scenario(
        scenario="What if customer count doubles?",
        task_results=result["task_results"],
    )
    assert unsupported["status"] == "unsupported"

    pdf = build_report_pdf(
        result=result,
        workspace=result["workspace"],
        intelligence={"charts": charts, "policy_citations": []},
    )
    assert pdf.startswith(b"%PDF")

    # Regression test for oversized content pagination (LayoutError prevention)
    very_long_text = "\n".join([f"Line {i}: SYNAPSEOPS WORKFLOW RESULT - Paragraph testing defensive pagination with large content." for i in range(300)])
    long_result = sample_result()
    long_result["final_output"] = very_long_text
    long_result["task_results"][0]["output"] = very_long_text
    long_result["critic_report"]["summary"] = very_long_text

    long_pdf = build_report_pdf(
        result=long_result,
        workspace=long_result["workspace"],
        intelligence={"charts": charts, "policy_citations": [{"citation": "doc.pdf (page 1, chunk 1)", "text": very_long_text}]},
    )
    assert long_pdf.startswith(b"%PDF")
    assert len(long_pdf) > 10000

    print("decision intelligence synthetic tests: passed")


if __name__ == "__main__":
    main()
