"""Enforce strict project partitions for legacy intake and single-plant data.

Revision ID: 0007_strict_project_partitions
Revises: 0006_real_institutional_intake
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_strict_project_partitions"
down_revision = "0006_real_institutional_intake"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _strict_project_policy(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS project_scoped_intake ON {table_name}"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS data_spine_access ON {table_name}"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS strict_project_partition ON {table_name}"))
    op.execute(sa.text(
        f"CREATE POLICY strict_project_partition ON {table_name} FOR ALL "
        "USING (project_id IS NOT NULL "
        "AND project_id=current_setting('app.project_id', true) "
        "AND longyun_can_access_project(project_id)) "
        "WITH CHECK (project_id IS NOT NULL "
        "AND project_id=current_setting('app.project_id', true) "
        "AND longyun_can_access_project(project_id))"
    ))


def _sample_child_policy(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS strict_project_partition ON {table_name}"))
    op.execute(sa.text(
        f"CREATE POLICY strict_project_partition ON {table_name} FOR ALL "
        f"USING (sample_id IS NOT NULL AND EXISTS ("
        f"SELECT 1 FROM biological_sample sample WHERE sample.id={table_name}.sample_id "
        "AND sample.project_id IS NOT NULL "
        "AND sample.project_id=current_setting('app.project_id', true) "
        "AND longyun_can_access_project(sample.project_id))) "
        f"WITH CHECK (sample_id IS NOT NULL AND EXISTS ("
        f"SELECT 1 FROM biological_sample sample WHERE sample.id={table_name}.sample_id "
        "AND sample.project_id IS NOT NULL "
        "AND sample.project_id=current_setting('app.project_id', true) "
        "AND longyun_can_access_project(sample.project_id)))"
    ))


def _derived_project_policy(table_name: str, project_predicate: str) -> None:
    """Protect a child table through its project-owning parent record."""
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS strict_project_partition ON {table_name}"))
    op.execute(sa.text(
        f"CREATE POLICY strict_project_partition ON {table_name} FOR ALL "
        f"USING ({project_predicate}) WITH CHECK ({project_predicate})"
    ))


def upgrade() -> None:
    # Recover safe project attribution where the owning records already carry it.
    op.execute(sa.text(
        "UPDATE source_review source SET project_id=batch.project_id "
        "FROM data_import_batch batch "
        "WHERE source.unified_import_batch_id=batch.id AND source.project_id IS NULL"
    ))
    op.execute(sa.text(
        "UPDATE phenotype_observation observation SET project_id=source.project_id "
        "FROM source_review source "
        "WHERE observation.source_review_id=source.id AND observation.project_id IS NULL"
    ))
    op.execute(sa.text(
        "UPDATE root_phenotype_observation observation SET project_id=source.project_id "
        "FROM source_review source "
        "WHERE observation.source_review_id=source.id AND observation.project_id IS NULL"
    ))

    if "project_id" not in _columns("biological_sample"):
        op.add_column("biological_sample", sa.Column("project_id", sa.String(36), nullable=True))
    inspector = sa.inspect(op.get_bind())
    if "fk_biological_sample_project" not in {item.get("name") for item in inspector.get_foreign_keys("biological_sample")}:
        op.create_foreign_key(
            "fk_biological_sample_project",
            "biological_sample",
            "research_project",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.execute(sa.text(
        "UPDATE biological_sample sample SET project_id=trial.project_id "
        "FROM trial_entry entry JOIN field_trial trial ON trial.id=entry.trial_id "
        "WHERE sample.trial_entry_id=entry.id AND sample.project_id IS NULL"
    ))

    # Recover the trial package/project relationship in both directions.  Old
    # demo rows without an attributable project deliberately remain NULL and
    # become invisible to the application role after strict RLS is enabled.
    op.execute(sa.text(
        "UPDATE trial_data_package package SET project_id=batch.project_id "
        "FROM trial_import_batch batch "
        "WHERE batch.published_package_id=package.id "
        "AND package.project_id IS NULL AND batch.project_id IS NOT NULL"
    ))
    op.execute(sa.text(
        "UPDATE trial_data_package package SET project_id=source.project_id "
        "FROM (SELECT package_id, min(project_id) AS project_id "
        "      FROM field_trial WHERE project_id IS NOT NULL GROUP BY package_id "
        "      HAVING count(DISTINCT project_id)=1) source "
        "WHERE package.id=source.package_id AND package.project_id IS NULL"
    ))
    op.execute(sa.text(
        "UPDATE field_trial trial SET project_id=package.project_id "
        "FROM trial_data_package package "
        "WHERE trial.package_id=package.id AND trial.project_id IS NULL "
        "AND package.project_id IS NOT NULL"
    ))
    op.execute(sa.text(
        "UPDATE breeding_program program SET project_id=source.project_id "
        "FROM (SELECT program_id, min(project_id) AS project_id "
        "      FROM biological_sample WHERE project_id IS NOT NULL GROUP BY program_id "
        "      HAVING count(DISTINCT project_id)=1) source "
        "WHERE program.id=source.program_id AND program.project_id IS NULL"
    ))
    op.execute(sa.text(
        "UPDATE breeding_program program SET project_id=source.project_id "
        "FROM (SELECT link.program_id, min(scope.project_id) AS project_id "
        "      FROM breeding_program_material link "
        "      JOIN data_material_project_scope scope ON scope.material_id=link.material_id "
        "      GROUP BY link.program_id HAVING count(DISTINCT scope.project_id)=1) source "
        "WHERE program.id=source.program_id AND program.project_id IS NULL"
    ))

    # The same file, variety/trait or plant code may legitimately occur in two projects.
    op.execute(sa.text("DROP INDEX IF EXISTS uq_source_review_file_hash"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_source_review_project_file_hash"))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_source_review_project_file_hash "
        "ON source_review(project_id, file_hash) "
        "WHERE project_id IS NOT NULL AND file_hash IS NOT NULL"
    ))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_phenotype_variety_trait"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_root_phenotype_variety_trait"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_phenotype_project_variety_trait"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_root_phenotype_project_variety_trait"))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_phenotype_project_variety_trait "
        "ON phenotype_observation(project_id, variety_id, trait_code) WHERE project_id IS NOT NULL"
    ))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_root_phenotype_project_variety_trait "
        "ON root_phenotype_observation(project_id, variety_id, trait_code) WHERE project_id IS NOT NULL"
    ))
    op.execute(sa.text(
        "ALTER TABLE biological_sample DROP CONSTRAINT IF EXISTS "
        "biological_sample_program_id_sample_code_key"
    ))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_biological_sample_project_program_code"))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_biological_sample_project_program_code "
        "ON biological_sample(project_id, program_id, sample_code) WHERE project_id IS NOT NULL"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_biological_sample_project_material "
        "ON biological_sample(project_id, material_id, data_status)"
    ))

    for table_name in (
        "source_review",
        "phenotype_observation",
        "root_phenotype_observation",
        "biological_sample",
        "trial_import_batch",
        "trial_data_package",
        "field_trial",
        "breeding_program",
        "data_file_asset",
        "data_import_batch",
        "data_entity_lineage",
        "data_project_completeness",
        "data_material_project_scope",
    ):
        _strict_project_policy(table_name)
    for table_name in ("field_survey_observation", "field_survey_photo", "breeding_selection_record"):
        _sample_child_policy(table_name)
    trial_child_policies = {
        "breeding_program_material": (
            "program_id IS NOT NULL AND EXISTS (SELECT 1 FROM breeding_program program "
            "WHERE program.id=breeding_program_material.program_id "
            "AND program.project_id IS NOT NULL "
            "AND program.project_id=current_setting('app.project_id', true) "
            "AND longyun_can_access_project(program.project_id))"
        ),
        "breeding_generation_record": (
            "program_id IS NOT NULL AND EXISTS (SELECT 1 FROM breeding_program program "
            "WHERE program.id=breeding_generation_record.program_id "
            "AND program.project_id IS NOT NULL "
            "AND program.project_id=current_setting('app.project_id', true) "
            "AND longyun_can_access_project(program.project_id))"
        ),
        "trial_environment_metric": (
            "trial_id IS NOT NULL AND EXISTS (SELECT 1 FROM field_trial trial "
            "WHERE trial.id=trial_environment_metric.trial_id "
            "AND trial.project_id IS NOT NULL "
            "AND trial.project_id=current_setting('app.project_id', true) "
            "AND longyun_can_access_project(trial.project_id))"
        ),
        "trial_treatment": (
            "trial_id IS NOT NULL AND EXISTS (SELECT 1 FROM field_trial trial "
            "WHERE trial.id=trial_treatment.trial_id "
            "AND trial.project_id IS NOT NULL "
            "AND trial.project_id=current_setting('app.project_id', true) "
            "AND longyun_can_access_project(trial.project_id))"
        ),
        "trial_entry": (
            "trial_id IS NOT NULL AND EXISTS (SELECT 1 FROM field_trial trial "
            "WHERE trial.id=trial_entry.trial_id "
            "AND trial.project_id IS NOT NULL "
            "AND trial.project_id=current_setting('app.project_id', true) "
            "AND longyun_can_access_project(trial.project_id))"
        ),
        "trial_management_event": (
            "treatment_id IS NOT NULL AND EXISTS ("
            "SELECT 1 FROM trial_treatment treatment JOIN field_trial trial ON trial.id=treatment.trial_id "
            "WHERE treatment.id=trial_management_event.treatment_id "
            "AND trial.project_id IS NOT NULL "
            "AND trial.project_id=current_setting('app.project_id', true) "
            "AND longyun_can_access_project(trial.project_id))"
        ),
        "trial_phenotype_observation": (
            "entry_id IS NOT NULL AND EXISTS ("
            "SELECT 1 FROM trial_entry entry JOIN field_trial trial ON trial.id=entry.trial_id "
            "WHERE entry.id=trial_phenotype_observation.entry_id "
            "AND trial.project_id IS NOT NULL "
            "AND trial.project_id=current_setting('app.project_id', true) "
            "AND longyun_can_access_project(trial.project_id))"
        ),
        "trial_source_file": (
            "package_id IS NOT NULL AND EXISTS (SELECT 1 FROM trial_data_package package "
            "WHERE package.id=trial_source_file.package_id "
            "AND package.project_id IS NOT NULL "
            "AND package.project_id=current_setting('app.project_id', true) "
            "AND longyun_can_access_project(package.project_id))"
        ),
        "trial_analysis_run": (
            "package_id IS NOT NULL AND EXISTS (SELECT 1 FROM trial_data_package package "
            "WHERE package.id=trial_analysis_run.package_id "
            "AND package.project_id IS NOT NULL "
            "AND package.project_id=current_setting('app.project_id', true) "
            "AND longyun_can_access_project(package.project_id))"
        ),
    }
    for table_name, predicate in trial_child_policies.items():
        _derived_project_policy(table_name, predicate)
    op.execute(sa.text(
        "ALTER VIEW v_trial_material_summary SET (security_invoker = true)"
    ))


def downgrade() -> None:
    for table_name in (
        "trial_environment_metric",
        "trial_treatment",
        "trial_entry",
        "trial_management_event",
        "trial_phenotype_observation",
        "trial_source_file",
        "trial_analysis_run",
        "breeding_program_material",
        "breeding_generation_record",
    ):
        op.execute(sa.text(f"DROP POLICY IF EXISTS strict_project_partition ON {table_name}"))
    for table_name in ("field_survey_observation", "field_survey_photo", "breeding_selection_record"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS strict_project_partition ON {table_name}"))
    for table_name in (
        "source_review",
        "phenotype_observation",
        "root_phenotype_observation",
        "biological_sample",
        "trial_import_batch",
        "trial_data_package",
        "field_trial",
        "breeding_program",
        "data_file_asset",
        "data_import_batch",
        "data_entity_lineage",
        "data_project_completeness",
        "data_material_project_scope",
    ):
        op.execute(sa.text(f"DROP POLICY IF EXISTS strict_project_partition ON {table_name}"))
    for table_name in ("source_review", "phenotype_observation", "root_phenotype_observation"):
        op.execute(sa.text(
            f"CREATE POLICY project_scoped_intake ON {table_name} FOR ALL "
            "USING (project_id IS NULL OR longyun_can_access_project(project_id)) "
            "WITH CHECK (project_id IS NULL OR longyun_can_access_project(project_id))"
        ))
    for table_name, owner_column in (
        ("data_file_asset", "owner_id"),
        ("data_import_batch", "created_by"),
    ):
        op.execute(sa.text(
            f"CREATE POLICY data_spine_access ON {table_name} FOR ALL "
            f"USING ({owner_column}=current_setting('app.research_user_id', true) "
            f"OR longyun_can_access_project(project_id)) "
            f"WITH CHECK ({owner_column}=current_setting('app.research_user_id', true) "
            f"OR longyun_can_access_project(project_id))"
        ))
    for table_name in (
        "data_entity_lineage",
        "data_project_completeness",
        "data_material_project_scope",
    ):
        op.execute(sa.text(
            f"CREATE POLICY data_spine_access ON {table_name} FOR ALL "
            "USING (longyun_can_access_project(project_id)) "
            "WITH CHECK (longyun_can_access_project(project_id))"
        ))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_biological_sample_project_material"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_biological_sample_project_program_code"))
    op.create_unique_constraint(
        "biological_sample_program_id_sample_code_key",
        "biological_sample",
        ["program_id", "sample_code"],
    )
    op.execute(sa.text("DROP INDEX IF EXISTS uq_root_phenotype_project_variety_trait"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_phenotype_project_variety_trait"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_source_review_project_file_hash"))
    op.drop_constraint("fk_biological_sample_project", "biological_sample", type_="foreignkey")
    op.drop_column("biological_sample", "project_id")
