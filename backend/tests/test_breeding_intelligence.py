import unittest

from app.breeding_intelligence import (
    DEFAULT_RECOMMENDATION_WEIGHTS,
    build_intelligence_pdf,
    build_material_analysis_from_records,
    rank_parent_combinations,
    recommendation_csv,
)


def entity(entity_type, entity_key, payload, batch="batch-1", dataset_type="germplasm"):
    return {
        "entity_type": entity_type,
        "entity_key": entity_key,
        "payload": payload,
        "source_batch_id": batch,
        "dataset_type": dataset_type,
        "source_file_name": f"{dataset_type}.xlsx",
        "object_bucket": "longyun-hnnf",
        "object_key": f"project/{dataset_type}.xlsx",
        "file_sha256": "a" * 64,
    }


class GermplasmAnalysisTests(unittest.TestCase):
    def test_analysis_uses_only_available_records_and_names_missing_categories(self):
        result = build_material_analysis_from_records(
            institution_id="hainan-nanfan",
            project_id="project-1",
            material_key="M001",
            entities=[
                entity("germplasm", "M001", {"germplasm_id": "M001", "name": "南繁一号", "aliases": "NF-1"}),
                entity("phenotype_observation", "OBS-1", {"germplasm_id": "M001", "trait_code": "yield", "value": 610, "unit": "kg/亩"}, "batch-2", "phenotype"),
                entity("literature_document", "DOC-1", {"file_name": "南繁一号试验总结.txt", "text": "南繁一号在试验中完成观测。"}, "batch-3", "literature"),
            ],
            relations=[],
            issues=[],
        )
        self.assertTrue(result["sections"]["phenotype"]["available"])
        self.assertTrue(result["sections"]["literature"]["available"])
        self.assertIn("系谱", result["missing_categories"])
        self.assertIn("基因型", result["missing_categories"])
        self.assertEqual(result["evidence_counts"]["phenotype"], 1)
        self.assertGreaterEqual(len(result["sources"]), 3)
        self.assertNotIn("不存在的信息", result["summary"])

    def test_unknown_material_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不存在"):
            build_material_analysis_from_records(
                institution_id="hainan-nanfan", project_id="project-1", material_key="M404",
                entities=[], relations=[], issues=[],
            )


class ParentRecommendationTests(unittest.TestCase):
    def profiles(self):
        return {
            "M001": {
                "material_key": "M001", "name": "南繁一号",
                "traits": {"yield": [610, 605, 620], "lodging": [1], "disease": [2], "quality": [80], "complementarity": [105]},
                "trait_evidence": {"yield": [{"source": {"batch_id": "b1", "entity_key": "o1"}}]},
                "parents": {"P001", "P002"}, "genotype_sample": True, "sources": [{"batch_id": "b0"}],
            },
            "M002": {
                "material_key": "M002", "name": "南繁二号",
                "traits": {"yield": [590, 600, 595], "lodging": [2], "disease": [1], "quality": [85], "complementarity": [92]},
                "trait_evidence": {"yield": [{"source": {"batch_id": "b2", "entity_key": "o2"}}]},
                "parents": {"P003", "P004"}, "genotype_sample": True, "sources": [{"batch_id": "b0"}],
            },
            "M003": {
                "material_key": "M003", "name": "南繁三号",
                "traits": {"yield": [580, 570, 585], "lodging": [3], "disease": [3], "quality": [75], "complementarity": [110]},
                "trait_evidence": {"yield": [{"source": {"batch_id": "b3", "entity_key": "o3"}}]},
                "parents": {"P001", "P005"}, "genotype_sample": False, "sources": [{"batch_id": "b0"}],
            },
        }

    def test_recommendation_is_explicitly_auxiliary_traceable_and_downgrades_missing_genotype(self):
        result = rank_parent_combinations(self.profiles(), DEFAULT_RECOMMENDATION_WEIGHTS, "高产稳产抗病")
        self.assertIn("辅助推荐", result["title"])
        self.assertIn("不是确定性预测", result["disclaimer"])
        self.assertEqual(len(result["recommendations"]), 3)
        top = result["recommendations"][0]
        self.assertTrue(top["recommendation_reasons"])
        self.assertTrue(top["data_gaps"])
        self.assertTrue(any("遗传距离" in gap or "基因型" in gap for gap in top["data_gaps"]))
        self.assertLess(top["evidence_coverage"], 1)

    def test_common_parent_is_reported_as_risk(self):
        result = rank_parent_combinations(self.profiles(), DEFAULT_RECOMMENDATION_WEIGHTS, "高产")
        pair = next(item for item in result["recommendations"] if {item["female_parent"]["material_key"], item["male_parent"]["material_key"]} == {"M001", "M003"})
        self.assertTrue(any("共同亲本" in risk for risk in pair["risks"]))

    def test_reports_are_exportable(self):
        result = rank_parent_combinations(self.profiles(), DEFAULT_RECOMMENDATION_WEIGHTS, "高产")
        self.assertTrue(recommendation_csv(result).startswith(b"\xef\xbb\xbf"))
        self.assertTrue(build_intelligence_pdf("亲本组合辅助推荐报告", result).startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
