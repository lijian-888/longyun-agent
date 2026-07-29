"""Traceable demo breeding dossiers and Jiangxi approval-support reports.

The governed regional-trial tables keep measured facts.  This module adds the
separate breeding-history layer that an approval-oriented report also needs:
programme, parent relationship, breeding stages and selection records.  The
seeded records are deliberately marked as simulated; they demonstrate the
data shape only and must never be treated as a real pedigree certificate.
"""

from __future__ import annotations

import html
import io
import json
import re
import uuid
from datetime import datetime
from typing import Any

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import text
from sqlalchemy.orm import Session


PDF_FONT = "STSong-Light"
MOCK_PROGRAM_CODE = "JX-RICE-DEMO-2021"
MOCK_PROGRAM_NAME = "优质抗病杂交稻区域试验与审定辅助演示项目"
NAMESPACE = uuid.UUID("5b4d9df5-1ae9-4f6b-9cee-7f48678893b0")
MOCK_CANDIDATE_CODES = tuple(f"ME-A{index:02d}" for index in range(1, 9))


class BreedingDossierError(ValueError):
    """Raised when a report request cannot be matched to a published dossier."""


def _id(*parts: object) -> str:
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _number(value: Any, digits: int = 2, fallback: str = "-") -> str:
    if value is None:
        return fallback
    try:
        return f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _ensure_font() -> None:
    if PDF_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(PDF_FONT))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "BreedingReportTitle", parent=base["Title"], fontName=PDF_FONT,
            fontSize=18, leading=25, textColor=colors.HexColor("#103f35"), spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "BreedingReportSubtitle", parent=base["BodyText"], fontName=PDF_FONT,
            fontSize=9, leading=14, textColor=colors.HexColor("#526c62"), spaceAfter=9,
        ),
        "heading": ParagraphStyle(
            "BreedingReportHeading", parent=base["Heading2"], fontName=PDF_FONT,
            fontSize=12.5, leading=18, textColor=colors.HexColor("#103f35"), spaceBefore=11, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "BreedingReportBody", parent=base["BodyText"], fontName=PDF_FONT,
            fontSize=9.1, leading=15, textColor=colors.HexColor("#18231f"), spaceAfter=3,
        ),
        "small": ParagraphStyle(
            "BreedingReportSmall", parent=base["BodyText"], fontName=PDF_FONT,
            fontSize=7.7, leading=11, textColor=colors.HexColor("#4d665c"), spaceAfter=2,
        ),
        "table": ParagraphStyle(
            "BreedingReportTable", parent=base["BodyText"], fontName=PDF_FONT,
            fontSize=7.6, leading=10.5, textColor=colors.HexColor("#18231f"),
        ),
    }


def _paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    content = html.escape(str(value if value not in (None, "") else "-"))
    return Paragraph(content.replace("\n", "<br/>"), style)


def _table(rows: list[list[Any]], widths: list[float], styles: dict[str, ParagraphStyle]) -> Table:
    table = Table(
        [[_paragraph(value, styles["table"]) for value in row] for row in rows],
        colWidths=widths,
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7f2ed")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#103f35")),
        ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#c9ddd4")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fbfa")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _yield_chart(records: list[dict[str, Any]]) -> Drawing | None:
    values = [(f"{item['trial_year']}\n{item['site_name']}", item.get("yield_per_mu")) for item in records]
    values = [(label, float(value)) for label, value in values if value is not None][:12]
    if not values:
        return None
    width, height = 490, 205
    drawing = Drawing(width, height)
    drawing.add(String(0, height - 14, "已发布区域试验中各环境平均产量", fontName=PDF_FONT, fontSize=10, fillColor=colors.HexColor("#103f35")))
    left, bottom, chart_width, chart_height = 43, 36, 428, 132
    minimum, maximum = min(value for _, value in values), max(value for _, value in values)
    start = max(0.0, minimum - max((maximum - minimum) * 0.18, 10.0))
    ceiling = maximum + max((maximum - minimum) * 0.16, 10.0)
    scale = chart_height / max(ceiling - start, 1.0)
    for fraction in (0, 0.5, 1):
        value = start + (ceiling - start) * fraction
        y = bottom + (value - start) * scale
        drawing.add(Line(left, y, left + chart_width, y, strokeColor=colors.HexColor("#d5e4de"), strokeWidth=0.5))
        drawing.add(String(0, y - 3, _number(value, 0), fontName=PDF_FONT, fontSize=7, fillColor=colors.HexColor("#58736a")))
    drawing.add(Line(left, bottom, left, bottom + chart_height, strokeColor=colors.HexColor("#7e998e"), strokeWidth=0.8))
    drawing.add(Line(left, bottom, left + chart_width, bottom, strokeColor=colors.HexColor("#7e998e"), strokeWidth=0.8))
    slot = chart_width / len(values)
    bar_width = min(24, slot * 0.58)
    for index, (label, value) in enumerate(values):
        x = left + index * slot + (slot - bar_width) / 2
        bar_height = max(2, (value - start) * scale)
        drawing.add(Rect(x, bottom, bar_width, bar_height, fillColor=colors.HexColor("#16806a"), strokeColor=None))
        drawing.add(String(x - 4, bottom + bar_height + 4, _number(value, 1), fontName=PDF_FONT, fontSize=6.6, fillColor=colors.HexColor("#183e34")))
        first, second = label.split("\n", 1)
        drawing.add(String(x - 5, 19, first, fontName=PDF_FONT, fontSize=6.6, fillColor=colors.HexColor("#58736a")))
        drawing.add(String(x - 8, 9, second, fontName=PDF_FONT, fontSize=6.3, fillColor=colors.HexColor("#58736a")))
    return drawing


def _short_label(value: Any, limit: int = 18) -> str:
    text_value = str(value or "未补充")
    return text_value if len(text_value) <= limit else f"{text_value[:limit - 1]}…"


def _pedigree_diagram(parents: list[dict[str, Any]], material: dict[str, Any]) -> Drawing:
    """Render a light-weight, evidence-labelled pedigree sketch for the report.

    It deliberately represents only the two recorded parent links.  A future
    full pedigree graph can replace this drawing without changing the report
    contract or creating another storage system in the demo.
    """
    width, height = 490, 148
    drawing = Drawing(width, height)
    drawing.add(String(0, height - 12, "系谱关系示意（仅展示当前档案已记录的亲本关系）", fontName=PDF_FONT, fontSize=9.5, fillColor=colors.HexColor("#103f35")))
    positions = {"female": (20, 78), "male": (20, 30)}
    parent_map = {str(item.get("parent_role")): item for item in parents}
    candidate_x, candidate_y, box_w, box_h = 305, 54, 155, 37
    drawing.add(Rect(candidate_x, candidate_y, box_w, box_h, fillColor=colors.HexColor("#e1f0ea"), strokeColor=colors.HexColor("#16745f"), strokeWidth=0.8, rx=3, ry=3))
    drawing.add(String(candidate_x + 9, candidate_y + 23, _short_label(material.get("material_name"), 17), fontName=PDF_FONT, fontSize=9, fillColor=colors.HexColor("#103f35")))
    drawing.add(String(candidate_x + 9, candidate_y + 10, str(material.get("material_code") or "-"), fontName=PDF_FONT, fontSize=7.4, fillColor=colors.HexColor("#58736a")))
    drawing.add(String(238, 66, "杂交组合", fontName=PDF_FONT, fontSize=8, fillColor=colors.HexColor("#58736a")))
    for role, label in (("female", "雌亲本"), ("male", "父本")):
        parent = parent_map.get(role)
        x, y = positions[role]
        drawing.add(Rect(x, y, 165, 32, fillColor=colors.HexColor("#f8fbfa"), strokeColor=colors.HexColor("#9dc5b5"), strokeWidth=0.7, rx=3, ry=3))
        drawing.add(String(x + 7, y + 20, f"{label}：{_short_label(parent.get('material_name') if parent else '未补充', 13)}", fontName=PDF_FONT, fontSize=8.3, fillColor=colors.HexColor("#183e34")))
        drawing.add(String(x + 7, y + 8, str(parent.get("material_code") if parent else "无亲本记录"), fontName=PDF_FONT, fontSize=6.9, fillColor=colors.HexColor("#58736a")))
        line_y = y + 16
        drawing.add(Line(x + 165, line_y, candidate_x - 8, candidate_y + box_h / 2, strokeColor=colors.HexColor("#5f9684"), strokeWidth=1.1))
    drawing.add(String(20, 8, "说明：亲本来源、特征与选配依据见下表；当前为演示模拟档案，正式版应以原始系谱凭证替换。", fontName=PDF_FONT, fontSize=7.1, fillColor=colors.HexColor("#58736a")))
    return drawing


def _trait_description_rows(averages: dict[str, float | None]) -> list[list[str]]:
    """Separate descriptive wording from trial-derived supporting values."""
    return [
        ["材料类型与总体描述", "当前档案仅确认其为区域试验候选杂交组合。全生育期、株型、叶色、剑叶、穗型和粒型等标准描述尚未录入，不能由区域试验均值推定。"],
        ["产量与农艺表现", f"在已发布标准施氮优先口径下，平均产量 {_number(averages.get('yield_per_mu'))} kg/亩，株高 {_number(averages.get('plant_height'))} cm，千粒重 {_number(averages.get('thousand_grain_weight'))} g，结实率 {_number(averages.get('seed_setting_rate'))} %。这些为试验观测支撑，不等同于完整特征描述。"],
        ["米质相关观测", f"已记录整精米率 {_number(averages.get('head_rice_rate'))} %、垩白度 {_number(averages.get('chalkiness_degree'))} %。尚未关联统一检测方法、送检批次和正式品质结论。"],
        ["抗性与抗倒性观测", f"已记录穗瘟等级 {_number(averages.get('panicle_blast_score'))}、倒伏等级 {_number(averages.get('lodging_score'))}。现有记录只能说明区域试验观测情况，不能替代规范抗性鉴定或抗倒伏结论。"],
    ]


def ensure_breeding_dossier_schema(session: Session) -> None:
    """Create the optional breeding-history layer without touching trial facts."""
    statements = (
        """
        CREATE TABLE IF NOT EXISTS breeding_program (
            id VARCHAR(36) PRIMARY KEY,
            program_code VARCHAR(100) NOT NULL UNIQUE,
            program_name VARCHAR(300) NOT NULL,
            crop_name VARCHAR(80) NOT NULL DEFAULT '水稻',
            breeding_target TEXT NOT NULL,
            target_ecological_zone VARCHAR(300),
            leading_unit VARCHAR(300),
            start_year INTEGER,
            status VARCHAR(40) NOT NULL DEFAULT 'active',
            description TEXT,
            is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS breeding_program_material (
            id VARCHAR(36) PRIMARY KEY,
            program_id VARCHAR(36) NOT NULL REFERENCES breeding_program(id) ON DELETE CASCADE,
            material_id VARCHAR(36) NOT NULL REFERENCES breeding_material(id),
            material_role VARCHAR(80) NOT NULL,
            selection_stage VARCHAR(120),
            entry_status VARCHAR(40) NOT NULL DEFAULT 'active',
            source_note TEXT,
            is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE(program_id, material_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS breeding_pedigree_relationship (
            id VARCHAR(36) PRIMARY KEY,
            child_material_id VARCHAR(36) NOT NULL REFERENCES breeding_material(id),
            parent_material_id VARCHAR(36) NOT NULL REFERENCES breeding_material(id),
            parent_role VARCHAR(40) NOT NULL,
            relationship_type VARCHAR(80) NOT NULL DEFAULT 'hybrid_parent',
            parent_origin TEXT,
            parent_trait_summary TEXT,
            combination_basis TEXT,
            source_record_no VARCHAR(120),
            source_note TEXT,
            is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE(child_material_id, parent_material_id, parent_role)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS breeding_generation_record (
            id VARCHAR(36) PRIMARY KEY,
            program_id VARCHAR(36) NOT NULL REFERENCES breeding_program(id) ON DELETE CASCADE,
            material_id VARCHAR(36) NOT NULL REFERENCES breeding_material(id),
            event_sequence INTEGER NOT NULL,
            event_year INTEGER,
            event_site VARCHAR(200),
            generation_label VARCHAR(100),
            breeding_stage VARCHAR(160) NOT NULL,
            event_type VARCHAR(100) NOT NULL,
            selection_method TEXT,
            result_summary TEXT,
            source_note TEXT,
            is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE(program_id, material_id, event_sequence)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS breeding_selection_record (
            id VARCHAR(36) PRIMARY KEY,
            program_id VARCHAR(36) NOT NULL REFERENCES breeding_program(id) ON DELETE CASCADE,
            material_id VARCHAR(36) NOT NULL REFERENCES breeding_material(id),
            generation_record_id VARCHAR(36) REFERENCES breeding_generation_record(id) ON DELETE SET NULL,
            selection_year INTEGER,
            selection_site VARCHAR(200),
            selection_criterion TEXT NOT NULL,
            selection_decision VARCHAR(60) NOT NULL,
            input_material_count INTEGER,
            retained_material_count INTEGER,
            elimination_rate NUMERIC(5,2),
            retention_reason TEXT,
            source_record_no VARCHAR(120),
            evidence_summary TEXT,
            source_note TEXT,
            is_simulated BOOLEAN NOT NULL DEFAULT FALSE
        )
        """,
        "ALTER TABLE breeding_pedigree_relationship ADD COLUMN IF NOT EXISTS parent_origin TEXT",
        "ALTER TABLE breeding_pedigree_relationship ADD COLUMN IF NOT EXISTS parent_trait_summary TEXT",
        "ALTER TABLE breeding_pedigree_relationship ADD COLUMN IF NOT EXISTS combination_basis TEXT",
        "ALTER TABLE breeding_pedigree_relationship ADD COLUMN IF NOT EXISTS source_record_no VARCHAR(120)",
        "ALTER TABLE breeding_selection_record ADD COLUMN IF NOT EXISTS input_material_count INTEGER",
        "ALTER TABLE breeding_selection_record ADD COLUMN IF NOT EXISTS retained_material_count INTEGER",
        "ALTER TABLE breeding_selection_record ADD COLUMN IF NOT EXISTS elimination_rate NUMERIC(5,2)",
        "ALTER TABLE breeding_selection_record ADD COLUMN IF NOT EXISTS retention_reason TEXT",
        "ALTER TABLE breeding_selection_record ADD COLUMN IF NOT EXISTS source_record_no VARCHAR(120)",
        "CREATE INDEX IF NOT EXISTS ix_program_material_material ON breeding_program_material(material_id)",
        "CREATE INDEX IF NOT EXISTS ix_pedigree_child ON breeding_pedigree_relationship(child_material_id)",
        "CREATE INDEX IF NOT EXISTS ix_generation_material ON breeding_generation_record(material_id, event_sequence)",
        "CREATE INDEX IF NOT EXISTS ix_selection_material ON breeding_selection_record(material_id, selection_year)",
    )
    for statement in statements:
        session.execute(text(statement))


def _upsert_parent_material(session: Session, code: str, name: str, role: str) -> str:
    existing = session.execute(text("SELECT id FROM breeding_material WHERE material_code = :code"), {"code": code}).scalar_one_or_none()
    if existing:
        return str(existing)
    material_id = _id("parent", code)
    session.execute(text("""
        INSERT INTO breeding_material (id, material_code, material_name, material_type, is_check, aliases, pedigree_summary)
        VALUES (:id, :code, :name, :material_type, FALSE, CAST(:aliases AS jsonb), :summary)
    """), {
        "id": material_id,
        "code": code,
        "name": name,
        "material_type": "水稻亲本材料（演示）",
        "aliases": _json([]),
        "summary": f"{role}；平台演示模拟亲本记录，不作为真实亲本血缘证明。",
    })
    return material_id


def _demo_parent_profile(index: int, role: str) -> dict[str, str]:
    """Return explicitly simulated parent dossier facts for the demo package.

    Keeping these details in the seeded dossier makes the report structure
    useful without pretending that a regional-trial mean is a real pedigree
    certificate or a parent-characterization result.
    """
    record_prefix = f"DEMO-PAR-{index:02d}"
    if role == "female":
        return {
            "origin": "江西水稻育种平台演示亲本库；雌亲本来源为系统模拟档案。",
            "traits": "演示档案记载：株型较整齐、结实基础较好，作为不育系母本提供细胞质不育条件。",
            "basis": "组合设计以雌亲本的结实基础和不育系属性为母本条件，配合恢复系的恢复性与产量构成表现；该依据为演示设定，正式组合须引用亲本鉴定与原始选配记录。",
            "record_no": f"{record_prefix}-F",
        }
    return {
        "origin": "江西水稻育种平台演示亲本库；父本来源为系统模拟档案。",
        "traits": "演示档案记载：恢复性、穗粒数和千粒重基础较好，作为恢复系父本用于恢复育性并补充产量构成。",
        "basis": "组合设计以雌亲本的结实基础和不育系属性为母本条件，配合恢复系的恢复性与产量构成表现；该依据为演示设定，正式组合须引用亲本鉴定与原始选配记录。",
        "record_no": f"{record_prefix}-M",
    }


def seed_mock_breeding_dossiers(session: Session) -> int:
    """Attach mock dossiers to the existing simulated regional-trial materials.

    The function is idempotent and intentionally does nothing until the
    regional-trial package has already published candidate material records.
    """
    candidate_rows = session.execute(text("""
        SELECT id, material_code, material_name
        FROM breeding_material
        WHERE material_code = ANY(CAST(:codes AS text[]))
        ORDER BY material_code
    """), {"codes": list(MOCK_CANDIDATE_CODES)}).mappings().all()
    if not candidate_rows:
        return 0

    program_id = _id("program", MOCK_PROGRAM_CODE)
    session.execute(text("""
        INSERT INTO breeding_program (
            id, program_code, program_name, crop_name, breeding_target,
            target_ecological_zone, leading_unit, start_year, status, description, is_simulated
        ) VALUES (
            :id, :code, :name, '水稻', :target, :zone, :unit, 2021, 'demo', :description, TRUE
        ) ON CONFLICT (program_code) DO UPDATE SET
            program_name = EXCLUDED.program_name,
            breeding_target = EXCLUDED.breeding_target,
            target_ecological_zone = EXCLUDED.target_ecological_zone,
            description = EXCLUDED.description,
            is_simulated = TRUE
    """), {
        "id": program_id,
        "code": MOCK_PROGRAM_CODE,
        "name": MOCK_PROGRAM_NAME,
        "target": "面向江西中籼稻区的高产稳产、较好米质、较低穗瘟与倒伏风险组合筛选。",
        "zone": "赣北平原稻作区、赣东丘陵稻作区、赣南丘陵双季稻区",
        "unit": "江西水稻育种平台演示课题组",
        "description": "该项目、系谱、世代和选择记录均为系统演示模拟数据；仅用于展示审定辅助材料的数据组织方式。",
    })

    for index, material in enumerate(candidate_rows, start=1):
        material_id, code, name = str(material["id"]), str(material["material_code"]), str(material["material_name"])
        female_code, male_code = f"JX-CMS-A{index:02d}", f"JX-RF-R{index:02d}"
        female_id = _upsert_parent_material(session, female_code, f"赣不育系 A{index:02d}", "雌亲本")
        male_id = _upsert_parent_material(session, male_code, f"赣恢复系 R{index:02d}", "父本")
        session.execute(text("""
            INSERT INTO breeding_program_material (
                id, program_id, material_id, material_role, selection_stage, entry_status, source_note, is_simulated
            ) VALUES (:id, :program_id, :material_id, 'candidate_hybrid', '区域试验候选组合', 'active', :note, TRUE)
            ON CONFLICT (program_id, material_id) DO UPDATE SET
                selection_stage = EXCLUDED.selection_stage, source_note = EXCLUDED.source_note, is_simulated = TRUE
        """), {
            "id": _id("program-material", MOCK_PROGRAM_CODE, code), "program_id": program_id, "material_id": material_id,
            "note": "由模拟区域试验资料包关联生成；真实项目须由育种团队补录并审核。",
        })
        for parent_id, role, parent_code in ((female_id, "female", female_code), (male_id, "male", male_code)):
            profile = _demo_parent_profile(index, role)
            session.execute(text("""
                INSERT INTO breeding_pedigree_relationship (
                    id, child_material_id, parent_material_id, parent_role, relationship_type,
                    parent_origin, parent_trait_summary, combination_basis, source_record_no, source_note, is_simulated
                ) VALUES (
                    :id, :child_id, :parent_id, :role, 'hybrid_parent',
                    :origin, :traits, :basis, :record_no, :note, TRUE
                )
                ON CONFLICT (child_material_id, parent_material_id, parent_role) DO UPDATE SET
                    parent_origin = EXCLUDED.parent_origin,
                    parent_trait_summary = EXCLUDED.parent_trait_summary,
                    combination_basis = EXCLUDED.combination_basis,
                    source_record_no = EXCLUDED.source_record_no,
                    source_note = EXCLUDED.source_note,
                    is_simulated = TRUE
            """), {
                "id": _id("pedigree", code, parent_code), "child_id": material_id, "parent_id": parent_id, "role": role,
                "origin": profile["origin"], "traits": profile["traits"], "basis": profile["basis"], "record_no": profile["record_no"],
                "note": "演示组合关系，正式审定材料应替换为经育种者确认的亲本血缘来源。",
            })

        stage_rows = [
            (1, 2021, "海南南繁基地", "F1 组合", "杂交组合创建", "人工杂交", "完成不育系与恢复系组合配制，保留进入组合观察。"),
            (2, 2022, "南昌育种圃", "F1 组合", "组合观察", "株型、结实率与抗病初筛", "组合性状达到演示设定的保留阈值，进入品比试验。"),
            (3, 2023, "南昌试验点", "品比阶段", "品种比较试验", "随机区组小区比较", "纳入模拟多环境资料包，获得首年小区级观测。"),
            (4, 2024, "江西多点", "区域试验阶段", "区域试验", "多点随机区组试验", "在多生态区继续记录产量、品质、病害与倒伏性状。"),
            (5, 2025, "江西多点", "区域试验阶段", "续试与汇总", "多年多点汇总与稳定性分析", "形成审定辅助草稿所需的演示性选育过程和试验表现摘要。"),
        ]
        generation_ids: dict[int, str] = {}
        for sequence, year, site, generation, stage, method, result in stage_rows:
            record_id = _id("generation", code, sequence)
            generation_ids[sequence] = record_id
            session.execute(text("""
                INSERT INTO breeding_generation_record (
                    id, program_id, material_id, event_sequence, event_year, event_site, generation_label,
                    breeding_stage, event_type, selection_method, result_summary, source_note, is_simulated
                ) VALUES (
                    :id, :program_id, :material_id, :sequence, :year, :site, :generation,
                    :stage, :event_type, :method, :result, :note, TRUE
                ) ON CONFLICT (program_id, material_id, event_sequence) DO UPDATE SET
                    event_year = EXCLUDED.event_year, event_site = EXCLUDED.event_site,
                    generation_label = EXCLUDED.generation_label, breeding_stage = EXCLUDED.breeding_stage,
                    event_type = EXCLUDED.event_type, selection_method = EXCLUDED.selection_method,
                    result_summary = EXCLUDED.result_summary, source_note = EXCLUDED.source_note, is_simulated = TRUE
            """), {
                "id": record_id, "program_id": program_id, "material_id": material_id, "sequence": sequence,
                "year": year, "site": site, "generation": generation, "stage": stage,
                "event_type": stage, "method": method, "result": result,
                "note": "平台演示模拟记录；正式项目应关联原始田间记录、实验编号或审核人。",
            })
        selection_rows = [
            (1, 2021, "海南南繁基地", "亲本组合确认与杂交成功核验", "retain", 1, 1, 0, "完成杂交组合创建，保留 F1 组合观察。", f"DEMO-SEL-{code}-2021-01", "完成组合配制的演示原始记录。"),
            (2, 2022, "南昌育种圃", "株型、结实率、田间病害初筛", "advance", 48, 12, 75, "满足演示保留阈值的组合进入品比阶段。", f"DEMO-SEL-{code}-2022-02", "组合观察达到演示保留阈值。"),
            (3, 2023, "南昌试验点", "随机区组品比产量与主要农艺性状", "advance", 12, 5, 58.33, "保留产量与主要农艺性状达到演示筛选口径的候选组合。", f"DEMO-SEL-{code}-2023-03", "进入区域试验阶段，等待多环境重复验证。"),
            (4, 2024, "江西多点", "多点产量、倒伏和病害观测完整性", "advance", 5, 3, 40, "保留完成多点观测且无缺失关键性状的组合。", f"DEMO-SEL-{code}-2024-04", "进入续试与汇总阶段。"),
            (5, 2025, "江西多点", "多年多点产量、稳定性、倒伏和穗瘟风险综合审查", "candidate", 3, 1, 66.67, "形成审定辅助草稿候选对象；不代表已通过任何审定。", f"DEMO-SEL-{code}-2025-05", "形成审定辅助草稿候选对象；不代表已通过任何审定。"),
        ]
        for sequence, year, site, criterion, decision, input_count, retained_count, elimination_rate, retention_reason, source_record_no, evidence in selection_rows:
            selection_id = _id("selection", code, sequence)
            session.execute(text("""
                INSERT INTO breeding_selection_record (
                    id, program_id, material_id, generation_record_id, selection_year, selection_site,
                    selection_criterion, selection_decision, input_material_count, retained_material_count,
                    elimination_rate, retention_reason, source_record_no, evidence_summary, source_note, is_simulated
                ) VALUES (
                    :id, :program_id, :material_id, :generation_id, :year, :site,
                    :criterion, :decision, :input_count, :retained_count,
                    :elimination_rate, :retention_reason, :source_record_no, :evidence, :note, TRUE
                )
                ON CONFLICT (id) DO UPDATE SET
                    selection_criterion = EXCLUDED.selection_criterion,
                    selection_decision = EXCLUDED.selection_decision,
                    input_material_count = EXCLUDED.input_material_count,
                    retained_material_count = EXCLUDED.retained_material_count,
                    elimination_rate = EXCLUDED.elimination_rate,
                    retention_reason = EXCLUDED.retention_reason,
                    source_record_no = EXCLUDED.source_record_no,
                    evidence_summary = EXCLUDED.evidence_summary,
                    source_note = EXCLUDED.source_note, is_simulated = TRUE
            """), {
                "id": selection_id, "program_id": program_id, "material_id": material_id,
                "generation_id": generation_ids[sequence], "year": year, "site": site,
                "criterion": criterion, "decision": decision, "input_count": input_count, "retained_count": retained_count,
                "elimination_rate": elimination_rate, "retention_reason": retention_reason, "source_record_no": source_record_no, "evidence": evidence,
                "note": "平台演示模拟选择记录，须由育种负责人确认后才可作为正式档案。",
            })
    return len(candidate_rows)


def is_breeding_report_request(question: str) -> bool:
    normalized = str(question or "")
    return "品种选育报告" in normalized or ("选育报告" in normalized and any(token in normalized for token in ("生成", "导出", "PDF", "报告")))


def _normalized(value: Any) -> str:
    return re.sub(r"[\s()（）\[\]【】_\-－]", "", str(value or "")).lower()


def _resolve_material(session: Session, question: str) -> dict[str, Any]:
    rows = session.execute(text("""
        SELECT material.id, material.material_code, material.material_name, material.aliases,
               program.id AS program_id, program.program_code, program.program_name, program.breeding_target,
               program.target_ecological_zone, program.leading_unit, program.start_year, program.description,
               program.is_simulated
        FROM breeding_program_material link
        JOIN breeding_program program ON program.id = link.program_id
        JOIN breeding_material material ON material.id = link.material_id
        WHERE link.material_role = 'candidate_hybrid'
        ORDER BY material.material_code
    """)).mappings().all()
    question_key = _normalized(question)
    matches: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        aliases = row.get("aliases") or []
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except (TypeError, ValueError, json.JSONDecodeError):
                aliases = []
        keys = [row["material_code"], row["material_name"], *aliases]
        if any(_normalized(item) and _normalized(item) in question_key for item in keys):
            matches.append(row)
    if len(matches) == 1:
        return matches[0]
    available = "、".join(f"{row['material_name']}（{row['material_code']}）" for row in rows)
    if not matches:
        raise BreedingDossierError(f"未识别要生成选育报告的候选材料。请在问题中写明材料代码或名称，例如“为候选A-08（ME-A08）生成品种选育报告”。当前可用：{available}。")
    raise BreedingDossierError("问题中命中了多个候选材料，请只指定一个材料后再生成品种选育报告。")


def build_breeding_report_context(session: Session, question: str) -> dict[str, Any]:
    """Collect only published trial facts plus the explicitly simulated dossier."""
    material = _resolve_material(session, question)
    material_id = str(material["id"])
    parents = [dict(row) for row in session.execute(text("""
        SELECT relation.parent_role, parent.material_code, parent.material_name, relation.relationship_type,
               relation.parent_origin, relation.parent_trait_summary, relation.combination_basis,
               relation.source_record_no, relation.source_note
        FROM breeding_pedigree_relationship relation
        JOIN breeding_material parent ON parent.id = relation.parent_material_id
        WHERE relation.child_material_id = :material_id
        ORDER BY CASE relation.parent_role WHEN 'female' THEN 1 WHEN 'male' THEN 2 ELSE 3 END
    """), {"material_id": material_id}).mappings().all()]
    generations = [dict(row) for row in session.execute(text("""
        SELECT event_sequence, event_year, event_site, generation_label, breeding_stage, event_type,
               selection_method, result_summary, source_note
        FROM breeding_generation_record
        WHERE material_id = :material_id AND program_id = :program_id
        ORDER BY event_sequence
    """), {"material_id": material_id, "program_id": material["program_id"]}).mappings().all()]
    selections = [dict(row) for row in session.execute(text("""
        SELECT selection.selection_year, selection.selection_site, selection.selection_criterion, selection.selection_decision,
               selection.input_material_count, selection.retained_material_count, selection.elimination_rate,
               selection.retention_reason, selection.source_record_no, selection.evidence_summary, selection.source_note,
               generation.generation_label, generation.breeding_stage
        FROM breeding_selection_record selection
        LEFT JOIN breeding_generation_record generation ON generation.id = selection.generation_record_id
        WHERE selection.material_id = :material_id AND selection.program_id = :program_id
        ORDER BY selection.selection_year, selection.id
    """), {"material_id": material_id, "program_id": material["program_id"]}).mappings().all()]
    trial_records = [dict(row) for row in session.execute(text("""
        SELECT summary.trial_year, summary.site_name, summary.ecological_zone, summary.treatment_code,
               summary.treatment_name, summary.yield_per_mu, summary.plant_height,
               summary.thousand_grain_weight, summary.seed_setting_rate, summary.head_rice_rate,
               summary.chalkiness_degree, summary.panicle_blast_score, summary.lodging_score,
               package.package_code, package.package_name
        FROM v_trial_material_summary summary
        JOIN field_trial trial ON trial.id = summary.trial_id
        JOIN trial_data_package package ON package.id = trial.package_id
        WHERE summary.material_id = :material_id
          AND trial.data_status = 'published'
          AND package.governance_status = 'published'
        ORDER BY summary.trial_year, summary.site_name, summary.treatment_code
    """), {"material_id": material_id}).mappings().all()]
    standard_records = [item for item in trial_records if item.get("treatment_code") == "M1"] or trial_records
    numeric_fields = ("yield_per_mu", "plant_height", "thousand_grain_weight", "seed_setting_rate", "head_rice_rate", "chalkiness_degree", "panicle_blast_score", "lodging_score")
    averages: dict[str, float | None] = {}
    for field in numeric_fields:
        values = [float(item[field]) for item in standard_records if item.get(field) is not None]
        averages[field] = round(sum(values) / len(values), 2) if values else None
    environments = sorted({f"{item['trial_year']}年·{item['site_name']}" for item in standard_records})
    zones = sorted({str(item["ecological_zone"]) for item in standard_records if item.get("ecological_zone")})
    risks: list[str] = []
    if averages.get("panicle_blast_score") is not None:
        risks.append(
            f"穗瘟观测：标准施氮优先口径下均值为 {_number(averages['panicle_blast_score'])} 级；"
            "该值来自区域试验观测，尚不能替代规范抗性鉴定，应列为后续复核项目。"
        )
    if averages.get("lodging_score") is not None:
        risks.append(
            f"倒伏观测：标准施氮优先口径下均值为 {_number(averages['lodging_score'])} 级；"
            "需在高施氮、强风或高密度环境下结合原始田间记录继续评估。"
        )
    if averages.get("chalkiness_degree") is not None:
        risks.append(
            f"米质复核：已记录垩白度 {_number(averages['chalkiness_degree'])} %，但当前档案未关联统一检测方法、批次和正式送检结论，"
            "不能据此形成品种品质等级结论。"
        )
    if averages.get("head_rice_rate") is not None:
        risks.append(
            f"加工品质复核：已记录整精米率 {_number(averages['head_rice_rate'])} %，"
            "仍需以规范样品制备和正式检测报告确认。"
        )
    if len(environments) < 6:
        risks.append("试验覆盖：当前有效试验环境数有限；区域适应性与稳定性应以符合审定规范的正式区试安排和完整原始记录为准。")
    evidence_gaps = [
        "标准图片：当前档案未上传植株、穗部、谷粒及米样标准图片，不可将空白章节视为已满足申报要求。",
        "品种描述：全生育期、株型、叶色、剑叶、穗型、粒型等标准描述尚未形成可追溯记录。",
    ]
    evidence = [
        {
            "priority": 1,
            "type": "published_regional_trial_data",
            "title": f"已发布区域试验数据：{standard_records[0]['package_name'] if standard_records else '未找到'}",
            "detail": f"材料 {material['material_name']}（{material['material_code']}）在标准施氮优先口径下的 {len(environments)} 个试验环境记录。",
        },
        {
            "priority": 2,
            "type": "simulated_breeding_dossier",
            "title": f"模拟育种档案：{material['program_name']}",
            "detail": "亲本、世代、选择记录为演示模拟数据，需由育种者以原始档案和审核记录替换。",
        },
        {
            "priority": 3,
            "type": "approval_requirement",
            "title": "江西省品种审定标准第十一条（用户提供文件）",
            "detail": "报告按“亲本血缘、选育方法与世代、特征特性、建议试验区域及栽培要点、缺陷与注意事项”的材料框架组织。",
        },
    ]
    return {
        "material": material,
        "parents": parents,
        "generations": generations,
        "selections": selections,
        "trial_records": standard_records,
        "averages": averages,
        "environments": environments,
        "ecological_zones": zones,
        "risks": risks,
        "evidence_gaps": evidence_gaps,
        "evidence": evidence,
        "is_simulated": True,
    }


def build_breeding_report_evidence_context(context: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    material = context["material"]
    payload = {
        "scope": "品种选育报告的已发布区域试验数据与模拟育种档案",
        "material": {"code": material["material_code"], "name": material["material_name"]},
        "parent_combination": [
            {
                "role": item["parent_role"],
                "code": item["material_code"],
                "name": item["material_name"],
                "origin": item.get("parent_origin"),
                "trait_summary": item.get("parent_trait_summary"),
                "combination_basis": item.get("combination_basis"),
                "source_record_no": item.get("source_record_no"),
            }
            for item in context["parents"]
        ],
        "selection_timeline": context["generations"],
        "selection_stages": context["selections"],
        "feature_description_scope": _trait_description_rows(context["averages"]),
        "published_trial_summary": context["averages"],
        "observed_risks": context["risks"],
        "evidence_gaps": context["evidence_gaps"],
        "report_rules": [
            "选育过程和亲本关系为平台演示模拟数据，必须明确标识，不能作为真实审定申请凭证。",
            "区域试验表现仅引用已发布结构化记录；不补造未测量的品质、抗性或标准图片信息。",
            "输出应将建议与正式审定结论区分，提醒补充照片、品质、抗性和审核材料。",
        ],
    }
    return "品种选育报告可追溯档案（JSON）：\n" + _json(payload), list(context["evidence"])


def build_breeding_report_pdf(context: dict[str, Any], generated_at: datetime | None = None) -> bytes:
    """Render the Jiangxi approval-support draft from governed and labelled data."""
    _ensure_font()
    styles = _styles()
    generated_at = generated_at or datetime.now()
    material = context["material"]
    averages = context["averages"]
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm, title=f"{material['material_name']}品种选育报告（审定辅助草稿）",
    )
    story: list[Any] = [
        Paragraph("水稻品种选育报告（审定辅助草稿）", styles["title"]),
        Paragraph(
            f"材料：{material['material_name']}（{material['material_code']}） · 生成时间：{generated_at.strftime('%Y-%m-%d %H:%M')} · 仅保存于当前账号结果库",
            styles["subtitle"],
        ),
        Paragraph("重要提示：本报告中的亲本组合、世代和选择记录为平台演示模拟数据；区域试验表现来自已发布的模拟资料包。它用于展示审定辅助材料的数据组织方式，不构成真实品种审定申请、亲本血缘证明、抗性或品质结论。", styles["body"]),
        Paragraph("一、选育对象与报告范围", styles["heading"]),
        _table([
            ["项目", "内容"],
            ["材料名称", f"{material['material_name']}（{material['material_code']}）"],
            ["所属项目", material["program_name"]],
            ["育种目标", material["breeding_target"]],
            ["建议关注生态区", material.get("target_ecological_zone") or "待育种团队确认"],
            ["数据范围", f"已发布标准施氮优先口径下 {len(context['environments'])} 个试验环境；选育档案为模拟数据"],
        ], [105, 385], styles),
        Paragraph("二、亲本组合及血缘关系", styles["heading"]),
    ]
    role_labels = {"female": "雌亲本", "male": "父本"}
    parent_rows = [["亲本角色", "亲本名称/代码", "亲本来源", "来源记录编号", "血缘关系说明"]]
    for parent in context["parents"]:
        parent_rows.append([
            role_labels.get(parent["parent_role"], parent["parent_role"]),
            f"{parent.get('material_name') or '未补充'}\n{parent.get('material_code') or '-'}",
            parent.get("parent_origin") or "未补充亲本来源",
            parent.get("source_record_no") or "未补充",
            "当前档案记录为杂交亲本；正式材料须附育种者确认的亲本血缘来源和凭证。",
        ])
    story.extend([
        Paragraph("2.1 亲本关系及来源", styles["body"]),
        _table(parent_rows if len(parent_rows) > 1 else [parent_rows[0], ["-", "未补充", "未补充", "未补充", "需补录"]], [52, 82, 120, 74, 162], styles),
        Spacer(1, 5),
        _pedigree_diagram(context["parents"], material),
        Paragraph("2.2 亲本特征特性与互补关系", styles["body"]),
    ])
    parent_trait_rows = [["亲本角色", "关键优势（档案记载）", "与组合的互补关系"]]
    for parent in context["parents"]:
        parent_trait_rows.append([
            role_labels.get(parent["parent_role"], parent["parent_role"]),
            parent.get("parent_trait_summary") or "未补充亲本特征特性记录",
            "见组合选配依据；正式版须以亲本鉴定资料和原始记录为准。",
        ])
    story.append(_table(parent_trait_rows if len(parent_trait_rows) > 1 else [parent_trait_rows[0], ["-", "未补充", "需补录"]], [70, 225, 195], styles))
    combination_basis = "；".join(dict.fromkeys(
        str(item.get("combination_basis")) for item in context["parents"] if item.get("combination_basis")
    )) or "当前未录入组合选择依据。"
    generation_table = _table([
        ["年份", "地点", "世代/阶段", "方法", "结果摘要", "来源记录"],
        *[[item.get("event_year"), item.get("event_site"), item.get("generation_label") or item.get("breeding_stage"), item.get("selection_method"), item.get("result_summary"), item.get("source_note")] for item in context["generations"]],
    ], [34, 62, 73, 96, 149, 76], styles)
    story.extend([
        Paragraph("2.3 组合选择依据", styles["body"]),
        _paragraph_or_default(combination_basis, styles),
        Paragraph("三、选育方法、世代与选择过程", styles["heading"]),
        KeepTogether([Paragraph("3.1 世代与关键阶段", styles["body"]), generation_table]),
        Spacer(1, 5),
        Paragraph("3.2 阶段筛选与保留依据", styles["body"]),
    ])
    decision_labels = {"retain": "保留", "advance": "晋级", "candidate": "候选"}
    selection_rows = [["阶段", "材料数量\n（入选/保留）", "选择性状", "淘汰比例", "保留原因", "原始记录编号"]]
    for item in context["selections"]:
        input_count = _number(item.get("input_material_count"), 0)
        retained_count = _number(item.get("retained_material_count"), 0)
        selection_rows.append([
            item.get("generation_label") or item.get("breeding_stage") or f"{item.get('selection_year')} 年阶段",
            f"{input_count} / {retained_count}（{decision_labels.get(item.get('selection_decision'), item.get('selection_decision') or '-') }）",
            item.get("selection_criterion") or "未补充",
            f"{_number(item.get('elimination_rate'))} %" if item.get("elimination_rate") is not None else "未补充",
            item.get("retention_reason") or item.get("evidence_summary") or "未补充",
            item.get("source_record_no") or "未补充",
        ])
    story.append(_table(selection_rows if len(selection_rows) > 1 else [selection_rows[0], ["未补充", "-", "-", "-", "-", "-"]], [64, 66, 106, 49, 135, 70], styles))
    trial_support_table = _table([
        ["指标", "标准施氮优先汇总值", "数据说明"],
        ["平均产量", f"{_number(averages.get('yield_per_mu'))} kg/亩", "各已发布试验环境的小区重复均值再汇总"],
        ["株高", f"{_number(averages.get('plant_height'))} cm", "仅作区域试验观测汇总，不替代品种特征描述"],
        ["千粒重", f"{_number(averages.get('thousand_grain_weight'))} g", "已发布小区级观测汇总"],
        ["结实率", f"{_number(averages.get('seed_setting_rate'))} %", "已发布小区级观测汇总"],
        ["整精米率", f"{_number(averages.get('head_rice_rate'))} %", "需以统一检测方法和正式送检结果复核"],
        ["垩白度", f"{_number(averages.get('chalkiness_degree'))} %", "需以统一检测方法和正式送检结果复核"],
        ["穗瘟等级", _number(averages.get('panicle_blast_score')), "区域试验观测，非规范抗性鉴定结论"],
        ["倒伏等级", _number(averages.get('lodging_score')), "需结合田间记录与环境条件复核"],
    ], [85, 130, 275], styles)
    story.extend([
        Paragraph("四、特征特性", styles["heading"]),
        Paragraph("4.1 品种描述（当前可形成部分）", styles["body"]),
        _table([["描述维度", "可追溯描述"]] + _trait_description_rows(averages), [100, 390], styles),
        Spacer(1, 5),
        KeepTogether([Paragraph("4.2 区域试验数据支撑", styles["body"]), trial_support_table]),
    ])
    chart = _yield_chart(context["trial_records"])
    if chart:
        story.extend([Spacer(1, 5), KeepTogether([chart, Spacer(1, 4)])])
    story.extend([
        Paragraph("五、标准图片", styles["heading"]),
        _table([
            ["图片类别", "当前档案状态", "正式材料要求"],
            ["植株标准图片", "未上传", "补充田间植株全株图，并记录拍摄时间、地点、材料和生育期。"],
            ["穗部标准图片", "未上传", "补充代表性稻穗图，并关联材料与试验记录。"],
            ["谷粒、糙米与精米图片", "未上传", "补充清晰样品图，并关联品质检测批次。"],
        ], [120, 100, 270], styles),
        _paragraph_or_default("本章节不生成或替代标准图片。当前只显示图片清单与缺口；正式版应上传并审核真实原始图片后再生成报告。", styles),
        Paragraph("六、适宜区域依据", styles["heading"]),
        _paragraph_or_default(
            f"本材料在已发布模拟记录中覆盖的生态区包括：{'、'.join(context['ecological_zones']) or '未记录'}。"
            "这只用于形成下一轮试验布点和区域适应性验证范围，不能直接形成适宜区域结论；正式适宜区域应以符合审定规范的区域试验、生产试验和审核意见为准。",
            styles,
        ),
        Paragraph("七、栽培技术要点", styles["heading"]),
        _paragraph_or_default(
            "当前可追溯记录仅覆盖试验处理与观测结果，未形成播期、密度、水分管理、施肥配方和病虫害防控的系统化栽培档案。"
            "因此本报告不编造具体栽培参数；下一轮试验建议保持标准施氮与较高施氮的成对记录，并补充土壤、气象、病害压力和统一品质检测结果，以形成可验证的技术要点。",
            styles,
        ),
        KeepTogether([
            Paragraph("八、主要缺陷、风险与注意事项", styles["heading"]),
            Paragraph("8.1 基于当前已发布观测的复核事项", styles["body"]),
            *[_paragraph_or_default(f"• {item}", styles) for item in context["risks"]],
            Paragraph("8.2 当前档案的证据缺口", styles["body"]),
            *[_paragraph_or_default(f"• {item}", styles) for item in context["evidence_gaps"]],
        ]),
        Paragraph("九、审定辅助材料完整性检查", styles["heading"]),
        _table([
            ["审定材料要点", "当前演示状态", "正式提交前需补充"],
            ["亲本组合与血缘关系", "已生成来源、关系、组合依据和示意图（模拟）", "亲本来源、系谱凭证和育种者审核签字"],
            ["选育方法、世代和特性描述", "已生成关键阶段、材料数量和来源记录编号（模拟）", "原始选育记录、世代鉴定记录和来源定位"],
            ["品种特征特性与标准图片", "有区域试验支撑；标准图片未上传", "真实品种描述、田间植株、穗部和米粒标准图片及拍摄元数据"],
            ["适宜区域与栽培技术要点", "仅形成验证范围和资料缺口提示", "正式区试、生产试验、栽培验证和技术要点记录"],
            ["主要缺陷、风险与注意事项", "已按当前观测和证据缺口分别列示", "规范抗性、品质、产量和安全性复核结论"],
        ], [145, 145, 200], styles),
        Paragraph("十、数据来源与追溯", styles["heading"]),
    ])
    for evidence in context["evidence"]:
        story.append(_paragraph_or_default(f"{evidence['priority']}. {evidence['title']}：{evidence['detail']}", styles))
    document.build(story)
    return buffer.getvalue()


def _paragraph_or_default(value: str, styles: dict[str, ParagraphStyle]) -> Paragraph:
    return _paragraph(value, styles["body"])
