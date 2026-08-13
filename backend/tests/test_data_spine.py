from __future__ import annotations

import unittest

from app.data_spine import feature_readiness, require_data_domain


def _by_code(rows: list[dict]) -> dict[str, dict]:
    return {row["feature_code"]: row for row in rows}


class DataSpineReadinessTests(unittest.TestCase):
    def test_empty_project_is_blocked_and_explains_missing_data(self) -> None:
        results = _by_code(feature_readiness([]))

        germplasm = results["germplasm_intelligence"]
        self.assertEqual(germplasm["readiness_status"], "blocked")
        self.assertEqual(germplasm["missing_required"], ["germplasm"])
        self.assertEqual(germplasm["readiness_score"], 0.0)
        self.assertTrue(any(item["effect"] for item in germplasm["missing_data_effects"]))

        trial = results["trial_analysis"]
        self.assertEqual(trial["missing_required"], ["trial", "phenotype"])

    def test_parent_assistance_becomes_basic_ready_with_required_domains(self) -> None:
        results = _by_code(feature_readiness(["germplasm", "pedigree", "phenotype"]))
        parent = results["parent_selection_assistance"]

        self.assertEqual(parent["readiness_status"], "basic_ready")
        self.assertEqual(parent["readiness_score"], 80.0)
        self.assertEqual(parent["missing_required"], [])
        self.assertEqual(parent["missing_recommended"], ["genotype", "environment", "trial"])

    def test_all_evidence_domains_make_every_feature_fully_ready(self) -> None:
        results = feature_readiness(
            [
                "germplasm",
                "pedigree",
                "phenotype",
                "environment",
                "management",
                "genotype",
                "trial",
                "literature",
            ]
        )

        self.assertTrue(all(item["readiness_status"] == "fully_ready" for item in results))
        self.assertTrue(all(item["readiness_score"] == 100.0 for item in results))

    def test_unknown_domain_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported data domain"):
            require_data_domain("unknown")
