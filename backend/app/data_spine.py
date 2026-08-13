"""Unified project data intake, lineage and feature-readiness services.

This module is intentionally independent of any one spreadsheet layout.  An
institution may map its own columns, while the platform still records which
business domain a file belongs to, which project may use it, and which product
capabilities have enough published evidence to run.
"""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from .object_storage import StoredObject


DATA_DOMAIN_LABELS: dict[str, str] = {
    "germplasm": "种质资源",
    "pedigree": "系谱关系",
    "phenotype": "表型观测",
    "environment": "环境数据",
    "management": "田间管理",
    "genotype": "基因型数据",
    "trial": "试验设计与试验单元",
    "literature": "文献与行业情报",
    "mixed": "混合资料包",
}


@dataclass(frozen=True)
class FeatureRequirement:
    code: str
    name: str
    required: tuple[str, ...]
    recommended: tuple[str, ...]
    missing_effects: dict[str, str]


FEATURE_REQUIREMENTS: dict[str, FeatureRequirement] = {
    "germplasm_intelligence": FeatureRequirement(
        code="germplasm_intelligence",
        name="种质资源智能解析",
        required=("germplasm",),
        recommended=("pedigree", "phenotype", "genotype"),
        missing_effects={
            "germplasm": "无法建立材料主档，也无法把同一材料在不同文件中的记录归并。",
            "pedigree": "无法解释亲缘来源、祖先贡献和近交风险。",
            "phenotype": "只能解释材料身份，不能总结多年多点性状表现。",
            "genotype": "不能把材料与分子标记、关键等位基因或候选基因关联。",
        },
    ),
    "parent_selection_assistance": FeatureRequirement(
        code="parent_selection_assistance",
        name="亲本组合辅助推荐",
        required=("germplasm", "pedigree", "phenotype"),
        recommended=("genotype", "environment", "trial"),
        missing_effects={
            "germplasm": "无法识别候选亲本及其稳定编号。",
            "pedigree": "无法排查近亲组合和估计亲缘互补性。",
            "phenotype": "无法依据育种目标比较亲本的性状优势与短板。",
            "genotype": "推荐只能基于系谱和表型，不能利用标记、等位基因和遗传距离。",
            "environment": "不能判断亲本表现是否受目标生态区环境影响。",
            "trial": "缺少试验设计上下文，跨年跨点结果的可比性会降低。",
        },
    ),
    "trial_analysis": FeatureRequirement(
        code="trial_analysis",
        name="田间试验数据自动分析",
        required=("trial", "phenotype"),
        recommended=("environment", "management", "germplasm"),
        missing_effects={
            "trial": "无法识别地点、年份、处理、重复和小区，不能进行正式统计分析。",
            "phenotype": "没有可计算的性状观测值。",
            "environment": "无法解释气象、土壤和地点差异造成的环境效应。",
            "management": "无法区分施肥、灌溉等管理措施带来的影响。",
            "germplasm": "结果只能保留原始材料名称，不能稳定回连种质档案。",
        },
    ),
    "literature_intelligence": FeatureRequirement(
        code="literature_intelligence",
        name="育种文献与行业情报智能挖掘",
        required=("literature",),
        recommended=("germplasm", "phenotype", "genotype", "trial"),
        missing_effects={
            "literature": "没有可检索、可引用的授权文献或情报资料。",
            "germplasm": "文献中的材料名称无法稳定回连院内种质档案。",
            "phenotype": "不能把文献结论与院内真实性状表现对照。",
            "genotype": "不能把论文中的基因、标记和等位基因与院内材料关联。",
            "trial": "不能将文献结论放入本院具体试验设计和生态区背景中解释。",
        },
    ),
}


IMPORT_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"uploading", "cancelled"}),
    "uploading": frozenset({"validating", "failed", "cancelled"}),
    "validating": frozenset({"ready", "failed", "cancelled"}),
    "ready": frozenset({"validating", "published", "cancelled"}),
    "published": frozenset(),
    "failed": frozenset({"validating", "cancelled"}),
    "cancelled": frozenset(),
}


def require_data_domain(value: str) -> str:
    domain = (value or "").strip().lower()
    if domain not in DATA_DOMAIN_LABELS:
        raise ValueError(f"unsupported data domain: {value}")
    return domain


def feature_readiness(
    available_domains: Iterable[str],
    evidence_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Calculate deterministic readiness without invoking a language model."""
    available = frozenset(require_data_domain(item) for item in available_domains)
    evidence = evidence_summary or {}
    results: list[dict[str, Any]] = []
    for requirement in FEATURE_REQUIREMENTS.values():
        missing_required = [item for item in requirement.required if item not in available]
        missing_recommended = [item for item in requirement.recommended if item not in available]
        required_ratio = (
            (len(requirement.required) - len(missing_required)) / len(requirement.required)
            if requirement.required
            else 1.0
        )
        recommended_ratio = (
            (len(requirement.recommended) - len(missing_recommended)) / len(requirement.recommended)
            if requirement.recommended
            else 1.0
        )
        score = round((required_ratio * 0.8 + recommended_ratio * 0.2) * 100, 1)
        if missing_required:
            status = "blocked"
        elif missing_recommended:
            status = "basic_ready"
        else:
            status = "fully_ready"
        missing = [
            {
                "domain": domain,
                "domain_name": DATA_DOMAIN_LABELS[domain],
                "required": domain in missing_required,
                "effect": requirement.missing_effects[domain],
            }
            for domain in (*missing_required, *missing_recommended)
        ]
        results.append(
            {
                "feature_code": requirement.code,
                "feature_name": requirement.name,
                "readiness_status": status,
                "readiness_score": score,
                "required_domains": [
                    {"code": item, "name": DATA_DOMAIN_LABELS[item]}
                    for item in requirement.required
                ],
                "recommended_domains": [
                    {"code": item, "name": DATA_DOMAIN_LABELS[item]}
                    for item in requirement.recommended
                ],
                "available_domains": sorted(available),
                "missing_required": missing_required,
                "missing_recommended": missing_recommended,
                "missing_data_effects": missing,
                "evidence_summary": evidence,
            }
        )
    return results


def create_import_batch(
    session: Session,
    *,
    project_id: str,
    display_name: str,
    data_domain: str,
    created_by: str,
    template_version_id: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    batch_id = str(uuid4())
    row = session.execute(
        text(
            """
            INSERT INTO data_import_batch(
                id, project_id, display_name, data_domain,
                template_version_id, created_by, notes
            ) VALUES (
                :id, :project_id, :display_name, :data_domain,
                :template_version_id, :created_by, :notes
            )
            RETURNING *
            """
        ),
        {
            "id": batch_id,
            "project_id": project_id,
            "display_name": display_name.strip(),
            "data_domain": require_data_domain(data_domain),
            "template_version_id": template_version_id,
            "created_by": created_by,
            "notes": notes,
        },
    ).mappings().one()
    session.commit()
    return dict(row)


def get_import_batch(session: Session, batch_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if for_update else ""
    row = session.execute(
        text("SELECT * FROM data_import_batch WHERE id=:batch_id" + suffix),
        {"batch_id": batch_id},
    ).mappings().first()
    return dict(row) if row else None


def list_import_batches(
    session: Session,
    *,
    project_id: str,
    data_domain: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"project_id": project_id, "limit": min(max(limit, 1), 200)}
    domain_clause = ""
    if data_domain:
        params["data_domain"] = require_data_domain(data_domain)
        domain_clause = " AND data_domain=:data_domain"
    rows = session.execute(
        text(
            "SELECT * FROM data_import_batch WHERE project_id=:project_id"
            + domain_clause
            + " ORDER BY created_at DESC LIMIT :limit"
        ),
        params,
    ).mappings().all()
    return [dict(row) for row in rows]


def register_import_file(
    session: Session,
    *,
    batch_id: str,
    owner_id: str,
    original_file_name: str,
    content_type: str,
    source_role: str,
    storage_backend: str,
    stored: StoredObject,
) -> dict[str, Any]:
    batch = get_import_batch(session, batch_id, for_update=True)
    if not batch:
        raise LookupError("import batch not found")
    if batch["status"] not in {"created", "uploading"}:
        raise ValueError("files can only be attached before validation starts")
    asset_id = str(uuid4())
    import_file_id = str(uuid4())
    session.execute(
        text(
            """
            INSERT INTO data_file_asset(
                id, project_id, owner_id, data_domain, asset_role,
                original_file_name, content_type, size_bytes, sha256,
                object_locator, storage_backend, encryption_key_id
            ) VALUES (
                :id, :project_id, :owner_id, :data_domain, 'source',
                :original_file_name, :content_type, :size_bytes, :sha256,
                :object_locator, :storage_backend, :encryption_key_id
            )
            """
        ),
        {
            "id": asset_id,
            "project_id": batch["project_id"],
            "owner_id": owner_id,
            "data_domain": batch["data_domain"],
            "original_file_name": original_file_name,
            "content_type": content_type or "application/octet-stream",
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
            "object_locator": stored.locator,
            "storage_backend": storage_backend,
            "encryption_key_id": stored.kms_key_id or None,
        },
    )
    row = session.execute(
        text(
            """
            INSERT INTO data_import_file(
                id, import_batch_id, file_asset_id, source_role
            ) VALUES (:id, :batch_id, :asset_id, :source_role)
            RETURNING *
            """
        ),
        {
            "id": import_file_id,
            "batch_id": batch_id,
            "asset_id": asset_id,
            "source_role": (source_role or "primary").strip(),
        },
    ).mappings().one()
    session.execute(
        text("UPDATE data_import_batch SET status='uploading', updated_at=now() WHERE id=:batch_id"),
        {"batch_id": batch_id},
    )
    session.commit()
    result = dict(row)
    result["asset"] = {
        "id": asset_id,
        "original_file_name": original_file_name,
        "size_bytes": stored.size_bytes,
        "sha256": stored.sha256,
    }
    return result


def record_entity_lineage(
    session: Session,
    *,
    project_id: str,
    import_batch_id: str,
    file_asset_id: str,
    entity_type: str,
    entity_id: str,
    source_row_number: int | None = None,
    relationship_type: str = "created_from",
    locator: dict[str, Any] | None = None,
) -> None:
    """Attach one governed business record to its exact source file and row."""
    session.execute(
        text(
            """
            INSERT INTO data_entity_lineage(
                id, project_id, import_batch_id, file_asset_id,
                source_row_number, entity_type, entity_id,
                relationship_type, locator
            ) VALUES (
                :id, :project_id, :import_batch_id, :file_asset_id,
                :source_row_number, :entity_type, :entity_id,
                :relationship_type, CAST(:locator AS jsonb)
            )
            ON CONFLICT (
                import_batch_id, file_asset_id, entity_type,
                entity_id, source_row_number
            ) DO NOTHING
            """
        ),
        {
            "id": str(uuid4()),
            "project_id": project_id,
            "import_batch_id": import_batch_id,
            "file_asset_id": file_asset_id,
            "source_row_number": source_row_number,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "relationship_type": relationship_type,
            "locator": json.dumps(locator or {}, ensure_ascii=False),
        },
    )


def _normalized_identity(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip()).lower()


def ensure_canonical_material(
    session: Session,
    *,
    project_id: str,
    variety_id: str,
    material_name: str,
    material_code: str | None,
    material_type: str | None,
    aliases: Iterable[str],
    female_parent: str | None,
    male_parent: str | None,
    actor_id: str,
) -> str:
    """Resolve or create the canonical material behind a legacy variety row.

    Institution supplied codes win.  When a source only contains a name, the
    platform creates an explicit internal code rather than pretending that the
    name is a verified institution identifier.
    """
    supplied_code = (material_code or "").strip()
    normalized_code = _normalized_identity(supplied_code)
    normalized_name = _normalized_identity(material_name)
    material_id = session.scalar(
        text(
            """
            SELECT material_id FROM data_legacy_variety_material_link
            WHERE project_id=:project_id
              AND variety_id=:variety_id AND status='confirmed'
            LIMIT 1
            """
        ),
        {"project_id": project_id, "variety_id": variety_id},
    )
    if not material_id and normalized_code:
        material_id = session.scalar(
            text(
                """
                SELECT material_id FROM data_material_identifier
                WHERE project_id=:project_id
                  AND normalized_value=:normalized_value
                  AND identifier_type='institution_material_code'
                LIMIT 1
                """
            ),
            {"project_id": project_id, "normalized_value": normalized_code},
        )
    if not material_id and supplied_code:
        material_id = session.scalar(
            text("SELECT id FROM breeding_material WHERE material_code=:material_code LIMIT 1"),
            {"material_code": supplied_code},
        )
    if not material_id and normalized_name:
        material_id = session.scalar(
            text(
                """
                SELECT material.id FROM breeding_material material
                JOIN data_material_project_scope scope
                  ON scope.material_id=material.id
                 AND scope.project_id=:project_id
                WHERE lower(regexp_replace(trim(material.material_name), '\\s+', '', 'g'))=:normalized_name
                ORDER BY material.created_at LIMIT 1
                """
            ),
            {"normalized_name": normalized_name, "project_id": project_id},
        )
    if not material_id:
        material_id = str(uuid4())
        effective_code = supplied_code or (
            "LY-INT-" + hashlib.sha256(
                f"{project_id}|{normalized_name}|{variety_id}".encode("utf-8")
            ).hexdigest()[:12].upper()
        )
        pedigree_parts = []
        if female_parent:
            pedigree_parts.append(f"母本：{female_parent}")
        if male_parent:
            pedigree_parts.append(f"父本：{male_parent}")
        session.execute(
            text(
                """
                INSERT INTO breeding_material(
                    id, material_code, material_name, material_type,
                    is_check, aliases, pedigree_summary
                ) VALUES (
                    :id, :material_code, :material_name, :material_type,
                    false, CAST(:aliases AS jsonb), :pedigree_summary
                )
                """
            ),
            {
                "id": material_id,
                "material_code": effective_code,
                "material_name": material_name,
                "material_type": material_type or "水稻育种材料",
                "aliases": json.dumps(list(dict.fromkeys(item for item in aliases if item)), ensure_ascii=False),
                "pedigree_summary": "；".join(pedigree_parts) or None,
            },
        )

    session.execute(
        text(
            """
            INSERT INTO data_material_project_scope(
                project_id, material_id, access_level, source, created_by
            ) VALUES (:project_id, :material_id, 'project', 'governed_import', :created_by)
            ON CONFLICT (project_id, material_id) DO NOTHING
            """
        ),
        {"project_id": project_id, "material_id": material_id, "created_by": actor_id},
    )
    identifier_value = supplied_code or session.scalar(
        text("SELECT material_code FROM breeding_material WHERE id=:material_id"),
        {"material_id": material_id},
    )
    identifier_type = "institution_material_code" if supplied_code else "platform_internal_code"
    session.execute(
        text(
            """
            INSERT INTO data_material_identifier(
                id, project_id, material_id, source_system, identifier_type,
                identifier_value, normalized_value, is_primary,
                verification_status, created_by
            ) VALUES (
                :id, :project_id, :material_id, 'institution', :identifier_type,
                :identifier_value, :normalized_value, true,
                :verification_status, :created_by
            )
            ON CONFLICT (project_id, source_system, identifier_type, normalized_value) DO NOTHING
            """
        ),
        {
            "id": str(uuid4()),
            "project_id": project_id,
            "material_id": material_id,
            "identifier_type": identifier_type,
            "identifier_value": identifier_value,
            "normalized_value": _normalized_identity(str(identifier_value)),
            "verification_status": "verified" if supplied_code else "generated",
            "created_by": actor_id,
        },
    )
    for alias in dict.fromkeys([material_name, *aliases]):
        normalized_alias = _normalized_identity(alias)
        if not normalized_alias:
            continue
        session.execute(
            text(
                """
                INSERT INTO data_material_alias(
                    id, project_id, material_id, alias_name, normalized_alias,
                    alias_type, verification_status, created_by
                ) VALUES (
                    :id, :project_id, :material_id, :alias_name, :normalized_alias,
                    'institution_alias', 'verified', :created_by
                )
                ON CONFLICT (project_id, material_id, normalized_alias) DO NOTHING
                """
            ),
            {
                "id": str(uuid4()),
                "project_id": project_id,
                "material_id": material_id,
                "alias_name": alias,
                "normalized_alias": normalized_alias,
                "created_by": actor_id,
            },
        )
    session.execute(
        text(
            """
            INSERT INTO data_legacy_variety_material_link(
                project_id, variety_id, material_id, match_method, confidence,
                status, reviewed_by, reviewed_at
            ) VALUES (
                :project_id, :variety_id, :material_id, :match_method, 1.0,
                'confirmed', :reviewed_by, now()
            )
            ON CONFLICT (project_id, variety_id, material_id) DO UPDATE SET
                status='confirmed', reviewed_by=excluded.reviewed_by,
                reviewed_at=excluded.reviewed_at
            """
        ),
        {
            "project_id": project_id,
            "variety_id": variety_id,
            "material_id": material_id,
            "match_method": "institution_code" if supplied_code else "confirmed_name",
            "reviewed_by": actor_id,
        },
    )
    return str(material_id)


def ensure_pedigree_relationships(
    session: Session,
    *,
    project_id: str,
    child_material_id: str,
    female_parent: str | None,
    male_parent: str | None,
    actor_id: str,
) -> list[str]:
    """Resolve parent labels to canonical materials and persist the pedigree edges.

    Parent labels in historical spreadsheets are often either a material code or
    a name.  Unresolved labels are retained as project-scoped placeholder
    materials, so later imports can reconcile them without losing the edge.
    """

    relationship_ids: list[str] = []
    for role, raw_parent in (("female", female_parent), ("male", male_parent)):
        parent_label = (raw_parent or "").strip()
        normalized = _normalized_identity(parent_label)
        if not normalized:
            continue
        parent_material_id = session.scalar(
            text(
                """
                SELECT material_id FROM data_material_identifier
                WHERE project_id=:project_id AND normalized_value=:normalized
                UNION ALL
                SELECT alias.material_id FROM data_material_alias alias
                WHERE alias.project_id=:project_id
                  AND alias.normalized_alias=:normalized
                UNION ALL
                SELECT material.id FROM breeding_material material
                JOIN data_material_project_scope scope
                  ON scope.material_id=material.id
                 AND scope.project_id=:project_id
                WHERE lower(regexp_replace(trim(material.material_code), '\\s+', '', 'g'))=:normalized
                   OR lower(regexp_replace(trim(material.material_name), '\\s+', '', 'g'))=:normalized
                LIMIT 1
                """
            ),
            {"normalized": normalized, "project_id": project_id},
        )
        if not parent_material_id:
            parent_material_id = str(uuid4())
            generated_code = "LY-PARENT-" + hashlib.sha256(
                f"{project_id}|{normalized}".encode("utf-8")
            ).hexdigest()[:12].upper()
            session.execute(
                text(
                    """
                    INSERT INTO breeding_material(
                        id, material_code, material_name, material_type,
                        is_check, aliases, pedigree_summary
                    ) VALUES (
                        :id, :material_code, :material_name,
                        '待补充亲本材料', false, '[]'::jsonb, NULL
                    )
                    """
                ),
                {
                    "id": parent_material_id,
                    "material_code": generated_code,
                    "material_name": parent_label,
                },
            )
            session.execute(
                text(
                    """
                    INSERT INTO data_material_alias(
                        id, project_id, material_id, alias_name, normalized_alias,
                        alias_type, verification_status, created_by
                    ) VALUES (
                        :id, :project_id, :material_id, :alias_name, :normalized_alias,
                        'pedigree_source_label', 'pending', :created_by
                    ) ON CONFLICT (project_id, material_id, normalized_alias) DO NOTHING
                    """
                ),
                {
                    "id": str(uuid4()),
                    "project_id": project_id,
                    "material_id": parent_material_id,
                    "alias_name": parent_label,
                    "normalized_alias": normalized,
                    "created_by": actor_id,
                },
            )
        session.execute(
            text(
                """
                INSERT INTO data_material_project_scope(
                    project_id, material_id, access_level, source, created_by
                ) VALUES (
                    :project_id, :material_id, 'project',
                    'pedigree_import', :created_by
                ) ON CONFLICT (project_id, material_id) DO NOTHING
                """
            ),
            {
                "project_id": project_id,
                "material_id": parent_material_id,
                "created_by": actor_id,
            },
        )
        relationship_id = session.scalar(
            text(
                """
                INSERT INTO breeding_pedigree_relationship(
                    id, project_id, child_material_id, parent_material_id, parent_role,
                    relationship_type, source_note, is_simulated
                ) VALUES (
                    :id, :project_id, :child_material_id, :parent_material_id, :parent_role,
                    'hybrid_parent', 'governed_import', false
                ) ON CONFLICT (project_id, child_material_id, parent_material_id, parent_role)
                DO UPDATE SET source_note=excluded.source_note
                RETURNING id
                """
            ),
            {
                "id": str(uuid4()),
                "project_id": project_id,
                "child_material_id": child_material_id,
                "parent_material_id": parent_material_id,
                "parent_role": role,
            },
        )
        relationship_ids.append(str(relationship_id))
    return relationship_ids


def refresh_import_batch_counts(
    session: Session,
    import_batch_id: str,
    *,
    finalize: bool,
    publish_without_review: bool,
) -> dict[str, Any]:
    """Recount governed rows and optionally advance a completed intake batch."""
    batch = get_import_batch(session, import_batch_id, for_update=True)
    if not batch:
        raise LookupError("import batch not found")
    accepted_count = int(session.scalar(
        text(
            """
            SELECT count(DISTINCT COALESCE(source_row_number::text, entity_id))
            FROM data_entity_lineage
            WHERE import_batch_id=:batch_id AND entity_type='variety_basic'
            """
        ),
        {"batch_id": import_batch_id},
    ) or 0)
    session.execute(
        text(
            """
            UPDATE data_import_batch SET
                row_count=GREATEST(row_count, :accepted_count),
                accepted_count=:accepted_count,
                updated_at=now()
            WHERE id=:batch_id
            """
        ),
        {"batch_id": import_batch_id, "accepted_count": accepted_count},
    )
    session.commit()
    if finalize and accepted_count:
        current = get_import_batch(session, import_batch_id)
        if current and current["status"] == "validating":
            transition_import_batch(
                session,
                batch_id=import_batch_id,
                target_status="ready",
                row_count=accepted_count,
                accepted_count=accepted_count,
            )
        if publish_without_review:
            current = get_import_batch(session, import_batch_id)
            if current and current["status"] == "ready":
                transition_import_batch(
                    session,
                    batch_id=import_batch_id,
                    target_status="published",
                    row_count=accepted_count,
                    accepted_count=accepted_count,
                )
    return get_import_batch(session, import_batch_id) or {}


def publish_ready_batches_for_sources(session: Session, source_ids: Iterable[str]) -> list[str]:
    """Publish ready batches once none of their surviving observations are pending."""
    ids = list(dict.fromkeys(item for item in source_ids if item))
    if not ids:
        return []
    rows = session.execute(
        text(
            """
            SELECT DISTINCT unified_import_batch_id AS batch_id
            FROM source_review
            WHERE id=ANY(:source_ids) AND unified_import_batch_id IS NOT NULL
            """
        ),
        {"source_ids": ids},
    ).mappings().all()
    published: list[str] = []
    for row in rows:
        batch_id = str(row["batch_id"])
        batch = get_import_batch(session, batch_id)
        if not batch or batch["status"] != "ready":
            continue
        pending = int(session.scalar(
            text(
                """
                SELECT count(*) FROM data_entity_lineage lineage
                JOIN phenotype_observation observation
                  ON lineage.entity_type='phenotype_observation'
                 AND observation.id=lineage.entity_id
                WHERE lineage.import_batch_id=:batch_id
                  AND observation.publish_status<>'published'
                """
            ),
            {"batch_id": batch_id},
        ) or 0)
        if pending:
            continue
        transition_import_batch(
            session,
            batch_id=batch_id,
            target_status="published",
            row_count=int(batch["row_count"] or 0),
            accepted_count=int(batch["accepted_count"] or 0),
            rejected_count=int(batch["rejected_count"] or 0),
            warning_count=int(batch["warning_count"] or 0),
        )
        published.append(batch_id)
    return published


def transition_import_batch(
    session: Session,
    *,
    batch_id: str,
    target_status: str,
    row_count: int | None = None,
    accepted_count: int | None = None,
    rejected_count: int | None = None,
    warning_count: int | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = (target_status or "").strip().lower()
    batch = get_import_batch(session, batch_id, for_update=True)
    if not batch:
        raise LookupError("import batch not found")
    current = str(batch["status"])
    if target not in IMPORT_STATUS_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"invalid import status transition: {current} -> {target}")

    counts = {
        "row_count": batch["row_count"] if row_count is None else max(0, row_count),
        "accepted_count": batch["accepted_count"] if accepted_count is None else max(0, accepted_count),
        "rejected_count": batch["rejected_count"] if rejected_count is None else max(0, rejected_count),
        "warning_count": batch["warning_count"] if warning_count is None else max(0, warning_count),
    }
    if counts["accepted_count"] + counts["rejected_count"] > counts["row_count"]:
        raise ValueError("accepted and rejected counts cannot exceed row count")
    if target == "published":
        file_count = session.scalar(
            text("SELECT count(*) FROM data_import_file WHERE import_batch_id=:batch_id"),
            {"batch_id": batch_id},
        )
        open_errors = session.scalar(
            text(
                """
                SELECT count(*) FROM data_import_row_error
                WHERE import_batch_id=:batch_id AND severity='error'
                  AND resolution_status='open'
                """
            ),
            {"batch_id": batch_id},
        )
        if not file_count:
            raise ValueError("an import batch cannot be published without a source file")
        if counts["accepted_count"] <= 0:
            raise ValueError("an import batch cannot be published without accepted rows")
        if open_errors:
            raise ValueError("resolve blocking row errors before publication")

    timestamps = {
        "validated_at": datetime.now(timezone.utc) if target == "ready" else batch.get("validated_at"),
        "published_at": datetime.now(timezone.utc) if target == "published" else batch.get("published_at"),
    }
    row = session.execute(
        text(
            """
            UPDATE data_import_batch SET
                status=:status,
                row_count=:row_count,
                accepted_count=:accepted_count,
                rejected_count=:rejected_count,
                warning_count=:warning_count,
                summary=CAST(:summary AS jsonb),
                validated_at=:validated_at,
                published_at=:published_at,
                updated_at=now()
            WHERE id=:batch_id
            RETURNING *
            """
        ),
        {
            "batch_id": batch_id,
            "status": target,
            **counts,
            "summary": json.dumps(summary if summary is not None else batch["summary"]),
            **timestamps,
        },
    ).mappings().one()
    if target == "published":
        assess_project_readiness(session, batch["project_id"], persist=True)
    else:
        session.commit()
    return dict(row)


def assess_project_readiness(
    session: Session,
    project_id: str,
    *,
    persist: bool = False,
) -> list[dict[str, Any]]:
    # A published batch is not evidence by itself.  It only enables a feature
    # after at least one domain-appropriate business entity has exact lineage
    # back to a source row.  This prevents empty/manual batch counters from
    # producing a false "ready" status.
    domain_entities: dict[str, frozenset[str]] = {
        "germplasm": frozenset({"breeding_material", "variety_basic"}),
        "pedigree": frozenset({"breeding_pedigree_relationship"}),
        "phenotype": frozenset({"trial_phenotype_observation", "phenotype_observation", "root_phenotype_observation"}),
        "environment": frozenset({"trial_environment_metric"}),
        "management": frozenset({"trial_management_event"}),
        "genotype": frozenset({"genotype_asset", "genotype_asset_version"}),
        "trial": frozenset({"trial_entry"}),
        "literature": frozenset({"knowledge_document"}),
        "mixed": frozenset(),
    }
    rows = session.execute(
        text(
            """
            SELECT batch.data_domain, batch.id AS batch_id,
                   batch.warning_count, batch.published_at,
                   lineage.entity_type, count(DISTINCT lineage.entity_id) AS entity_count
            FROM data_import_batch batch
            JOIN data_entity_lineage lineage ON lineage.import_batch_id=batch.id
            WHERE batch.project_id=:project_id AND batch.status='published'
            GROUP BY batch.data_domain, batch.id, batch.warning_count,
                     batch.published_at, lineage.entity_type
            """
        ),
        {"project_id": project_id},
    ).mappings().all()
    aggregates: dict[str, dict[str, Any]] = {}
    for row in rows:
        domain = str(row["data_domain"])
        allowed = domain_entities.get(domain, frozenset())
        if row["entity_type"] not in allowed:
            continue
        item = aggregates.setdefault(domain, {
            "batch_ids": set(),
            "linked_entity_count": 0,
            "warning_count": 0,
            "latest_published_at": None,
            "entity_types": set(),
        })
        if row["batch_id"] not in item["batch_ids"]:
            item["batch_ids"].add(row["batch_id"])
            item["warning_count"] += int(row["warning_count"] or 0)
        item["linked_entity_count"] += int(row["entity_count"] or 0)
        item["entity_types"].add(row["entity_type"])
        published_at = row["published_at"]
        if published_at and (item["latest_published_at"] is None or published_at > item["latest_published_at"]):
            item["latest_published_at"] = published_at
    evidence = {
        domain: {
            "batch_count": len(item["batch_ids"]),
            "accepted_count": item["linked_entity_count"],
            "linked_entity_count": item["linked_entity_count"],
            "linked_entity_types": sorted(item["entity_types"]),
            "warning_count": item["warning_count"],
            "latest_published_at": item["latest_published_at"].isoformat()
            if item["latest_published_at"]
            else None,
        }
        for domain, item in aggregates.items()
        if item["linked_entity_count"] > 0
    }
    results = feature_readiness(evidence.keys(), evidence)
    if persist:
        for item in results:
            session.execute(
                text(
                    """
                    INSERT INTO data_project_completeness(
                        id, project_id, feature_code, readiness_status,
                        readiness_score, available_domains, missing_required,
                        missing_recommended, evidence_summary, assessed_at
                    ) VALUES (
                        :id, :project_id, :feature_code, :readiness_status,
                        :readiness_score, CAST(:available_domains AS jsonb),
                        CAST(:missing_required AS jsonb), CAST(:missing_recommended AS jsonb),
                        CAST(:evidence_summary AS jsonb), now()
                    )
                    ON CONFLICT (project_id, feature_code) DO UPDATE SET
                        readiness_status=excluded.readiness_status,
                        readiness_score=excluded.readiness_score,
                        available_domains=excluded.available_domains,
                        missing_required=excluded.missing_required,
                        missing_recommended=excluded.missing_recommended,
                        evidence_summary=excluded.evidence_summary,
                        assessed_at=now()
                    """
                ),
                {
                    "id": str(uuid4()),
                    "project_id": project_id,
                    "feature_code": item["feature_code"],
                    "readiness_status": item["readiness_status"],
                    "readiness_score": item["readiness_score"],
                    "available_domains": json.dumps(item["available_domains"]),
                    "missing_required": json.dumps(item["missing_required"]),
                    "missing_recommended": json.dumps(item["missing_recommended"]),
                    "evidence_summary": json.dumps(item["evidence_summary"]),
                },
            )
        session.commit()
    return results


def serialize_record(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: serialize_record(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_record(item) for item in value]
    return value
