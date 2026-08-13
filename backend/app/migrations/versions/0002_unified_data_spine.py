"""Add the project-scoped data asset, import and lineage spine.

Revision ID: 0002_unified_data_spine
Revises: 0001_existing_schema
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0002_unified_data_spine"
down_revision: Union[str, Sequence[str], None] = "0001_existing_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DATA_DOMAINS = (
    "germplasm",
    "pedigree",
    "phenotype",
    "environment",
    "management",
    "genotype",
    "trial",
    "literature",
    "mixed",
)


def _assert_compatibility_schema() -> None:
    inspector = sa.inspect(op.get_bind())
    required = {
        "research_project",
        "project_membership",
        "breeding_material",
        "variety_basic",
        "trial_data_package",
        "field_trial",
        "trial_import_batch",
        "genotype_asset",
        "knowledge_document",
        "research_session",
    }
    missing = sorted(required.difference(inspector.get_table_names()))
    if missing:
        raise RuntimeError(
            "The Longyun compatibility schema must be initialized before Alembic; "
            f"missing tables: {', '.join(missing)}"
        )


def _add_column(table_name: str, column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    if column.name not in {item["name"] for item in inspector.get_columns(table_name)}:
        op.add_column(table_name, column)


def _add_fk(
    table_name: str,
    constraint_name: str,
    local_column: str,
    remote_table: str,
    remote_column: str = "id",
    ondelete: str = "SET NULL",
) -> None:
    inspector = sa.inspect(op.get_bind())
    if constraint_name not in {
        item.get("name") for item in inspector.get_foreign_keys(table_name)
    }:
        op.create_foreign_key(
            constraint_name,
            table_name,
            remote_table,
            [local_column],
            [remote_column],
            ondelete=ondelete,
        )


def _add_index(table_name: str, index_name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    if index_name not in {item["name"] for item in inspector.get_indexes(table_name)}:
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    _assert_compatibility_schema()

    op.create_table(
        "data_material_identifier",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "material_id",
            sa.String(36),
            sa.ForeignKey("breeding_material.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_system", sa.String(120), nullable=False, server_default="institution"),
        sa.Column("identifier_type", sa.String(80), nullable=False),
        sa.Column("identifier_value", sa.String(300), nullable=False),
        sa.Column("normalized_value", sa.String(300), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verification_status", sa.String(30), nullable=False, server_default="verified"),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="system-migration"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "source_system",
            "identifier_type",
            "normalized_value",
            name="uq_data_material_identifier_namespace",
        ),
    )
    op.create_index(
        "ix_data_material_identifier_material",
        "data_material_identifier",
        ["material_id", "identifier_type"],
    )

    op.create_table(
        "data_material_alias",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "material_id",
            sa.String(36),
            sa.ForeignKey("breeding_material.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias_name", sa.String(300), nullable=False),
        sa.Column("normalized_alias", sa.String(300), nullable=False),
        sa.Column("alias_type", sa.String(80), nullable=False, server_default="institution_alias"),
        sa.Column("source_locator", sa.Text()),
        sa.Column("verification_status", sa.String(30), nullable=False, server_default="verified"),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="system-migration"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("material_id", "normalized_alias", name="uq_data_material_alias"),
    )
    op.create_index("ix_data_material_alias_lookup", "data_material_alias", ["normalized_alias"])

    op.create_table(
        "data_material_project_scope",
        sa.Column(
            "project_id",
            sa.String(36),
            sa.ForeignKey("research_project.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "material_id",
            sa.String(36),
            sa.ForeignKey("breeding_material.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("access_level", sa.String(30), nullable=False, server_default="project"),
        sa.Column("source", sa.String(80), nullable=False, server_default="import"),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "data_legacy_variety_material_link",
        sa.Column(
            "variety_id",
            sa.String(36),
            sa.ForeignKey("variety_basic.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "material_id",
            sa.String(36),
            sa.ForeignKey("breeding_material.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("match_method", sa.String(80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="candidate"),
        sa.Column("reviewed_by", sa.String(120)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "data_file_asset",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("research_project.id", ondelete="SET NULL")),
        sa.Column("owner_id", sa.String(120), nullable=False),
        sa.Column("data_domain", sa.String(40), nullable=False),
        sa.Column("asset_role", sa.String(80), nullable=False, server_default="source"),
        sa.Column("original_file_name", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(160)),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("object_locator", sa.Text(), nullable=False, unique=True),
        sa.Column("storage_backend", sa.String(30), nullable=False, server_default="minio"),
        sa.Column("confidentiality_level", sa.String(30), nullable=False, server_default="institution_private"),
        sa.Column("encryption_key_id", sa.String(300)),
        sa.Column("lifecycle_status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("retention_until", sa.DateTime(timezone=True)),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            f"data_domain IN {DATA_DOMAINS}",
            name="ck_data_file_asset_domain",
        ),
    )
    op.create_index("ix_data_file_asset_project_domain", "data_file_asset", ["project_id", "data_domain"])
    op.create_index("ix_data_file_asset_owner_created", "data_file_asset", ["owner_id", "created_at"])

    op.create_table(
        "data_import_batch",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("research_project.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("display_name", sa.String(300), nullable=False),
        sa.Column("data_domain", sa.String(40), nullable=False),
        sa.Column("template_version_id", sa.String(36), sa.ForeignKey("template_version.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(30), nullable=False, server_default="created"),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("accepted_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            f"data_domain IN {DATA_DOMAINS}",
            name="ck_data_import_batch_domain",
        ),
        sa.CheckConstraint(
            "status IN ('created','uploading','validating','ready','published','failed','cancelled')",
            name="ck_data_import_batch_status",
        ),
    )
    op.create_index("ix_data_import_batch_project_status", "data_import_batch", ["project_id", "status", "created_at"])

    op.create_table(
        "data_import_file",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("import_batch_id", sa.String(36), sa.ForeignKey("data_import_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_asset_id", sa.String(36), sa.ForeignKey("data_file_asset.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_role", sa.String(100), nullable=False),
        sa.Column("sheet_name", sa.String(200)),
        sa.Column("parse_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("detected_columns", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("row_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("import_batch_id", "file_asset_id", "source_role", name="uq_data_import_file_role"),
    )
    op.create_index("ix_data_import_file_batch", "data_import_file", ["import_batch_id", "parse_status"])

    op.create_table(
        "data_field_mapping",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("import_batch_id", sa.String(36), sa.ForeignKey("data_import_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("import_file_id", sa.String(36), sa.ForeignKey("data_import_file.id", ondelete="CASCADE")),
        sa.Column("source_column", sa.String(300), nullable=False),
        sa.Column("target_entity", sa.String(100), nullable=False),
        sa.Column("target_field", sa.String(160), nullable=False),
        sa.Column("transform_rule", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("match_confidence", sa.Float()),
        sa.Column("status", sa.String(30), nullable=False, server_default="suggested"),
        sa.Column("confirmed_by", sa.String(120)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "import_batch_id",
            "import_file_id",
            "source_column",
            name="uq_data_field_mapping_source",
        ),
    )

    op.create_table(
        "data_import_row_error",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("import_batch_id", sa.String(36), sa.ForeignKey("data_import_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("import_file_id", sa.String(36), sa.ForeignKey("data_import_file.id", ondelete="CASCADE")),
        sa.Column("source_row_number", sa.BigInteger()),
        sa.Column("severity", sa.String(20), nullable=False, server_default="error"),
        sa.Column("error_code", sa.String(100), nullable=False),
        sa.Column("source_column", sa.String(300)),
        sa.Column("raw_value", sa.Text()),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("resolution_status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("resolved_by", sa.String(120)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_data_import_error_batch", "data_import_row_error", ["import_batch_id", "severity", "resolution_status"])

    op.create_table(
        "data_entity_lineage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("research_project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("import_batch_id", sa.String(36), sa.ForeignKey("data_import_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_asset_id", sa.String(36), sa.ForeignKey("data_file_asset.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_row_number", sa.BigInteger()),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("relationship_type", sa.String(80), nullable=False, server_default="created_from"),
        sa.Column("locator", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "import_batch_id",
            "file_asset_id",
            "entity_type",
            "entity_id",
            "source_row_number",
            name="uq_data_entity_lineage_source",
        ),
    )
    op.create_index("ix_data_entity_lineage_entity", "data_entity_lineage", ["entity_type", "entity_id"])
    op.create_index("ix_data_entity_lineage_project", "data_entity_lineage", ["project_id", "import_batch_id"])

    op.create_table(
        "data_project_completeness",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("research_project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feature_code", sa.String(80), nullable=False),
        sa.Column("readiness_status", sa.String(30), nullable=False),
        sa.Column("readiness_score", sa.Float(), nullable=False),
        sa.Column("available_domains", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("missing_required", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("missing_recommended", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "feature_code", name="uq_data_project_completeness_feature"),
    )

    project_columns = {
        "breeding_program": "project_id",
        "trial_data_package": "project_id",
        "field_trial": "project_id",
        "genotype_asset": "project_id",
        "knowledge_document": "project_id",
        "research_session": "project_id",
        "source_review": "project_id",
        "trial_import_batch": "project_id",
    }
    for table_name, column_name in project_columns.items():
        _add_column(table_name, sa.Column(column_name, sa.String(36), nullable=True))
        _add_fk(table_name, f"fk_{table_name}_{column_name}", column_name, "research_project")
        _add_index(table_name, f"ix_{table_name}_{column_name}", [column_name])

    file_asset_columns = (
        "genotype_asset",
        "knowledge_document",
        "research_attachment",
        "research_result",
        "source_review",
        "trial_import_batch",
    )
    for table_name in file_asset_columns:
        _add_column(table_name, sa.Column("file_asset_id", sa.String(36), nullable=True))
        _add_fk(table_name, f"fk_{table_name}_file_asset_id", "file_asset_id", "data_file_asset")
        _add_index(table_name, f"ix_{table_name}_file_asset_id", ["file_asset_id"])

    _add_column("trial_import_batch", sa.Column("unified_import_batch_id", sa.String(36), nullable=True))
    _add_fk(
        "trial_import_batch",
        "fk_trial_import_batch_unified_import_batch_id",
        "unified_import_batch_id",
        "data_import_batch",
    )
    _add_index("trial_import_batch", "ix_trial_import_batch_unified", ["unified_import_batch_id"])

    # Preserve both historical material representations. Exact-name matches are
    # only candidates until a data processor confirms them.
    op.execute(sa.text("""
        INSERT INTO data_material_identifier (
            id, material_id, source_system, identifier_type,
            identifier_value, normalized_value, is_primary
        )
        SELECT md5('material-code:' || id), id, 'institution', 'material_code',
               material_code, lower(trim(material_code)), true
        FROM breeding_material
        ON CONFLICT (source_system, identifier_type, normalized_value) DO NOTHING
    """))
    op.execute(sa.text("""
        INSERT INTO data_material_alias (
            id, material_id, alias_name, normalized_alias, alias_type
        )
        SELECT md5('material-name:' || id), id, material_name,
               lower(regexp_replace(trim(material_name), '\\s+', '', 'g')), 'canonical_name'
        FROM breeding_material
        ON CONFLICT (material_id, normalized_alias) DO NOTHING
    """))
    op.execute(sa.text("""
        INSERT INTO data_legacy_variety_material_link (
            variety_id, material_id, match_method, confidence, status
        )
        SELECT v.id, m.id, 'normalized_exact_name', 1.0, 'candidate'
        FROM variety_basic v
        JOIN breeding_material m
          ON lower(regexp_replace(trim(v.variety_name), '\\s+', '', 'g')) =
             lower(regexp_replace(trim(m.material_name), '\\s+', '', 'g'))
        ON CONFLICT (variety_id, material_id) DO NOTHING
    """))

    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION longyun_can_access_project(candidate_project_id VARCHAR)
        RETURNS BOOLEAN
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT
                COALESCE(current_setting('app.institution_admin', true), 'false') = 'true'
                OR EXISTS (
                    SELECT 1 FROM project_membership membership
                    WHERE membership.project_id = candidate_project_id
                      AND membership.user_id = current_setting('app.research_user_id', true)
                )
        $$
    """))
    op.execute(sa.text("REVOKE ALL ON FUNCTION longyun_can_access_project(VARCHAR) FROM PUBLIC"))

    owner_project_tables = {
        "data_file_asset": "owner_id",
        "data_import_batch": "created_by",
    }
    for table_name, owner_column in owner_project_tables.items():
        op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS data_spine_access ON {table_name}"))
        op.execute(sa.text(
            f"CREATE POLICY data_spine_access ON {table_name} FOR ALL "
            f"USING ({owner_column} = current_setting('app.research_user_id', true) "
            f"OR longyun_can_access_project(project_id)) "
            f"WITH CHECK ({owner_column} = current_setting('app.research_user_id', true) "
            f"OR longyun_can_access_project(project_id))"
        ))

    direct_project_tables = (
        "data_entity_lineage",
        "data_project_completeness",
        "data_material_project_scope",
    )
    for table_name in direct_project_tables:
        op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS data_spine_access ON {table_name}"))
        op.execute(sa.text(
            f"CREATE POLICY data_spine_access ON {table_name} FOR ALL "
            f"USING (longyun_can_access_project(project_id)) "
            f"WITH CHECK (longyun_can_access_project(project_id))"
        ))

    # Child records do not duplicate project_id. Their policy deliberately
    # follows the owning import batch so a caller cannot reach mappings,
    # validation errors or source files from another project by guessing IDs.
    for table_name in (
        "data_import_file",
        "data_field_mapping",
        "data_import_row_error",
    ):
        op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS data_spine_access ON {table_name}"))
        op.execute(sa.text(
            f"CREATE POLICY data_spine_access ON {table_name} FOR ALL "
            f"USING (EXISTS (SELECT 1 FROM data_import_batch batch "
            f"WHERE batch.id = {table_name}.import_batch_id AND "
            f"(batch.created_by = current_setting('app.research_user_id', true) "
            f"OR longyun_can_access_project(batch.project_id)))) "
            f"WITH CHECK (EXISTS (SELECT 1 FROM data_import_batch batch "
            f"WHERE batch.id = {table_name}.import_batch_id AND "
            f"(batch.created_by = current_setting('app.research_user_id', true) "
            f"OR longyun_can_access_project(batch.project_id))))"
        ))

    for table_name in ("data_material_identifier", "data_material_alias"):
        op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS data_spine_access ON {table_name}"))
        op.execute(sa.text(
            f"CREATE POLICY data_spine_access ON {table_name} FOR ALL "
            f"USING (COALESCE(current_setting('app.institution_admin', true), 'false') = 'true' "
            f"OR EXISTS (SELECT 1 FROM data_material_project_scope scope "
            f"WHERE scope.material_id = {table_name}.material_id "
            f"AND longyun_can_access_project(scope.project_id))) "
            f"WITH CHECK (COALESCE(current_setting('app.institution_admin', true), 'false') = 'true' "
            f"OR EXISTS (SELECT 1 FROM data_material_project_scope scope "
            f"WHERE scope.material_id = {table_name}.material_id "
            f"AND longyun_can_access_project(scope.project_id)))"
        ))

    op.execute(sa.text("ALTER TABLE data_legacy_variety_material_link ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE data_legacy_variety_material_link FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS data_spine_access ON data_legacy_variety_material_link"))
    op.execute(sa.text("""
        CREATE POLICY data_spine_access ON data_legacy_variety_material_link FOR ALL
        USING (
            COALESCE(current_setting('app.institution_admin', true), 'false') = 'true'
            OR EXISTS (
                SELECT 1 FROM data_material_project_scope scope
                WHERE scope.material_id = data_legacy_variety_material_link.material_id
                  AND longyun_can_access_project(scope.project_id)
            )
        )
        WITH CHECK (
            COALESCE(current_setting('app.institution_admin', true), 'false') = 'true'
            OR EXISTS (
                SELECT 1 FROM data_material_project_scope scope
                WHERE scope.material_id = data_legacy_variety_material_link.material_id
                  AND longyun_can_access_project(scope.project_id)
            )
        )
    """))


def downgrade() -> None:
    op.drop_constraint("fk_trial_import_batch_unified_import_batch_id", "trial_import_batch", type_="foreignkey")
    op.drop_index("ix_trial_import_batch_unified", table_name="trial_import_batch")
    op.drop_column("trial_import_batch", "unified_import_batch_id")

    for table_name in (
        "genotype_asset",
        "knowledge_document",
        "research_attachment",
        "research_result",
        "source_review",
        "trial_import_batch",
    ):
        op.drop_index(f"ix_{table_name}_file_asset_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_file_asset_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "file_asset_id")

    for table_name in (
        "breeding_program",
        "trial_data_package",
        "field_trial",
        "genotype_asset",
        "knowledge_document",
        "research_session",
        "source_review",
        "trial_import_batch",
    ):
        op.drop_index(f"ix_{table_name}_project_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_project_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "project_id")

    for table_name in (
        "data_project_completeness",
        "data_entity_lineage",
        "data_import_row_error",
        "data_field_mapping",
        "data_import_file",
        "data_import_batch",
        "data_file_asset",
        "data_legacy_variety_material_link",
        "data_material_project_scope",
        "data_material_alias",
        "data_material_identifier",
    ):
        op.drop_table(table_name)

    # Policies are owned by the data-spine tables and disappear with them.
    # Drop the helper last, otherwise PostgreSQL correctly rejects the
    # downgrade because those policies still depend on the function.
    op.execute(sa.text("DROP FUNCTION IF EXISTS longyun_can_access_project(VARCHAR)"))
