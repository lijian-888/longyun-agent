"""Privacy-preserving AI request accounting.

The log deliberately stores identity, route, status, timing and provider usage
but never prompts, model responses, attachment text or API credentials.  Each
institution keeps its log in its own business database; platform operators can
aggregate it through the offline ``tenant_admin usage`` command.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


AI_USAGE_STATUSES = frozenset({"submitted", "running", "completed", "failed", "cancelled"})


def ensure_ai_usage_schema(session: Session) -> None:
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_usage_log (
            id VARCHAR(36) PRIMARY KEY,
            request_id VARCHAR(80) NOT NULL UNIQUE,
            institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
            owner_id VARCHAR(120) NOT NULL,
            project_id VARCHAR(36),
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
    session.execute(text(
        "ALTER TABLE ai_usage_log ADD COLUMN IF NOT EXISTS project_id VARCHAR(36)"
    ))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ai_usage_institution_time "
        "ON ai_usage_log(institution_id, created_at DESC)"
    ))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ai_usage_owner_time "
        "ON ai_usage_log(institution_id, owner_id, created_at DESC)"
    ))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ai_usage_project_time "
        "ON ai_usage_log(institution_id, project_id, created_at DESC)"
    ))
    session.execute(text("ALTER TABLE ai_usage_log ENABLE ROW LEVEL SECURITY"))
    session.execute(text("ALTER TABLE ai_usage_log FORCE ROW LEVEL SECURITY"))
    session.execute(text("DROP POLICY IF EXISTS ai_usage_private_owner ON ai_usage_log"))
    has_project_helper = bool(session.execute(text(
        "SELECT to_regprocedure('longyun_can_access_project(character varying)') IS NOT NULL"
    )).scalar())
    project_guard = (
        " AND project_id IS NOT NULL"
        " AND project_id = current_setting('app.project_id', true)"
        " AND longyun_can_access_project(project_id)"
        if has_project_helper
        else ""
    )
    session.execute(text(f"""
        CREATE POLICY ai_usage_private_owner ON ai_usage_log FOR ALL
        USING (
            institution_id = current_setting('app.institution_id', true)
            AND owner_id = current_setting('app.research_user_id', true)
            {project_guard}
        )
        WITH CHECK (
            institution_id = current_setting('app.institution_id', true)
            AND owner_id = current_setting('app.research_user_id', true)
            {project_guard}
        )
    """))


def start_ai_usage(
    session: Session,
    *,
    request_id: str,
    institution_id: str,
    owner_id: str,
    project_id: str,
    route: str,
    provider_name: str,
    provider_host: str,
    model_alias: str,
    workflow_run_id: str | None = None,
    research_session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    log_id = str(uuid4())
    session.execute(text("""
        INSERT INTO ai_usage_log(
            id, request_id, institution_id, owner_id, project_id, route,
            workflow_run_id, research_session_id, provider_name,
            provider_host, model_alias, status, usage_metadata
        ) VALUES (
            :id, :request_id, :institution_id, :owner_id, :project_id, :route,
            :workflow_run_id, :research_session_id, :provider_name,
            :provider_host, :model_alias, 'submitted', CAST(:metadata AS jsonb)
        )
        ON CONFLICT (request_id) DO NOTHING
    """), {
        "id": log_id,
        "request_id": request_id[:80],
        "institution_id": institution_id,
        "owner_id": owner_id,
        "project_id": project_id,
        "route": route[:60],
        "workflow_run_id": workflow_run_id,
        "research_session_id": research_session_id,
        "provider_name": provider_name[:120],
        "provider_host": provider_host[:240],
        "model_alias": model_alias[:160],
        "metadata": json.dumps(metadata or {}, ensure_ascii=False),
    })
    return log_id


def update_ai_usage(
    session: Session,
    request_id: str,
    *,
    status: str,
    usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if status not in AI_USAGE_STATUSES:
        raise ValueError(f"unsupported AI usage status: {status}")
    usage = usage or {}
    session.execute(text("""
        UPDATE ai_usage_log
        SET status = CAST(:status AS VARCHAR(30)),
            prompt_tokens = COALESCE(:prompt_tokens, prompt_tokens),
            completion_tokens = COALESCE(:completion_tokens, completion_tokens),
            total_tokens = COALESCE(:total_tokens, total_tokens),
            latency_ms = COALESCE(:latency_ms, latency_ms),
            error_code = :error_code,
            usage_metadata = usage_metadata || CAST(:metadata AS jsonb),
            completed_at = CASE
                WHEN CAST(:status AS VARCHAR(30)) IN ('completed', 'failed', 'cancelled') THEN now()
                ELSE completed_at
            END,
            updated_at = now()
        WHERE request_id = :request_id
    """), {
        "request_id": request_id[:80],
        "status": status,
        "prompt_tokens": max(0, int(usage.get("prompt_tokens") or 0)) if usage else None,
        "completion_tokens": max(0, int(usage.get("completion_tokens") or 0)) if usage else None,
        "total_tokens": max(0, int(usage.get("total_tokens") or 0)) if usage else None,
        "latency_ms": max(0, int(latency_ms)) if latency_ms is not None else None,
        "error_code": (error_code or "")[:80] or None,
        "metadata": json.dumps(metadata or {}, ensure_ascii=False),
    })


def utc_milliseconds_since(started_at: datetime) -> int:
    now = datetime.now(timezone.utc)
    value = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    return max(0, int((now - value).total_seconds() * 1000))
