"""Connect the legacy spreadsheet workbench to project-scoped intake.

Revision ID: 0003_project_intake
Revises: 0002_unified_data_spine
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_project_intake"
down_revision = "0002_unified_data_spine"
branch_labels = None
depends_on = None


def _add_column(table_name: str, column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    if column.name not in {item["name"] for item in inspector.get_columns(table_name)}:
        op.add_column(table_name, column)


def _add_fk(table_name: str, name: str, local: str, remote_table: str) -> None:
    inspector = sa.inspect(op.get_bind())
    if name not in {item.get("name") for item in inspector.get_foreign_keys(table_name)}:
        op.create_foreign_key(name, table_name, remote_table, [local], ["id"], ondelete="SET NULL")


def _add_index(table_name: str, name: str, columns: list[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    if name not in {item["name"] for item in inspector.get_indexes(table_name)}:
        op.create_index(name, table_name, columns)


def _project_policy(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS project_scoped_intake ON {table_name}"))
    op.execute(sa.text(
        f"CREATE POLICY project_scoped_intake ON {table_name} FOR ALL "
        "USING (project_id IS NULL OR longyun_can_access_project(project_id)) "
        "WITH CHECK (project_id IS NULL OR longyun_can_access_project(project_id))"
    ))


def upgrade() -> None:
    _add_column("source_review", sa.Column("unified_import_batch_id", sa.String(36), nullable=True))
    _add_fk(
        "source_review",
        "fk_source_review_unified_import_batch_id",
        "unified_import_batch_id",
        "data_import_batch",
    )
    _add_index(
        "source_review",
        "ix_source_review_unified_import_batch_id",
        ["unified_import_batch_id"],
    )

    for table_name in ("phenotype_observation", "root_phenotype_observation"):
        _add_column(table_name, sa.Column("project_id", sa.String(36), nullable=True))
        _add_fk(table_name, f"fk_{table_name}_project_id", "project_id", "research_project")
        _add_index(table_name, f"ix_{table_name}_project_id", ["project_id"])

    _project_policy("source_review")
    _project_policy("phenotype_observation")
    _project_policy("root_phenotype_observation")


def downgrade() -> None:
    for table_name in ("root_phenotype_observation", "phenotype_observation"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS project_scoped_intake ON {table_name}"))
        op.execute(sa.text(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY"))
        op.drop_index(f"ix_{table_name}_project_id", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_project_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "project_id")

    op.execute(sa.text("DROP POLICY IF EXISTS project_scoped_intake ON source_review"))
    op.execute(sa.text("ALTER TABLE source_review NO FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE source_review DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_source_review_unified_import_batch_id", table_name="source_review")
    op.drop_constraint(
        "fk_source_review_unified_import_batch_id",
        "source_review",
        type_="foreignkey",
    )
    op.drop_column("source_review", "unified_import_batch_id")
