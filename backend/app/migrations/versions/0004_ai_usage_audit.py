"""Add privacy-preserving per-account AI usage audit.

Revision ID: 0004_ai_usage_audit
Revises: 0003_project_intake
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_ai_usage_audit"
down_revision = "0003_project_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS ai_usage_log (
            id VARCHAR(36) PRIMARY KEY,
            request_id VARCHAR(80) NOT NULL UNIQUE,
            institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
            owner_id VARCHAR(120) NOT NULL,
            route VARCHAR(60) NOT NULL,
            workflow_run_id VARCHAR(36),
            research_session_id VARCHAR(36),
            provider_name VARCHAR(120),
            provider_host VARCHAR(240),
            model_alias VARCHAR(160),
            status VARCHAR(30) NOT NULL DEFAULT 'submitted',
            prompt_tokens BIGINT NOT NULL DEFAULT 0,
            completion_tokens BIGINT NOT NULL DEFAULT 0,
            total_tokens BIGINT NOT NULL DEFAULT 0,
            latency_ms BIGINT,
            error_code VARCHAR(80),
            usage_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_ai_usage_institution_time "
        "ON ai_usage_log(institution_id, created_at DESC)"
    ))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_ai_usage_owner_time "
        "ON ai_usage_log(institution_id, owner_id, created_at DESC)"
    ))
    op.execute(sa.text("ALTER TABLE ai_usage_log ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE ai_usage_log FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text("DROP POLICY IF EXISTS ai_usage_private_owner ON ai_usage_log"))
    op.execute(sa.text("""
        CREATE POLICY ai_usage_private_owner ON ai_usage_log FOR ALL
        USING (
            institution_id = current_setting('app.institution_id', true)
            AND owner_id = current_setting('app.research_user_id', true)
        )
        WITH CHECK (
            institution_id = current_setting('app.institution_id', true)
            AND owner_id = current_setting('app.research_user_id', true)
        )
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS ai_usage_log"))
