"""Bind conversations, artifacts and controlled analyses to research projects.

Revision ID: 0005_project_boundaries
Revises: 0004_ai_usage_audit
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_project_boundaries"
down_revision = "0004_ai_usage_audit"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_project_column(table_name: str) -> None:
    if "project_id" not in _columns(table_name):
        op.add_column(table_name, sa.Column("project_id", sa.String(36), nullable=True))
    inspector = sa.inspect(op.get_bind())
    fk_name = f"fk_{table_name}_project_id"
    if fk_name not in {item.get("name") for item in inspector.get_foreign_keys(table_name)}:
        op.create_foreign_key(
            fk_name,
            table_name,
            "research_project",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )
    index_name = f"ix_{table_name}_project_id"
    if index_name not in {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}:
        op.create_index(index_name, table_name, ["project_id"])


def _private_project_policy(table_name: str, owner_column: str = "owner_id") -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS private_project_boundary ON {table_name}"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS research_owner_only ON {table_name}"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS gwas_analysis_plan_owner_only ON {table_name}"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS ai_usage_private_owner ON {table_name}"))
    op.execute(sa.text(
        f"CREATE POLICY private_project_boundary ON {table_name} FOR ALL "
        f"USING ({owner_column} = current_setting('app.research_user_id', true) "
        "AND project_id IS NOT NULL "
        "AND project_id = current_setting('app.project_id', true) "
        "AND longyun_can_access_project(project_id)) "
        f"WITH CHECK ({owner_column} = current_setting('app.research_user_id', true) "
        "AND project_id IS NOT NULL "
        "AND project_id = current_setting('app.project_id', true) "
        "AND longyun_can_access_project(project_id))"
    ))


def upgrade() -> None:
    # An idempotency key is reusable in a different project. Rebuild the old
    # institution/user index once during migration instead of on every start.
    op.execute(sa.text("DROP INDEX IF EXISTS uq_agent_workflow_idempotency"))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_agent_workflow_idempotency "
        "ON agent_workflow_run(institution_id, project_id, owner_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    ))

    for table_name in ("research_attachment", "research_result", "gwas_analysis_plan"):
        _add_project_column(table_name)

    _add_project_column("knowledge_chunk")
    op.execute(sa.text(
        "UPDATE knowledge_chunk chunk SET project_id = document.project_id "
        "FROM knowledge_document document "
        "WHERE chunk.document_id = document.id AND chunk.project_id IS DISTINCT FROM document.project_id"
    ))

    # Keep personal conversations/results private while additionally requiring
    # access to their project. Legacy NULL rows are quarantined until explicitly
    # assigned; silently exposing them in every selected project is unsafe.
    for table_name in ("research_session", "research_attachment", "research_result", "gwas_analysis_plan"):
        _private_project_policy(table_name)

    # AI usage contains no prompt text, but project attribution is needed for
    # quotas, audit and eventual project-level deletion.
    _add_project_column("ai_usage_log")
    _private_project_policy("ai_usage_log")


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_agent_workflow_idempotency"))
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_agent_workflow_idempotency "
        "ON agent_workflow_run(institution_id, owner_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    ))

    for table_name in ("research_session", "research_attachment", "research_result", "gwas_analysis_plan", "ai_usage_log"):
        op.execute(sa.text(f"DROP POLICY IF EXISTS private_project_boundary ON {table_name}"))

    for table_name in ("ai_usage_log", "knowledge_chunk", "gwas_analysis_plan", "research_result", "research_attachment"):
        index_name = f"ix_{table_name}_project_id"
        fk_name = f"fk_{table_name}_project_id"
        op.drop_index(index_name, table_name=table_name)
        op.drop_constraint(fk_name, table_name, type_="foreignkey")
        op.drop_column(table_name, "project_id")
