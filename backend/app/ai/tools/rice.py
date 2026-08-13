"""Reviewed rice-research tools used by the four Longyun sub-agents.

All SQL is static and parameterized.  A user question can influence bounded
search values, never a table name, column name, SQL fragment or executable
program.  Statistical values are delegated to the existing controlled local
statistics engine rather than calculated by the language model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...trial_package import build_published_trial_evidence
from ..contracts import EvidenceReference, ToolResult
from .core import (
    AgentToolContext,
    ControlledTool,
    ControlledToolRegistry,
    ToolExecutionError,
    default_arguments,
)


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=20000)
    limit: int = Field(default=10, ge=1, le=50)


class ValidationPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=20000)


SessionFactory = Callable[[], Session]
SessionContextSetter = Callable[[Session, AgentToolContext], None]


def _require_project(context: AgentToolContext) -> str:
    if not context.project_id:
        raise ToolExecutionError(
            "project_required",
            "该工具会读取机构科研数据，必须先选择一个有权访问的课题。",
        )
    return context.project_id


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _tokens(question: str, limit: int = 6) -> list[str]:
    stop = {
        "请问", "分析", "回答", "数据", "材料", "水稻", "进行", "哪些", "什么",
        "根据", "这个", "一个", "可以", "如何", "是否", "需要", "给出", "建议",
    }
    raw = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,30}|[\u4e00-\u9fff]{2,8}", question or "")
    result: list[str] = []
    for token in raw:
        normalized = token.strip()
        if normalized in stop or normalized in result:
            continue
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def _search_predicate(columns: tuple[str, ...], tokens: list[str]) -> tuple[str, dict[str, str]]:
    if not tokens:
        return "TRUE", {}
    groups: list[str] = []
    parameters: dict[str, str] = {}
    for index, token in enumerate(tokens):
        key = f"term_{index}"
        parameters[key] = f"%{token}%"
        groups.append("(" + " OR ".join(f"{column} ILIKE :{key}" for column in columns) + ")")
    return " OR ".join(groups), parameters


def _result(
    tool_code: str,
    rows: list[dict[str, Any]] | dict[str, Any],
    *,
    summary: str,
    evidence: list[EvidenceReference] | None = None,
    empty_status: str = "not_applicable",
    data_classification: str = "institution_private",
) -> ToolResult:
    has_data = bool(rows)
    return ToolResult(
        tool_code=tool_code,
        status="completed" if has_data else empty_status,
        data_classification=data_classification,
        summary=summary,
        data=_json_safe(rows),
        evidence=evidence or [],
    )


def build_rice_tool_registry(
    session_factory: SessionFactory,
    set_context: SessionContextSetter,
) -> ControlledToolRegistry:
    registry = ControlledToolRegistry()

    @contextmanager
    def scoped_session(context: AgentToolContext):
        session = session_factory()
        try:
            set_context(session, context)
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def query_germplasm(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        project_id = _require_project(context)
        tokens = _tokens(request.query)
        predicate, params = _search_predicate(
            ("material_code", "material_name", "material_type", "pedigree_summary"), tokens
        )
        with scoped_session(context) as session:
            rows = session.execute(
                text(
                    "SELECT id, material_code, material_name, material_type, is_check, "
                    "aliases, pedigree_summary FROM breeding_material material "
                    "JOIN data_material_project_scope scope ON scope.material_id = material.id "
                    "WHERE scope.project_id = :project_id AND (" + predicate + ") " +
                    " ORDER BY is_check DESC, material_code LIMIT :limit"
                ),
                {**params, "project_id": project_id, "limit": request.limit},
            ).mappings().all()
        records = [dict(row) for row in rows]
        evidence = [
            EvidenceReference(
                evidence_id=f"material:{row['id']}",
                title=str(row.get("material_name") or row.get("material_code")),
                source="governed_germplasm",
                locator=str(row.get("material_code") or ""),
            )
            for row in records
        ]
        return _result(
            "query_germplasm", records,
            summary=f"检索到 {len(records)} 条受控种质/育种材料记录。",
            evidence=evidence,
        )

    def read_genotype_qc(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        project_id = _require_project(context)
        with scoped_session(context) as session:
            rows = session.execute(text("""
                SELECT version.id, asset.id AS asset_id, asset.title, asset.reference_assembly,
                       asset.population_type, version.version_number, version.status,
                       version.qc_template_code, version.qc_template_version, version.qc_summary,
                       version.published_at
                FROM genotype_asset_version version
                JOIN genotype_asset asset ON asset.id = version.asset_id
                WHERE asset.project_id = :project_id
                  AND version.status IN ('analysis_ready', 'published')
                ORDER BY COALESCE(version.published_at, version.updated_at) DESC
                LIMIT :limit
            """), {"project_id": project_id, "limit": request.limit}).mappings().all()
        records = [dict(row) for row in rows]
        evidence = [
            EvidenceReference(
                evidence_id=f"genotype-version:{row['id']}",
                title=str(row.get("title") or "基因型质控结果"),
                source="private_genotype_qc",
                locator=f"version {row.get('version_number')}",
            ) for row in records
        ]
        return _result(
            "read_genotype_qc", records,
            summary=f"读取到 {len(records)} 个当前账号已发布或分析就绪的基因型质控版本。",
            evidence=evidence,
        )

    def read_gwas_result(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        project_id = _require_project(context)
        with scoped_session(context) as session:
            rows = session.execute(text("""
                SELECT id, status, trait_name, reference_assembly, purpose, workflow_code,
                       parameters, preflight, result_manifest, created_at, updated_at
                FROM gwas_analysis_plan
                WHERE project_id = :project_id
                  AND owner_id = :owner_id
                  AND status IN ('completed', 'result_ready')
                ORDER BY updated_at DESC LIMIT :limit
            """), {
                "project_id": project_id,
                "owner_id": context.owner_user_id,
                "limit": request.limit,
            }).mappings().all()
        records = [dict(row) for row in rows]
        evidence = [
            EvidenceReference(
                evidence_id=f"gwas:{row['id']}",
                title=f"GWAS：{row.get('trait_name') or '未命名性状'}",
                source="controlled_gwas_run",
                locator=str(row.get("workflow_code") or ""),
            ) for row in records
        ]
        return _result(
            "read_gwas_result", records,
            summary=f"读取到 {len(records)} 个当前账号的受控GWAS结果。",
            evidence=evidence,
        )

    def search_chunks(
        context: AgentToolContext,
        request: SearchInput,
        *,
        scope_clause: str,
        tool_code: str,
        source: str,
        data_classification: str = "institution_private",
    ) -> ToolResult:
        tokens = _tokens(request.query)
        predicate, params = _search_predicate(("document.display_title", "chunk.content"), tokens)
        with scoped_session(context) as session:
            rows = session.execute(text("""
                SELECT chunk.id, chunk.document_id, document.display_title, document.source_organization,
                       document.author, document.publication_year, document.source_url,
                       chunk.source_locator, left(chunk.content, 3500) AS excerpt
                FROM knowledge_chunk chunk
                JOIN knowledge_document document ON document.id = chunk.document_id
                WHERE (
                    (document.scope = 'public' AND document.status = 'published'
                     AND chunk.document_status = 'published')
                    OR
                    (document.scope = 'private' AND document.status = 'ready')
                )
                  AND (""" + scope_clause + ") AND (" + predicate + ") "
                "ORDER BY document.updated_at DESC, chunk.ordinal LIMIT :limit"
            ), {
                **params,
                "owner_id": context.owner_user_id,
                "project_id": context.project_id,
                "limit": request.limit,
            }).mappings().all()
        records = [dict(row) for row in rows]
        evidence = [
            EvidenceReference(
                evidence_id=f"knowledge:{row['document_id']}:{row['id']}",
                title=str(row.get("display_title") or "知识库文档"),
                source=source,
                locator=str(row.get("source_locator") or ""),
            ) for row in records
        ]
        return _result(
            tool_code, records,
            summary=f"检索到 {len(records)} 个可追溯知识片段。",
            evidence=evidence,
            data_classification=data_classification,
        )

    def search_gene_evidence(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        return search_chunks(
            context, SearchInput.model_validate(payload),
            scope_clause=(
                "document.scope = 'public' OR "
                "(document.scope = 'private' AND document.owner_id = :owner_id "
                "AND document.project_id = :project_id)"
            ),
            tool_code="search_gene_evidence",
            source="authorized_gene_knowledge",
        )

    def query_parent_candidates(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        project_id = _require_project(context)
        with scoped_session(context) as session:
            rows = session.execute(text("""
                SELECT material.id, material.material_code, material.material_name,
                       material.material_type, material.pedigree_summary,
                       round(avg(summary.yield_per_mu)::numeric, 2) AS mean_yield,
                       round(avg(summary.seed_setting_rate)::numeric, 2) AS mean_seed_setting_rate,
                       round(avg(summary.head_rice_rate)::numeric, 2) AS mean_head_rice_rate,
                       round(avg(summary.chalkiness_degree)::numeric, 2) AS mean_chalkiness_degree,
                       round(avg(summary.panicle_blast_score)::numeric, 2) AS mean_panicle_blast_score,
                       round(avg(summary.lodging_score)::numeric, 2) AS mean_lodging_score,
                       count(DISTINCT summary.trial_id) AS trial_count
                FROM breeding_material material
                JOIN data_material_project_scope scope
                  ON scope.material_id = material.id AND scope.project_id = :project_id
                LEFT JOIN v_trial_material_summary summary
                  ON summary.material_id = material.id
                 AND EXISTS (
                    SELECT 1 FROM field_trial trial
                    WHERE trial.id = summary.trial_id AND trial.project_id = :project_id
                 )
                WHERE material.is_check = FALSE
                GROUP BY material.id, material.material_code, material.material_name,
                         material.material_type, material.pedigree_summary
                ORDER BY count(DISTINCT summary.trial_id) DESC, avg(summary.yield_per_mu) DESC NULLS LAST
                LIMIT :limit
            """), {"project_id": project_id, "limit": request.limit}).mappings().all()
        records = [dict(row) for row in rows]
        evidence = [
            EvidenceReference(
                evidence_id=f"parent-candidate:{row['id']}",
                title=str(row.get("material_name") or row.get("material_code")),
                source="governed_trial_summary",
                locator=f"{row.get('trial_count') or 0}个试验",
            ) for row in records
        ]
        return _result(
            "query_parent_candidates", records,
            summary=f"形成 {len(records)} 条亲本候选基础记录；这不是配合力或杂交优势预测。",
            evidence=evidence,
        )

    def score_parent_constraints(context: AgentToolContext, payload: BaseModel, prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        candidate_result = next((item for item in prior if item.tool_code == "query_parent_candidates"), None)
        candidates = candidate_result.data if candidate_result and isinstance(candidate_result.data, list) else []
        objective = request.query.casefold()
        scored: list[dict[str, Any]] = []
        for candidate in candidates:
            score = 0.0
            reasons: list[str] = []
            if any(word in objective for word in ("高产", "产量")) and candidate.get("mean_yield") is not None:
                score += min(float(candidate["mean_yield"]) / 1000.0, 1.0) * 35
                reasons.append("具有已发布试验的平均产量观测")
            if any(word in objective for word in ("抗病", "稻瘟", "穗瘟")) and candidate.get("mean_panicle_blast_score") is not None:
                score += max(0.0, 9.0 - float(candidate["mean_panicle_blast_score"])) / 9.0 * 25
                reasons.append("具有穗瘟等级观测")
            if any(word in objective for word in ("抗倒", "倒伏")) and candidate.get("mean_lodging_score") is not None:
                score += max(0.0, 9.0 - float(candidate["mean_lodging_score"])) / 9.0 * 20
                reasons.append("具有倒伏等级观测")
            if any(word in objective for word in ("品质", "米质")) and candidate.get("mean_head_rice_rate") is not None:
                score += min(float(candidate["mean_head_rice_rate"]) / 100.0, 1.0) * 20
                reasons.append("具有整精米率观测")
            scored.append({
                "material_id": candidate.get("id"),
                "material_code": candidate.get("material_code"),
                "material_name": candidate.get("material_name"),
                "constraint_score": round(score, 2),
                "matched_evidence": reasons,
                "blocking_missing_data": ["亲缘系数", "花期匹配", "亲本配合力"]
                if not candidate.get("pedigree_summary") else ["花期匹配", "亲本配合力"],
            })
        scored.sort(key=lambda item: item["constraint_score"], reverse=True)
        evidence = candidate_result.evidence if candidate_result else []
        return _result(
            "score_parent_constraints", scored[: request.limit],
            summary="已按问题中明确出现的目标性状进行可解释约束评分；缺失关键字段的候选不得作为确定性配组结论。",
            evidence=evidence,
            data_classification=(
                candidate_result.data_classification
                if candidate_result else "institution_private"
            ),
        )

    def read_kinship(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        project_id = _require_project(context)
        with scoped_session(context) as session:
            rows = session.execute(text("""
                SELECT relationship.id, child.material_code AS child_code,
                       child.material_name AS child_name, parent.material_code AS parent_code,
                       parent.material_name AS parent_name, relationship.parent_role,
                       relationship.relationship_type, relationship.source_record_no,
                       relationship.source_note, relationship.is_simulated
                FROM breeding_pedigree_relationship relationship
                JOIN breeding_material child ON child.id = relationship.child_material_id
                JOIN breeding_material parent ON parent.id = relationship.parent_material_id
                JOIN data_material_project_scope scope
                  ON scope.material_id = child.id AND scope.project_id = :project_id
                WHERE relationship.project_id = :project_id
                ORDER BY relationship.is_simulated, child.material_code, relationship.parent_role
                LIMIT :limit
            """), {"project_id": project_id, "limit": request.limit}).mappings().all()
        records = [dict(row) for row in rows]
        evidence = [
            EvidenceReference(
                evidence_id=f"pedigree:{row['id']}",
                title=f"{row.get('child_name')}—{row.get('parent_name')}",
                source="breeding_pedigree",
                locator=str(row.get("source_record_no") or ""),
            ) for row in records if not row.get("is_simulated")
        ]
        return _result(
            "read_kinship", records,
            summary=f"读取到 {len(records)} 条系谱关系，其中模拟记录不能作为正式亲缘证据。",
            evidence=evidence,
        )

    def build_validation_plan(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        request = ValidationPlanInput.model_validate(payload)
        plan = {
            "objective": request.objective,
            "required_before_recommendation": [
                "核验亲本材料身份、系谱来源和亲缘系数",
                "补充目标生态区的抽穗期与花期重叠数据",
                "核验目标性状的多年多点原始观测和检测方法",
                "明确必须保留和必须排除的关键等位基因",
            ],
            "field_validation": [
                "设置亲本与组合对照并保留重复",
                "记录结实率、产量构成、抗性、品质和花期",
                "将辅助排序与真实组合后代表现分开评价",
            ],
            "claim_boundary": "当前仅提供数据约束下的辅助推荐，不构成杂交优势或产量预测。",
        }
        return _result(
            "build_validation_plan", plan,
            summary="已生成配组前置核验和田间验证计划。",
            data_classification="desensitized",
        )

    def validate_trial_package(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        project_id = _require_project(context)
        with scoped_session(context) as session:
            row = session.execute(text("""
                SELECT package.id, package.package_code, package.package_name,
                       package.governance_status, package.is_simulated, package.created_at,
                       count(DISTINCT trial.id) AS trial_count,
                       count(DISTINCT entry.id) AS entry_count,
                       count(observation.id) AS observation_count
                FROM trial_data_package package
                LEFT JOIN field_trial trial ON trial.package_id = package.id AND trial.data_status = 'published'
                LEFT JOIN trial_entry entry ON entry.trial_id = trial.id
                LEFT JOIN trial_phenotype_observation observation
                  ON observation.entry_id = entry.id AND observation.publish_status = 'published'
                WHERE package.governance_status = 'published'
                  AND package.project_id = :project_id
                GROUP BY package.id, package.package_code, package.package_name,
                         package.governance_status, package.is_simulated, package.created_at
                ORDER BY package.created_at DESC LIMIT 1
            """), {"project_id": project_id}).mappings().first()
        record = dict(row) if row else {}
        evidence = []
        if row:
            evidence.append(EvidenceReference(
                evidence_id=f"trial-package:{row['id']}",
                title=str(row.get("package_name") or row.get("package_code")),
                source="published_trial_package",
                locator=str(row.get("package_code") or ""),
            ))
        return _result(
            "validate_trial_package", record,
            summary="已核验最新已发布试验资料包及其试验、条目和观测数量。" if row else "没有可用的已发布试验资料包。",
            evidence=evidence,
        )

    def run_trial_statistics(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        project_id = _require_project(context)
        with scoped_session(context) as session:
            evidence_text, cards = build_published_trial_evidence(
                session,
                request.query,
                requested_by=context.owner_user_id,
                project_id=project_id,
            )
        if not evidence_text:
            return ToolResult(
                tool_code="run_trial_statistics",
                status="not_applicable",
                summary="问题未命中已审核的试验统计流程，未运行任何统计模型。",
            )
        evidence = [
            EvidenceReference(
                evidence_id=f"trial-evidence:{index + 1}",
                title=str(card.get("title") or "区域试验证据"),
                source=str(card.get("kind") or "controlled_trial_analysis"),
                locator=str(card.get("detail") or "")[:1000],
            ) for index, card in enumerate(cards[:20])
        ]
        return _result(
            "run_trial_statistics",
            {"controlled_result": evidence_text[:30000], "source_cards": cards[:20]},
            summary="已调用受控本地试验统计/描述性分析流程。",
            evidence=evidence,
        )

    def read_trial_result(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        project_id = _require_project(context)
        with scoped_session(context) as session:
            rows = session.execute(text("""
                SELECT run.id, run.package_id, run.analysis_type, run.analysis_version,
                       run.result_json, run.limitation_note, run.request_question,
                       run.model_formula, run.engine_name, run.source_record_count,
                       run.source_trial_ids, run.completed_at
                FROM trial_analysis_run run
                JOIN trial_data_package package ON package.id = run.package_id
                WHERE run.requested_by = :owner_id AND run.status = 'completed'
                  AND package.project_id = :project_id
                ORDER BY run.completed_at DESC NULLS LAST, run.created_at DESC
                LIMIT :limit
            """), {
                "owner_id": context.owner_user_id,
                "project_id": project_id,
                "limit": request.limit,
            }).mappings().all()
        records = [dict(row) for row in rows]
        evidence = [
            EvidenceReference(
                evidence_id=f"trial-analysis:{row['id']}",
                title=f"试验分析：{row.get('analysis_type')}",
                source=str(row.get("engine_name") or "controlled_trial_analysis"),
                locator=str(row.get("model_formula") or ""),
            ) for row in records
        ]
        return _result(
            "read_trial_result", records,
            summary=f"读取到 {len(records)} 个当前账号的可追溯试验分析结果。",
            evidence=evidence,
        )

    def generate_trial_report(context: AgentToolContext, payload: BaseModel, prior: list[ToolResult]) -> ToolResult:
        request = ValidationPlanInput.model_validate(payload)
        available = [item.tool_code for item in prior if item.status == "completed"]
        outline = {
            "title": "区域试验分析报告",
            "objective": request.objective,
            "sections": ["任务与数据范围", "试验设计核验", "统计方法与版本", "结果", "局限性", "下一轮验证"],
            "available_sources": available,
            "generation_status": "outline_ready",
            "note": "正式报告文件应由报告服务基于受控分析产物生成，不由大模型直接填造统计值。",
        }
        return _result("generate_trial_report", outline, summary="已生成可审计报告大纲。")

    def search_private_knowledge(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        _require_project(context)
        return search_chunks(
            context, SearchInput.model_validate(payload),
            scope_clause=(
                "document.scope = 'private' AND document.owner_id = :owner_id "
                "AND document.project_id = :project_id"
            ),
            tool_code="search_private_knowledge",
            source="private_knowledge",
        )

    def search_public_knowledge(context: AgentToolContext, payload: BaseModel, _prior: list[ToolResult]) -> ToolResult:
        return search_chunks(
            context, SearchInput.model_validate(payload),
            scope_clause="document.scope = 'public'",
            tool_code="search_public_knowledge",
            source="published_knowledge",
            data_classification="public",
        )

    def read_document_evidence(context: AgentToolContext, payload: BaseModel, prior: list[ToolResult]) -> ToolResult:
        request = SearchInput.model_validate(payload)
        prior_rows: list[dict[str, Any]] = []
        prior_evidence: list[EvidenceReference] = []
        for item in prior:
            if item.tool_code not in {"search_private_knowledge", "search_public_knowledge"}:
                continue
            if isinstance(item.data, list):
                prior_rows.extend(item.data)
            prior_evidence.extend(item.evidence)
        rows = prior_rows[: request.limit]
        classification = (
            "institution_private"
            if any(item.data_classification == "institution_private" for item in prior)
            else "public"
        )
        return _result(
            "read_document_evidence", rows,
            summary=f"整理了 {len(rows)} 个已授权文档证据片段。",
            evidence=prior_evidence[: request.limit],
            data_classification=classification,
        )

    def build_evidence_matrix(context: AgentToolContext, payload: BaseModel, prior: list[ToolResult]) -> ToolResult:
        ValidationPlanInput.model_validate(payload)
        matrix = []
        seen: set[str] = set()
        for item in prior:
            for evidence in item.evidence:
                if evidence.evidence_id in seen:
                    continue
                seen.add(evidence.evidence_id)
                matrix.append({
                    "evidence_id": evidence.evidence_id,
                    "title": evidence.title,
                    "source": evidence.source,
                    "locator": evidence.locator,
                    "provided_by_tool": item.tool_code,
                })
        classification = (
            "institution_private"
            if any(item.data_classification == "institution_private" for item in prior)
            else "public"
        )
        return _result(
            "build_evidence_matrix", matrix,
            summary=f"构建了包含 {len(matrix)} 条来源的证据矩阵。",
            evidence=[e for item in prior for e in item.evidence],
            data_classification=classification,
        )

    search_builder = default_arguments
    objective_builder = lambda context, _results: {"objective": context.user_request}
    definitions = (
        ControlledTool("query_germplasm", "检索受控种质材料", SearchInput, query_germplasm, search_builder),
        ControlledTool("read_genotype_qc", "读取当前账号基因型质控结果", SearchInput, read_genotype_qc, search_builder),
        ControlledTool("read_gwas_result", "读取当前账号GWAS结果", SearchInput, read_gwas_result, search_builder),
        ControlledTool("search_gene_evidence", "检索授权基因证据", SearchInput, search_gene_evidence, search_builder),
        ControlledTool("query_parent_candidates", "查询亲本候选观测", SearchInput, query_parent_candidates, search_builder),
        ControlledTool("score_parent_constraints", "执行可解释亲本约束评分", SearchInput, score_parent_constraints, search_builder),
        ControlledTool("read_kinship", "读取系谱和亲缘证据", SearchInput, read_kinship, search_builder),
        ControlledTool("build_validation_plan", "生成配组验证计划", ValidationPlanInput, build_validation_plan, objective_builder),
        ControlledTool("validate_trial_package", "核验试验资料包", SearchInput, validate_trial_package, search_builder),
        ControlledTool("run_trial_statistics", "调用受控本地统计", SearchInput, run_trial_statistics, search_builder, timeout_seconds=180),
        ControlledTool("read_trial_result", "读取可追溯试验结果", SearchInput, read_trial_result, search_builder),
        ControlledTool("generate_trial_report", "生成报告大纲", ValidationPlanInput, generate_trial_report, objective_builder),
        ControlledTool("search_private_knowledge", "检索当前账号私有知识", SearchInput, search_private_knowledge, search_builder),
        ControlledTool("search_public_knowledge", "检索平台已发布知识", SearchInput, search_public_knowledge, search_builder),
        ControlledTool("read_document_evidence", "读取授权文档片段", SearchInput, read_document_evidence, search_builder),
        ControlledTool("build_evidence_matrix", "构建证据矩阵", ValidationPlanInput, build_evidence_matrix, objective_builder),
    )
    for definition in definitions:
        registry.register(definition)
    return registry
