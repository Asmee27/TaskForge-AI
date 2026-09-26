from __future__ import annotations

import io
import re
from html import escape
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String, Wedge
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.document_rag import retrieve_passages


_NUMERIC_FIELDS = {
    "amount", "average", "avg", "count", "price", "quantity", "revenue",
    "sales", "total", "value", "volume", "cost", "rate", "percent",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("%", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _field_hint(name: str) -> bool:
    lowered = re.sub(r"[^a-z0-9]+", "_", name.lower())
    return any(part in lowered.split("_") for part in _NUMERIC_FIELDS)


def _structured_evidence(task_results: list[dict]) -> list[dict]:
    evidence = []
    for task in task_results or []:
        if task.get("status") not in {"completed", "degraded"}:
            continue
        for item in task.get("structured_evidence", []) or []:
            data = item.get("data") or {}
            if item.get("observed") and isinstance(data, dict):
                evidence.append({
                    "task_id": task.get("task_id"),
                    "tool": item.get("tool"),
                    "data": data,
                })
    return evidence


def build_charts(task_results: list[dict]) -> list[dict]:
    charts = []
    for evidence in _structured_evidence(task_results):
        data = evidence["data"]
        rows = data.get("rows") or []
        columns = [str(column) for column in data.get("columns") or []]
        if not (2 <= len(rows) <= 20) or len(columns) < 2:
            continue

        numeric_columns = [
            column
            for column in columns
            if sum(_number(row.get(column)) is not None for row in rows) >= max(2, len(rows) // 2)
        ]
        label_columns = [column for column in columns if column not in numeric_columns]
        if not numeric_columns or not label_columns:
            continue

        label_column = label_columns[0]
        value_column = numeric_columns[0]
        points = []
        for row in rows:
            value = _number(row.get(value_column))
            label = row.get(label_column)
            if value is None or label in (None, ""):
                continue
            points.append({"label": str(label), "value": value})
        if len(points) < 2:
            continue

        label_lower = label_column.lower()
        if any(token in label_lower for token in ("date", "time", "month", "year")):
            chart_type = "line"
        elif len(points) <= 8 and all(point["value"] >= 0 for point in points):
            chart_type = "pie"
        else:
            chart_type = "bar"

        charts.append({
            "type": chart_type,
            "title": f"Observed {value_column} by {label_column}",
            "labels": [point["label"] for point in points],
            "values": [point["value"] for point in points],
            "observed": True,
            "source": f"Analyst SQL evidence, task {evidence['task_id']}",
        })
    return charts[:8]


def _observed_values(task_results: list[dict]) -> list[dict]:
    values = []
    for evidence in _structured_evidence(task_results):
        data = evidence["data"]
        for row in data.get("rows") or []:
            for key, value in row.items():
                numeric = _number(value)
                if numeric is not None and _field_hint(str(key)):
                    values.append({
                        "metric": str(key),
                        "value": numeric,
                        "task_id": evidence["task_id"],
                        "observed": True,
                    })
    return values


def simulate_scenario(*, scenario: str, task_results: list[dict]) -> dict[str, Any]:
    match = re.search(
        r"(?:from|starting at)\s*(\d+(?:\.\d+)?)\s*%?\s*(?:to|up to)\s*(\d+(?:\.\d+)?)\s*%?",
        scenario or "",
        flags=re.IGNORECASE,
    )
    if not match or "discount" not in (scenario or "").lower():
        return {
            "status": "unsupported",
            "scenario": scenario,
            "message": "Only percentage discount scenarios with observed numeric evidence are supported.",
            "observed": [],
            "simulated": [],
        }

    old_rate = float(match.group(1))
    new_rate = float(match.group(2))
    observed = _observed_values(task_results)
    candidates = [
        item for item in observed
        if any(token in item["metric"].lower() for token in ("revenue", "sales", "amount", "value", "price"))
    ]
    if not candidates:
        return {
            "status": "unsupported",
            "scenario": scenario,
            "message": "No suitable observed revenue, sales, amount, value, or price evidence was available.",
            "observed": observed,
            "simulated": [],
        }

    factor = 1 - ((new_rate - old_rate) / 100)
    simulated = [
        {
            "metric": item["metric"],
            "value": round(item["value"] * factor, 6),
            "observed": False,
            "simulated": True,
            "source_task_id": item["task_id"],
        }
        for item in candidates
    ]
    return {
        "status": "simulated",
        "scenario": scenario,
        "assumption": (
            "Simulated values apply the discount-rate change proportionally "
            "to the observed metric; this is not historical fact or a forecast."
        ),
        "observed": [{**item, "simulated": False} for item in candidates],
        "simulated": simulated,
        "change": {"from_percent": old_rate, "to_percent": new_rate},
    }


def build_intelligence(*, result: dict[str, Any], workspace_id: str) -> dict[str, Any]:
    task_results = result.get("task_results", [])
    citations = []
    try:
        citations = retrieve_passages(
            workspace_id=workspace_id,
            query=result.get("goal") or "business policy recommendation approval requirements",
            top_k=8,
        )
    except Exception:
        citations = []
    return {
        "charts": build_charts(task_results),
        "observed_evidence": _observed_values(task_results),
        "policy_citations": citations,
    }


def _pdf_chart(story: list, chart: dict) -> None:
    labels = chart["labels"]
    values = chart["values"]
    drawing = Drawing(500, 230)
    drawing.add(String(0, 215, chart["title"], fontSize=12, fillColor=colors.HexColor("#18314f")))
    palette = [colors.HexColor(value) for value in ("#67e8f9", "#818cf8", "#fbbf24", "#fb7185", "#4ade80")]

    if chart["type"] == "pie":
        total = sum(values) or 1
        start = 0
        for index, value in enumerate(values):
            extent = (value / total) * 360
            drawing.add(Wedge(145, 112, 78, start, extent, fillColor=palette[index % len(palette)], strokeColor=colors.white))
            start += extent
        for index, (label, value) in enumerate(zip(labels, values)):
            drawing.add(Rect(270, 185 - index * 22, 8, 8, fillColor=palette[index % len(palette)], strokeColor=None))
            drawing.add(String(284, 184 - index * 22, f"{str(label)[:22]}: {value:,.2f}", fontSize=8, fillColor=colors.HexColor("#334155")))
    else:
        max_value = max(values) or 1
        left = 38
        baseline = 35
        plot_height = 145
        plot_width = 430
        drawing.add(Line(left, baseline, left + plot_width, baseline, strokeColor=colors.HexColor("#94a3b8")))
        points = []
        for index, (label, value) in enumerate(zip(labels, values)):
            x = left + (index * plot_width / max(len(values) - 1, 1))
            y = baseline + (value / max_value) * plot_height
            points.append((x, y))
            if chart["type"] == "bar":
                drawing.add(Rect(x - 10, baseline, 20, max(y - baseline, 1), fillColor=palette[index % len(palette)], strokeColor=None))
            else:
                drawing.add(Circle(x, y, 3, fillColor=palette[0], strokeColor=None))
            drawing.add(String(x - 18, 18, str(label)[:10], fontSize=7, fillColor=colors.HexColor("#475569")))
        if chart["type"] == "line" and len(points) > 1:
            drawing.add(PolyLine([coordinate for point in points for coordinate in point], strokeColor=palette[0], strokeWidth=2))
    story.append(drawing)
    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph(f"Observed evidence source: {escape(chart['source'])}", getSampleStyleSheet()["BodyText"]))
    story.append(Spacer(1, 0.18 * inch))


def build_report_pdf(*, result: dict[str, Any], workspace: dict[str, Any], intelligence: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#18314f")))
    story = [
        Paragraph("SynapseOps Decision Intelligence Report", styles["ReportTitle"]),
        Spacer(1, 0.18 * inch),
        Paragraph(f"Workspace: {escape(str(workspace.get('company_name') or workspace.get('workspace_id') or 'Unknown'))}", styles["BodyText"]),
        Paragraph(f"Goal: {escape(str(result.get('goal') or 'Not available'))}", styles["BodyText"]),
        Spacer(1, 0.18 * inch),
    ]

    plan = result.get("plan") or {}
    story.append(Paragraph("Execution Plan", styles["Heading2"]))
    for task in plan.get("tasks", []):
        story.append(Paragraph(f"Task {task.get('id')}: {escape(str(task.get('description', '')))}", styles["BodyText"]))
    story.append(Spacer(1, 0.12 * inch))

    story.append(Paragraph("Observed Charts", styles["Heading2"]))
    charts = intelligence.get("charts") or []
    if charts:
        for chart in charts:
            _pdf_chart(story, chart)
    else:
        story.append(Paragraph("No suitable structured Analyst evidence was available for charting.", styles["BodyText"]))

    story.append(Paragraph("Key Evidence", styles["Heading2"]))
    for item in result.get("task_results", []):
        story.append(Paragraph(f"Task {item.get('task_id')} ({item.get('agent')}):", styles["Heading3"]))
        story.append(Paragraph(escape(str(item.get("output") or item.get("error") or "No output.")), styles["BodyText"]))

    synthesis = result.get("synthesis_result") or {}
    story.append(Paragraph("Synthesis", styles["Heading2"]))
    story.append(Paragraph(escape(str(synthesis.get("content") or "No synthesis available.")), styles["BodyText"]))

    critic = result.get("critic_report") or {}
    story.append(Paragraph("Critic and Policy Review", styles["Heading2"]))
    story.append(Paragraph(f"Verdict: {escape(str(critic.get('verdict', 'unknown')))} | Evidence quality: {escape(str(critic.get('evidence_quality', 'unknown')))}", styles["BodyText"]))
    story.append(Paragraph(escape(str(critic.get("summary") or "No critic summary available.")), styles["BodyText"]))
    for finding in critic.get("findings", []) or []:
        story.append(Paragraph(f"{escape(str(finding.get('severity')))}: {escape(str(finding.get('message')))}", styles["BodyText"]))

    story.append(Paragraph("Policy / RAG Citations", styles["Heading2"]))
    citations = intelligence.get("policy_citations") or []
    if citations:
        for citation in citations:
            story.append(Paragraph(f"{escape(str(citation.get('citation')))}: {escape(str(citation.get('text')))}", styles["BodyText"]))
    else:
        story.append(Paragraph("No workspace policy passages were retrieved.", styles["BodyText"]))

    story.append(Paragraph("Recommendation and Action Status", styles["Heading2"]))
    story.append(Paragraph(escape(str(result.get("final_output") or "No final recommendation available.")), styles["BodyText"]))
    story.append(Paragraph(f"Action status: {escape(str(result.get('action_status') or 'none'))}", styles["BodyText"]))

    budget = result.get("runtime_budget") or {}
    story.append(Paragraph("Runtime Safety", styles["Heading2"]))
    story.append(Paragraph(escape(str(budget)), styles["BodyText"]))

    SimpleDocTemplate(buffer, pagesize=letter, rightMargin=0.6 * inch, leftMargin=0.6 * inch, topMargin=0.6 * inch, bottomMargin=0.6 * inch).build(story)
    return buffer.getvalue()
