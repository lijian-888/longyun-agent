import os
import unittest
import uuid
from urllib.parse import quote

from fastapi.testclient import TestClient
from sqlalchemy import text


@unittest.skipUnless(os.getenv("RUN_DB_INTEGRATION") == "1", "set RUN_DB_INTEGRATION=1 to use the local PostgreSQL demo")
class SinglePlantApiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app import main
        from app.auth import CurrentUser
        from app.tenancy import tenant_database_manager

        cls.main = main
        cls.institution_id = os.getenv("TEST_INSTITUTION_ID", "longyun-demo")
        cls.tenant_database_manager = tenant_database_manager
        cls.created_fixture_ids = None
        with tenant_database_manager.migration_session(cls.institution_id) as session:
            row = session.execute(text(
                """
                SELECT bp.program_code, bp.project_id, bpm.program_id, bm.material_code, bpm.material_id,
                       te.id AS trial_entry_id, ft.trial_code, te.plot_no, tt.treatment_code,
                       gsm.id AS mapping_id, gsm.owner_id, gsm.version_id, gsm.fid, gsm.iid,
                       gsm.sample_id AS old_sample_id, gsm.status AS old_status, gsm.note AS old_note,
                       ga.id AS asset_id
                FROM breeding_program_material bpm
                JOIN breeding_program bp ON bp.id=bpm.program_id
                JOIN breeding_material bm ON bm.id=bpm.material_id
                JOIN trial_entry te ON te.material_id=bpm.material_id
                JOIN field_trial ft ON ft.id=te.trial_id
                JOIN trial_treatment tt ON tt.id=te.treatment_id
                JOIN genotype_sample_mapping gsm ON gsm.material_id=bpm.material_id
                JOIN genotype_asset_version gav ON gav.id=gsm.version_id
                JOIN genotype_asset ga ON ga.id=gav.asset_id
                WHERE gsm.sample_id IS NULL
                  AND bp.project_id IS NOT NULL
                  AND ft.project_id=bp.project_id
                  AND ga.project_id=bp.project_id
                ORDER BY bp.program_code, bm.material_code, te.id, gsm.id
                LIMIT 1
                """
            )).first()
            if not row:
                ids = {name: str(uuid.uuid4()) for name in (
                    "project", "program", "material", "program_material", "package", "site",
                    "trial", "treatment", "entry", "asset", "version", "mapping",
                )}
                suffix = uuid.uuid4().hex[:10]
                owner_id = "codex-integration-researcher"
                session.execute(text("""
                    INSERT INTO research_project(
                        id, institution_id, project_name, research_direction, created_by
                    ) VALUES (
                        :id, :institution_id, :name, 'Single-plant integration', :created_by
                    )
                """), {
                    "id": ids["project"], "institution_id": cls.institution_id,
                    "name": f"Acceptance project {suffix}", "created_by": owner_id,
                })
                session.execute(text("""
                    INSERT INTO project_membership(project_id, institution_id, user_id, project_role)
                    VALUES (:project_id, :institution_id, :user_id, 'member')
                """), {
                    "project_id": ids["project"], "institution_id": cls.institution_id,
                    "user_id": owner_id,
                })
                session.execute(text("""
                    INSERT INTO breeding_program(
                        id, project_id, program_code, program_name, breeding_target
                    ) VALUES (
                        :id, :project_id, :code, 'Acceptance breeding programme',
                        'Single-plant traceability'
                    )
                """), {
                    "id": ids["program"], "project_id": ids["project"],
                    "code": f"IT-PROG-{suffix}",
                })
                session.execute(text("""
                    INSERT INTO breeding_material(id, material_code, material_name, material_type)
                    VALUES (:id, :code, 'Acceptance material', 'breeding_line')
                """), {"id": ids["material"], "code": f"IT-MAT-{suffix}"})
                session.execute(text("""
                    INSERT INTO data_material_project_scope(
                        project_id, material_id, access_level, source, created_by
                    ) VALUES (
                        :project_id, :material_id, 'project', 'integration-test', :created_by
                    )
                """), {
                    "project_id": ids["project"], "material_id": ids["material"],
                    "created_by": owner_id,
                })
                session.execute(text("""
                    INSERT INTO breeding_program_material(id, program_id, material_id, material_role)
                    VALUES (:id, :program_id, :material_id, 'candidate')
                """), {
                    "id": ids["program_material"], "program_id": ids["program"],
                    "material_id": ids["material"],
                })
                session.execute(text("""
                    INSERT INTO trial_data_package(
                        id, project_id, package_code, package_name, dataset_type
                    ) VALUES (
                        :id, :project_id, :code, 'Acceptance trial package', 'single_plant'
                    )
                """), {
                    "id": ids["package"], "project_id": ids["project"],
                    "code": f"IT-PKG-{suffix}",
                })
                session.execute(text("""
                    INSERT INTO trial_site(id, site_code, site_name, ecological_zone)
                    VALUES (:id, :code, 'Acceptance site', 'integration-test-zone')
                """), {"id": ids["site"], "code": f"IT-SITE-{suffix}"})
                session.execute(text("""
                    INSERT INTO field_trial(
                        id, project_id, trial_code, package_id, site_id,
                        trial_year, trial_name, replicate_count
                    ) VALUES (
                        :id, :project_id, :code, :package_id, :site_id,
                        2026, 'Acceptance field trial', 1
                    )
                """), {
                    "id": ids["trial"], "code": f"IT-TRIAL-{suffix}",
                    "project_id": ids["project"], "package_id": ids["package"],
                    "site_id": ids["site"],
                })
                session.execute(text("""
                    INSERT INTO trial_treatment(id, trial_id, treatment_code, treatment_name)
                    VALUES (:id, :trial_id, 'T1', 'Acceptance treatment')
                """), {"id": ids["treatment"], "trial_id": ids["trial"]})
                session.execute(text("""
                    INSERT INTO trial_entry(
                        id, trial_id, treatment_id, material_id, replicate_no, block_no,
                        plot_no, raw_material_name, source_locator
                    ) VALUES (
                        :id, :trial_id, :treatment_id, :material_id, 1, 1,
                        'PLOT-1', 'Acceptance material', 'integration-fixture:1'
                    )
                """), {
                    "id": ids["entry"], "trial_id": ids["trial"],
                    "treatment_id": ids["treatment"], "material_id": ids["material"],
                })
                session.execute(text("""
                    INSERT INTO genotype_asset(
                        id, project_id, owner_id, title, source_format,
                        reference_assembly, population_type, status
                    ) VALUES (
                        :id, :project_id, :owner_id, 'Acceptance genotype asset', 'plink',
                        'IRGSP-1.0', 'breeding_panel', 'published'
                    )
                """), {
                    "id": ids["asset"], "project_id": ids["project"],
                    "owner_id": owner_id,
                })
                session.execute(text("""
                    INSERT INTO genotype_asset_version(
                        id, asset_id, owner_id, version_number, status,
                        qc_template_code, qc_template_version, reference_assembly
                    ) VALUES (
                        :id, :asset_id, :owner_id, 1, 'published',
                        'acceptance', '1.0', 'IRGSP-1.0'
                    )
                """), {
                    "id": ids["version"], "asset_id": ids["asset"], "owner_id": owner_id,
                })
                session.execute(text("""
                    UPDATE genotype_asset SET current_version_id=:version_id WHERE id=:asset_id
                """), {"version_id": ids["version"], "asset_id": ids["asset"]})
                session.execute(text("""
                    INSERT INTO genotype_sample_mapping(
                        id, version_id, owner_id, fid, iid, material_id, status
                    ) VALUES (
                        :id, :version_id, :owner_id, 'FAM-1', 'IND-1', :material_id, 'mapped'
                    )
                """), {
                    "id": ids["mapping"], "version_id": ids["version"],
                    "owner_id": owner_id, "material_id": ids["material"],
                })
                session.commit()
                cls.created_fixture_ids = ids
                row = session.execute(text(
                    """
                    SELECT bp.program_code, bp.project_id, bpm.program_id, bm.material_code, bpm.material_id,
                           te.id AS trial_entry_id, ft.trial_code, te.plot_no, tt.treatment_code,
                           gsm.id AS mapping_id, gsm.owner_id, gsm.version_id, gsm.fid, gsm.iid,
                           gsm.sample_id AS old_sample_id, gsm.status AS old_status, gsm.note AS old_note,
                           ga.id AS asset_id
                    FROM breeding_program_material bpm
                    JOIN breeding_program bp ON bp.id=bpm.program_id
                    JOIN breeding_material bm ON bm.id=bpm.material_id
                    JOIN trial_entry te ON te.material_id=bpm.material_id
                    JOIN field_trial ft ON ft.id=te.trial_id
                    JOIN trial_treatment tt ON tt.id=te.treatment_id
                    JOIN genotype_sample_mapping gsm ON gsm.material_id=bpm.material_id
                    JOIN genotype_asset_version gav ON gav.id=gsm.version_id
                    JOIN genotype_asset ga ON ga.id=gav.asset_id
                    WHERE gsm.id=:mapping_id
                    """
                ), {"mapping_id": ids["mapping"]}).first()
            if row:
                session.execute(text("""
                    INSERT INTO project_membership(project_id, institution_id, user_id, project_role)
                    VALUES (:project_id, :institution_id, :user_id, 'member')
                    ON CONFLICT (project_id, user_id) DO NOTHING
                """), {
                    "project_id": row.project_id,
                    "institution_id": cls.institution_id,
                    "user_id": row.owner_id,
                })
                session.commit()
        if not row:
            raise unittest.SkipTest("demo database has no compatible programme/material/trial/genotype fixture")
        cls.fixture = dict(row._mapping)
        cls.sample_code = f"CODEX-IT-{uuid.uuid4().hex[:12]}"
        cls.sample_id = None
        cls.task_id = None
        cls.survey_unit_id = None
        cls.processor = CurrentUser(
            id="codex-integration-processor",
            username="codex.processor",
            display_name="Codex integration processor",
            roles=frozenset({"data_processor"}),
            institution_id=cls.institution_id,
        )
        cls.researcher = CurrentUser(
            id=cls.fixture["owner_id"],
            username="codex.researcher",
            display_name="Codex integration researcher",
            roles=frozenset({"researcher"}),
            institution_id=cls.institution_id,
        )

        async def processor_override():
            return cls.processor

        async def researcher_override():
            return cls.researcher

        async def platform_override():
            return cls.processor

        def research_session_override():
            session = tenant_database_manager.session_factory(cls.institution_id)()
            try:
                main._set_research_owner(session, cls.researcher.id, cls.institution_id)
                yield session
            finally:
                session.close()

        main.app.dependency_overrides[main.require_data_processor] = processor_override
        main.app.dependency_overrides[main.require_researcher] = researcher_override
        main.app.dependency_overrides[main.require_data_platform_user] = platform_override
        main.app.dependency_overrides[main.require_genotype_user] = researcher_override
        main.app.dependency_overrides[main.get_current_user] = platform_override
        main.app.dependency_overrides[main.get_session] = research_session_override
        main.app.dependency_overrides[main.get_research_session] = research_session_override
        main.app.dependency_overrides[main.get_genotype_session] = research_session_override
        cls.client = TestClient(main.app, base_url="http://localhost")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.main.app.dependency_overrides.clear()
        fixture = cls.fixture
        with cls.tenant_database_manager.migration_session(cls.institution_id) as session:
            session.execute(text(
                """
                UPDATE genotype_sample_mapping
                SET sample_id=:sample_id, status=:status, note=:note, updated_at=now()
                WHERE id=:mapping_id
                """
            ), {
                "sample_id": fixture["old_sample_id"],
                "status": fixture["old_status"],
                "note": fixture["old_note"],
                "mapping_id": fixture["mapping_id"],
            })
            if cls.sample_id:
                session.execute(
                    text("DELETE FROM breeding_selection_record WHERE sample_id=:sample_id"),
                    {"sample_id": cls.sample_id},
                )
                session.execute(
                    text("DELETE FROM field_survey_photo WHERE sample_id=:sample_id"),
                    {"sample_id": cls.sample_id},
                )
                session.execute(text(
                    """
                    DELETE FROM field_survey_plot
                    WHERE id IN (
                      SELECT plot_id FROM field_survey_observation WHERE sample_id=:sample_id
                    )
                    """
                ), {"sample_id": cls.sample_id})
                session.execute(
                    text("DELETE FROM biological_sample WHERE id=:sample_id"),
                    {"sample_id": cls.sample_id},
                )
            if cls.task_id:
                session.execute(text(
                    """
                    DELETE FROM field_survey_task t WHERE t.id=:task_id
                    AND NOT EXISTS (SELECT 1 FROM field_survey_plot p WHERE p.task_id=t.id)
                    """
                ), {"task_id": cls.task_id})
            if cls.created_fixture_ids:
                ids = cls.created_fixture_ids
                session.execute(text("DELETE FROM genotype_asset WHERE id=:id"), {"id": ids["asset"]})
                session.execute(text("DELETE FROM field_trial WHERE id=:id"), {"id": ids["trial"]})
                session.execute(text("DELETE FROM trial_data_package WHERE id=:id"), {"id": ids["package"]})
                session.execute(text("DELETE FROM trial_site WHERE id=:id"), {"id": ids["site"]})
                session.execute(
                    text("DELETE FROM breeding_program_material WHERE id=:id"),
                    {"id": ids["program_material"]},
                )
                session.execute(text("DELETE FROM breeding_material WHERE id=:id"), {"id": ids["material"]})
                session.execute(text("DELETE FROM breeding_program WHERE id=:id"), {"id": ids["program"]})
                session.execute(text("DELETE FROM research_project WHERE id=:id"), {"id": ids["project"]})
            session.commit()

    def test_complete_single_plant_api_flow(self) -> None:
        fixture = self.fixture
        record = {
            "program_code": fixture["program_code"],
            "material_code": fixture["material_code"],
            "sample_code": self.sample_code,
            "sample_type": "individual_plant",
            "trial_code": fixture["trial_code"],
            "treatment_code": fixture["treatment_code"],
            "plot_no": fixture["plot_no"],
            "generation_label": "F6",
            "plant_no": 1,
        }
        preview = self.client.post(
            "/api/single-plants/import/preview-json",
            json={
                "project_id": fixture["project_id"],
                "records": [record],
                "mode": "create_only",
            },
        )
        self.assertEqual(200, preview.status_code, preview.text)
        self.assertTrue(preview.json()["can_publish"])

        published = self.client.post(
            "/api/single-plants/import/publish-json",
            json={
                "project_id": fixture["project_id"],
                "records": [record],
                "mode": "create_only",
            },
        )
        self.assertEqual(200, published.status_code, published.text)
        self.__class__.sample_id = published.json()["sample_ids"][0]

        lookup = self.client.get(
            f"/api/single-plants/lookup?keyword={quote(self.sample_code, safe='')}"
            f"&project_id={fixture['project_id']}"
        )
        self.assertEqual(200, lookup.status_code, lookup.text)
        self.assertEqual(self.sample_id, lookup.json()[0]["id"])

        observation = self.client.post(
            f"/api/single-plants/{self.sample_id}/observations"
            f"?project_id={fixture['project_id']}",
            json={
                "observation_stage": "成熟期",
                "trait_code": "plant_height",
                "trait_name": "株高",
                "value_numeric": 102.5,
                "unit": "cm",
            },
        )
        self.assertEqual(200, observation.status_code, observation.text)
        self.__class__.task_id = observation.json()["task_id"]
        self.__class__.survey_unit_id = observation.json()["survey_unit_id"]

        mapping_path = (
            f"/api/genotype-assets/{fixture['asset_id']}/versions/{fixture['version_id']}"
            f"/mappings/{quote(fixture['fid'], safe='')}/{quote(fixture['iid'], safe='')}/single-plant"
        )
        mapping = self.client.patch(
            f"{mapping_path}?project_id={fixture['project_id']}",
            json={"sample_id": self.sample_id, "note": "single-plant integration verification"},
        )
        self.assertEqual(200, mapping.status_code, mapping.text)
        self.assertEqual(self.sample_id, mapping.json()["sample_id"])

        genotype_version = self.client.get(
            f"/api/genotype-assets/{fixture['asset_id']}?version_id={fixture['version_id']}"
            f"&project_id={fixture['project_id']}"
        )
        self.assertEqual(200, genotype_version.status_code, genotype_version.text)
        refreshed_mapping = next(
            item for item in genotype_version.json()["mappings"] if item["id"] == fixture["mapping_id"]
        )
        self.assertEqual(self.sample_id, refreshed_mapping["sample_id"])
        self.assertEqual(self.sample_code, refreshed_mapping["sample_code"])

        decision = self.client.post(
            f"/api/single-plants/{self.sample_id}/selection-decisions"
            f"?project_id={fixture['project_id']}",
            json={
                "decision": "retained",
                "selection_criterion": "株高与基因型证据联合复核",
                "evidence_summary": "集成验证记录",
            },
        )
        self.assertEqual(200, decision.status_code, decision.text)

        detail = self.client.get(
            f"/api/single-plants/{self.sample_id}?project_id={fixture['project_id']}"
        )
        self.assertEqual(200, detail.status_code, detail.text)
        body = detail.json()
        self.assertEqual("retained", body["sample"]["selection_status"])
        self.assertEqual(1, body["evidence_counts"]["observations"])
        self.assertEqual(1, body["evidence_counts"]["genotype_mappings"])
        self.assertEqual(1, body["evidence_counts"]["selection_records"])

        listed = self.client.get(
            f"/api/materials/{fixture['material_id']}/single-plants"
            f"?project_id={fixture['project_id']}"
        )
        self.assertEqual(200, listed.status_code, listed.text)
        self.assertIn(self.sample_id, {item["id"] for item in listed.json()})


if __name__ == "__main__":
    unittest.main()
