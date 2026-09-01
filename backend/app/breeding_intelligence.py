"""Evidence-backed germplasm analysis and auxiliary parent recommendation.

The institution database is the source of truth for imported breeding data.
This module reads it without copying business records, builds deterministic
evidence snapshots, and stores only private analysis/recommendation runs in the
main business database.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from itertools import combinations
from typing import Any, Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


ANALYSIS_VERSION = "longyun-germplasm-evidence-v1.0"
RECOMMENDATION_VERSION = "longyun-parent-auxiliary-v1.1"
DEFAULT_RECOMMENDATION_WEIGHTS: dict[str, float] = {
    "yield": 25.0,
    "stability": 20.0,
    "lodging": 10.0,
    "disease": 10.0,
    "complementarity": 10.0,
    "quality": 10.0,
    "pedigree": 10.0,
    "genotype": 5.0,
}
DEFAULT_RECOMMENDATION_FILTERS: dict[str, Any] = {
    "exclude_common_parent": False,
    "minimum_evidence_coverage": 0.0,
    "minimum_confidence": "低",
    "required_dimensions": [],
}
RECOMMENDATION_SORT_MODES = frozenset({"score", "evidence_coverage", "confidence"})
RECOMMENDATION_DIMENSION_LABELS = {
    "yield": "产量",
    "stability": "稳定性",
    "lodging": "抗倒伏",
    "disease": "抗病",
    "complementarity": "株高/生育期互补",
    "quality": "品质",
    "pedigree": "系谱",
    "genotype": "基因型遗传距离",
}
CONFIDENCE_RANK = {"低": 0, "中": 1, "高": 2}


def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def ensure_breeding_intelligence_schema(session: Session) -> None:
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS germplasm_analysis_run (
          id VARCHAR(36) PRIMARY KEY,
          project_id VARCHAR(36) NOT NULL,
          owner_id VARCHAR(120) NOT NULL,
          institution_id VARCHAR(80) NOT NULL,
          material_key VARCHAR(300) NOT NULL,
          analysis_version VARCHAR(80) NOT NULL,
          evidence_snapshot JSONB NOT NULL,
          result_json JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS parent_recommendation_rule (
          project_id VARCHAR(36) PRIMARY KEY,
          weights JSONB NOT NULL,
          filter_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
          sort_mode VARCHAR(40) NOT NULL DEFAULT 'score',
          rule_note TEXT NOT NULL DEFAULT '',
          version INTEGER NOT NULL DEFAULT 1,
          updated_by VARCHAR(120) NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    session.execute(text("ALTER TABLE parent_recommendation_rule ADD COLUMN IF NOT EXISTS filter_settings JSONB NOT NULL DEFAULT '{}'::jsonb"))
    session.execute(text("ALTER TABLE parent_recommendation_rule ADD COLUMN IF NOT EXISTS sort_mode VARCHAR(40) NOT NULL DEFAULT 'score'"))
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS parent_recommendation_run (
          id VARCHAR(36) PRIMARY KEY,
          project_id VARCHAR(36) NOT NULL,
          owner_id VARCHAR(120) NOT NULL,
          institution_id VARCHAR(80) NOT NULL,
          request_json JSONB NOT NULL,
          result_json JSONB NOT NULL,
          rule_version INTEGER NOT NULL,
          engine_version VARCHAR(80) NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_germplasm_analysis_owner_project "
        "ON germplasm_analysis_run(owner_id, project_id, created_at DESC)"
    ))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_parent_recommendation_owner_project "
        "ON parent_recommendation_run(owner_id, project_id, created_at DESC)"
    ))
    session.commit()


def _source_card(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "batch_id": str(row.get("source_batch_id") or row.get("batch_id") or ""),
        "dataset_type": row.get("dataset_type") or "",
        "file_name": row.get("source_file_name") or "",
        "bucket": row.get("object_bucket") or "",
        "object_key": row.get("object_key") or "",
        "file_sha256": row.get("file_sha256") or "",
        "entity_type": row.get("entity_type") or "",
        "entity_key": str(row.get("entity_key") or ""),
    }


def _read_entities(engine: Engine, institution_id: str, project_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    params = {"institution_id": institution_id, "project_id": project_id}
    with engine.connect() as connection:
        entities = [dict(row) for row in connection.execute(text("""
            SELECT entity.entity_type, entity.entity_key, entity.payload,
                   entity.source_batch_id, entity.created_at, entity.updated_at,
                   batch.dataset_type, batch.source_file_name, batch.object_bucket,
                   batch.object_key, batch.file_sha256, batch.status AS batch_status
            FROM data_entity entity
            JOIN ingest_batch batch ON batch.id = entity.source_batch_id
            WHERE entity.institution_id=:institution_id AND entity.project_id=:project_id
            ORDER BY entity.entity_type, entity.entity_key
        """), params).mappings()]
        relations = [dict(row) for row in connection.execute(text("""
            SELECT relation.*, batch.dataset_type, batch.source_file_name,
                   batch.object_bucket, batch.object_key, batch.file_sha256
            FROM data_relation relation
            JOIN ingest_batch batch ON batch.id = relation.source_batch_id
            WHERE relation.institution_id=:institution_id AND relation.project_id=:project_id
            ORDER BY relation.created_at
        """), params).mappings()]
        issues = [dict(row) for row in connection.execute(text("""
            SELECT issue.*, batch.dataset_type, batch.source_file_name,
                   batch.object_bucket, batch.object_key, batch.file_sha256
            FROM data_issue issue
            JOIN ingest_batch batch ON batch.id = issue.source_batch_id
            WHERE issue.institution_id=:institution_id AND issue.project_id=:project_id
              AND issue.resolved=FALSE
            ORDER BY issue.created_at DESC
        """), params).mappings()]
    return _json_safe(entities), _json_safe(relations), _json_safe(issues)


def list_materials(engine: Engine, institution_id: str, project_id: str, query: str = "") -> list[dict[str, Any]]:
    pattern = f"%{query.strip()}%"
    with engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT entity.entity_key, entity.payload, entity.source_batch_id,
                   batch.source_file_name, batch.object_bucket, batch.object_key,
                   batch.file_sha256
            FROM data_entity entity
            JOIN ingest_batch batch ON batch.id=entity.source_batch_id
            WHERE entity.institution_id=:institution_id AND entity.project_id=:project_id
              AND entity.entity_type='germplasm'
              AND (:query='' OR entity.entity_key ILIKE :pattern
                   OR COALESCE(entity.payload->>'name', '') ILIKE :pattern)
            ORDER BY entity.entity_key LIMIT 500
        """), {
            "institution_id": institution_id,
            "project_id": project_id,
            "query": query.strip(),
            "pattern": pattern,
        }).mappings().all()
    return [
        {
            "material_key": str(row["entity_key"]),
            "name": (row["payload"] or {}).get("name") or str(row["entity_key"]),
            "species": (row["payload"] or {}).get("species") or "",
            "origin": (row["payload"] or {}).get("origin") or "",
            "source": _source_card(dict(row)),
        }
        for row in rows
    ]


def build_material_analysis_from_records(
    *,
    institution_id: str,
    project_id: str,
    material_key: str,
    entities: Iterable[dict[str, Any]],
    relations: Iterable[dict[str, Any]],
    issues: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    entity_rows = [dict(item) for item in entities]
    relation_rows = [dict(item) for item in relations]
    issue_rows = [dict(item) for item in issues]
    germplasm = next(
        (
            item for item in entity_rows
            if item.get("entity_type") == "germplasm" and str(item.get("entity_key")) == material_key
        ),
        None,
    )
    if not germplasm:
        raise ValueError("所选种质材料不存在于当前课题的机构数据中。")
    basic = dict(germplasm.get("payload") or {})
    names = {material_key, str(basic.get("name") or "")}
    aliases = basic.get("aliases") or basic.get("alias") or []
    if isinstance(aliases, str):
        aliases = [item.strip() for item in aliases.replace("；", ",").split(",") if item.strip()]
    names.update(str(item) for item in aliases)
    names.discard("")

    pedigree = [
        item for item in entity_rows
        if item.get("entity_type") == "pedigree"
        and (
            str(item.get("entity_key")) == material_key
            or material_key in {
                str((item.get("payload") or {}).get("child_id") or ""),
                str((item.get("payload") or {}).get("female_parent_id") or ""),
                str((item.get("payload") or {}).get("male_parent_id") or ""),
            }
        )
    ]
    phenotypes = [
        item for item in entity_rows
        if item.get("entity_type") == "phenotype_observation"
        and str((item.get("payload") or {}).get("germplasm_id") or "") == material_key
    ]
    environment_ids = {
        str((item.get("payload") or {}).get("environment_id"))
        for item in phenotypes if (item.get("payload") or {}).get("environment_id")
    }
    environments = [
        item for item in entity_rows
        if item.get("entity_type") == "environment" and str(item.get("entity_key")) in environment_ids
    ]
    genotype_samples = [
        item for item in entity_rows
        if item.get("entity_type") == "genotype_sample"
        and (
            str(item.get("entity_key")) == material_key
            or str((item.get("payload") or {}).get("sample_id") or "") == material_key
        )
    ]
    genotype_dataset_ids = {
        str((item.get("payload") or {}).get("dataset_id"))
        for item in genotype_samples if (item.get("payload") or {}).get("dataset_id")
    }
    genotype_datasets = [
        item for item in entity_rows
        if item.get("entity_type") == "genotype_dataset" and str(item.get("entity_key")) in genotype_dataset_ids
    ]
    literature = []
    for item in entity_rows:
        if item.get("entity_type") != "literature_document":
            continue
        payload = item.get("payload") or {}
        haystack = f"{payload.get('file_name', '')}\n{payload.get('text', '')}".lower()
        if any(name.lower() in haystack for name in names if len(name) >= 2):
            excerpt_start = min((haystack.find(name.lower()) for name in names if name.lower() in haystack), default=0)
            raw_text = str(payload.get("text") or "")
            excerpt = raw_text[max(0, excerpt_start - 120):excerpt_start + 380]
            literature.append({**item, "evidence_excerpt": excerpt.strip()})

    related_keys = {material_key}
    related_keys.update(str(item.get("entity_key")) for item in pedigree + phenotypes + genotype_samples)
    linked_relations = [
        item for item in relation_rows
        if str(item.get("source_entity_key")) in related_keys or str(item.get("target_entity_key")) == material_key
    ]
    related_issues = [
        item for item in issue_rows
        if str(item.get("entity_key") or "") in related_keys
        or str(item.get("source_batch_id") or "") in {str(row.get("source_batch_id")) for row in pedigree + phenotypes + genotype_samples}
    ]

    sections = {
        "basic": {"available": True, "records": [basic]},
        "aliases": {"available": bool(aliases), "records": list(aliases)},
        "pedigree": {"available": bool(pedigree), "records": [item.get("payload") or {} for item in pedigree]},
        "phenotype": {"available": bool(phenotypes), "records": [item.get("payload") or {} for item in phenotypes]},
        "environment": {"available": bool(environments), "records": [item.get("payload") or {} for item in environments]},
        "genotype": {
            "available": bool(genotype_samples),
            "records": [item.get("payload") or {} for item in genotype_samples],
            "datasets": [item.get("payload") or {} for item in genotype_datasets],
        },
        "literature": {
            "available": bool(literature),
            "records": [
                {**(item.get("payload") or {}), "text": None, "evidence_excerpt": item.get("evidence_excerpt", "")}
                for item in literature
            ],
        },
    }
    category_labels = {
        "aliases": "别名",
        "pedigree": "系谱",
        "phenotype": "表型/试验",
        "environment": "环境",
        "genotype": "基因型",
        "literature": "相关资料",
    }
    missing = [label for key, label in category_labels.items() if not sections[key]["available"]]
    sources_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    evidence_rows = [germplasm, *pedigree, *phenotypes, *environments, *genotype_samples, *genotype_datasets, *literature]
    for row in evidence_rows:
        source = _source_card(row)
        sources_by_identity[(source["batch_id"], source["entity_key"])] = source
    uncertainties = [str(item.get("message")) for item in related_issues if item.get("message")]
    if missing:
        uncertainties.append(f"当前未导入或未关联：{'、'.join(missing)}；相应结论不生成。")
    summary_parts = [f"已汇总种质 {basic.get('name') or material_key}（{material_key}）的可用证据。"]
    if phenotypes:
        summary_parts.append(f"包含 {len(phenotypes)} 条表型/试验观测。")
    if pedigree:
        summary_parts.append(f"包含 {len(pedigree)} 条相关系谱记录。")
    if literature:
        summary_parts.append(f"在 {len(literature)} 份资料中定位到名称或编号证据。")
    return {
        "analysis_version": ANALYSIS_VERSION,
        "institution_id": institution_id,
        "project_id": project_id,
        "material_key": material_key,
        "material_name": basic.get("name") or material_key,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": "".join(summary_parts),
        "sections": sections,
        "missing_categories": missing,
        "uncertainties": uncertainties or ["未发现导入层记录的未解决异常；结论仍仅适用于当前已导入数据。"],
        "relations": linked_relations,
        "issues": related_issues,
        "sources": list(sources_by_identity.values()),
        "evidence_counts": {key: len(value.get("records", [])) for key, value in sections.items()},
    }


def build_material_analysis(engine: Engine, institution_id: str, project_id: str, material_key: str) -> dict[str, Any]:
    entities, relations, issues = _read_entities(engine, institution_id, project_id)
    return build_material_analysis_from_records(
        institution_id=institution_id,
        project_id=project_id,
        material_key=material_key,
        entities=entities,
        relations=relations,
        issues=issues,
    )


def save_material_analysis(session: Session, owner_id: str, result: dict[str, Any]) -> str:
    run_id = str(uuid.uuid4())
    evidence = {
        "sources": result.get("sources", []),
        "relations": result.get("relations", []),
        "issues": result.get("issues", []),
    }
    session.execute(text("""
        INSERT INTO germplasm_analysis_run (
          id, project_id, owner_id, institution_id, material_key,
          analysis_version, evidence_snapshot, result_json
        ) VALUES (
          :id, :project_id, :owner_id, :institution_id, :material_key,
          :analysis_version, CAST(:evidence AS jsonb), CAST(:result AS jsonb)
        )
    """), {
        "id": run_id,
        "project_id": result["project_id"],
        "owner_id": owner_id,
        "institution_id": result["institution_id"],
        "material_key": result["material_key"],
        "analysis_version": ANALYSIS_VERSION,
        "evidence": json.dumps(_json_safe(evidence), ensure_ascii=False),
        "result": json.dumps(_json_safe(result), ensure_ascii=False),
    })
    session.commit()
    return run_id


def get_material_analysis_run(session: Session, run_id: str, owner_id: str, project_id: str) -> dict[str, Any] | None:
    row = session.execute(text("""
        SELECT id, result_json, created_at FROM germplasm_analysis_run
        WHERE id=:id AND owner_id=:owner_id AND project_id=:project_id
    """), {"id": run_id, "owner_id": owner_id, "project_id": project_id}).mappings().first()
    if not row:
        return None
    result = dict(row["result_json"] or {})
    result["run_id"] = str(row["id"])
    result["created_at"] = _json_safe(row["created_at"])
    return result


def get_recommendation_rule(session: Session, project_id: str) -> dict[str, Any]:
    row = session.execute(text("""
        SELECT weights, filter_settings, sort_mode, rule_note, version, updated_by, updated_at
        FROM parent_recommendation_rule WHERE project_id=:project_id
    """), {"project_id": project_id}).mappings().first()
    if not row:
        return {
            "project_id": project_id,
            "weights": DEFAULT_RECOMMENDATION_WEIGHTS,
            "filter_settings": DEFAULT_RECOMMENDATION_FILTERS,
            "sort_mode": "score",
            "rule_note": "默认辅助推荐权重；缺失的证据维度不参与得分并降低可信程度。",
            "version": 1,
            "updated_by": "system-default",
            "updated_at": None,
        }
    result = {"project_id": project_id, **_json_safe(dict(row))}
    result["filter_settings"] = _normalize_filter_settings(result.get("filter_settings"))
    if result.get("sort_mode") not in RECOMMENDATION_SORT_MODES:
        result["sort_mode"] = "score"
    return result


def _validate_weights(weights: dict[str, float]) -> dict[str, float]:
    unknown = sorted(set(weights) - set(DEFAULT_RECOMMENDATION_WEIGHTS))
    if unknown:
        raise ValueError(f"未知权重维度：{'、'.join(unknown)}。")
    merged = {**DEFAULT_RECOMMENDATION_WEIGHTS, **{key: float(value) for key, value in weights.items()}}
    if any(not math.isfinite(value) or value < 0 or value > 100 for value in merged.values()) or sum(merged.values()) <= 0:
        raise ValueError("各项权重必须是 0–100 之间的有限数值，且总和必须大于 0。")
    return merged


def _normalize_filter_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    raw = {**DEFAULT_RECOMMENDATION_FILTERS, **(settings or {})}
    coverage = _number(raw.get("minimum_evidence_coverage"))
    if coverage is None or coverage < 0 or coverage > 1:
        raise ValueError("最低证据覆盖率必须在 0–1 之间。")
    confidence = str(raw.get("minimum_confidence") or "低")
    if confidence not in CONFIDENCE_RANK:
        raise ValueError("最低可信程度只能是低、中或高。")
    dimensions = list(dict.fromkeys(str(item).strip() for item in (raw.get("required_dimensions") or []) if str(item).strip()))
    unknown = sorted(set(dimensions) - set(DEFAULT_RECOMMENDATION_WEIGHTS))
    if unknown:
        raise ValueError(f"未知必需证据维度：{'、'.join(unknown)}。")
    return {
        "exclude_common_parent": bool(raw.get("exclude_common_parent")),
        "minimum_evidence_coverage": float(coverage),
        "minimum_confidence": confidence,
        "required_dimensions": dimensions,
    }


def update_recommendation_rule(
    session: Session,
    project_id: str,
    weights: dict[str, float],
    rule_note: str,
    updated_by: str,
    filter_settings: dict[str, Any] | None = None,
    sort_mode: str = "score",
) -> dict[str, Any]:
    merged = _validate_weights(weights)
    normalized_filters = _normalize_filter_settings(filter_settings)
    if sort_mode not in RECOMMENDATION_SORT_MODES:
        raise ValueError("排序方式只能是综合评分、证据覆盖率或可信程度。")
    session.execute(text("""
        INSERT INTO parent_recommendation_rule(project_id, weights, filter_settings, sort_mode, rule_note, version, updated_by)
        VALUES (:project_id, CAST(:weights AS jsonb), CAST(:filter_settings AS jsonb), :sort_mode, :rule_note, 1, :updated_by)
        ON CONFLICT (project_id) DO UPDATE SET
          weights=EXCLUDED.weights, filter_settings=EXCLUDED.filter_settings,
          sort_mode=EXCLUDED.sort_mode, rule_note=EXCLUDED.rule_note,
          version=parent_recommendation_rule.version + 1,
          updated_by=EXCLUDED.updated_by, updated_at=now()
    """), {
        "project_id": project_id,
        "weights": json.dumps(merged, ensure_ascii=False),
        "filter_settings": json.dumps(normalized_filters, ensure_ascii=False),
        "sort_mode": sort_mode,
        "rule_note": rule_note.strip(),
        "updated_by": updated_by,
    })
    session.commit()
    return get_recommendation_rule(session, project_id)


def _trait_bucket(code: str) -> str | None:
    value = code.lower().replace(" ", "")
    if any(word in value for word in ("yield", "产量", "亩产")):
        return "yield"
    if any(word in value for word in ("lodg", "倒伏")):
        return "lodging"
    if any(word in value for word in ("disease", "blast", "稻瘟", "病害", "抗病")):
        return "disease"
    if any(word in value for word in ("height", "株高", "duration", "生育期", "成熟期")):
        return "complementarity"
    if any(word in value for word in ("quality", "米质", "整精米", "垩白", "蛋白", "直链淀粉")):
        return "quality"
    return None


def _candidate_profiles(entities: list[dict[str, Any]], candidate_keys: list[str]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {
        key: {
            "material_key": key,
            "name": key,
            "traits": defaultdict(list),
            "trait_evidence": defaultdict(list),
            "parents": set(),
            "genotype_sample": False,
            "sources": [],
        }
        for key in candidate_keys
    }
    datasets = {str(item.get("entity_key")): item for item in entities if item.get("entity_type") == "genotype_dataset"}
    for item in entities:
        kind = item.get("entity_type")
        key = str(item.get("entity_key"))
        payload = item.get("payload") or {}
        if kind == "germplasm" and key in profiles:
            profiles[key]["name"] = payload.get("name") or key
            profiles[key]["sources"].append(_source_card(item))
        elif kind == "phenotype_observation":
            material = str(payload.get("germplasm_id") or "")
            if material not in profiles:
                continue
            bucket = _trait_bucket(str(payload.get("trait_code") or ""))
            numeric = _number(payload.get("value"))
            if bucket and numeric is not None:
                trait_code = str(payload.get("trait_code") or "")
                scoring_value = -numeric if bucket == "quality" and any(
                    marker in trait_code.lower() for marker in ("chalk", "垩白")
                ) else numeric
                profiles[material]["traits"][bucket].append(scoring_value)
                profiles[material]["trait_evidence"][bucket].append({
                    "trait_code": trait_code,
                    "value": numeric,
                    "unit": payload.get("unit") or "",
                    "trial_id": payload.get("trial_id") or "",
                    "environment_id": payload.get("environment_id") or "",
                    "source": _source_card(item),
                })
        elif kind == "pedigree":
            child = str(payload.get("child_id") or key)
            if child in profiles:
                profiles[child]["parents"].update(
                    str(value) for value in (payload.get("female_parent_id"), payload.get("male_parent_id")) if value
                )
                profiles[child]["sources"].append(_source_card(item))
        elif kind == "genotype_sample" and key in profiles:
            dataset = datasets.get(str(payload.get("dataset_id") or ""))
            profiles[key]["genotype_sample"] = bool(dataset)
            profiles[key]["sources"].append(_source_card(item))
    return profiles


def _candidate_norms(profiles: dict[str, dict[str, Any]]) -> dict[str, tuple[float, float]]:
    output: dict[str, tuple[float, float]] = {}
    for bucket in ("yield", "lodging", "disease", "quality", "complementarity"):
        values = [
            statistics.mean(profile["traits"][bucket])
            for profile in profiles.values() if profile["traits"].get(bucket)
        ]
        if values:
            output[bucket] = (min(values), max(values))
    return output


def _normalize(value: float, low: float, high: float, inverse: bool = False) -> float:
    result = 0.75 if high == low else (value - low) / (high - low)
    return max(0.0, min(1.0, 1.0 - result if inverse else result))


def _apply_text_constraints(
    constraints: list[str],
    weights: dict[str, float],
    filter_settings: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any], list[str], list[str], dict[str, float]]:
    """Translate a small, explicit constraint vocabulary into deterministic rules.

    Anything outside this vocabulary is returned for manual review instead of
    being silently claimed as applied.
    """
    applied: list[str] = []
    manual_review: list[str] = []
    adjusted_weights = dict(weights)
    settings = dict(filter_settings)
    required = list(settings.get("required_dimensions") or [])
    weight_adjustments: dict[str, float] = {}
    dimension_terms = {
        "yield": ("产量", "高产"),
        "stability": ("稳定", "稳产"),
        "lodging": ("抗倒伏", "倒伏"),
        "disease": ("抗病", "病害", "稻瘟"),
        "complementarity": ("互补", "株高", "生育期"),
        "quality": ("品质", "米质"),
        "pedigree": ("系谱",),
        "genotype": ("基因型", "遗传距离"),
    }
    for constraint in constraints:
        compact = constraint.replace(" ", "")
        recognized = False
        if any(term in compact for term in ("避免共同亲本", "排除共同亲本", "无共同亲本", "避免近缘", "排除近缘")):
            settings["exclude_common_parent"] = True
            applied.append(f"已自动执行：{constraint}（排除现有系谱显示共同亲本的组合）")
            recognized = True
        coverage_match = re.search(r"(?:证据覆盖|覆盖率)(?:至少|不低于|>=?)?\s*(\d{1,3})\s*%", constraint)
        if coverage_match:
            coverage = int(coverage_match.group(1)) / 100
            if coverage > 1:
                raise ValueError("限制条件中的证据覆盖率不能超过 100%。")
            settings["minimum_evidence_coverage"] = max(float(settings["minimum_evidence_coverage"]), coverage)
            applied.append(f"已自动执行：证据覆盖率至少 {coverage:.0%}")
            recognized = True
        if any(term in compact for term in ("至少中可信", "可信程度至少中", "不接受低可信")):
            settings["minimum_confidence"] = "中"
            applied.append("已自动执行：可信程度至少为中")
            recognized = True
        if any(term in compact for term in ("仅高可信", "必须高可信", "可信程度为高")):
            settings["minimum_confidence"] = "高"
            applied.append("已自动执行：可信程度必须为高")
            recognized = True
        for key, terms in dimension_terms.items():
            if any(f"必须有{term}" in compact or f"需要{term}证据" in compact for term in terms):
                if key not in required:
                    required.append(key)
                applied.append(f"已自动执行：必须具备{RECOMMENDATION_DIMENSION_LABELS[key]}证据")
                recognized = True
            if any(f"优先{term}" in compact for term in terms):
                adjusted_weights[key] = min(100.0, float(adjusted_weights.get(key, 0)) * 1.5)
                weight_adjustments[key] = adjusted_weights[key]
                applied.append(f"已自动执行：提高{RECOMMENDATION_DIMENSION_LABELS[key]}权重至 {adjusted_weights[key]:g}")
                recognized = True
        if not recognized:
            manual_review.append(constraint)
    settings["required_dimensions"] = required
    return adjusted_weights, _normalize_filter_settings(settings), applied, manual_review, weight_adjustments


def rank_parent_combinations(
    profiles: dict[str, dict[str, Any]],
    weights: dict[str, float],
    breeding_goal: str,
    constraints: list[str] | None = None,
    filter_settings: dict[str, Any] | None = None,
    sort_mode: str = "score",
) -> dict[str, Any]:
    if len(profiles) < 2:
        raise ValueError("至少选择 2 个候选亲本。")
    weights = _validate_weights(weights)
    if sort_mode not in RECOMMENDATION_SORT_MODES:
        raise ValueError("排序方式只能是综合评分、证据覆盖率或可信程度。")
    norms = _candidate_norms(profiles)
    constraints = [item.strip() for item in (constraints or []) if item.strip()]
    configured_filters = _normalize_filter_settings(filter_settings)
    weights, configured_filters, applied_constraints, manual_constraints, weight_adjustments = _apply_text_constraints(
        constraints, weights, configured_filters,
    )
    ranked: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for left_key, right_key in combinations(profiles, 2):
        left, right = profiles[left_key], profiles[right_key]
        dimension_scores: dict[str, float] = {}
        reasons: list[str] = []
        risks: list[str] = []
        gaps: list[str] = []
        evidence: list[dict[str, Any]] = [
            {"evidence_type": "governed_source_record", "material_key": profile["material_key"], "source": source}
            for profile in (left, right) for source in profile.get("sources", [])
        ]
        for bucket in ("yield", "lodging", "disease", "quality"):
            left_values, right_values = left["traits"].get(bucket, []), right["traits"].get(bucket, [])
            if not left_values or not right_values or bucket not in norms:
                gaps.append(f"缺少双方可比的{ {'yield':'产量','lodging':'倒伏','disease':'病害','quality':'品质'}[bucket] }证据")
                continue
            low, high = norms[bucket]
            pair_value = statistics.mean([statistics.mean(left_values), statistics.mean(right_values)])
            inverse = bucket in {"lodging", "disease"}
            dimension_scores[bucket] = _normalize(pair_value, low, high, inverse=inverse)
            reasons.append(f"{bucket} 维度使用双方已导入观测均值计算")
            evidence.extend(left["trait_evidence"].get(bucket, []) + right["trait_evidence"].get(bucket, []))

        yield_values = left["traits"].get("yield", []) + right["traits"].get("yield", [])
        if len(yield_values) >= 4 and statistics.mean(yield_values) != 0:
            cv = statistics.pstdev(yield_values) / abs(statistics.mean(yield_values))
            dimension_scores["stability"] = max(0.0, min(1.0, 1.0 - cv))
            reasons.append(f"多条产量观测的变异系数为 {cv:.3f}，用于稳产维度")
        else:
            gaps.append("产量观测不足，无法可靠计算稳产性")

        complement_values = [
            statistics.mean(values)
            for values in (left["traits"].get("complementarity", []), right["traits"].get("complementarity", []))
            if values
        ]
        if len(complement_values) == 2 and "complementarity" in norms:
            low, high = norms["complementarity"]
            span = max(high - low, 1.0)
            dimension_scores["complementarity"] = min(1.0, abs(complement_values[0] - complement_values[1]) / span)
            reasons.append("按已导入株高/生育期类观测差异评价互补性")
        else:
            gaps.append("缺少双方可比的株高或生育期互补证据")

        common_parents = sorted(left["parents"] & right["parents"])
        if left["parents"] or right["parents"]:
            dimension_scores["pedigree"] = 0.2 if common_parents else 1.0
            if common_parents:
                risks.append(f"双方存在共同亲本：{'、'.join(common_parents)}，需关注近缘风险")
            else:
                reasons.append("现有系谱记录未发现共同亲本")
        else:
            gaps.append("缺少一方或双方系谱，不能完整评估近缘风险")

        if left["genotype_sample"] and right["genotype_sample"]:
            gaps.append("双方已有基因型样本，但当前导入层未提供可计算遗传距离的标记矩阵，该维度不计分")
        else:
            gaps.append("缺少双方基因型样本或遗传距离证据")

        effective = {key: float(weights.get(key, 0)) for key in dimension_scores if float(weights.get(key, 0)) > 0}
        denominator = sum(effective.values())
        score = sum(dimension_scores[key] * weight for key, weight in effective.items()) / denominator * 100 if denominator else 0.0
        total_configured = sum(float(value) for value in weights.values() if float(value) > 0)
        evidence_coverage = denominator / total_configured if total_configured else 0.0
        if evidence_coverage >= 0.75 and len(evidence) >= 4:
            confidence = "高"
        elif evidence_coverage >= 0.45:
            confidence = "中"
        else:
            confidence = "低"
        if confidence != "高":
            risks.append(f"证据权重覆盖率为 {evidence_coverage:.0%}，推荐可信程度为{confidence}")
        if manual_constraints:
            risks.append(f"以下自由文本限制无法由现有结构化数据自动判定，需人工复核：{'、'.join(manual_constraints)}")
        item = {
            "female_parent": {"material_key": left_key, "name": left["name"]},
            "male_parent": {"material_key": right_key, "name": right["name"]},
            "score": round(score, 2),
            "confidence": confidence,
            "evidence_coverage": round(evidence_coverage, 4),
            "dimension_scores": {key: round(value * 100, 2) for key, value in dimension_scores.items()},
            "recommendation_reasons": reasons or ["没有足够的已导入证据形成正向推荐理由"],
            "risks": risks or ["未从当前规则识别显式冲突；仍需育种专家复核"],
            "data_gaps": gaps,
            "evidence": evidence[:50],
        }
        exclusion_reasons = []
        if configured_filters["exclude_common_parent"] and common_parents:
            exclusion_reasons.append(f"存在共同亲本：{'、'.join(common_parents)}")
        if evidence_coverage < configured_filters["minimum_evidence_coverage"]:
            exclusion_reasons.append(
                f"证据覆盖率 {evidence_coverage:.0%} 低于最低要求 {configured_filters['minimum_evidence_coverage']:.0%}"
            )
        if CONFIDENCE_RANK[confidence] < CONFIDENCE_RANK[configured_filters["minimum_confidence"]]:
            exclusion_reasons.append(f"可信程度 {confidence} 低于最低要求 {configured_filters['minimum_confidence']}")
        missing_required = [
            key for key in configured_filters["required_dimensions"] if key not in dimension_scores
        ]
        if missing_required:
            exclusion_reasons.append(
                "缺少必需证据维度：" + "、".join(RECOMMENDATION_DIMENSION_LABELS[key] for key in missing_required)
            )
        if exclusion_reasons:
            excluded.append({
                "female_parent": item["female_parent"],
                "male_parent": item["male_parent"],
                "reasons": exclusion_reasons,
            })
        else:
            ranked.append(item)
    common_tail = lambda item: (item["female_parent"]["material_key"], item["male_parent"]["material_key"])
    if sort_mode == "evidence_coverage":
        ranked.sort(key=lambda item: (-item["evidence_coverage"], -item["score"], *common_tail(item)))
    elif sort_mode == "confidence":
        ranked.sort(key=lambda item: (-CONFIDENCE_RANK[item["confidence"]], -item["score"], *common_tail(item)))
    else:
        ranked.sort(key=lambda item: (-item["score"], -item["evidence_coverage"], *common_tail(item)))
    selection_warning = ""
    if not ranked:
        selection_warning = "所有候选组合均被当前筛选条件排除；系统未生成不符合约束的推荐，请放宽条件或补充证据。"
    all_sources: dict[tuple[str, str, str], dict[str, Any]] = {}
    for profile in profiles.values():
        for source in profile.get("sources", []):
            identity = (
                str(source.get("batch_id") or ""),
                str(source.get("entity_type") or ""),
                str(source.get("entity_key") or ""),
            )
            all_sources[identity] = source
    return {
        "title": "亲本组合辅助推荐",
        "disclaimer": "本结果为辅助推荐，不是确定性预测结论；必须结合育种专家判断和后续试验验证。",
        "breeding_goal": breeding_goal.strip(),
        "constraints": constraints,
        "applied_constraints": applied_constraints,
        "manual_review_constraints": manual_constraints,
        "filter_settings": configured_filters,
        "sort_mode": sort_mode,
        "sort_mode_label": {"score": "综合评分", "evidence_coverage": "证据覆盖率", "confidence": "可信程度"}[sort_mode],
        "excluded_combinations": excluded,
        "selection_warning": selection_warning,
        "constraint_weight_adjustments": weight_adjustments,
        "weights": weights,
        "sources": list(all_sources.values()),
        "method": "按当前课题及本次请求配置的筛选条件、权重和排序方式，对实际存在的证据维度归一化后排序；缺失维度不虚构、不计分并降低可信程度。",
        "engine_version": RECOMMENDATION_VERSION,
        "recommendations": ranked,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_parent_recommendation(
    engine: Engine,
    institution_id: str,
    project_id: str,
    candidate_keys: list[str],
    weights: dict[str, float],
    breeding_goal: str,
    constraints: list[str] | None = None,
    filter_settings: dict[str, Any] | None = None,
    sort_mode: str = "score",
) -> dict[str, Any]:
    entities, _, _ = _read_entities(engine, institution_id, project_id)
    profiles = _candidate_profiles(entities, candidate_keys)
    missing = [key for key, profile in profiles.items() if not profile["sources"]]
    if missing:
        raise ValueError(f"候选亲本不存在于当前课题：{'、'.join(missing)}。")
    return rank_parent_combinations(
        profiles, weights, breeding_goal, constraints,
        filter_settings=filter_settings, sort_mode=sort_mode,
    )


def save_parent_recommendation(
    session: Session,
    *,
    owner_id: str,
    institution_id: str,
    project_id: str,
    request_payload: dict[str, Any],
    result: dict[str, Any],
    rule_version: int,
) -> str:
    run_id = str(uuid.uuid4())
    session.execute(text("""
        INSERT INTO parent_recommendation_run (
          id, project_id, owner_id, institution_id, request_json,
          result_json, rule_version, engine_version
        ) VALUES (
          :id, :project_id, :owner_id, :institution_id, CAST(:request AS jsonb),
          CAST(:result AS jsonb), :rule_version, :engine_version
        )
    """), {
        "id": run_id,
        "project_id": project_id,
        "owner_id": owner_id,
        "institution_id": institution_id,
        "request": json.dumps(_json_safe(request_payload), ensure_ascii=False),
        "result": json.dumps(_json_safe(result), ensure_ascii=False),
        "rule_version": rule_version,
        "engine_version": RECOMMENDATION_VERSION,
    })
    session.commit()
    return run_id


def get_parent_recommendation_run(session: Session, run_id: str, owner_id: str, project_id: str) -> dict[str, Any] | None:
    row = session.execute(text("""
        SELECT id, result_json, request_json, rule_version, created_at
        FROM parent_recommendation_run
        WHERE id=:id AND owner_id=:owner_id AND project_id=:project_id
    """), {"id": run_id, "owner_id": owner_id, "project_id": project_id}).mappings().first()
    if not row:
        return None
    return {
        "run_id": str(row["id"]),
        "request": row["request_json"] or {},
        "rule_version": row["rule_version"],
        "created_at": _json_safe(row["created_at"]),
        **dict(row["result_json"] or {}),
    }


def recommendation_csv(result: dict[str, Any]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["声明", result.get("disclaimer", "辅助推荐")])
    writer.writerow(["育种目标", result.get("breeding_goal", "")])
    writer.writerow(["筛选条件", json.dumps(result.get("filter_settings") or {}, ensure_ascii=False)])
    writer.writerow(["排序方式", result.get("sort_mode_label") or result.get("sort_mode") or "综合评分"])
    writer.writerow(["评分权重", json.dumps(result.get("weights") or {}, ensure_ascii=False)])
    writer.writerow(["已执行自由文本条件", "；".join(result.get("applied_constraints") or [])])
    writer.writerow(["需人工复核条件", "；".join(result.get("manual_review_constraints") or [])])
    writer.writerow(["排除组合数", len(result.get("excluded_combinations") or [])])
    writer.writerow(["排序", "母本编号", "母本名称", "父本编号", "父本名称", "评分", "可信程度", "证据覆盖率", "推荐理由", "风险", "数据缺口"])
    for index, item in enumerate(result.get("recommendations", []), start=1):
        writer.writerow([
            index,
            item["female_parent"]["material_key"], item["female_parent"]["name"],
            item["male_parent"]["material_key"], item["male_parent"]["name"],
            item["score"], item["confidence"], f"{item['evidence_coverage']:.0%}",
            "；".join(item.get("recommendation_reasons", [])),
            "；".join(item.get("risks", [])),
            "；".join(item.get("data_gaps", [])),
        ])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _pdf_font() -> str:
    name = "STSong-Light"
    try:
        pdfmetrics.getFont(name)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(name))
    return name


def build_intelligence_pdf(title: str, result: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    font = _pdf_font()
    styles = getSampleStyleSheet()
    for style_name in ("Title", "Heading1", "Heading2", "BodyText"):
        styles[style_name].fontName = font
        styles[style_name].wordWrap = "CJK"
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=title)
    story: list[Any] = [Paragraph(title, styles["Title"]), Spacer(1, 5 * mm)]
    if result.get("disclaimer"):
        story.extend([Paragraph(str(result["disclaimer"]), styles["BodyText"]), Spacer(1, 3 * mm)])
    if result.get("summary"):
        story.extend([Paragraph("综合结论", styles["Heading1"]), Paragraph(str(result["summary"]), styles["BodyText"])])
    if result.get("recommendations") is not None:
        story.extend([
            Paragraph("育种目标与方法", styles["Heading1"]),
            Paragraph(str(result.get("breeding_goal") or "未填写"), styles["BodyText"]),
            Paragraph(str(result.get("method") or ""), styles["BodyText"]),
            Paragraph(f"筛选条件：{json.dumps(result.get('filter_settings') or {}, ensure_ascii=False)}", styles["BodyText"]),
            Paragraph(f"排序方式：{result.get('sort_mode_label') or result.get('sort_mode') or '综合评分'}", styles["BodyText"]),
            Paragraph(f"评分权重：{json.dumps(result.get('weights') or {}, ensure_ascii=False)}", styles["BodyText"]),
        ])
        if result.get("applied_constraints"):
            story.append(Paragraph(f"已自动执行的自由文本条件：{'；'.join(result['applied_constraints'])}", styles["BodyText"]))
        if result.get("manual_review_constraints"):
            story.append(Paragraph(f"需人工复核、未声称自动执行：{'；'.join(result['manual_review_constraints'])}", styles["BodyText"]))
        if result.get("selection_warning"):
            story.append(Paragraph(str(result["selection_warning"]), styles["BodyText"]))
        for index, item in enumerate(result.get("recommendations", []), start=1):
            story.append(Paragraph(f"{index}. {item['female_parent']['name']} × {item['male_parent']['name']}（{item['score']} 分，可信程度 {item['confidence']}）", styles["Heading2"]))
            for heading, values in (("推荐理由", item.get("recommendation_reasons", [])), ("风险", item.get("risks", [])), ("数据缺口", item.get("data_gaps", []))):
                story.append(Paragraph(f"{heading}：{'；'.join(values) or '无'}", styles["BodyText"]))
    else:
        section_labels = {"basic": "基本信息", "aliases": "别名", "pedigree": "系谱", "phenotype": "表型/试验", "environment": "环境", "genotype": "基因型", "literature": "相关资料"}
        for key, label in section_labels.items():
            section = (result.get("sections") or {}).get(key) or {}
            story.append(Paragraph(label, styles["Heading1"]))
            if not section.get("available"):
                story.append(Paragraph("当前数据中缺失，未生成推断。", styles["BodyText"]))
                continue
            rows = section.get("records") or []
            for row in rows[:20]:
                story.append(Paragraph(json.dumps(row, ensure_ascii=False, default=str) if isinstance(row, dict) else str(row), styles["BodyText"]))
        story.append(Paragraph("不确定性与数据缺口", styles["Heading1"]))
        for item in result.get("uncertainties", []):
            story.append(Paragraph(f"• {item}", styles["BodyText"]))
    story.append(PageBreak())
    story.append(Paragraph("可追溯数据来源", styles["Heading1"]))
    sources = result.get("sources") or []
    if not sources and result.get("recommendations"):
        for item in result["recommendations"]:
            for evidence in item.get("evidence", []):
                if evidence.get("source"):
                    sources.append(evidence["source"])
    table_rows = [["批次", "数据类型", "文件", "实体"]]
    seen: set[tuple[str, str]] = set()
    for item in sources:
        identity = (str(item.get("batch_id") or ""), str(item.get("entity_key") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        table_rows.append([identity[0], item.get("dataset_type") or "", item.get("file_name") or "", f"{item.get('entity_type') or ''}:{identity[1]}"])
    if len(table_rows) == 1:
        table_rows.append(["-", "-", "没有可列示来源", "-"])
    table = Table(table_rows[:81], colWidths=[40 * mm, 30 * mm, 62 * mm, 45 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font), ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEFE8")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#8AA69C")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    document.build(story)
    return buffer.getvalue()
