"""Partition material identity evidence and pedigree edges by project.

Revision ID: 0009_project_material_evidence
Revises: 0008_base_showcase_read_policy
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_project_material_evidence"
down_revision = "0008_base_showcase_read_policy"
branch_labels = None
depends_on = None


PROJECT_TABLES = (
    "data_material_identifier",
    "data_material_alias",
    "data_legacy_variety_material_link",
    "breeding_pedigree_relationship",
)


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _foreign_keys(table_name: str) -> set[str]:
    return {item.get("name") for item in sa.inspect(op.get_bind()).get_foreign_keys(table_name)}


def _add_project_column(table_name: str) -> None:
    if "project_id" not in _columns(table_name):
        op.add_column(table_name, sa.Column("project_id", sa.String(36), nullable=True))
    constraint_name = f"fk_{table_name}_project_id"
    if constraint_name not in _foreign_keys(table_name):
        op.create_foreign_key(
            constraint_name,
            table_name,
            "research_project",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )


def _drop_unique_for_columns(table_name: str, columns: set[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    for constraint in inspector.get_unique_constraints(table_name):
        if set(constraint.get("column_names") or ()) == columns and constraint.get("name"):
            op.drop_constraint(constraint["name"], table_name, type_="unique")


def _drop_primary_key(table_name: str, columns: set[str]) -> None:
    primary_key = sa.inspect(op.get_bind()).get_pk_constraint(table_name)
    if set(primary_key.get("constrained_columns") or ()) == columns and primary_key.get("name"):
        op.drop_constraint(primary_key["name"], table_name, type_="primary")


def _strict_project_policy(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    for policy_name in ("data_spine_access", "strict_project_partition"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}"))
    op.execute(sa.text(
        f"CREATE POLICY strict_project_partition ON {table_name} FOR ALL "
        "USING (project_id IS NOT NULL "
        "AND project_id=current_setting('app.project_id', true) "
        "AND longyun_can_access_project(project_id)) "
        "WITH CHECK (project_id IS NOT NULL "
        "AND project_id=current_setting('app.project_id', true) "
        "AND longyun_can_access_project(project_id))"
    ))


def upgrade() -> None:
    for table_name in PROJECT_TABLES:
        _add_project_column(table_name)

    # Existing data-spine tables are FORCE RLS protected. The migration owner
    # temporarily bypasses those policies only for deterministic backfill; the
    # strict policies are restored before this transaction commits.
    for table_name in (*PROJECT_TABLES, "data_material_project_scope"):
        op.execute(sa.text(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY"))

    # Remove institution-wide uniqueness before cloning evidence into every
    # project where the canonical material is explicitly in scope.
    _drop_unique_for_columns(
        "data_material_identifier",
        {"source_system", "identifier_type", "normalized_value"},
    )
    _drop_unique_for_columns(
        "data_material_alias", {"material_id", "normalized_alias"}
    )
    _drop_primary_key(
        "data_legacy_variety_material_link", {"variety_id", "material_id"}
    )
    _drop_unique_for_columns(
        "breeding_pedigree_relationship",
        {"child_material_id", "parent_material_id", "parent_role"},
    )

    # Assign each old row to one attributable project, then clone it to any
    # additional projects that explicitly scoped the same canonical material.
    op.execute(sa.text("""
        UPDATE data_material_identifier evidence
        SET project_id=scope.project_id
        FROM (
            SELECT material_id, min(project_id) AS project_id
            FROM data_material_project_scope GROUP BY material_id
        ) scope
        WHERE evidence.material_id=scope.material_id AND evidence.project_id IS NULL
    """))
    op.execute(sa.text("""
        INSERT INTO data_material_identifier(
            id, project_id, material_id, source_system, identifier_type,
            identifier_value, normalized_value, is_primary,
            verification_status, metadata, created_by, created_at, updated_at
        )
        SELECT md5('project-identifier:' || evidence.id || ':' || scope.project_id),
               scope.project_id, evidence.material_id, evidence.source_system,
               evidence.identifier_type, evidence.identifier_value,
               evidence.normalized_value, evidence.is_primary,
               evidence.verification_status, evidence.metadata,
               evidence.created_by, evidence.created_at, evidence.updated_at
        FROM data_material_identifier evidence
        JOIN data_material_project_scope scope ON scope.material_id=evidence.material_id
        WHERE evidence.project_id IS NOT NULL AND scope.project_id<>evidence.project_id
          AND NOT EXISTS (
              SELECT 1 FROM data_material_identifier existing
              WHERE existing.project_id=scope.project_id
                AND existing.source_system=evidence.source_system
                AND existing.identifier_type=evidence.identifier_type
                AND existing.normalized_value=evidence.normalized_value
          )
    """))

    op.execute(sa.text("""
        UPDATE data_material_alias evidence
        SET project_id=scope.project_id
        FROM (
            SELECT material_id, min(project_id) AS project_id
            FROM data_material_project_scope GROUP BY material_id
        ) scope
        WHERE evidence.material_id=scope.material_id AND evidence.project_id IS NULL
    """))
    op.execute(sa.text("""
        INSERT INTO data_material_alias(
            id, project_id, material_id, alias_name, normalized_alias,
            alias_type, source_locator, verification_status, created_by, created_at
        )
        SELECT md5('project-alias:' || evidence.id || ':' || scope.project_id),
               scope.project_id, evidence.material_id, evidence.alias_name,
               evidence.normalized_alias, evidence.alias_type,
               evidence.source_locator, evidence.verification_status,
               evidence.created_by, evidence.created_at
        FROM data_material_alias evidence
        JOIN data_material_project_scope scope ON scope.material_id=evidence.material_id
        WHERE evidence.project_id IS NOT NULL AND scope.project_id<>evidence.project_id
          AND NOT EXISTS (
              SELECT 1 FROM data_material_alias existing
              WHERE existing.project_id=scope.project_id
                AND existing.material_id=evidence.material_id
                AND existing.normalized_alias=evidence.normalized_alias
          )
    """))

    op.execute(sa.text("""
        UPDATE data_legacy_variety_material_link evidence
        SET project_id=scope.project_id
        FROM (
            SELECT material_id, min(project_id) AS project_id
            FROM data_material_project_scope GROUP BY material_id
        ) scope
        WHERE evidence.material_id=scope.material_id AND evidence.project_id IS NULL
    """))
    op.execute(sa.text("""
        INSERT INTO data_legacy_variety_material_link(
            project_id, variety_id, material_id, match_method, confidence,
            status, reviewed_by, reviewed_at, created_at
        )
        SELECT scope.project_id, evidence.variety_id, evidence.material_id,
               evidence.match_method, evidence.confidence, evidence.status,
               evidence.reviewed_by, evidence.reviewed_at, evidence.created_at
        FROM data_legacy_variety_material_link evidence
        JOIN data_material_project_scope scope ON scope.material_id=evidence.material_id
        WHERE evidence.project_id IS NOT NULL AND scope.project_id<>evidence.project_id
          AND NOT EXISTS (
              SELECT 1 FROM data_legacy_variety_material_link existing
              WHERE existing.project_id=scope.project_id
                AND existing.variety_id=evidence.variety_id
                AND existing.material_id=evidence.material_id
          )
    """))

    op.execute(sa.text("""
        UPDATE breeding_pedigree_relationship relationship
        SET project_id=scope.project_id
        FROM (
            SELECT material_id, min(project_id) AS project_id
            FROM data_material_project_scope GROUP BY material_id
        ) scope
        WHERE relationship.child_material_id=scope.material_id
          AND relationship.project_id IS NULL
    """))
    op.execute(sa.text("""
        INSERT INTO breeding_pedigree_relationship(
            id, project_id, child_material_id, parent_material_id, parent_role,
            relationship_type, parent_origin, parent_trait_summary,
            combination_basis, source_record_no, source_note, is_simulated
        )
        SELECT md5('project-pedigree:' || relationship.id || ':' || scope.project_id),
               scope.project_id, relationship.child_material_id,
               relationship.parent_material_id, relationship.parent_role,
               relationship.relationship_type, relationship.parent_origin,
               relationship.parent_trait_summary, relationship.combination_basis,
               relationship.source_record_no, relationship.source_note,
               relationship.is_simulated
        FROM breeding_pedigree_relationship relationship
        JOIN data_material_project_scope scope
          ON scope.material_id=relationship.child_material_id
        WHERE relationship.project_id IS NOT NULL
          AND scope.project_id<>relationship.project_id
          AND NOT EXISTS (
              SELECT 1 FROM breeding_pedigree_relationship existing
              WHERE existing.project_id=scope.project_id
                AND existing.child_material_id=relationship.child_material_id
                AND existing.parent_material_id=relationship.parent_material_id
                AND existing.parent_role=relationship.parent_role
          )
    """))
    # A parent reached through a project pedigree edge must also be explicitly
    # scoped to that project; this prevents a relationship from exposing an
    # otherwise unrelated institution-level material.
    op.execute(sa.text("""
        INSERT INTO data_material_project_scope(
            project_id, material_id, access_level, source, created_by
        )
        SELECT DISTINCT relationship.project_id, relationship.parent_material_id,
               'project', 'pedigree_backfill', 'system-migration'
        FROM breeding_pedigree_relationship relationship
        WHERE relationship.project_id IS NOT NULL
        ON CONFLICT (project_id, material_id) DO NOTHING
    """))

    op.create_unique_constraint(
        "uq_data_material_identifier_project_namespace",
        "data_material_identifier",
        ["project_id", "source_system", "identifier_type", "normalized_value"],
    )
    op.create_unique_constraint(
        "uq_data_material_alias_project",
        "data_material_alias",
        ["project_id", "material_id", "normalized_alias"],
    )
    op.create_unique_constraint(
        "uq_data_legacy_variety_material_project",
        "data_legacy_variety_material_link",
        ["project_id", "variety_id", "material_id"],
    )
    op.create_unique_constraint(
        "uq_breeding_pedigree_project_edge",
        "breeding_pedigree_relationship",
        ["project_id", "child_material_id", "parent_material_id", "parent_role"],
    )
    op.create_index(
        "ix_data_material_identifier_project_material",
        "data_material_identifier",
        ["project_id", "material_id", "identifier_type"],
    )
    op.create_index(
        "ix_data_material_alias_project_lookup",
        "data_material_alias",
        ["project_id", "normalized_alias"],
    )
    op.create_index(
        "ix_data_legacy_variety_project",
        "data_legacy_variety_material_link",
        ["project_id", "variety_id"],
    )
    op.create_index(
        "ix_breeding_pedigree_project_child",
        "breeding_pedigree_relationship",
        ["project_id", "child_material_id"],
    )

    for table_name in PROJECT_TABLES:
        _strict_project_policy(table_name)
    op.execute(sa.text("ALTER TABLE data_material_project_scope FORCE ROW LEVEL SECURITY"))

    # Preserve the explicitly simulated, product-level base showcase while
    # keeping all real project pedigree edges behind the strict policy above.
    op.execute(sa.text(
        "DROP POLICY IF EXISTS base_showcase_read ON breeding_pedigree_relationship"
    ))
    op.execute(sa.text("""
        CREATE POLICY base_showcase_read ON breeding_pedigree_relationship FOR SELECT
        USING (
            project_id IS NULL AND is_simulated=TRUE
            AND EXISTS (
                SELECT 1
                FROM breeding_program_material link
                JOIN breeding_program program ON program.id=link.program_id
                WHERE link.material_id=breeding_pedigree_relationship.child_material_id
                  AND program.program_code='JX-RICE-DEMO-2021'
                  AND program.is_simulated=TRUE
            )
        )
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP POLICY IF EXISTS base_showcase_read ON breeding_pedigree_relationship"
    ))
    for table_name in PROJECT_TABLES:
        op.execute(sa.text(f"DROP POLICY IF EXISTS strict_project_partition ON {table_name}"))

    # Collapse project copies before restoring the former institution-wide
    # uniqueness. Downgrade is intentionally lossy only for duplicate evidence
    # copies created by this migration, never for canonical material rows.
    op.execute(sa.text("""
        DELETE FROM data_material_identifier item USING data_material_identifier keep
        WHERE item.id>keep.id AND item.source_system=keep.source_system
          AND item.identifier_type=keep.identifier_type
          AND item.normalized_value=keep.normalized_value
    """))
    op.execute(sa.text("""
        DELETE FROM data_material_alias item USING data_material_alias keep
        WHERE item.id>keep.id AND item.material_id=keep.material_id
          AND item.normalized_alias=keep.normalized_alias
    """))
    op.execute(sa.text("""
        DELETE FROM data_legacy_variety_material_link item
        USING data_legacy_variety_material_link keep
        WHERE item.ctid>keep.ctid AND item.variety_id=keep.variety_id
          AND item.material_id=keep.material_id
    """))
    op.execute(sa.text("""
        DELETE FROM breeding_pedigree_relationship item
        USING breeding_pedigree_relationship keep
        WHERE item.id>keep.id AND item.child_material_id=keep.child_material_id
          AND item.parent_material_id=keep.parent_material_id
          AND item.parent_role=keep.parent_role
    """))

    op.drop_index("ix_breeding_pedigree_project_child", table_name="breeding_pedigree_relationship")
    op.drop_index("ix_data_legacy_variety_project", table_name="data_legacy_variety_material_link")
    op.drop_index("ix_data_material_alias_project_lookup", table_name="data_material_alias")
    op.drop_index("ix_data_material_identifier_project_material", table_name="data_material_identifier")
    op.drop_constraint("uq_breeding_pedigree_project_edge", "breeding_pedigree_relationship", type_="unique")
    op.drop_constraint("uq_data_legacy_variety_material_project", "data_legacy_variety_material_link", type_="unique")
    op.drop_constraint("uq_data_material_alias_project", "data_material_alias", type_="unique")
    op.drop_constraint("uq_data_material_identifier_project_namespace", "data_material_identifier", type_="unique")

    for table_name in reversed(PROJECT_TABLES):
        op.drop_constraint(f"fk_{table_name}_project_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "project_id")

    op.create_unique_constraint(
        "uq_data_material_identifier_namespace",
        "data_material_identifier",
        ["source_system", "identifier_type", "normalized_value"],
    )
    op.create_unique_constraint(
        "uq_data_material_alias",
        "data_material_alias",
        ["material_id", "normalized_alias"],
    )
    op.create_primary_key(
        "data_legacy_variety_material_link_pkey",
        "data_legacy_variety_material_link",
        ["variety_id", "material_id"],
    )
    op.create_unique_constraint(
        "breeding_pedigree_relationship_child_material_id_parent_material_id_parent_role_key",
        "breeding_pedigree_relationship",
        ["child_material_id", "parent_material_id", "parent_role"],
    )

    for table_name in ("data_material_identifier", "data_material_alias"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS data_spine_access ON {table_name}"))
        op.execute(sa.text(
            f"CREATE POLICY data_spine_access ON {table_name} FOR ALL "
            "USING (COALESCE(current_setting('app.institution_admin', true), 'false')='true' "
            f"OR EXISTS (SELECT 1 FROM data_material_project_scope scope "
            f"WHERE scope.material_id={table_name}.material_id "
            "AND longyun_can_access_project(scope.project_id))) "
            "WITH CHECK (COALESCE(current_setting('app.institution_admin', true), 'false')='true' "
            f"OR EXISTS (SELECT 1 FROM data_material_project_scope scope "
            f"WHERE scope.material_id={table_name}.material_id "
            "AND longyun_can_access_project(scope.project_id)))"
        ))
    op.execute(sa.text("""
        CREATE POLICY data_spine_access ON data_legacy_variety_material_link FOR ALL
        USING (
            COALESCE(current_setting('app.institution_admin', true), 'false')='true'
            OR EXISTS (
                SELECT 1 FROM data_material_project_scope scope
                WHERE scope.material_id=data_legacy_variety_material_link.material_id
                  AND longyun_can_access_project(scope.project_id)
            )
        )
        WITH CHECK (
            COALESCE(current_setting('app.institution_admin', true), 'false')='true'
            OR EXISTS (
                SELECT 1 FROM data_material_project_scope scope
                WHERE scope.material_id=data_legacy_variety_material_link.material_id
                  AND longyun_can_access_project(scope.project_id)
            )
        )
    """))
    op.execute(sa.text("ALTER TABLE breeding_pedigree_relationship NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE breeding_pedigree_relationship DISABLE ROW LEVEL SECURITY"))
