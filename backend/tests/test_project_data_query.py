import unittest

from app.project_data_query import (
    PROJECT_DATASETS,
    ProjectDataQueryError,
    project_data_catalog,
    query_project_data,
)


class _MappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _RecordingSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = ""
        self.parameters = {}

    def execute(self, statement, parameters):
        self.statement = str(statement)
        self.parameters = parameters
        return _MappingsResult(self.rows)


class ProjectDataQueryTests(unittest.TestCase):
    def test_catalog_exposes_business_domains_not_legacy_tables(self):
        codes = [item["code"] for item in project_data_catalog()["datasets"]]
        self.assertEqual(
            codes,
            ["germplasm", "pedigree", "trial_phenotype", "environment", "management", "genotype_assets"],
        )
        self.assertNotIn("rice_phenotype", codes)
        self.assertNotIn("root_phenotype", codes)

    def test_every_dataset_has_an_explicit_project_predicate(self):
        for dataset in PROJECT_DATASETS:
            with self.subTest(dataset=dataset.code):
                self.assertIn(":project_id", dataset.project_predicate)

    def test_query_is_allowlisted_and_project_bound(self):
        session = _RecordingSession([
            {"_row_id": "material-1", "material_code": "M001", "material_name": "耐盐材料一号"},
        ])
        result = query_project_data(
            session,
            project_id="project-1",
            dataset_code="germplasm",
            selected_field_codes=["material_code", "material_name"],
            search="耐盐",
            filters=[],
            limit=100,
            offset=0,
        )
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(session.parameters["project_id"], "project-1")
        self.assertIn("scope.project_id=:project_id", session.statement)
        self.assertNotIn("rice_phenotype", session.statement)

    def test_rejects_unknown_dataset_and_field(self):
        session = _RecordingSession([])
        with self.assertRaises(ProjectDataQueryError):
            query_project_data(
                session,
                project_id="project-1",
                dataset_code="arbitrary_table",
                selected_field_codes=[],
                search="",
                filters=[],
                limit=100,
                offset=0,
            )
        with self.assertRaises(ProjectDataQueryError):
            query_project_data(
                session,
                project_id="project-1",
                dataset_code="germplasm",
                selected_field_codes=["raw_storage_path"],
                search="",
                filters=[],
                limit=100,
                offset=0,
            )


if __name__ == "__main__":
    unittest.main()
