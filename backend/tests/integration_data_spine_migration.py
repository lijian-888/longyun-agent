"""Isolated PostgreSQL smoke test for the versioned data spine.

Run this only against a disposable database. The caller supplies DATABASE_URL,
MIGRATION_DATABASE_URL and DEFAULT_INSTITUTION_ID.
"""

from __future__ import annotations

import os
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.data_spine import create_import_batch
from app.schema_migrations import run_business_schema_migrations


HISTORICAL_SCHEMA = (
    "CREATE TABLE institution(id VARCHAR(64) PRIMARY KEY, name VARCHAR(240) NOT NULL)",
    """
    CREATE TABLE research_project(
        id VARCHAR(36) PRIMARY KEY,
        institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
        project_name VARCHAR(240) NOT NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'active',
        created_by VARCHAR(120) NOT NULL
    )
    """,
    """
    CREATE TABLE project_membership(
        project_id VARCHAR(36) NOT NULL REFERENCES research_project(id),
        institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
        user_id VARCHAR(120) NOT NULL,
        project_role VARCHAR(40) NOT NULL,
        PRIMARY KEY(project_id, user_id)
    )
    """,
    """
    CREATE TABLE breeding_material(
        id VARCHAR(36) PRIMARY KEY,
        material_code VARCHAR(100) NOT NULL UNIQUE,
        material_name VARCHAR(200) NOT NULL
    )
    """,
    "CREATE TABLE variety_basic(id VARCHAR(36) PRIMARY KEY, variety_name VARCHAR(200) NOT NULL)",
    "CREATE TABLE trial_data_package(id VARCHAR(36) PRIMARY KEY)",
    "CREATE TABLE field_trial(id VARCHAR(36) PRIMARY KEY, package_id VARCHAR(36) REFERENCES trial_data_package(id))",
    "CREATE TABLE trial_entry(id VARCHAR(36) PRIMARY KEY, trial_id VARCHAR(36) REFERENCES field_trial(id))",
    "CREATE TABLE genotype_asset(id VARCHAR(36) PRIMARY KEY)",
    "CREATE TABLE knowledge_document(id VARCHAR(36) PRIMARY KEY)",
    "CREATE TABLE knowledge_chunk(id VARCHAR(36) PRIMARY KEY, document_id VARCHAR(36) REFERENCES knowledge_document(id))",
    "CREATE TABLE research_session(id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(120) NOT NULL DEFAULT 'migration-owner')",
    """
    CREATE TABLE breeding_program(
        id VARCHAR(36) PRIMARY KEY,
        program_code VARCHAR(100) UNIQUE,
        is_simulated BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    """
    CREATE TABLE breeding_program_material(
        program_id VARCHAR(36) NOT NULL REFERENCES breeding_program(id),
        material_id VARCHAR(36) NOT NULL REFERENCES breeding_material(id),
        is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY(program_id, material_id)
    )
    """,
    """
    CREATE TABLE breeding_pedigree_relationship(
        id VARCHAR(36) PRIMARY KEY,
        child_material_id VARCHAR(36) NOT NULL REFERENCES breeding_material(id),
        parent_material_id VARCHAR(36) NOT NULL REFERENCES breeding_material(id),
        parent_role VARCHAR(40) NOT NULL,
        relationship_type VARCHAR(80) NOT NULL DEFAULT 'hybrid_parent',
        parent_origin TEXT,
        parent_trait_summary TEXT,
        combination_basis TEXT,
        source_record_no VARCHAR(120),
        source_note TEXT,
        is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE(child_material_id, parent_material_id, parent_role)
    )
    """,
    "CREATE TABLE breeding_generation_record(id VARCHAR(36) PRIMARY KEY, program_id VARCHAR(36) REFERENCES breeding_program(id))",
    "CREATE TABLE trial_environment_metric(id VARCHAR(36) PRIMARY KEY, trial_id VARCHAR(36) REFERENCES field_trial(id))",
    "CREATE TABLE trial_treatment(id VARCHAR(36) PRIMARY KEY, trial_id VARCHAR(36) REFERENCES field_trial(id))",
    "CREATE TABLE trial_management_event(id VARCHAR(36) PRIMARY KEY, treatment_id VARCHAR(36) REFERENCES trial_treatment(id))",
    "CREATE TABLE trial_phenotype_observation(id VARCHAR(36) PRIMARY KEY, entry_id VARCHAR(36) REFERENCES trial_entry(id))",
    "CREATE TABLE trial_source_file(id VARCHAR(36) PRIMARY KEY, package_id VARCHAR(36) REFERENCES trial_data_package(id))",
    "CREATE TABLE trial_analysis_run(id VARCHAR(36) PRIMARY KEY, package_id VARCHAR(36) REFERENCES trial_data_package(id))",
    "CREATE VIEW v_trial_material_summary AS SELECT id AS trial_id FROM field_trial",
    "CREATE TABLE source_review(id VARCHAR(36) PRIMARY KEY, file_hash VARCHAR(64))",
    "CREATE TABLE phenotype_observation(id VARCHAR(36) PRIMARY KEY, source_review_id VARCHAR(36) REFERENCES source_review(id), variety_id VARCHAR(36), trait_code VARCHAR(120))",
    "CREATE TABLE root_phenotype_observation(id VARCHAR(36) PRIMARY KEY, source_review_id VARCHAR(36) REFERENCES source_review(id), variety_id VARCHAR(36), trait_code VARCHAR(120))",
    "CREATE TABLE biological_sample(id VARCHAR(36) PRIMARY KEY, program_id VARCHAR(36), material_id VARCHAR(36), trial_entry_id VARCHAR(36) REFERENCES trial_entry(id), sample_code VARCHAR(120), data_status VARCHAR(30))",
    "CREATE TABLE field_survey_observation(id VARCHAR(36) PRIMARY KEY, sample_id VARCHAR(36) REFERENCES biological_sample(id))",
    "CREATE TABLE field_survey_photo(id VARCHAR(36) PRIMARY KEY, sample_id VARCHAR(36) REFERENCES biological_sample(id))",
    "CREATE TABLE breeding_selection_record(id VARCHAR(36) PRIMARY KEY, sample_id VARCHAR(36) REFERENCES biological_sample(id))",
    "CREATE TABLE research_attachment(id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(120) NOT NULL DEFAULT 'migration-owner')",
    "CREATE TABLE research_result(id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(120) NOT NULL DEFAULT 'migration-owner')",
    "CREATE TABLE gwas_analysis_plan(id VARCHAR(36) PRIMARY KEY, owner_id VARCHAR(120) NOT NULL DEFAULT 'migration-owner')",
    """
    CREATE TABLE agent_workflow_run(
        id VARCHAR(36) PRIMARY KEY,
        institution_id VARCHAR(64) NOT NULL,
        project_id VARCHAR(36),
        owner_id VARCHAR(120) NOT NULL,
        idempotency_key VARCHAR(120)
    )
    """,
    "CREATE TABLE template_version(id VARCHAR(36) PRIMARY KEY)",
    """
    CREATE TABLE trial_import_batch(
        id VARCHAR(36) PRIMARY KEY,
        published_package_id VARCHAR(36) REFERENCES trial_data_package(id)
    )
    """,
)


def _set_request_context(
    session: Session,
    *,
    user_id: str,
    institution_id: str,
    project_id: str,
    institution_admin: bool = False,
) -> None:
    session.execute(
        text("SELECT set_config('app.research_user_id', :value, true)"),
        {"value": user_id},
    )
    session.execute(
        text("SELECT set_config('app.institution_id', :value, true)"),
        {"value": institution_id},
    )
    session.execute(
        text("SELECT set_config('app.institution_admin', :value, true)"),
        {"value": "true" if institution_admin else "false"},
    )
    session.execute(
        text("SELECT set_config('app.project_id', :value, true)"),
        {"value": project_id},
    )


def run() -> None:
    institution_id = os.environ["DEFAULT_INSTITUTION_ID"]
    migration_engine = create_engine(os.environ["MIGRATION_DATABASE_URL"], pool_pre_ping=True)
    application_url = make_url(os.environ["DATABASE_URL"])
    application_role = application_url.username
    application_password = application_url.password
    migration_role = make_url(os.environ["MIGRATION_DATABASE_URL"]).username
    if not application_role or not application_password or application_role == migration_role:
        raise RuntimeError(
            "DATABASE_URL must use a dedicated non-owner application role with a password"
        )
    project_id = str(uuid4())
    second_project_id = str(uuid4())
    project_a_material_id = str(uuid4())
    project_b_material_id = str(uuid4())
    base_program_id = str(uuid4())
    base_material_id = str(uuid4())
    base_sample_id = str(uuid4())
    owner_id = f"{institution_id}-member-a"
    outsider_id = f"{institution_id}-member-b"
    with migration_engine.begin() as connection:
        for statement in HISTORICAL_SCHEMA:
            connection.execute(text(statement))
        connection.execute(
            text("INSERT INTO institution(id, name) VALUES (:id, '隔离测试机构')"),
            {"id": institution_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO research_project(
                    id, institution_id, project_name, created_by
                ) VALUES (:id, :institution_id, '迁移隔离测试课题二', :owner_id)
                """
            ),
            {"id": second_project_id, "institution_id": institution_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO breeding_material(id, material_code, material_name)
                VALUES ('material-before-migration', 'MAT-001', '迁移前材料')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO project_membership(
                    project_id, institution_id, user_id, project_role
                ) VALUES (:project_id, :institution_id, :owner_id, 'member')
                """
            ),
            {
                "project_id": second_project_id,
                "institution_id": institution_id,
                "owner_id": owner_id,
            },
        )
    run_business_schema_migrations(os.environ["MIGRATION_DATABASE_URL"])

    with migration_engine.begin() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == "0009_project_material_evidence", revision
        tables = {
            row[0]
            for row in connection.execute(
                text(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema='public' AND table_name LIKE 'data_%'
                    """
                )
            )
        }
        assert {
            "data_file_asset",
            "data_import_batch",
            "data_import_file",
            "data_field_mapping",
            "data_import_row_error",
            "data_entity_lineage",
            "data_project_completeness",
            "data_mapping_profile",
            "data_import_staging_row",
            "data_entity_identifier",
            "data_record_revision",
        }.issubset(tables)
        preserved = connection.scalar(
            text("SELECT count(*) FROM breeding_material WHERE material_code='MAT-001'")
        )
        assert preserved == 1, "migration lost a historical material record"
        for table_name in (
            "data_material_identifier",
            "data_material_alias",
            "data_legacy_variety_material_link",
            "breeding_pedigree_relationship",
        ):
            project_column = connection.scalar(text("""
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:table_name
                  AND column_name='project_id'
            """), {"table_name": table_name})
            assert project_column == 1, f"{table_name}.project_id was not created"
        connection.execute(
            text(
                """
                INSERT INTO research_project(
                    id, institution_id, project_name, created_by
                ) VALUES (:id, :institution_id, '迁移隔离测试课题', :owner_id)
                """
            ),
            {"id": project_id, "institution_id": institution_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO project_membership(
                    project_id, institution_id, user_id, project_role
                ) VALUES (:project_id, :institution_id, :owner_id, 'member')
                """
            ),
            {
                "project_id": project_id,
                "institution_id": institution_id,
                "owner_id": owner_id,
            },
        )
        connection.execute(text("""
            INSERT INTO breeding_material(id, material_code, material_name)
            VALUES
                (:material_a, 'PROJECT-A-MAT', '课题甲同名材料'),
                (:material_b, 'PROJECT-B-MAT', '课题乙同名材料')
        """), {"material_a": project_a_material_id, "material_b": project_b_material_id})
        connection.execute(text("""
            INSERT INTO data_material_project_scope(
                project_id, material_id, access_level, source, created_by
            ) VALUES
                (:project_a, :material_a, 'project', 'integration-test', :owner_id),
                (:project_b, :material_b, 'project', 'integration-test', :owner_id)
        """), {
            "project_a": project_id,
            "project_b": second_project_id,
            "material_a": project_a_material_id,
            "material_b": project_b_material_id,
            "owner_id": owner_id,
        })
        connection.execute(text("""
            INSERT INTO data_material_identifier(
                id, project_id, material_id, source_system, identifier_type,
                identifier_value, normalized_value, is_primary, created_by
            ) VALUES
                (:id_a, :project_a, :material_a, 'institution', 'institution_material_code',
                 '共享编号', 'shared-code', TRUE, :owner_id),
                (:id_b, :project_b, :material_b, 'institution', 'institution_material_code',
                 '共享编号', 'shared-code', TRUE, :owner_id)
        """), {
            "id_a": str(uuid4()),
            "id_b": str(uuid4()),
            "project_a": project_id,
            "project_b": second_project_id,
            "material_a": project_a_material_id,
            "material_b": project_b_material_id,
            "owner_id": owner_id,
        })
        connection.execute(
            text(
                """
                INSERT INTO breeding_material(id, material_code, material_name)
                VALUES (:id, 'BASE-SHOWCASE-MATERIAL', '基地大屏展示材料')
                """
            ),
            {"id": base_material_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO breeding_program(id, program_code, is_simulated, project_id)
                VALUES (:id, 'JX-RICE-DEMO-2021', TRUE, NULL)
                """
            ),
            {"id": base_program_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO biological_sample(
                    id, program_id, material_id, sample_code, data_status, project_id
                ) VALUES (
                    :id, :program_id, :material_id, 'BASE-PLOT-001', 'published', NULL
                )
                """
            ),
            {
                "id": base_sample_id,
                "program_id": base_program_id,
                "material_id": base_material_id,
            },
        )
        quoted_role = connection.dialect.identifier_preparer.quote(application_role)
        role_exists = connection.scalar(
            text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role_name)"),
            {"role_name": application_role},
        )
        if not role_exists:
            connection.execute(text(
                f"CREATE ROLE {quoted_role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS"
            ))
        escaped_password = application_password.replace("'", "''")
        connection.execute(text(f"ALTER ROLE {quoted_role} PASSWORD '{escaped_password}'"))
        connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {quoted_role}"))
        connection.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                f"IN SCHEMA public TO {quoted_role}"
            )
        )
        connection.execute(
            text(
                "GRANT EXECUTE ON FUNCTION longyun_can_access_project(VARCHAR) "
                f"TO {quoted_role}"
            )
        )

    application_engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    with Session(application_engine, expire_on_commit=False) as session:
        _set_request_context(
            session,
            user_id=owner_id,
            institution_id=institution_id,
            project_id=project_id,
        )
        batch = create_import_batch(
            session,
            project_id=project_id,
            display_name="种质资源隔离测试",
            data_domain="germplasm",
            created_by=owner_id,
        )
        batch_id = batch["id"]

    with Session(application_engine) as session:
        _set_request_context(
            session,
            user_id=outsider_id,
            institution_id=institution_id,
            project_id=project_id,
        )
        invisible = session.scalar(
            text("SELECT count(*) FROM data_import_batch WHERE id=:batch_id"),
            {"batch_id": batch_id},
        )
        assert invisible == 0, "non-member read another project's import batch"

    # The same verified user belongs to both projects. Merely having both
    # memberships must not make identity evidence from both visible at once;
    # app.project_id is the mandatory second boundary.
    with Session(application_engine) as session:
        _set_request_context(
            session,
            user_id=owner_id,
            institution_id=institution_id,
            project_id=project_id,
        )
        visible_a = session.scalar(text(
            "SELECT count(*) FROM data_material_identifier WHERE normalized_value='shared-code'"
        ))
        leaked_b = session.scalar(text(
            "SELECT count(*) FROM data_material_identifier WHERE material_id=:material_b"
        ), {"material_b": project_b_material_id})
        assert visible_a == 1, "active project lost its material identity evidence"
        assert leaked_b == 0, "project A could read project B material identity evidence"

    with Session(application_engine) as session:
        _set_request_context(
            session,
            user_id=owner_id,
            institution_id=institution_id,
            project_id=second_project_id,
        )
        visible_b = session.scalar(text(
            "SELECT count(*) FROM data_material_identifier WHERE normalized_value='shared-code'"
        ))
        leaked_a = session.scalar(text(
            "SELECT count(*) FROM data_material_identifier WHERE material_id=:material_a"
        ), {"material_a": project_a_material_id})
        assert visible_b == 1, "second project lost its material identity evidence"
        assert leaked_a == 0, "project B could read project A material identity evidence"

    with Session(application_engine) as session:
        _set_request_context(
            session,
            user_id=outsider_id,
            institution_id=institution_id,
            project_id=project_id,
            institution_admin=True,
        )
        visible_to_processor = session.scalar(
            text("SELECT count(*) FROM data_import_batch WHERE id=:batch_id"),
            {"batch_id": batch_id},
        )
        assert visible_to_processor == 1, "institution data processor lost institution-level access"

    with Session(application_engine) as session:
        _set_request_context(
            session,
            user_id=outsider_id,
            institution_id=institution_id,
            project_id=project_id,
        )
        visible_showcase = session.scalar(
            text("SELECT count(*) FROM biological_sample WHERE id=:sample_id"),
            {"sample_id": base_sample_id},
        )
        assert visible_showcase == 1, "approved base showcase became unreadable"
        update_result = session.execute(
            text("UPDATE biological_sample SET sample_code='ILLEGAL-WRITE' WHERE id=:sample_id"),
            {"sample_id": base_sample_id},
        )
        assert update_result.rowcount == 0, "base showcase read policy accidentally allowed writes"

    print(f"{institution_id}: revision=0009_project_material_evidence rls=passed")


if __name__ == "__main__":
    run()
