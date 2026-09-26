from app.agents.critic_agent import _build_currency_findings
from app.models.workspace_models import WorkspaceContext


def findings(currency, output):
    return _build_currency_findings(
        workspace=WorkspaceContext(currency=currency),
        text=output,
    )


def main():
    assert not findings("USD", "Revenue was $100 USD.")
    assert not findings("INR", "Revenue was INR 100.")
    assert findings("USD", "Revenue was INR 100 (assumed from the dataset).")
    assert findings(None, "Revenue was ₹100.")
    assert not findings(None, "Revenue was reported in monetary units.")

    unknown_context = WorkspaceContext().to_agent_context()
    assert "Currency: UNKNOWN" in unknown_context
    assert "INR" not in unknown_context
    assert "USD" not in unknown_context
    assert "BRL" not in unknown_context

    print("currency safety synthetic tests: passed")


if __name__ == "__main__":
    main()
