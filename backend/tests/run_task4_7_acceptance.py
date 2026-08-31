"""Operational acceptance for Task 4–7 against an explicitly named project.

The target project must already contain the governed institution demo, a
published regional-trial package and at least one reviewed/published knowledge
document.  The script creates traceable trial-analysis runs but does not alter
source data.
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.breeding_intelligence import (
    DEFAULT_RECOMMENDATION_WEIGHTS,
    build_intelligence_pdf,
    build_material_analysis,
    recommendation_csv,
    run_parent_recommendation,
)
from app.institution_data import InstitutionDataSettings, InstitutionDatabaseManager
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
        ["避免未提示的近缘风险"],
    )
    assert recommendation["recommendations"], "AC5.1: no recommendation"
    assert "辅助推荐" in recommendation["title"] and "不是确定性预测" in recommendation["disclaimer"]
    assert all(item["recommendation_reasons"] and item["data_gaps"] for item in recommendation["recommendations"])
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
        questions = (
            "多年多点平均产量、相对增产、波动和有效环境稳定性分析",
            "土壤 pH、有效磷、降雨和环境影响关联分析",
            "不同施氮管理措施与材料施氮交互效应分析",
            "高产材料的产量、抗病、株高和米质权衡分析",
        )
        for question in questions:
            result = run_controlled_trial_analysis(session, dict(package), question, "task4-7-acceptance")
            assert result and result.get("analysis_run_id")
            assert result.get("quality_check") and result["quality_check"].get("method")
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

    print(json.dumps({
        "status": "passed",
        "project_id": args.project_id,
        "task4": {"material": material_result["material_key"], "sources": len(material_result["sources"]), "missing": material_result["missing_categories"]},
        "task5": {"combinations": len(recommendation["recommendations"]), "top_confidence": recommendation["recommendations"][0]["confidence"]},
        "task6": trial_results,
        "task7": dict(knowledge),
    }, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
