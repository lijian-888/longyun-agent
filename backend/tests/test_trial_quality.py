import unittest

from app.trial_statistics import quality_check_from_records


class TrialQualityCheckTests(unittest.TestCase):
    def test_missing_outlier_and_structure_issues_are_explicit(self):
        records = [
            {"trait_code": "yield_per_mu", "value_numeric": value, "source_locator": f"yield.xlsx/row-{index}"}
            for index, value in enumerate([500, 501, 502, 503, 504, 505, 506, 900], start=1)
        ]
        records.append({"trait_code": "plant_height", "value_numeric": None, "source_locator": "height.xlsx/row-9"})
        result = quality_check_from_records(records, [{
            "trial_id": "trial-1", "trial_code": "T-1",
            "design_validation_status": "blocked", "design_metadata": {"issues": ["缺少一个区组"]},
        }])
        self.assertEqual(result["missing_value_count"], 1)
        self.assertEqual(result["outlier_count"], 1)
        self.assertGreaterEqual(result["structure_issue_count"], 2)
        self.assertIn("不自动删除", result["outliers"][0]["message"])


if __name__ == "__main__":
    unittest.main()
