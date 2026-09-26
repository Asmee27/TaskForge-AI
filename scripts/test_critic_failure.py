import app.agents.critic_agent as critic
from app.models.workspace_models import WorkspaceContext


class BindingFailure:
    def with_structured_output(self, schema):
        raise RuntimeError("synthetic structured binding failure")


class InvocationFailure:
    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        raise RuntimeError("synthetic provider invocation failure")


class ParsingFailure:
    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        return {"not": "a valid CriticReport"}


def assert_safe_failure(factory, expected_text):
    original = critic.get_llm
    critic.get_llm = factory
    try:
        report = critic.review_workflow(
            goal="Recommend reorder 5000 units.",
            workspace=WorkspaceContext(
                workspace_id="critic-failure",
                policies={"max_reorder_quantity_without_approval": 1000},
            ),
            task_results=[{"status": "completed", "output": "Observed evidence."}],
        )
    finally:
        critic.get_llm = original

    assert report.verdict == "blocked"
    assert report.evidence_quality == "low"
    assert report.overall_confidence == 0
    assert any(expected_text in finding.message for finding in report.findings)
    assert any(finding.category == "policy" for finding in report.findings)
    assert "insufficient" in report.summary.lower()


def main():
    assert_safe_failure(lambda: (_ for _ in ()).throw(RuntimeError("synthetic LLM creation failure")), "synthetic LLM creation failure")
    assert_safe_failure(lambda: BindingFailure(), "synthetic structured binding failure")
    assert_safe_failure(lambda: InvocationFailure(), "synthetic provider invocation failure")
    assert_safe_failure(lambda: ParsingFailure(), "ValidationError")
    print("critic failure synthetic tests: passed")


if __name__ == "__main__":
    main()
