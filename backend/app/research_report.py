"""On-demand, traceable PDF reports for the agricultural research assistant.

The report renderer deliberately receives only persisted assistant output,
evidence cards and (when present) a reviewed local statistical result. It does
not call an LLM and does not invent a chart from prose. The caller may stream
the resulting bytes to the researcher and archive that exact artifact in the
researcher's private result library.
"""

from __future__ import annotations

import html
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


PDF_FONT = "STSong-Light"
REPORT_REQUEST_PATTERN = re.compile(
    r"(?:生成|创建|导出|输出|制作|编制|撰写|出具|形成|整理|给我(?:一份|个)?)"
    r"[^。！？!?]{0,24}(?:报告|pdf|word|docx)"
    r"|(?:报告|pdf|word|docx)[^。！？!?]{0,16}(?:生成|创建|导出|输出|下载|制作|编制|撰写|出具)",
    re.IGNORECASE,
)


def is_report_request(question: str) -> bool:
    """Only create a report for an explicit user request, never by default."""
    return bool(REPORT_REQUEST_PATTERN.search(str(question or "")))


def _ensure_font() -> None:
    if PDF_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(PDF_FONT))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ResearchReportTitle", parent=base["Title"], fontName=PDF_FONT,
            fontSize=18, leading=24, textColor=colors.HexColor("#103f35"), spaceAfter=7,
        ),
        "subtitle": ParagraphStyle(
            "ResearchReportSubtitle", parent=base["BodyText"], fontName=PDF_FONT,
            fontSize=9, leading=14, textColor=colors.HexColor("#5b756c"), spaceAfter=11,
        ),
        "heading": ParagraphStyle(
            "ResearchReportHeading", parent=base["Heading2"], fontName=PDF_FONT,
            fontSize=13, leading=18, textColor=colors.HexColor("#103f35"), spaceBefore=12, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "ResearchReportBody", parent=base["BodyText"], fontName=PDF_FONT,
            fontSize=9.2, leading=15, textColor=colors.HexColor("#17221f"), spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "ResearchReportSmall", parent=base["BodyText"], fontName=PDF_FONT,
            fontSize=7.7, leading=11, textColor=colors.HexColor("#486158"), spaceAfter=2,
        ),
        "table": ParagraphStyle(
            "ResearchReportTable", parent=base["BodyText"], fontName=PDF_FONT,
            fontSize=7.6, leading=10, textColor=colors.HexColor("#17221f"),
        ),
    }


def _text(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _paragraph(text: Any, style: ParagraphStyle) -> Paragraph:
    cleaned = html.escape(_text(text, ""))
    cleaned = cleaned.replace("\n", "<br/>")
    return Paragraph(cleaned or "-", style)


def _clean_markdown_line(line: str) -> str:
    line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
    line = re.sub(r"^\s*[-*+]\s+", "• ", line)
    line = re.sub(r"^\s*\d+[.)]\s+", "", line)
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）", line)
    return line.strip()


def _strip_inline_markdown(value: str) -> str:
    """Keep table cells readable without interpreting model-supplied markup."""
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）", value).strip()


def _pipe_table_row(raw_line: str) -> list[str] | None:
    line = raw_line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        return None
    cells = [_strip_inline_markdown(cell) for cell in line.strip("|").split("|")]
    return cells if len(cells) >= 2 else None


def _is_pipe_table_separator(raw_line: str) -> bool:
    cells = _pipe_table_row(raw_line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _dot_table_row(raw_line: str) -> list[str] | None:
    """Recognize model output such as `指标 · 候选A · 对照 · 差异`.

    The model sometimes converts a table to a sequence of dot-separated lines.
    Treat it as a table only when each line has at least three cells; ordinary
    bullet lists remain paragraphs.
    """
    line = _strip_inline_markdown(raw_line.strip())
    line = re.sub(r"^\s*[-*+]\s+", "", line)
    delimiter = "•" if "•" in line else "·" if "·" in line else None
    if not delimiter:
        return None
    cells = [cell.strip() for cell in line.split(delimiter)]
    return cells if len(cells) >= 3 and all(cells) else None


def _table_widths(rows: list[list[str]], total_width: float = 490) -> list[float]:
    """Allocate page width by visible cell content, with safe minimum widths."""
    column_count = max(len(row) for row in rows)
    weights: list[float] = []
    for column in range(column_count):
        longest = max((len(str(row[column])) if column < len(row) else 0 for row in rows), default=1)
        weights.append(max(5.0, min(float(longest), 18.0)))
    minimum = 44.0 if column_count >= 7 else 52.0 if column_count >= 5 else 68.0
    minimum_total = minimum * column_count
    if minimum_total >= total_width:
        return [total_width / column_count] * column_count
    remainder = total_width - minimum_total
    weight_total = sum(weights) or 1.0
    return [minimum + remainder * (weight / weight_total) for weight in weights]


def _answer_flowables(answer: str, styles: dict[str, ParagraphStyle]) -> list[Any]:
    """Render prose, Markdown tables and dot-separated data grids faithfully."""
    flowables: list[Any] = []
    raw_lines = str(answer or "").splitlines()
    index = 0
    previous_blank = True
    while index < len(raw_lines):
        raw_line = raw_lines[index]

        # Standard Markdown table: header, separator, then one or more rows.
        header = _pipe_table_row(raw_line)
        if header and index + 2 < len(raw_lines) and _is_pipe_table_separator(raw_lines[index + 1]):
            rows = [header]
            index += 2
            while index < len(raw_lines):
                row = _pipe_table_row(raw_lines[index])
                if not row or len(row) != len(header):
                    break
                rows.append(row)
                index += 1
            if len(rows) >= 2:
                flowables.extend([_table(rows, _table_widths(rows), styles), Spacer(1, 6)])
                previous_blank = False
                continue

        # A model may flatten a table to lines separated by `·` or `•`.
        dot_row = _dot_table_row(raw_line)
        if dot_row and index + 1 < len(raw_lines):
            next_dot_row = _dot_table_row(raw_lines[index + 1])
            if next_dot_row and len(next_dot_row) == len(dot_row):
                rows = [dot_row]
                index += 1
                while index < len(raw_lines):
                    row = _dot_table_row(raw_lines[index])
                    if not row or len(row) != len(dot_row):
                        break
                    rows.append(row)
                    index += 1
                flowables.extend([_table(rows, _table_widths(rows), styles), Spacer(1, 6)])
                previous_blank = False
                continue

        line = _clean_markdown_line(raw_line)
        index += 1
        if not line:
            if not previous_blank:
                flowables.append(Spacer(1, 3))
            previous_blank = True
            continue
        previous_blank = False
        is_heading = bool(re.match(r"^(?:一|二|三|四|五|六|七|八|九|十|\d+)[、.．]", line)) or raw_line.lstrip().startswith("#")
        flowables.append(_paragraph(line, styles["heading"] if is_heading else styles["body"]))
    return flowables or [_paragraph("本轮未获得可写入报告的文字分析。", styles["body"])]


def _table(rows: list[list[Any]], widths: list[float], styles: dict[str, ParagraphStyle], repeat_rows: int = 1) -> Table:
    rendered = [[_paragraph(value, styles["table"]) for value in row] for row in rows]
    table = Table(rendered, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f3ef")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#103f35")),
        ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbded6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fbfa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _bar_chart(title: str, values: list[tuple[str, float]], unit: str) -> Drawing | None:
    usable = [(str(label), float(value)) for label, value in values if value is not None]
    if not usable:
        return None
    usable = usable[:8]
    width, height = 490, 210
    drawing = Drawing(width, height)
    drawing.add(String(0, height - 14, title, fontName=PDF_FONT, fontSize=10, fillColor=colors.HexColor("#103f35")))
    chart_left, chart_bottom, chart_width, chart_height = 40, 37, 430, 135
    max_value = max(value for _, value in usable)
    min_value = min(0.0, min(value for _, value in usable))
    spread = max(max_value - min_value, 1.0)
    chart_max = max_value + spread * 0.14
    chart_min = min_value - spread * 0.08 if min_value < 0 else 0.0
    scale = chart_height / max(chart_max - chart_min, 1.0)
    zero_y = chart_bottom + (0 - chart_min) * scale
    drawing.add(Line(chart_left, chart_bottom, chart_left, chart_bottom + chart_height, strokeColor=colors.HexColor("#7f9d91"), strokeWidth=0.7))
    drawing.add(Line(chart_left, zero_y, chart_left + chart_width, zero_y, strokeColor=colors.HexColor("#7f9d91"), strokeWidth=0.7))
    for fraction in (0, 0.5, 1):
        tick_value = chart_min + (chart_max - chart_min) * fraction
        tick_y = chart_bottom + chart_height * fraction
        drawing.add(Line(chart_left, tick_y, chart_left + chart_width, tick_y, strokeColor=colors.HexColor("#d9e7e1"), strokeWidth=0.4))
        drawing.add(String(1, tick_y - 3, f"{tick_value:.1f}", fontName=PDF_FONT, fontSize=6.6, fillColor=colors.HexColor("#58736a")))
    slot_width = chart_width / len(usable)
    bar_width = max(16, min(36, slot_width * 0.58))
    for index, (label, value) in enumerate(usable):
        x = chart_left + index * slot_width + (slot_width - bar_width) / 2
        value_y = chart_bottom + (value - chart_min) * scale
        y = min(zero_y, value_y)
        bar_height = abs(value_y - zero_y)
        drawing.add(Rect(x, y, bar_width, max(bar_height, 0.8), fillColor=colors.HexColor("#16806a"), strokeColor=None))
        drawing.add(String(x + bar_width / 2, value_y + (5 if value >= 0 else -10), f"{value:.1f}", textAnchor="middle", fontName=PDF_FONT, fontSize=6.6, fillColor=colors.HexColor("#183e34")))
        display_label = label if len(label) <= 9 else f"{label[:8]}…"
        drawing.add(String(x + bar_width / 2, 20, display_label, textAnchor="middle", fontName=PDF_FONT, fontSize=6.4, fillColor=colors.HexColor("#35584d")))
    drawing.add(String(chart_left, height - 29, f"单位：{unit}；图表只使用本轮可追溯的结构化统计结果。", fontName=PDF_FONT, fontSize=7, fillColor=colors.HexColor("#58736a")))
    return drawing


def _analysis_table_and_chart(analysis: dict[str, Any] | None, styles: dict[str, ParagraphStyle]) -> tuple[list[Any], Drawing | None]:
    if not analysis:
        return [
            _paragraph("本轮没有可用于图表的数据型统计结果。为避免以文字推测数值，本报告不生成图表。", styles["body"])
        ], None

    analysis_type = str(analysis.get("analysis_type") or "")
    flowables: list[Any] = []
    chart: Drawing | None = None
    filters = analysis.get("filters") or {}
    if filters:
        flowables.append(_table(
            [["统计口径", "内容"], *[[key, _text(value)] for key, value in filters.items()]],
            [130, 360], styles,
        ))
        flowables.append(Spacer(1, 6))
    if analysis.get("model_formula"):
        flowables.append(_paragraph(f"模型或计算口径：{analysis['model_formula']}", styles["small"]))

    if analysis_type == "rcbd_same_trial_anova":
        records = analysis.get("material_means") or []
        rows = [["材料", "均值产量\n(kg/亩)", "株高\n(cm)", "千粒重\n(g)", "倒伏等级", "小区数"]]
        for item in records[:12]:
            rows.append([
                item.get("material_name") or item.get("material_code"), item.get("mean_yield_kg_per_mu"),
                item.get("mean_plant_height"), item.get("mean_thousand_grain_weight"), item.get("mean_lodging_score"), item.get("plot_count"),
            ])
        flowables.append(_table(rows, [105, 82, 70, 70, 70, 55], styles))
        chart = _bar_chart("各材料平均产量", [(item.get("material_name") or item.get("material_code"), item.get("mean_yield_kg_per_mu")) for item in records], "kg/亩")
        comparisons = (analysis.get("multiple_comparison") or {}).get("comparisons") or []
        if comparisons:
            flowables.extend([Spacer(1, 7), _paragraph("Tukey HSD 多重比较（仅展示前 12 条）", styles["small"]), _table(
                [["材料 A", "材料 B", "均值差", "校正 p 值", "是否显著"], *[
                    [row.get("group_a"), row.get("group_b"), row.get("mean_difference"), row.get("p_adjusted"), "是" if row.get("significant") else "否"]
                    for row in comparisons[:12]
                ]], [96, 96, 88, 100, 100], styles,
            )])
    elif analysis_type == "multi_environment_stability":
        records = analysis.get("material_stability") or []
        rows = [["材料", "平均产量\n(kg/亩)", "相对对照\n(%)", "产量 CV\n(%)", "有效环境数"]]
        for item in records[:12]:
            rows.append([
                item.get("material_name") or item.get("material_code"), item.get("mean_yield_kg_per_mu"),
                item.get("relative_yield_to_checks_percent"), item.get("yield_cv_percent"), item.get("environment_count"),
            ])
        flowables.append(_table(rows, [135, 95, 95, 85, 80], styles))
        chart = _bar_chart("材料跨环境平均产量", [(item.get("material_name") or item.get("material_code"), item.get("mean_yield_kg_per_mu")) for item in records], "kg/亩")
    elif analysis_type == "factorial_rcbd_management":
        records = analysis.get("material_treatment_effects") or []
        rows = [["材料", "标准施氮", "较高施氮", "产量变化", "倒伏变化"]]
        for item in records[:12]:
            rows.append([
                item.get("material_name") or item.get("material_code"), item.get("standard_n_mean_yield"), item.get("high_n_mean_yield"),
                item.get("yield_change_kg_per_mu"), item.get("lodging_change"),
            ])
        flowables.append(_table(rows, [130, 85, 85, 95, 95], styles))
        chart = _bar_chart("较高施氮相对标准施氮的产量变化", [(item.get("material_name") or item.get("material_code"), item.get("yield_change_kg_per_mu")) for item in records], "kg/亩")
    elif analysis_type == "trait_tradeoff":
        records = analysis.get("material_means") or []
        rows = [["材料", "平均产量", "整精米率", "垩白度", "倒伏等级"]]
        for item in records[:12]:
            rows.append([
                item.get("material_name") or item.get("material_code"), item.get("mean_yield"), item.get("mean_head_rice_rate"), item.get("mean_chalkiness_degree"), item.get("mean_lodging_score"),
            ])
        flowables.append(_table(rows, [130, 85, 95, 85, 95], styles))
        chart = _bar_chart("材料跨环境平均产量", [(item.get("material_name") or item.get("material_code"), item.get("mean_yield")) for item in records], "kg/亩")
    elif analysis_type == "performance_decline_evidence":
        records = analysis.get("records") or []
        rows = [["年份", "地点", "产量", "结实率", "千粒重", "病害压力", "降雨量"]]
        for item in records[:16]:
            rows.append([
                item.get("trial_year") or item.get("year"), item.get("site_name") or item.get("site"), item.get("mean_yield") or item.get("yield_kg_per_mu"),
                item.get("mean_setting") or item.get("seed_setting_rate"), item.get("mean_grain_weight") or item.get("thousand_grain_weight"),
                item.get("disease_pressure"), item.get("rainfall"),
            ])
        flowables.append(_table(rows, [48, 72, 68, 68, 68, 75, 75], styles))
        chart = _bar_chart("该材料在各试验环境的产量记录", [(f"{item.get('trial_year') or item.get('year')}-{item.get('site_name') or item.get('site')}", item.get("mean_yield") or item.get("yield_kg_per_mu")) for item in records], "kg/亩")
    elif analysis_type == "environment_association_regression":
        models = analysis.get("environment_models") or []
        rows = [["结果性状", "样本量", "R²", "调整 R²", "状态"]]
        for item in models:
            rows.append([item.get("outcome"), item.get("sample_size"), item.get("r_squared"), item.get("adjusted_r_squared"), item.get("status")])
        flowables.append(_table(rows, [150, 80, 80, 95, 85], styles))
        chart = _bar_chart("环境关联模型 R²", [(str(item.get("outcome")), item.get("r_squared")) for item in models if item.get("status") == "completed"], "R²")
    else:
        flowables.append(_paragraph("本轮统计结果已保存为可追溯 JSON，但当前报告模板尚未定义该分析类型的专用数据表与图表。为避免误绘，本报告仅保留文字分析和证据来源。", styles["body"]))

    limitations = analysis.get("limitations")
    if limitations:
        flowables.extend([Spacer(1, 7), _paragraph(f"统计局限：{limitations}", styles["small"])])
    return flowables, chart


def build_analysis_chart_png(analysis: dict[str, Any] | None) -> bytes | None:
    """Render a standalone PNG from persisted statistics, without an LLM.

    ReportLab's PDF renderer can work without its optional native PNG backend.
    Using Pillow here keeps the download path portable in the local Docker
    image, while the values and labels remain identical in meaning to the
    chart rendered in the PDF report.
    """
    if not analysis:
        return None

    analysis_type = str(analysis.get("analysis_type") or "")
    records = []
    title = ""
    unit = ""
    label_key = "material_name"
    value_key = ""
    if analysis_type == "rcbd_same_trial_anova":
        records, title, unit, value_key = analysis.get("material_means") or [], "各材料平均产量", "kg/亩", "mean_yield_kg_per_mu"
    elif analysis_type == "multi_environment_stability":
        records, title, unit, value_key = analysis.get("material_stability") or [], "材料跨环境平均产量", "kg/亩", "mean_yield_kg_per_mu"
    elif analysis_type == "factorial_rcbd_management":
        records, title, unit, value_key = analysis.get("material_treatment_effects") or [], "较高施氮相对标准施氮的产量变化", "kg/亩", "yield_change_kg_per_mu"
    elif analysis_type == "trait_tradeoff":
        records, title, unit, value_key = analysis.get("material_means") or [], "材料跨环境平均产量", "kg/亩", "mean_yield"
    elif analysis_type == "performance_decline_evidence":
        records, title, unit, value_key = analysis.get("records") or [], "该材料在各试验环境的产量记录", "kg/亩", "mean_yield"
        label_key = "trial_year"
    elif analysis_type == "environment_association_regression":
        records, title, unit, value_key = analysis.get("environment_models") or [], "环境关联模型 R²", "R²", "r_squared"
        label_key = "outcome"
        records = [item for item in records if item.get("status") == "completed"]
    else:
        return None

    series: list[tuple[str, float]] = []
    for item in records[:8]:
        raw_value = item.get(value_key)
        if analysis_type == "performance_decline_evidence":
            raw_value = raw_value if raw_value is not None else item.get("yield_kg_per_mu")
            label = f"{item.get('trial_year') or item.get('year') or '-'}-{item.get('site_name') or item.get('site') or '-'}"
        else:
            label = item.get(label_key) or item.get("material_code") or item.get("material_name") or "未命名"
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        series.append((str(label), value))
    if not series:
        return None

    font_path = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    if not font_path.exists():
        return None
    title_font = ImageFont.truetype(str(font_path), 28, index=0)
    body_font = ImageFont.truetype(str(font_path), 20, index=0)
    small_font = ImageFont.truetype(str(font_path), 17, index=0)
    width, height = 1280, 640
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((54, 38), title, font=title_font, fill="#103f35")
    draw.text((54, 80), f"单位：{unit}；图表只使用本轮可追溯的结构化统计结果。", font=small_font, fill="#58736a")

    left, top, right, bottom = 112, 136, 1220, 530
    max_value = max(value for _, value in series)
    min_value = min(0.0, min(value for _, value in series))
    spread = max(max_value - min_value, 1.0)
    chart_max = max_value + spread * 0.14
    chart_min = min_value - spread * 0.08 if min_value < 0 else 0.0
    scale = (bottom - top) / max(chart_max - chart_min, 1.0)
    zero_y = bottom - (0 - chart_min) * scale
    for fraction in (0, 0.5, 1):
        value = chart_min + (chart_max - chart_min) * fraction
        y = bottom - (value - chart_min) * scale
        draw.line((left, y, right, y), fill="#d9e7e1", width=1)
        draw.text((16, y - 10), f"{value:.1f}", font=small_font, fill="#58736a")
    draw.line((left, top, left, bottom), fill="#7f9d91", width=2)
    draw.line((left, zero_y, right, zero_y), fill="#7f9d91", width=2)
    slot_width = (right - left) / len(series)
    bar_width = max(28, min(82, slot_width * 0.56))
    for index, (label, value) in enumerate(series):
        x = left + index * slot_width + (slot_width - bar_width) / 2
        value_y = bottom - (value - chart_min) * scale
        y_top, y_bottom = sorted((zero_y, value_y))
        draw.rectangle((x, y_top, x + bar_width, max(y_bottom, y_top + 2)), fill="#16806a")
        value_text = f"{value:.2f}".rstrip("0").rstrip(".")
        box = draw.textbbox((0, 0), value_text, font=small_font)
        draw.text((x + bar_width / 2 - (box[2] - box[0]) / 2, value_y - 27 if value >= 0 else value_y + 7), value_text, font=small_font, fill="#183e34")
        display_label = label if len(label) <= 12 else f"{label[:11]}…"
        box = draw.textbbox((0, 0), display_label, font=small_font)
        draw.text((x + bar_width / 2 - (box[2] - box[0]) / 2, 556), display_label, font=small_font, fill="#35584d")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_research_report_pdf(
    *,
    question: str,
    answer: str,
    evidence: list[dict[str, Any]],
    analysis: dict[str, Any] | None,
    generated_at: datetime | None = None,
) -> bytes:
    """Generate an in-memory PDF under the agreed seven-section structure."""
    _ensure_font()
    styles = _styles()
    generated_at = generated_at or datetime.now()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm, title="隆耘 Agent 育种分析报告",
    )
    title = "区域试验统计分析报告" if analysis else "隆耘 Agent 育种分析报告"
    story: list[Any] = [
        Paragraph(title, styles["title"]),
        Paragraph(
            f"由隆耘 Agent 育种智能体按需生成 · {generated_at.strftime('%Y-%m-%d %H:%M')} · 已保存至当前账号的结果库",
            styles["subtitle"],
        ),
        Paragraph("一、问题与范围", styles["heading"]),
        _paragraph(question, styles["body"]),
        Paragraph("二、数据来源", styles["heading"]),
    ]
    source_rows = [["优先级", "来源", "说明"]]
    for item in evidence[:16]:
        source_rows.append([
            item.get("priority", "-"), item.get("title", "未命名证据"),
            item.get("detail") or item.get("source") or "-",
        ])
    if len(source_rows) == 1:
        source_rows.append(["-", "未使用外部或平台证据", "本报告仅包含本轮分析文字；不将通用模型知识伪装为可追溯数据来源。"])
    story.extend([_table(source_rows, [48, 185, 257], styles), Paragraph("三、数据表", styles["heading"])])
    analysis_flowables, chart = _analysis_table_and_chart(analysis, styles)
    story.extend(analysis_flowables)
    story.append(Paragraph("四、图表", styles["heading"]))
    if chart:
        story.append(KeepTogether([chart, Spacer(1, 4)]))
    else:
        story.append(_paragraph("没有可安全绘制的结构化数值结果。本报告不会根据大模型文字回答虚构图表。", styles["body"]))
    story.extend([Paragraph("五、AI 分析", styles["heading"]), *_answer_flowables(answer, styles)])
    story.append(Paragraph("六、风险与局限", styles["heading"]))
    if analysis and analysis.get("limitations"):
        story.append(_paragraph(analysis["limitations"], styles["body"]))
    else:
        story.append(_paragraph("本报告中的结论仅覆盖本轮实际引用的数据、附件和公开来源。若问题涉及病虫害、农药、施肥、种植或品种推广，需结合当地规范、完整试验设计与专业人员意见复核。", styles["body"]))
    story.append(Paragraph("七、参考来源", styles["heading"]))
    reference_items = []
    for index, item in enumerate(evidence[:24], start=1):
        value = item.get("title", "未命名来源")
        if item.get("url"):
            value = f"{value}：{item['url']}"
        elif item.get("source"):
            value = f"{value}：{item['source']}"
        reference_items.append(f"{index}. {value}")
    story.extend([_paragraph(item, styles["small"]) for item in reference_items] or [_paragraph("本轮未引用可列示的外部资料。", styles["small"])])
    document.build(story)
    return buffer.getvalue()
