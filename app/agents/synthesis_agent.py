from __future__ import annotations
from app.core.config import get_llm
from app.core.logging import log_event
from app.core.reliability import invoke_llm_with_retry

SYSTEM_PROMPT = """
You are the Evidence Synthesizer for SynapseOps AI.
Use ONLY completed worker evidence. Do not query SQL or the web.
Separate direct observations from cautious risks/opportunities and recommendations.
Never label a metric high, low, healthy, poor, good, bad, unusual, or concerning
unless supplied evidence contains a business target, policy threshold, comparison,
or external benchmark. Average discount amount is not discount usage frequency.
Do not infer causes from aggregates. Keep unsupported metrics unsupported.
Recommendations must be reversible next checks, not irreversible actions.
Currency may come ONLY from the supplied workspace metadata. If workspace
currency is unknown, say monetary units and do not use or assume INR, USD,
BRL, another currency code, or a currency symbol. Never infer currency from
dataset values, company name, dataset name, geography, timezone, or formatting.
Return: Operational observations; Potential risks / opportunities; Recommended next checks; Confidence; Limitations.
"""

def synthesize_evidence(*, goal: str, workspace_context: str, evidence: str) -> dict:
    log_event("SYNTHESIS", "Evidence synthesis started")
    prompt=f"""{SYSTEM_PROMPT}

WORKSPACE
{workspace_context}

BUSINESS GOAL
{goal}

COMPLETED WORKER EVIDENCE
{evidence}"""
    try:
        response=invoke_llm_with_retry(get_llm(),prompt,component="Evidence Synthesizer")
        content=getattr(response,"content",str(response))
        log_event("SYNTHESIS","Evidence synthesis completed")
        return {"status":"completed","content":content}
    except Exception as exc:
        log_event("SYNTHESIS",f"Evidence synthesis degraded | {type(exc).__name__}")
        return {"status":"degraded","content":"Synthesis unavailable. Use completed worker findings directly; no extra interpretation was added."}
