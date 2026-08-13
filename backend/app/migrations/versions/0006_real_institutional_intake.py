"""Add reusable field mapping, staging and cross-batch entity resolution.

Revision ID: 0006_real_institutional_intake
Revises: 0005_project_boundaries
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_real_institutional_intake"
down_revision = "0005_project_boundaries"
branch_labels = None
depends_on = None


CORE_FIELDS = (
    # Germplasm identity. Institution-specific columns map to these meanings.
    ("germplasm.material_code", "germplasm", "material", "material_code", "材料稳定编号", "text", True),
    ("germplasm.material_name", "germplasm", "material", "material_name", "材料名称", "text", True),
    ("germplasm.material_type", "germplasm", "material", "material_type", "材料类型", "text", False),
    ("germplasm.aliases", "germplasm", "material", "aliases", "别名", "text_list", False),
    ("germplasm.pedigree_summary", "germplasm", "material", "pedigree_summary", "系谱摘要", "text", False),
    # Trial/plot context. These fields make later supplements joinable.
    ("trial.trial_code", "trial", "trial", "trial_code", "试验稳定编号", "text", True),
    ("trial.trial_name", "trial", "trial", "trial_name", "试验名称", "text", True),
    ("trial.trial_year", "trial", "trial", "trial_year", "试验年份", "integer", True),
    ("trial.site_code", "trial", "site", "site_code", "地点稳定编号", "text", True),
    ("trial.site_name", "trial", "site", "site_name", "地点名称", "text", True),
    ("trial.design_type", "trial", "trial", "design_type", "试验设计", "text", False),
    ("trial.replicate_count", "trial", "trial", "replicate_count", "重复数", "integer", False),
    ("trial.material_code", "trial", "material", "material_code", "参试材料编号", "text", True),
    ("trial.treatment_code", "trial", "treatment", "treatment_code", "处理编号", "text", True),
    ("trial.treatment_name", "trial", "treatment", "treatment_name", "处理名称", "text", False),
    ("trial.replicate_no", "trial", "plot", "replicate_no", "重复号", "integer", True),
    ("trial.block_no", "trial", "plot", "block_no", "区组号", "integer", False),
    ("trial.plot_no", "trial", "plot", "plot_no", "小区稳定编号", "text", True),
    # Long-form phenotype observation. Wide traits may map through a profile adapter later.
    ("phenotype.trial_code", "phenotype", "trial", "trial_code", "试验稳定编号", "text", True),
    ("phenotype.material_code", "phenotype", "material", "material_code", "材料稳定编号", "text", True),
    ("phenotype.treatment_code", "phenotype", "treatment", "treatment_code", "处理编号", "text", True),
    ("phenotype.replicate_no", "phenotype", "plot", "replicate_no", "重复号", "integer", True),
    ("phenotype.plot_no", "phenotype", "plot", "plot_no", "小区稳定编号", "text", True),
    ("phenotype.trait_code", "phenotype", "observation", "trait_code", "性状代码", "text", True),
    ("phenotype.trait_name", "phenotype", "observation", "trait_name", "性状名称", "text", True),
    ("phenotype.value", "phenotype", "observation", "value", "观测值", "number_or_text", True),
    ("phenotype.unit", "phenotype", "observation", "unit", "单位", "text", False),
    ("phenotype.observation_stage", "phenotype", "observation", "observation_stage", "生育期", "text", True),
    ("phenotype.evaluation_method", "phenotype", "observation", "evaluation_method", "测量方法", "text", False),
    # Environment supplements link to an existing trial.
    ("environment.trial_code", "environment", "trial", "trial_code", "试验稳定编号", "text", True),
    ("environment.metric_code", "environment", "environment", "metric_code", "环境指标代码", "text", True),
    ("environment.metric_name", "environment", "environment", "metric_name", "环境指标名称", "text", True),
    ("environment.value", "environment", "environment", "value", "环境指标值", "number", True),
    ("environment.unit", "environment", "environment", "unit", "单位", "text", True),
    ("environment.collection_method", "environment", "environment", "collection_method", "采集方法", "text", False),
    # Management supplements link to trial and treatment.
    ("management.trial_code", "management", "trial", "trial_code", "试验稳定编号", "text", True),
    ("management.treatment_code", "management", "treatment", "treatment_code", "处理编号", "text", True),
    ("management.event_type", "management", "management", "event_type", "管理事件类型", "text", True),
    ("management.input_name", "management", "management", "input_name", "投入品或措施", "text", True),
    ("management.rate_per_mu", "management", "management", "rate_per_mu", "亩用量", "number", False),
    ("management.unit", "management", "management", "unit", "单位", "text", False),
    ("management.event_stage", "management", "management", "event_stage", "实施生育期", "text", False),
)


def _project_child_policy(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS real_intake_access ON {table_name}"))
    op.execute(sa.text(
        f"CREATE POLICY real_intake_access ON {table_name} FOR ALL "
        f"USING (EXISTS (SELECT 1 FROM data_import_batch batch "
        f"WHERE batch.id={table_name}.import_batch_id AND "
        f"(batch.created_by=current_setting('app.research_user_id', true) "
        f"OR longyun_can_access_project(batch.project_id)))) "
        f"WITH CHECK (EXISTS (SELECT 1 FROM data_import_batch batch "
        f"WHERE batch.id={table_name}.import_batch_id AND "
        f"(batch.created_by=current_setting('app.research_user_id', true) "
        f"OR longyun_can_access_project(batch.project_id))))"
    ))


def upgrade() -> None:
    op.create_table(
        "semantic_field_definition",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("field_code", sa.String(180), nullable=False, unique=True),
        sa.Column("data_domain", sa.String(40), nullable=False),
        sa.Column("target_entity", sa.String(100), nullable=False),
        sa.Column("target_field", sa.String(160), nullable=False),
        sa.Column("field_name", sa.String(240), nullable=False),
        sa.Column("value_type", sa.String(40), nullable=False),
        sa.Column("unit", sa.String(60)),
        sa.Column("scope", sa.String(30), nullable=False, server_default="platform_core"),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("validation_rules", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("description", sa.Text()),
        sa.Column("created_by", sa.String(120), nullable=False, server_default="platform-release"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("scope IN ('platform_core','institution_extension')", name="ck_semantic_field_scope"),
        sa.CheckConstraint("status IN ('active','retired')", name="ck_semantic_field_status"),
    )
    op.create_index("ix_semantic_field_domain", "semantic_field_definition", ["data_domain", "status"])

    op.create_table(
        "data_mapping_profile",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("research_project.id", ondelete="SET NULL")),
        sa.Column("profile_name", sa.String(240), nullable=False),
        sa.Column("data_domain", sa.String(40), nullable=False),
        sa.Column("source_signature", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("data_domain", "source_signature", "version", name="uq_mapping_profile_signature_version"),
    )
    op.create_index("ix_mapping_profile_project", "data_mapping_profile", ["project_id", "data_domain", "status"])

    op.create_table(
        "data_mapping_profile_field",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("mapping_profile_id", sa.String(36), sa.ForeignKey("data_mapping_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_column", sa.String(300), nullable=False),
        sa.Column("semantic_field_id", sa.String(36), sa.ForeignKey("semantic_field_definition.id", ondelete="RESTRICT")),
        sa.Column("mapping_action", sa.String(30), nullable=False, server_default="map"),
        sa.Column("transform_rule", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("mapping_profile_id", "source_column", name="uq_mapping_profile_source_column"),
        sa.CheckConstraint("mapping_action IN ('map','preserve','ignore')", name="ck_mapping_profile_action"),
    )

    op.add_column("data_import_batch", sa.Column("mapping_profile_id", sa.String(36)))
    op.create_foreign_key(
        "fk_data_import_batch_mapping_profile_id", "data_import_batch", "data_mapping_profile",
        ["mapping_profile_id"], ["id"], ondelete="SET NULL",
    )
    op.add_column("data_import_batch", sa.Column("binding_context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))

    op.create_table(
        "data_import_staging_row",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("import_batch_id", sa.String(36), sa.ForeignKey("data_import_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("import_file_id", sa.String(36), sa.ForeignKey("data_import_file.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_sheet", sa.String(200)),
        sa.Column("source_row_number", sa.BigInteger(), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False),
        sa.Column("raw_record", postgresql.JSONB(), nullable=False),
        sa.Column("mapped_record", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("validation_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("resolution_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("publish_status", sa.String(30), nullable=False, server_default="staged"),
        sa.Column("published_entity_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("import_file_id", "source_sheet", "source_row_number", name="uq_staging_source_row"),
    )
    op.create_index("ix_staging_batch_status", "data_import_staging_row", ["import_batch_id", "validation_status", "resolution_status"])

    op.create_table(
        "data_entity_identifier",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("identifier_namespace", sa.String(100), nullable=False, server_default="institution"),
        sa.Column("identifier_type", sa.String(80), nullable=False),
        sa.Column("identifier_value", sa.String(500), nullable=False),
        sa.Column("normalized_value", sa.String(500), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "entity_type", "identifier_namespace", "identifier_type", "normalized_value",
            name="uq_entity_identifier_stable",
        ),
    )
    op.create_index("ix_entity_identifier_entity", "data_entity_identifier", ["entity_type", "entity_id"])

    op.create_table(
        "data_entity_match_candidate",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("import_batch_id", sa.String(36), sa.ForeignKey("data_import_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("staging_row_id", sa.String(36), sa.ForeignKey("data_import_staging_row.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_entity_type", sa.String(80), nullable=False),
        sa.Column("target_entity_id", sa.String(80)),
        sa.Column("source_identifier", sa.String(500), nullable=False),
        sa.Column("match_method", sa.String(80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="candidate"),
        sa.Column("resolved_by", sa.String(120)),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('candidate','confirmed','rejected','created_new')", name="ck_entity_candidate_status"),
    )
    op.create_index("ix_entity_candidate_batch", "data_entity_match_candidate", ["import_batch_id", "status"])

    op.create_table(
        "data_record_revision",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("research_project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("import_batch_id", sa.String(36), sa.ForeignKey("data_import_batch.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(30), nullable=False),
        sa.Column("before_data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("after_data", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("changed_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("entity_type", "entity_id", "revision_no", name="uq_data_record_revision"),
    )
    op.create_index("ix_record_revision_project", "data_record_revision", ["project_id", "entity_type", "entity_id"])

    semantic_table = sa.table(
        "semantic_field_definition",
        sa.column("id", sa.String), sa.column("field_code", sa.String), sa.column("data_domain", sa.String),
        sa.column("target_entity", sa.String), sa.column("target_field", sa.String), sa.column("field_name", sa.String),
        sa.column("value_type", sa.String), sa.column("scope", sa.String), sa.column("status", sa.String),
        sa.column("is_required", sa.Boolean()), sa.column("aliases", postgresql.JSONB()), sa.column("validation_rules", postgresql.JSONB()),
        sa.column("created_by", sa.String),
    )
    op.bulk_insert(semantic_table, [
        {
            "id": f"core-{index:03d}", "field_code": code, "data_domain": domain,
            "target_entity": entity, "target_field": target, "field_name": name,
            "value_type": value_type, "scope": "platform_core", "status": "active",
            "is_required": required, "aliases": [], "validation_rules": {}, "created_by": "platform-release",
        }
        for index, (code, domain, entity, target, name, value_type, required) in enumerate(CORE_FIELDS, 1)
    ])

    # RLS follows the owning batch/project. Semantic definitions are an institution database catalog.
    for table_name in ("data_import_staging_row", "data_entity_match_candidate"):
        _project_child_policy(table_name)
    for table_name in ("data_mapping_profile", "data_record_revision"):
        op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"DROP POLICY IF EXISTS real_intake_access ON {table_name}"))
        project_expr = "project_id IS NULL OR longyun_can_access_project(project_id)" if table_name == "data_mapping_profile" else "longyun_can_access_project(project_id)"
        op.execute(sa.text(
            f"CREATE POLICY real_intake_access ON {table_name} FOR ALL "
            f"USING ({project_expr}) WITH CHECK ({project_expr})"
        ))

    # Mapping rows inherit the profile's project boundary.  Without this
    # policy a database role with direct table grants could enumerate another
    # project's source column names even though the API is protected.
    op.execute(sa.text("ALTER TABLE data_mapping_profile_field ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE data_mapping_profile_field FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS real_intake_access ON data_mapping_profile_field"))
    op.execute(sa.text(
        "CREATE POLICY real_intake_access ON data_mapping_profile_field FOR ALL "
        "USING (EXISTS (SELECT 1 FROM data_mapping_profile profile "
        "WHERE profile.id=data_mapping_profile_field.mapping_profile_id "
        "AND (profile.project_id IS NULL OR longyun_can_access_project(profile.project_id)))) "
        "WITH CHECK (EXISTS (SELECT 1 FROM data_mapping_profile profile "
        "WHERE profile.id=data_mapping_profile_field.mapping_profile_id "
        "AND (profile.project_id IS NULL OR longyun_can_access_project(profile.project_id))))"
    ))


def downgrade() -> None:
    op.drop_table("data_record_revision")
    op.drop_table("data_entity_match_candidate")
    op.drop_table("data_entity_identifier")
    op.drop_table("data_import_staging_row")
    op.drop_constraint("fk_data_import_batch_mapping_profile_id", "data_import_batch", type_="foreignkey")
    op.drop_column("data_import_batch", "binding_context")
    op.drop_column("data_import_batch", "mapping_profile_id")
    op.drop_table("data_mapping_profile_field")
    op.drop_table("data_mapping_profile")
    op.drop_table("semantic_field_definition")
