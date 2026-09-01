"""Operational acceptance for Task 4–7 against an explicitly named project.

The target project must already contain the governed institution demo, a
published regional-trial package and at least one reviewed/published knowledge
document.  The script creates traceable trial-analysis runs but does not alter
source data.
"""

from __future__ import annotations

import argparse
import json
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.breeding_intelligence import (
    DEFAULT_RECOMMENDATION_WEIGHTS,
    build_intelligence_pdf,
    build_material_analysis,
    recommendation_csv,
    run_parent_recommendation,
)
from app.institution_data import InstitutionDataSettings, InstitutionDatabaseManager
from app.main import (
    KnowledgeDocument,
    SessionLocal,
    _set_active_project,
    _set_knowledge_context,
    build_knowledge_evidence_context,
)
from app.research_report import build_analysis_chart_png
from app.trial_statistics import run_controlled_trial_analysis


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--institution-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--materials", nargs="+", required=True)
    args = parser.parse_args()

    settings = InstitutionDataSettings.from_env()
    institution_engine = InstitutionDatabaseManager(settings).engine(args.database)
    material_result = build_material_analysis(
        institution_engine, args.institution_id, args.project_id, args.materials[0]
    )
    assert material_result["sources"], "AC4.3/AC4.5: no traceable source"
    assert material_result["missing_categories"] is not None, "AC4.4: no missing-data declaration"
    material_pdf = build_intelligence_pdf("种质资源综合解析报告", material_result)
    assert material_pdf.startswith(b"%PDF")

    recommendation = run_parent_recommendation(
        institution_engine,
        args.institution_id,
        args.project_id,
        args.materials,
        DEFAULT_RECOMMENDATION_WEIGHTS,
        "验收：兼顾高产、稳产、抗倒伏、抗病和品质",
        ["避免共同亲本", "优先抗倒伏"],
        {"minimum_evidence_coverage": 0.25},
        "evidence_coverage",
    )
    assert recommendation["recommendations"], "AC5.1: no recommendation"
    assert "辅助推荐" in recommendation["title"] and "不是确定性预测" in recommendation["disclaimer"]
    assert all(item["recommendation_reasons"] and item["data_gaps"] for item in recommendation["recommendations"])
    assert recommendation["applied_constraints"] and recommendation["sort_mode"] == "evidence_coverage"
    assert recommendation["filter_settings"]["exclude_common_parent"] is True
    assert recommendation["constraint_weight_adjustments"].get("lodging")
    assert recommendation["sources"], "AC5.4: governed evidence sources are not traceable"
    assert build_intelligence_pdf("亲本组合辅助推荐报告", recommendation).startswith(b"%PDF")
    assert recommendation_csv(recommendation).startswith(b"\xef\xbb\xbf")

    main_engine = create_engine(settings.migration_database_url, pool_pre_ping=True)
    with Session(main_engine) as session:
        package = session.execute(text("""
            SELECT id, package_code, package_name, created_at
            FROM trial_data_package
            WHERE project_id=:project_id AND governance_status='published'
            ORDER BY created_at DESC LIMIT 1
        """), {"project_id": args.project_id}).mappings().first()
        assert package, "AC6.1: no published regional-trial package"
        trial_results = []
        trial_scope = session.execute(text("""
            SELECT trial.trial_year, site.site_name, material.material_code
            FROM field_trial trial
            JOIN trial_site site ON site.id=trial.site_id
            JOIN trial_entry entry ON entry.trial_id=trial.id
            JOIN breeding_material material ON material.id=entry.material_id
            WHERE trial.package_id=:package_id AND trial.data_status='published' AND material.is_check=FALSE
            ORDER BY trial.trial_year DESC, site.site_name, material.material_code LIMIT 1
        """), {"package_id": package["id"]}).mappings().one()
        questions = (
            f"同一试验 {trial_scope['trial_year']} {trial_scope['site_name']} 标准施氮全部材料方差分析和 Tukey 多重比较",
            "多年多点平均产量、相对增产、波动和有效环境稳定性分析",
            "土壤 pH、有效磷、降雨和环境影响关联分析",
            "不同施氮管理措施与材料施氮交互效应分析",
            "高产材料的产量、抗病、株高和米质权衡分析",
            f"{trial_scope['material_code']} 材料表现下降或异常的环境、病害和性状证据拆解",
        )
        for question in questions:
            result = run_controlled_trial_analysis(session, dict(package), question, "task4-7-acceptance")
            assert result and result.get("analysis_run_id")
            assert result.get("quality_check") and result["quality_check"].get("method")
            assert result["quality_check"].get("scope", {}).get("source_trial_ids")
            assert result["quality_check"].get("status") in {"passed", "passed_with_warnings"}
            assert build_analysis_chart_png(result), f"AC6.3: {result['analysis_type']} generated no chart"
            trial_results.append({"analysis_type": result["analysis_type"], "run_id": result["analysis_run_id"]})
        session.commit()
        knowledge = session.execute(text("""
            SELECT COUNT(DISTINCT document.id) AS document_count,
                   COUNT(chunk.id) AS chunk_count,
                   COUNT(*) FILTER (WHERE document.authorization_basis IS NULL OR document.license_scope IS NULL) AS unauthorized_count
            FROM knowledge_document document
            LEFT JOIN knowledge_chunk chunk ON chunk.document_id=document.id AND chunk.document_status='published'
            WHERE document.project_id=:project_id AND document.scope='public' AND document.status='published'
        """), {"project_id": args.project_id}).mappings().one()
        assert knowledge["document_count"] >= 1 and knowledge["chunk_count"] >= 1, "AC7.1: no published indexed document"
        assert knowledge["unauthorized_count"] == 0, "AC7.5: published document lacks authorization metadata"

    researcher = CurrentUser(
        id="task4-7-acceptance-researcher",
        username="wang.researcher",
        display_name="任务4-7验收科研人员",
        roles=frozenset({"researcher"}),
    )
    with SessionLocal() as retrieval_session:
        _set_knowledge_context(retrieval_session, researcher)
        _set_active_project(retrieval_session, args.project_id)
        knowledge_context, knowledge_cards = build_knowledge_evidence_context(
            retrieval_session,
            researcher,
            "public",
            "HNNF-G001、HNNF-G002 的种质解析和亲本辅助推荐有哪些可引用证据？",
        )
        assert knowledge_cards, "AC7.2/AC7.3: semantic retrieval returned no cited evidence"
        assert "来源：" in knowledge_context and "授权范围：" in knowledge_context, "AC7.3: evidence lacks source or license"
        assert all(item.get("excerpt") and item.get("detail") for item in knowledge_cards), "AC7.3/AC7.4: citation cards are incomplete"

        no_evidence_context, no_evidence_cards = build_knowledge_evidence_context(
            retrieval_session, researcher, "private", "不存在于本账号知识库的验收问题",
        )
        assert not no_evidence_cards and "未检索到" in no_evidence_context, "AC7.4: no-evidence uncertainty is not explicit"

    # Database-enforced isolation check.  The row is never committed: user A
    # can insert it, user B cannot see it, and rollback removes the test row.
    owner_a = CurrentUser("task47-owner-a", "task47.owner.a", "验收账号A", frozenset({"researcher"}))
    owner_b = CurrentUser("task47-owner-b", "task47.owner.b", "验收账号B", frozenset({"researcher"}))
    with SessionLocal() as isolation_session:
        _set_knowledge_context(isolation_session, owner_a)
        _set_active_project(isolation_session, args.project_id)
        document_id = str(uuid.uuid4())
        isolation_session.add(KnowledgeDocument(
            id=document_id, project_id=args.project_id, scope="private", owner_id=owner_a.id,
            original_file_name="task47-rls-check.txt", display_title="任务4-7私人知识隔离验收",
            content_type="text/plain", size_bytes=1, content_hash="0" * 64,
            storage_path="transaction-only", status="ready", parsing_status="parsed", indexing_status="ready",
        ))
        isolation_session.flush()
        _set_knowledge_context(isolation_session, owner_b)
        _set_active_project(isolation_session, args.project_id)
        visible_count = isolation_session.execute(
            text("SELECT COUNT(*) FROM knowledge_document WHERE id=:document_id"),
            {"document_id": document_id},
        ).scalar_one()
        assert visible_count == 0, "AC7.5: another account can read a private knowledge document"
        isolation_session.rollback()

    print(json.dumps({
        "status": "passed",
        "project_id": args.project_id,
        "task4": {"material": material_result["material_key"], "sources": len(material_result["sources"]), "missing": material_result["missing_categories"]},
        "task5": {"combinations": len(recommendation["recommendations"]), "top_confidence": recommendation["recommendations"][0]["confidence"]},
        "task6": trial_results,
        "task7": {**dict(knowledge), "retrieval_evidence_count": len(knowledge_cards), "private_rls_isolation": "passed", "no_evidence_uncertainty": "passed"},
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
