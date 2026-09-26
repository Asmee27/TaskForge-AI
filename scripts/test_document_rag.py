from io import BytesIO
from uuid import uuid4

from pypdf import PdfWriter
import app.core.document_rag as rag
import app.agents.critic_agent as critic
from app.core.persistence import connection, initialize_persistence
from app.models.policy_models import CriticReport
from app.models.workspace_models import WorkspaceContext


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def main():
    initialize_persistence()
    workspace_a = f"synthetic-rag-a-{uuid4().hex}"
    workspace_b = f"synthetic-rag-b-{uuid4().hex}"
    original_extract = rag._extract_chunks
    rag._extract_chunks = lambda raw: [
        {
            "page_number": 2,
            "chunk_index": 1,
            "text": "Acme policy requires manager approval for discounts above ten percent.",
        }
    ]

    try:
        document = rag.store_pdf_document(
            workspace_id=workspace_a,
            filename="acme-policy.pdf",
            raw=b"synthetic",
        )
        assert document["workspace_id"] == workspace_a
        assert document["filename"] == "acme-policy.pdf"
        assert document["page_count"] == 2

        passages_a = rag.retrieve_passages(
            workspace_id=workspace_a,
            query="manager approval discounts",
        )
        assert passages_a
        assert passages_a[0]["citation"] == "acme-policy.pdf (page 2, chunk 1)"
        assert rag.retrieve_passages(
            workspace_id=workspace_b,
            query="manager approval discounts",
        ) == []

        rag._extract_chunks = original_extract
        try:
            rag.store_pdf_document(
                workspace_id=workspace_a,
                filename="scanned.pdf",
                raw=blank_pdf(),
            )
        except ValueError as exc:
            assert "no extractable text" in str(exc).lower()
        else:
            raise AssertionError("blank PDF should be rejected")

        class SyntheticCriticLLM:
            def __init__(self):
                self.prompt = ""

            def with_structured_output(self, schema):
                return self

            def invoke(self, prompt):
                self.prompt = prompt
                return CriticReport(
                    verdict="approved",
                    overall_confidence=0.8,
                    evidence_quality="high",
                    findings=[],
                    summary="Synthetic review",
                )

        synthetic_critic = SyntheticCriticLLM()
        critic.get_llm = lambda: synthetic_critic
        critic.review_workflow(
            goal="Should we approve the discount?",
            workspace=WorkspaceContext(workspace_id=workspace_a),
            task_results=[],
            document_evidence=[
                {
                    "citation": "acme-policy.pdf (page 2, chunk 1)",
                    "text": "Manager approval is required.",
                }
            ],
        )
        assert "UNTRUSTED DOCUMENT PASSAGE" in synthetic_critic.prompt
        assert "acme-policy.pdf (page 2, chunk 1)" in synthetic_critic.prompt
    finally:
        rag._extract_chunks = original_extract
        with connection() as conn:
            conn.execute(
                "DELETE FROM workspace_document_chunks WHERE workspace_id IN (%s, %s)",
                (workspace_a, workspace_b),
            )
            conn.execute(
                "DELETE FROM workspace_documents WHERE workspace_id IN (%s, %s)",
                (workspace_a, workspace_b),
            )

    print("document RAG synthetic tests: passed")


if __name__ == "__main__":
    main()
