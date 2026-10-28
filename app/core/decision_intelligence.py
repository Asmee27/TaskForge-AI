from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from html import escape
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
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
            "source": "Observed business data",
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


from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#0F172A"))

        # Header (on pages > 1)
        if self._pageNumber > 1:
            self.drawString(43.2, 755, "SYNAPSEOPS AI  |  DECISION INTELLIGENCE REPORT")
            self.setFont("Helvetica", 8)
            self.setFillColor(colors.HexColor("#64748B"))
            self.drawRightString(568.8, 755, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(43.2, 747, 568.8, 747)

        # Footer (all pages)
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(43.2, 45, 568.8, 45)

        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#334155"))
        self.drawString(43.2, 30, "SYNAPSEOPS AI")
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))
        self.drawString(115, 30, "• Executive Decision Intelligence Report")

        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(568.8, 30, page_text)
        self.restoreState()


def _sanitize_error_text(text: Any) -> str:
    if not text:
        return "No output available."
    text_str = str(text)
    exception_indicators = [
        "NameError:", "TypeError:", "SyntaxError:", "ValueError:",
        "ModuleNotFoundError:", "AttributeError:", "ZeroDivisionError:",
        "KeyError:", "IndexError:", "ImportError:", "RuntimeError:",
        "Traceback (most recent call last):", "is not defined"
    ]
    if any(indicator in text_str for indicator in exception_indicators):
        return "Analysis task failed due to an internal processing error."
    return text_str


def _append_text_paragraphs(story: list, text: Any, style: ParagraphStyle, space_after: float = 3):
    """
    Splits text by lines into standalone Paragraph flowables so ReportLab
    can paginate naturally across page frames without Table LayoutError.
    """
    clean_text = _sanitize_error_text(text)
    lines = [line.strip() for line in clean_text.split("\n") if line.strip()]
    if not lines:
        story.append(Paragraph("No text content provided.", style))
        return

    for line in lines:
        story.append(Paragraph(escape(line), style))
        if space_after > 0:
            story.append(Spacer(1, space_after))


def _business_lines(text: Any) -> list[str]:
    """Keep useful narrative while excluding execution and implementation detail."""
    blocked = (
        "task ", "analyst", "research agent", "completed", "depends_on", "run_sql_query",
        "select ", " from ", " where ", "workflow", "duration_ms", "milliseconds",
        "worker evidence", "safety", "critic review", "action gate", "finding",
    )
    lines = []
    for line in _sanitize_error_text(text).splitlines():
        clean = line.strip()
        lowered = clean.lower()
        if clean and not any(token in lowered for token in blocked) and not set(clean) <= {"=", "-", "_", " "}:
            lines.append(clean)
    return lines


def _inventory_business_evidence(task_results: list[dict]) -> dict[str, Any]:
    """Normalize inventory totals and sampled rows to one deficit definition."""
    aggregate = None
    low_stock_count = None
    detail_rows = []
    aggregate_names = (
        "shortage", "deficit", "reorder_quantity", "reorder_qty", "required_units",
        "shortage_units", "deficit_units", "low_stock_products", "low_stock_count",
        "products_below_reorder",
    )
    for evidence in _structured_evidence(task_results):
        data = evidence["data"]
        for row in data.get("rows") or []:
            normalized = {str(key).lower(): value for key, value in row.items()}
            stock = next((
                _number(normalized[key]) for key in normalized
                if key in {"stock_quantity", "stock", "inventory", "on_hand", "onhand"}
            ), None)
            reorder = next((
                _number(normalized[key]) for key in normalized
                if key in {"reorder_level", "reorder_point", "minimum_stock", "min_stock"}
            ), None)
            if stock is not None and reorder is not None:
                detail_rows.append({
                    "product": str(row.get("product_id") or row.get("sku") or "Observed product"),
                    "stock": stock,
                    "reorder": reorder,
                    "deficit": max(reorder - stock, 0),
                })
                continue
            for key, value in normalized.items():
                numeric = _number(value)
                if numeric is None or not any(name in key for name in aggregate_names):
                    continue
                if any(name in key for name in ("low_stock_products", "low_stock_count", "products_below_reorder")):
                    low_stock_count = numeric
                elif any(name in key for name in ("shortage", "deficit", "required_units", "reorder_quantity", "reorder_qty")):
                    aggregate = numeric
    if low_stock_count is None and detail_rows:
        low_stock_count = sum(row["deficit"] > 0 for row in detail_rows)
    return {"aggregate": aggregate, "low_stock_count": low_stock_count, "details": detail_rows}


def _business_recommendation(*, result: dict[str, Any], inventory: dict[str, Any]) -> str:
    """Create a short recommendation from observed evidence and decision fields."""
    if inventory["aggregate"] is not None:
        quantity = f"{inventory['aggregate']:,.0f} units"
        if inventory["low_stock_count"] is not None:
            evidence = f"{quantity} across {inventory['low_stock_count']:,.0f} low-stock products"
        else:
            evidence = quantity
        recommendation = f"Review replenishment for the observed inventory requirement of {evidence}."
    elif inventory["details"]:
        sampled_total = sum(row["deficit"] for row in inventory["details"])
        recommendation = f"Review replenishment for the observed sample requirement of {sampled_total:,.0f} units."
    else:
        recommendation = "Use the available observed evidence for review before taking action."

    critic = result.get("critic_report") or {}
    if critic.get("requires_human_approval"):
        recommendation += " Obtain authorized human approval before execution."
    action_status = str(result.get("action_status") or "").lower()
    if "blocked" in action_status or str(critic.get("verdict") or "").lower() == "blocked":
        recommendation += " The proposed action remains blocked pending sufficient evidence or approval."
    return recommendation


def _evidence_rows(task_results: list[dict]) -> list[dict[str, Any]]:
    rows = []
    for item in _observed_values(task_results):
        if any(token in item["metric"].lower() for token in ("stock", "reorder", "shortage", "deficit", "quantity")):
            rows.append(item)
    return rows


def _legacy_build_report_pdf(*, result: dict[str, Any], workspace: dict[str, Any], intelligence: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=43.2,
        rightMargin=43.2,
        topMargin=54,
        bottomMargin=54,
    )
    usable_width = 525.6

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.white,
        alignment=TA_LEFT,
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor("#94A3B8"),
        alignment=TA_LEFT,
    )

    h1_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=14,
        spaceAfter=6,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=13.5,
        textColor=colors.HexColor("#334155"),
        spaceAfter=3,
    )

    bold_body_style = ParagraphStyle(
        "BoldReportBody",
        parent=body_style,
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#0F172A"),
    )

    callout_body_style = ParagraphStyle(
        "CalloutBody",
        parent=body_style,
        fontName="Helvetica-Oblique",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#1E293B"),
    )

    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#0F172A"),
    )

    table_cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#334155"),
    )

    code_cell_style = ParagraphStyle(
        "CodeCell",
        parent=styles["Normal"],
        fontName="Courier",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1E293B"),
    )

    story = []

    # 1. Header Banner (Bounded Table for title & metadata)
    header_data = [
        [
            Paragraph("SYNAPSEOPS DECISION INTELLIGENCE REPORT", title_style),
            Paragraph(f"<b>Generated:</b> {datetime.now(timezone.utc).strftime('%b %d, %Y')}<br/><b>Status:</b> {(result.get('status') or 'COMPLETED').upper()}", ParagraphStyle("HMeta", parent=subtitle_style, alignment=TA_RIGHT, textColor=colors.HexColor("#E2E8F0"))),
        ],
        [
            Paragraph("Automated Multi-Agent Business Intelligence & Governance Review", subtitle_style),
            Paragraph(f"<b>Workspace:</b> {escape(str(workspace.get('company_name') or workspace.get('workspace_id') or 'Demo'))}", ParagraphStyle("HMeta2", parent=subtitle_style, alignment=TA_RIGHT, textColor=colors.HexColor("#CBD5E1"))),
        ],
    ]
    header_table = Table(header_data, colWidths=[330, usable_width - 330])
    header_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0F172A")),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
    )
    story.append(header_table)
    story.append(Spacer(1, 10))

    # 2. Workspace + Business Goal Section
    story.append(Paragraph("<b>BUSINESS GOAL</b>", ParagraphStyle("GoalLabel", parent=bold_body_style, textColor=colors.HexColor("#1E40AF"))))
    goal_text = str(result.get("goal") or "No business goal provided.")
    _append_text_paragraphs(story, f'"{goal_text}"', callout_body_style, space_after=3)

    currency_info = workspace.get("currency") or "Monetary units (unspecified)"
    workspace_meta = f"<b>Company:</b> {escape(str(workspace.get('company_name') or 'N/A'))} &nbsp;|&nbsp; <b>Type:</b> {escape(str(workspace.get('business_type') or 'N/A'))} &nbsp;|&nbsp; <b>Currency:</b> {escape(str(currency_info))}"
    story.append(Paragraph(workspace_meta, ParagraphStyle("WMeta", parent=body_style, fontSize=8.5, textColor=colors.HexColor("#64748B"))))
    story.append(Spacer(1, 12))

    # 3. Executive Summary Section
    story.append(Paragraph("1. Executive Summary", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
    
    critic = result.get("critic_report") or {}
    verdict = str(critic.get("verdict", "review_required")).lower()
    verdict_color = "#166534" if verdict == "approved" else "#854D0E" if verdict == "review_required" else "#991B1B"
    verdict_bg = "#DCFCE7" if verdict == "approved" else "#FEF9C3" if verdict == "review_required" else "#FEE2E2"

    # Status summary row (bounded Table for compact status metrics)
    status_row = Table(
        [
            [
                Paragraph(f"<b>Overall Status:</b> {(result.get('status') or 'completed').upper()}", table_cell_style),
                Paragraph(f"<b>Safety Verdict:</b> <font color='{verdict_color}'><b>{verdict.upper()}</b></font>", table_cell_style),
                Paragraph(f"<b>Confidence:</b> {int((critic.get('overall_confidence') or 0)*100)}%", table_cell_style),
            ]
        ],
        colWidths=[175, 175, usable_width - 350],
    )
    status_row.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("PADDING", (0, 0), (-1, -1), 6),
            ("BACKGROUND", (1, 0), (1, 0), colors.HexColor(verdict_bg)),
        ])
    )
    story.append(status_row)
    story.append(Spacer(1, 6))

    # Render summary narrative as standalone Paragraph flowables (NO monolithic Table cell!)
    summary_text = str(result.get("final_output") or critic.get("summary") or "Workflow executed successfully.")
    _append_text_paragraphs(story, summary_text, body_style, space_after=3)
    story.append(Spacer(1, 12))

    # 4. Key Metrics & Observed Evidence Section
    story.append(Paragraph("2. Key Metrics & Observed Evidence", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
    
    observed_metrics = _observed_values(result.get("task_results", []))
    if observed_metrics:
        metrics_data = [[
            Paragraph("Metric Name", table_header_style),
            Paragraph("Observed Value", table_header_style),
            Paragraph("Source Task", table_header_style),
        ]]
        for item in observed_metrics[:10]:
            metrics_data.append([
                Paragraph(escape(item["metric"]), table_cell_style),
                Paragraph(f"{item['value']:,.2f}", code_cell_style),
                Paragraph(f"Task {item['task_id']}", table_cell_style),
            ])
        metrics_table = Table(metrics_data, colWidths=[200, 160, usable_width - 360])
        metrics_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("PADDING", (0, 0), (-1, -1), 5),
            ])
        )
        story.append(metrics_table)
    else:
        story.append(Paragraph("No structured numeric metrics were observed from database queries.", body_style))
    story.append(Spacer(1, 12))

    # 5. Execution Plan Section
    story.append(Paragraph("3. Execution Plan", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
    
    plan = result.get("plan") or {}
    tasks = plan.get("tasks") or []
    if tasks:
        plan_data = [[
            Paragraph("ID", table_header_style),
            Paragraph("Agent", table_header_style),
            Paragraph("Task Description", table_header_style),
            Paragraph("Dependencies", table_header_style),
        ]]
        for t in tasks:
            deps = ", ".join(str(d) for d in (t.get("depends_on") or [])) or "None"
            plan_data.append([
                Paragraph(str(t.get("id")), code_cell_style),
                Paragraph(str(t.get("agent", "")).upper(), table_header_style),
                Paragraph(escape(str(t.get("description", ""))), table_cell_style),
                Paragraph(deps, table_cell_style),
            ])
        plan_table = Table(plan_data, colWidths=[35, 85, 305, usable_width - 425])
        plan_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("PADDING", (0, 0), (-1, -1), 5),
            ])
        )
        story.append(plan_table)
    else:
        story.append(Paragraph("No execution plan details available.", body_style))
    story.append(Spacer(1, 12))

    # 6. Detailed Task Analysis Section (Rendered natively as Paragraph flowables, NOT wrapped in giant Tables)
    story.append(Paragraph("4. Detailed Task Analysis", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
    
    task_results = result.get("task_results") or []
    for item in task_results:
        t_status = str(item.get("status", "failed")).lower()
        t_color = "#166534" if t_status == "completed" else "#854D0E" if t_status == "degraded" else "#991B1B"
        
        raw_output = item.get("output") or item.get("error") or "No output produced."
        
        t_header = f"Task {item.get('task_id')} — Agent: {str(item.get('agent')).upper()} &nbsp;|&nbsp; Status: <font color='{t_color}'><b>{t_status.upper()}</b></font> ({item.get('duration_ms', 0)} ms)"
        story.append(Paragraph(t_header, bold_body_style))
        story.append(Paragraph(f"<b>Description:</b> {escape(str(item.get('description', '')))}", body_style))
        
        # Output lines rendered as individual Paragraph flowables allowing clean pagination
        _append_text_paragraphs(story, raw_output, body_style, space_after=2)
        story.append(Spacer(1, 8))

    story.append(Spacer(1, 6))

    # 7. Observed Evidence Charts Section
    story.append(Paragraph("5. Observed Evidence Charts", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
    charts = intelligence.get("charts") or []
    if charts:
        for chart in charts:
            _pdf_chart(story, chart)
    else:
        story.append(Paragraph("No suitable structured Analyst evidence was available for charting.", body_style))
    story.append(Spacer(1, 12))

    # 8. Policy / RAG Evidence Section (Rendered as Paragraph flowables for natural page splitting)
    story.append(Paragraph("6. Relevant Policy & RAG Evidence", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
    citations = intelligence.get("policy_citations") or []
    if citations:
        for c in citations:
            cit_header = f"<b>Citation:</b> {escape(str(c.get('citation')))}"
            story.append(Paragraph(cit_header, bold_body_style))
            _append_text_paragraphs(story, str(c.get("text", "")), body_style, space_after=2)
            story.append(Spacer(1, 4))
    else:
        story.append(Paragraph("No relevant workspace policy evidence found.", body_style))
    story.append(Spacer(1, 12))

    # 9. Safety & Critic Review Section
    story.append(Paragraph("7. Safety & Critic Review", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
    
    story.append(Paragraph(f"<b>Verdict:</b> <font color='{verdict_color}'><b>{verdict.upper()}</b></font> &nbsp;|&nbsp; <b>Evidence Quality:</b> {str(critic.get('evidence_quality', 'unknown')).upper()} &nbsp;|&nbsp; <b>Human Approval Required:</b> {'YES' if critic.get('requires_human_approval') else 'NO'}", bold_body_style))
    
    critic_summary = str(critic.get("summary") or "")
    if critic_summary:
        _append_text_paragraphs(story, critic_summary, body_style, space_after=3)
    
    findings = critic.get("findings") or []
    if findings:
        story.append(Spacer(1, 4))
        story.append(Paragraph("<b>Safety Findings & Warnings:</b>", bold_body_style))
        for f in findings:
            sev = str(f.get("severity", "info")).lower()
            sev_color = "#991B1B" if sev == "critical" else "#854D0E" if sev == "warning" else "#1E40AF"
            finding_text = f"<font color='{sev_color}'><b>[{sev.upper()} - {escape(str(f.get('category', '')))}]</b></font> {escape(str(f.get('message', '')))}"
            story.append(Paragraph(finding_text, body_style))
    else:
        story.append(Paragraph("No safety or policy findings were flagged by the Critic Agent.", body_style))
    story.append(Spacer(1, 12))

    # 10. Recommendation & Action Gate Section
    story.append(Paragraph("8. Recommendation & Action Gate", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
    
    rec_text = str(result.get("final_output") or "No recommendation generated.")
    story.append(Paragraph("<b>FINAL RECOMMENDATION</b>", bold_body_style))
    _append_text_paragraphs(story, rec_text, body_style, space_after=3)
    
    action_status = str(result.get("action_status") or "no_action_proposed")
    story.append(Paragraph(f"<b>Proposed Action Status:</b> {escape(action_status.upper())}", bold_body_style))
    story.append(Spacer(1, 12))

    # 11. Runtime Safety & Resource Budget Section (Bounded Table for runtime metrics)
    story.append(Paragraph("9. Runtime Safety & Resource Budget", h1_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))
    
    budget = result.get("runtime_budget") or {}
    limits = budget.get("limits") or {}
    circuit_data = result.get("runtime_circuit") or {}
    circuits = circuit_data.get("circuits") or {}

    b_steps = f"{budget.get('workflow_steps', result.get('workflow_steps', 0))} / {limits.get('max_workflow_steps', 12)}"
    b_tools = f"{budget.get('tool_calls', 0)} / {limits.get('max_tool_calls', 20)}"
    b_llm = f"{budget.get('llm_calls', 0)} / {limits.get('max_llm_calls', 30)}"
    b_tokens = f"{budget.get('total_tokens', 0):,} / {limits.get('max_total_tokens', 100000):,}"
    b_cost = f"${budget.get('estimated_cost_usd', 0.0):.4f} / ${limits.get('max_cost_usd', 1.0):.2f}"

    circuit_summary = ", ".join(f"{k.upper()}: {v.get('state', 'closed').upper()}" for k, v in circuits.items()) or "LLM: CLOSED, SQL: CLOSED, RESEARCH: CLOSED"

    budget_table_data = [
        [Paragraph("Safety Metric", table_header_style), Paragraph("Usage / Limit", table_header_style), Paragraph("Metric Status", table_header_style)],
        [Paragraph("Workflow Steps", table_cell_style), Paragraph(b_steps, code_cell_style), Paragraph("Normal", table_cell_style)],
        [Paragraph("Tool Calls", table_cell_style), Paragraph(b_tools, code_cell_style), Paragraph("Normal", table_cell_style)],
        [Paragraph("LLM Invocations", table_cell_style), Paragraph(b_llm, code_cell_style), Paragraph("Normal", table_cell_style)],
        [Paragraph("Total Tokens", table_cell_style), Paragraph(b_tokens, code_cell_style), Paragraph("Normal", table_cell_style)],
        [Paragraph("Estimated Cost", table_cell_style), Paragraph(b_cost, code_cell_style), Paragraph("Normal", table_cell_style)],
        [Paragraph("Circuit Breakers", table_cell_style), Paragraph(circuit_summary, code_cell_style), Paragraph("Active Guard", table_cell_style)],
        [Paragraph("Budget Exceeded", table_cell_style), Paragraph(str(budget.get("exceeded", False)).upper(), code_cell_style), Paragraph("PASSED" if not budget.get("exceeded") else "EXCEEDED", table_header_style)],
    ]
    budget_table = Table(budget_table_data, colWidths=[150, 220, usable_width - 370])
    budget_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            ("PADDING", (0, 0), (-1, -1), 5),
        ])
    )
    story.append(budget_table)

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def build_report_pdf(*, result: dict[str, Any], workspace: dict[str, Any], intelligence: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=43.2,
        rightMargin=43.2,
        topMargin=54,
        bottomMargin=54,
    )
    usable_width = 525.6
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("BusinessTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=colors.white)
    subtitle_style = ParagraphStyle("BusinessSubtitle", parent=styles["Normal"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=colors.HexColor("#CBD5E1"))
    heading_style = ParagraphStyle("BusinessHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, textColor=colors.HexColor("#0F172A"), spaceBefore=14, spaceAfter=6, keepWithNext=True)
    body_style = ParagraphStyle("BusinessBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=13.5, textColor=colors.HexColor("#334155"), spaceAfter=3)
    bold_style = ParagraphStyle("BusinessBold", parent=body_style, fontName="Helvetica-Bold", textColor=colors.HexColor("#0F172A"))
    callout_style = ParagraphStyle("BusinessCallout", parent=body_style, fontName="Helvetica-Oblique", fontSize=9.5, leading=14, textColor=colors.HexColor("#1E293B"))
    header_style = ParagraphStyle("BusinessTableHeader", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=colors.HexColor("#0F172A"))
    cell_style = ParagraphStyle("BusinessTableCell", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#334155"))

    story = []
    company = str(workspace.get("company_name") or workspace.get("workspace_id") or "Business workspace")
    critic = result.get("critic_report") or {}
    verdict = str(critic.get("verdict") or "review_required").lower()
    verdict_color = "#166534" if verdict == "approved" else "#991B1B" if verdict == "blocked" else "#854D0E"
    goal = str(result.get("goal") or "No business goal provided.")
    header = Table([
        [Paragraph("SYNAPSEOPS DECISION INTELLIGENCE REPORT", title_style), Paragraph(f"<b>Prepared:</b> {datetime.now(timezone.utc).strftime('%b %d, %Y')}", subtitle_style)],
        [Paragraph("Business decision brief", subtitle_style), Paragraph(f"<b>Organization:</b> {escape(company)}", subtitle_style)],
    ], colWidths=[330, usable_width - 330])
    header.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#0F172A")), ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10), ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12)]))
    story.extend([header, Spacer(1, 10)])

    def section(title: str) -> None:
        story.append(Paragraph(title, heading_style))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=6))

    section("Executive Summary")
    status = "Approved" if verdict == "approved" else "Review required" if verdict == "review_required" else "Blocked"
    status_table = Table([[Paragraph("Decision status", header_style), Paragraph("Safety verdict", header_style), Paragraph("Evidence quality", header_style)], [Paragraph(status, cell_style), Paragraph(f"<font color='{verdict_color}'><b>{escape(verdict.upper())}</b></font>", cell_style), Paragraph(str(critic.get("evidence_quality") or "Not assessed").title(), cell_style)]], colWidths=[175, 175, usable_width - 350])
    status_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")), ("PADDING", (0, 0), (-1, -1), 6)]))
    story.extend([status_table, Spacer(1, 6)])
    summary_lines = _business_lines(critic.get("summary") or result.get("final_output") or "No decision summary was generated.")
    for line in summary_lines:
        story.append(Paragraph(escape(line), body_style))

    section("Business Goal")
    story.append(Paragraph(escape(goal), callout_style))
    currency = workspace.get("currency") or "Monetary units (unspecified)"
    story.append(Paragraph(f"<b>Organization:</b> {escape(company)} &nbsp;|&nbsp; <b>Business type:</b> {escape(str(workspace.get('business_type') or 'Not specified'))} &nbsp;|&nbsp; <b>Currency:</b> {escape(str(currency))}", body_style))

    inventory = _inventory_business_evidence(result.get("task_results") or [])
    section("Key Findings")
    if inventory["aggregate"] is not None:
        scope = " across the full inventory scope"
        if inventory["low_stock_count"] is not None:
            scope = f" across <b>{inventory['low_stock_count']:,.0f} low-stock products</b>"
        story.append(Paragraph(f"Observed inventory requirement: <b>{inventory['aggregate']:,.0f} units</b>{scope}.", body_style))
    if inventory["details"]:
        sampled_total = sum(row["deficit"] for row in inventory["details"])
        product_label = "observed product" if len(inventory["details"]) == 1 else "observed products"
        story.append(Paragraph(f"The report sample contains {len(inventory['details']):,} {product_label} with a combined calculated deficit of <b>{sampled_total:,.0f} units</b>. This sample is a subset and is not added to the full-inventory aggregate.", body_style))
    if inventory["aggregate"] is None and not inventory["details"]:
        evidence_rows = _evidence_rows(result.get("task_results") or [])[:3]
        if evidence_rows:
            for item in evidence_rows:
                story.append(Paragraph(f"Observed {escape(item['metric'])}: <b>{item['value']:,.2f}</b>.", body_style))
        else:
            story.append(Paragraph("No structured finding was available for this decision.", body_style))

    section("Evidence & Insights")
    evidence_rows = _evidence_rows(result.get("task_results") or [])[:12]
    if evidence_rows:
        data = [[Paragraph("Observed measure", header_style), Paragraph("Value", header_style), Paragraph("Evidence scope", header_style)]]
        for item in evidence_rows:
            data.append([Paragraph(escape(item["metric"]), cell_style), Paragraph(f"{item['value']:,.2f}", cell_style), Paragraph("Observed data", cell_style)])
        table = Table(data, colWidths=[200, 150, usable_width - 350])
        table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")), ("PADDING", (0, 0), (-1, -1), 5)]))
        story.append(table)
    else:
        story.append(Paragraph("Structured observed measures were not available for tabulation.", body_style))
    if inventory["details"]:
        detail_data = [[Paragraph("Product", header_style), Paragraph("Stock", header_style), Paragraph("Reorder level", header_style), Paragraph("Calculated deficit", header_style)]]
        for row in inventory["details"][:10]:
            detail_data.append([Paragraph(escape(row["product"][:24]), cell_style), Paragraph(f"{row['stock']:,.0f}", cell_style), Paragraph(f"{row['reorder']:,.0f}", cell_style), Paragraph(f"{row['deficit']:,.0f}", cell_style)])
        detail_table = Table(detail_data, colWidths=[190, 90, 110, usable_width - 390])
        detail_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")), ("PADDING", (0, 0), (-1, -1), 5)]))
        story.append(Spacer(1, 8))
        story.append(detail_table)
    for chart in intelligence.get("charts") or []:
        _pdf_chart(story, chart)

    section("Recommendation")
    story.append(Paragraph(escape(_business_recommendation(result=result, inventory=inventory)), body_style))

    section("Risks & Limitations")
    findings = critic.get("findings") or []
    if findings:
        for finding in findings:
            message = str(finding.get("message") or "").strip()
            if message:
                story.append(Paragraph(escape(message), body_style))
    else:
        story.append(Paragraph("No material risks or limitations were flagged by the evidence review.", body_style))

    section("Policy & Safety")
    story.append(Paragraph(f"<b>Safety verdict:</b> <font color='{verdict_color}'><b>{escape(verdict.upper())}</b></font> &nbsp;|&nbsp; <b>Human approval required:</b> {'Yes' if critic.get('requires_human_approval') else 'No'}", bold_style))
    for citation in intelligence.get("policy_citations") or []:
        citation_text = str(citation.get("text") or "").strip()
        if citation_text:
            story.append(Paragraph(f"<b>Policy evidence:</b> {escape(citation_text)}", body_style))

    section("Decision & Approval")
    action_status = str(result.get("action_status") or "No action proposed").replace("_", " ").title()
    story.append(Paragraph(f"<b>Recommended disposition:</b> {escape(action_status)}", body_style))
    story.append(Paragraph("Actions requiring human approval remain gated until an authorized decision-maker confirms them.", body_style))

    section("System Assurance")
    budget = result.get("runtime_budget") or {}
    limits = budget.get("limits") or {}
    story.append(Paragraph("The analysis ran within configured safeguards and retained an auditable evidence trail.", body_style))
    story.append(Paragraph(f"Safeguard status: <b>{'Within limits' if not budget.get('exceeded') else 'Limit reached'}</b>. Detailed technical trace and administrative metrics remain available in Live Trace/Admin.", body_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()
