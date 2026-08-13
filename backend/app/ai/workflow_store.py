"""PostgreSQL persistence and tenant policies for agent workflow runs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from .model_policy import get_model_data_policy
from .usage import ensure_ai_usage_schema, start_ai_usage, update_ai_usage, utc_milliseconds_since


WORKFLOW_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def ensure_agent_workflow_schema(session: Session) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS institution (
            id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(240) NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            trial_expires_at TIMESTAMPTZ,
            retention_until TIMESTAMPTZ,
            settings JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS research_project (
            id VARCHAR(36) PRIMARY KEY,
            institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
            project_name VARCHAR(240) NOT NULL,
            research_direction VARCHAR(240),
            status VARCHAR(30) NOT NULL DEFAULT 'active',
            created_by VARCHAR(120) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS project_membership (
            project_id VARCHAR(36) NOT NULL REFERENCES research_project(id) ON DELETE CASCADE,
            institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
            user_id VARCHAR(120) NOT NULL,
            project_role VARCHAR(40) NOT NULL DEFAULT 'member',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (project_id, user_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_workflow_run (
            id VARCHAR(36) PRIMARY KEY,
            thread_id VARCHAR(80) NOT NULL UNIQUE,
            institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
            project_id VARCHAR(36) REFERENCES research_project(id) ON DELETE SET NULL,
            owner_id VARCHAR(120) NOT NULL,
            research_session_id VARCHAR(36),
            user_request TEXT NOT NULL,
            requested_agents JSONB NOT NULL DEFAULT '[]'::jsonb,
            evidence_context JSONB NOT NULL DEFAULT '[]'::jsonb,
            external_transfer_acknowledged BOOLEAN NOT NULL DEFAULT false,
            idempotency_key VARCHAR(120),
            plan JSONB NOT NULL DEFAULT '[]'::jsonb,
            status VARCHAR(30) NOT NULL DEFAULT 'queued',
            attempt_no INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            lease_owner VARCHAR(120),
            lease_expires_at TIMESTAMPTZ,
            heartbeat_at TIMESTAMPTZ,
            cancel_requested_at TIMESTAMPTZ,
            deadline_at TIMESTAMPTZ,
            next_retry_at TIMESTAMPTZ,
            final_content TEXT,
            model_alias VARCHAR(160),
            usage JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_code VARCHAR(80),
            error_detail TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_workflow_step (
            id VARCHAR(36) PRIMARY KEY,
            workflow_run_id VARCHAR(36) NOT NULL REFERENCES agent_workflow_run(id) ON DELETE CASCADE,
            institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
            project_id VARCHAR(36),
            owner_id VARCHAR(120) NOT NULL,
            agent_code VARCHAR(80) NOT NULL,
            agent_version VARCHAR(40) NOT NULL,
            contract_version VARCHAR(40) NOT NULL DEFAULT '1.0.0',
            attempt_no INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(30) NOT NULL,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            error_code VARCHAR(80),
            error_detail TEXT,
            tool_results JSONB NOT NULL DEFAULT '[]'::jsonb,
            artifact_id VARCHAR(36),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_artifact (
            id VARCHAR(36) PRIMARY KEY,
            workflow_run_id VARCHAR(36) NOT NULL REFERENCES agent_workflow_run(id) ON DELETE CASCADE,
            institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
            project_id VARCHAR(36),
            owner_id VARCHAR(120) NOT NULL,
            agent_code VARCHAR(80) NOT NULL,
            agent_name VARCHAR(160) NOT NULL,
            agent_version VARCHAR(40) NOT NULL,
            content TEXT NOT NULL,
            evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            artifact_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS workflow_event (
            id VARCHAR(36) PRIMARY KEY,
            workflow_run_id VARCHAR(36) NOT NULL REFERENCES agent_workflow_run(id) ON DELETE CASCADE,
            institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
            project_id VARCHAR(36),
            owner_id VARCHAR(120) NOT NULL,
            event_type VARCHAR(80) NOT NULL,
            event_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    )
    for statement in statements:
        session.execute(text(statement))

    # CREATE TABLE IF NOT EXISTS does not repair a historical, partially
    # bootstrapped table.  Add compatibility columns before creating indexes
    # or functions that reference them; otherwise an upgrade can fail on the
    # first index (for example when agent_workflow_run.created_at is absent).
    compatibility_columns = {
        "institution": (
            "status VARCHAR(30) NOT NULL DEFAULT 'active'",
            "trial_expires_at TIMESTAMPTZ",
            "retention_until TIMESTAMPTZ",
            "settings JSONB NOT NULL DEFAULT '{}'::jsonb",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ),
        "research_project": (
            "research_direction VARCHAR(240)",
            "status VARCHAR(30) NOT NULL DEFAULT 'active'",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ),
        "project_membership": (
            "project_role VARCHAR(40) NOT NULL DEFAULT 'member'",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ),
        "agent_workflow_run": (
            "thread_id VARCHAR(80)",
            "research_session_id VARCHAR(36)",
            "user_request TEXT NOT NULL DEFAULT ''",
            "requested_agents JSONB NOT NULL DEFAULT '[]'::jsonb",
            "evidence_context JSONB NOT NULL DEFAULT '[]'::jsonb",
            "external_transfer_acknowledged BOOLEAN NOT NULL DEFAULT false",
            "plan JSONB NOT NULL DEFAULT '[]'::jsonb",
            "status VARCHAR(30) NOT NULL DEFAULT 'queued'",
            "idempotency_key VARCHAR(120)",
            "attempt_no INTEGER NOT NULL DEFAULT 0",
            "max_attempts INTEGER NOT NULL DEFAULT 3",
            "lease_owner VARCHAR(120)",
            "lease_expires_at TIMESTAMPTZ",
            "heartbeat_at TIMESTAMPTZ",
            "cancel_requested_at TIMESTAMPTZ",
            "deadline_at TIMESTAMPTZ",
            "next_retry_at TIMESTAMPTZ",
            "final_content TEXT",
            "model_alias VARCHAR(160)",
            "usage JSONB NOT NULL DEFAULT '{}'::jsonb",
            "error_code VARCHAR(80)",
            "error_detail TEXT",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "started_at TIMESTAMPTZ",
            "completed_at TIMESTAMPTZ",
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ),
        "agent_workflow_step": (
            "project_id VARCHAR(36)",
            "contract_version VARCHAR(40) NOT NULL DEFAULT '1.0.0'",
            "attempt_no INTEGER NOT NULL DEFAULT 1",
            "started_at TIMESTAMPTZ",
            "completed_at TIMESTAMPTZ",
            "error_code VARCHAR(80)",
            "error_detail TEXT",
            "tool_results JSONB NOT NULL DEFAULT '[]'::jsonb",
            "artifact_id VARCHAR(36)",
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ),
        "agent_artifact": (
            "project_id VARCHAR(36)",
            "evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
            "artifact_metadata JSONB NOT NULL DEFAULT '{}'::jsonb",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ),
        "workflow_event": (
            "project_id VARCHAR(36)",
            "event_payload JSONB NOT NULL DEFAULT '{}'::jsonb",
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        ),
    }
    for table_name, definitions in compatibility_columns.items():
        for definition in definitions:
            session.execute(text(
                f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {definition}"
            ))

    session.execute(text(
        "UPDATE agent_workflow_run SET thread_id='legacy:' || id WHERE thread_id IS NULL"
    ))
    session.execute(text(
        "ALTER TABLE agent_workflow_run ALTER COLUMN thread_id SET NOT NULL"
    ))
    session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_workflow_thread_id "
        "ON agent_workflow_run(thread_id)"
    ))

    index_and_function_statements = (
        "CREATE INDEX IF NOT EXISTS ix_research_project_institution ON research_project(institution_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_project_membership_user ON project_membership(institution_id, user_id)",
        "CREATE INDEX IF NOT EXISTS ix_agent_workflow_owner ON agent_workflow_run(institution_id, owner_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_agent_workflow_project ON agent_workflow_run(institution_id, project_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_agent_artifact_run ON agent_artifact(workflow_run_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_workflow_event_run ON workflow_event(workflow_run_id, created_at)",
        """
        CREATE OR REPLACE FUNCTION current_institution_active_agent_workflow_count()
        RETURNS BIGINT
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT count(*)
            FROM public.agent_workflow_run
            WHERE institution_id = current_setting('app.institution_id', true)
              AND status IN ('queued', 'running')
        $$
        """,
    )
    for statement in index_and_function_statements:
        session.execute(text(statement))
    session.execute(
        text("REVOKE ALL ON FUNCTION current_institution_active_agent_workflow_count() FROM PUBLIC")
    )
    idempotency_index_definition = session.execute(text(
        "SELECT pg_get_indexdef(to_regclass('uq_agent_workflow_idempotency'))"
    )).scalar()
    if (
        not idempotency_index_definition
        or "project_id" not in idempotency_index_definition
    ):
        session.execute(text("DROP INDEX IF EXISTS uq_agent_workflow_idempotency"))
        session.execute(text(
            "CREATE UNIQUE INDEX uq_agent_workflow_idempotency "
            "ON agent_workflow_run(institution_id, project_id, owner_id, idempotency_key) "
            "WHERE idempotency_key IS NOT NULL"
        ))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_agent_workflow_lease "
        "ON agent_workflow_run(status, lease_expires_at) WHERE status = 'running'"
    ))
    session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_workflow_step_attempt "
        "ON agent_workflow_step(workflow_run_id, agent_code, attempt_no)"
    ))

    session.execute(
        text(
            """
            INSERT INTO institution(id, name, status)
            VALUES ('longyun-demo', '隆耘默认机构', 'active')
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    tenant_tables = (
        "research_project",
        "project_membership",
        "agent_workflow_run",
        "agent_workflow_step",
        "agent_artifact",
        "workflow_event",
    )
    for table_name in tenant_tables:
        session.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        session.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
        session.execute(text(f"DROP POLICY IF EXISTS institution_isolation ON {table_name}"))
        session.execute(
            text(
                f"CREATE POLICY institution_isolation ON {table_name} FOR ALL "
                "USING (institution_id = current_setting('app.institution_id', true)) "
                "WITH CHECK (institution_id = current_setting('app.institution_id', true))"
            )
        )

    private_tables = (
        "agent_workflow_run",
        "agent_workflow_step",
        "agent_artifact",
        "workflow_event",
    )
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
    for table_name in private_tables:
        session.execute(text(f"DROP POLICY IF EXISTS workflow_private_owner ON {table_name}"))
        # PostgreSQL ORs permissive policies, so the institution policy alone
        # would be too broad. Replace it with a combined institution/owner rule.
        session.execute(text(f"DROP POLICY IF EXISTS institution_isolation ON {table_name}"))
        session.execute(
            text(
                f"CREATE POLICY workflow_private_owner ON {table_name} FOR ALL USING ("
                "institution_id = current_setting('app.institution_id', true) AND "
                f"owner_id = current_setting('app.research_user_id', true){project_guard}) "
                "WITH CHECK (institution_id = current_setting('app.institution_id', true) AND "
                f"owner_id = current_setting('app.research_user_id', true){project_guard})"
            )
        )
    ensure_ai_usage_schema(session)
    session.commit()


def ensure_institution(session: Session, institution_id: str, name: str | None = None) -> None:
    session.execute(
        text(
            """
            INSERT INTO institution(id, name, status)
            VALUES (:institution_id, :name, 'active')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"institution_id": institution_id, "name": name or institution_id},
    )


def create_workflow_run(
    session: Session,
    *,
    institution_id: str,
    project_id: str,
    owner_id: str,
    user_request: str,
    requested_agents: list[str],
    evidence_context: list[dict[str, Any]] | None = None,
    research_session_id: str | None = None,
    external_transfer_acknowledged: bool = False,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
    deadline_at: datetime | None = None,
) -> dict[str, Any]:
    run_id = str(uuid4())
    thread_id = f"longyun:{institution_id}:{run_id}"
    row = session.execute(
        text(
            """
            INSERT INTO agent_workflow_run(
                id, thread_id, institution_id, project_id, owner_id,
                research_session_id, user_request, requested_agents,
                evidence_context, external_transfer_acknowledged, idempotency_key,
                max_attempts, deadline_at, status
            ) VALUES (
                :id, :thread_id, :institution_id, :project_id, :owner_id,
                :research_session_id, :user_request,
                CAST(:requested_agents AS jsonb), CAST(:evidence_context AS jsonb),
                :external_transfer_acknowledged, :idempotency_key,
                :max_attempts, :deadline_at, 'queued'
            )
            ON CONFLICT (institution_id, project_id, owner_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            DO UPDATE SET updated_at = agent_workflow_run.updated_at
            RETURNING *, (xmax = 0) AS was_created
            """
        ),
        {
            "id": run_id,
            "thread_id": thread_id,
            "institution_id": institution_id,
            "project_id": project_id,
            "owner_id": owner_id,
            "research_session_id": research_session_id,
            "user_request": user_request,
            "requested_agents": json.dumps(requested_agents, ensure_ascii=False),
            "evidence_context": json.dumps(evidence_context or [], ensure_ascii=False),
            "external_transfer_acknowledged": external_transfer_acknowledged,
            "idempotency_key": (idempotency_key or "").strip() or None,
            "max_attempts": max(1, min(int(max_attempts), 10)),
            "deadline_at": deadline_at,
        },
    ).mappings().one()
    if bool(row.get("was_created", True)):
        policy = get_model_data_policy()
        start_ai_usage(
            session,
            request_id=str(row["id"]),
            institution_id=institution_id,
            owner_id=owner_id,
            project_id=project_id,
            route="multi_agent_workflow",
            workflow_run_id=str(row["id"]),
            research_session_id=research_session_id,
            provider_name=policy.provider_name,
            provider_host=policy.provider_host,
            model_alias=os.getenv("LONGYUN_LLM_MODEL", "longyun-research"),
            metadata={"requested_agent_count": len(requested_agents)},
        )
    session.commit()
    return dict(row)


def get_workflow_run(
    session: Session,
    workflow_run_id: str,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    project_clause = "" if project_id is None else " AND project_id = :project_id"
    parameters: dict[str, Any] = {"id": workflow_run_id}
    if project_id is not None:
        parameters["project_id"] = project_id
    row = session.execute(
        text(
            "SELECT * FROM agent_workflow_run "
            f"WHERE id = :id{project_clause}"
        ),
        parameters,
    ).mappings().first()
    return dict(row) if row else None


def get_workflow_run_by_idempotency(
    session: Session,
    *,
    owner_id: str,
    project_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    row = session.execute(text("""
        SELECT * FROM agent_workflow_run
        WHERE owner_id = :owner_id AND project_id = :project_id
          AND idempotency_key = :idempotency_key
        ORDER BY created_at DESC LIMIT 1
    """), {
        "owner_id": owner_id,
        "project_id": project_id,
        "idempotency_key": idempotency_key,
    }).mappings().first()
    return dict(row) if row else None


def list_workflow_runs(
    session: Session,
    *,
    limit: int = 50,
    project_id: str,
) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT * FROM agent_workflow_run
            WHERE project_id = :project_id
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit, "project_id": project_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def list_workflow_events(session: Session, workflow_run_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT id, event_type, event_payload, created_at
            FROM workflow_event
            WHERE workflow_run_id = :workflow_run_id
            ORDER BY created_at, id
            """
        ),
        {"workflow_run_id": workflow_run_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def list_workflow_steps(session: Session, workflow_run_id: str) -> list[dict[str, Any]]:
    rows = session.execute(text("""
        SELECT id, agent_code, agent_version, contract_version, attempt_no,
               status, started_at, completed_at, error_code, error_detail,
               tool_results, artifact_id, updated_at
        FROM agent_workflow_step
        WHERE workflow_run_id = :workflow_run_id
        ORDER BY attempt_no, started_at, agent_code
    """), {"workflow_run_id": workflow_run_id}).mappings().all()
    return [dict(row) for row in rows]


def list_workflow_artifacts(session: Session, workflow_run_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT id, agent_code, agent_name, agent_version, content,
                   evidence_ids, artifact_metadata, created_at
            FROM agent_artifact
            WHERE workflow_run_id = :workflow_run_id
            ORDER BY created_at, id
            """
        ),
        {"workflow_run_id": workflow_run_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def claim_workflow_run(
    session: Session,
    workflow_run_id: str,
    *,
    lease_owner: str,
    lease_seconds: int,
) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            UPDATE agent_workflow_run
            SET status = 'running', started_at = COALESCE(started_at, now()),
                attempt_no = attempt_no + 1, lease_owner = :lease_owner,
                lease_expires_at = now() + (:lease_seconds * interval '1 second'),
                heartbeat_at = now(), error_code = NULL, error_detail = NULL,
                updated_at = now()
            WHERE id = :id
              AND cancel_requested_at IS NULL
              AND attempt_no < max_attempts
              AND (deadline_at IS NULL OR deadline_at > now())
              AND (
                    (status = 'queued' AND (next_retry_at IS NULL OR next_retry_at <= now()))
                 OR (status = 'running' AND lease_expires_at < now())
              )
            RETURNING *
            """
        ),
        {
            "id": workflow_run_id,
            "lease_owner": lease_owner[:120],
            "lease_seconds": max(30, min(int(lease_seconds), 3600)),
        },
    ).mappings().first()
    if row:
        update_ai_usage(session, workflow_run_id, status="running")
    session.commit()
    return dict(row) if row else None


def finalize_unclaimable_workflow_run(
    session: Session,
    workflow_run_id: str,
) -> dict[str, Any] | None:
    """Close a queued task that can no longer be claimed.

    Without this transition, an expired deadline or exhausted attempt budget
    would leave the task visible as ``queued`` forever.
    """
    row = session.execute(text("""
        UPDATE agent_workflow_run
        SET status = 'failed',
            error_code = CASE
                WHEN deadline_at IS NOT NULL AND deadline_at <= now()
                    THEN 'workflow_deadline_exceeded'
                ELSE 'workflow_attempts_exhausted'
            END,
            error_detail = CASE
                WHEN deadline_at IS NOT NULL AND deadline_at <= now()
                    THEN '任务在可执行前已超过截止时间，请重新提交。'
                ELSE '任务已达到最大尝试次数，请检查模型服务或联系管理员。'
            END,
            completed_at = now(), lease_owner = NULL,
            lease_expires_at = NULL, updated_at = now()
        WHERE id = :id AND status = 'queued'
          AND (
                (deadline_at IS NOT NULL AND deadline_at <= now())
             OR attempt_no >= max_attempts
          )
        RETURNING *
    """), {"id": workflow_run_id}).mappings().first()
    if row:
        update_ai_usage(
            session,
            workflow_run_id,
            status="failed",
            error_code=str(row.get("error_code") or "workflow_unclaimable"),
            latency_ms=utc_milliseconds_since(row["created_at"]),
        )
    session.commit()
    return dict(row) if row else None


def heartbeat_workflow_run(
    session: Session,
    workflow_run_id: str,
    *,
    lease_owner: str,
    lease_seconds: int,
) -> bool:
    updated = session.execute(text("""
        UPDATE agent_workflow_run
        SET heartbeat_at = now(),
            lease_expires_at = now() + (:lease_seconds * interval '1 second'),
            updated_at = now()
        WHERE id = :id AND status = 'running' AND lease_owner = :lease_owner
          AND cancel_requested_at IS NULL
    """), {
        "id": workflow_run_id,
        "lease_owner": lease_owner,
        "lease_seconds": max(30, min(int(lease_seconds), 3600)),
    }).rowcount
    session.commit()
    return bool(updated)


def workflow_should_stop(session: Session, workflow_run_id: str, *, lease_owner: str) -> str | None:
    row = session.execute(text("""
        SELECT cancel_requested_at, deadline_at, lease_owner, status
        FROM agent_workflow_run WHERE id = :id
    """), {"id": workflow_run_id}).mappings().first()
    if not row:
        return "任务不存在或当前账号无权访问。"
    if row["lease_owner"] != lease_owner or row["status"] != "running":
        return "任务执行租约已失效。"
    if row["cancel_requested_at"] is not None:
        return "任务已被用户取消。"
    if row["deadline_at"] is not None and row["deadline_at"] <= datetime.now(timezone.utc):
        return "任务已超过执行截止时间。"
    return None


def request_workflow_cancellation(session: Session, workflow_run_id: str) -> dict[str, Any] | None:
    row = session.execute(text("""
        UPDATE agent_workflow_run
        SET cancel_requested_at = COALESCE(cancel_requested_at, now()),
            status = CASE WHEN status = 'queued' THEN 'cancelled' ELSE status END,
            completed_at = CASE WHEN status = 'queued' THEN now() ELSE completed_at END,
            updated_at = now()
        WHERE id = :id AND status IN ('queued', 'running')
        RETURNING *
    """), {"id": workflow_run_id}).mappings().first()
    if row and row.get("status") == "cancelled":
        update_ai_usage(
            session,
            workflow_run_id,
            status="cancelled",
            error_code="workflow_cancelled",
            latency_ms=utc_milliseconds_since(row["created_at"]),
        )
    session.commit()
    return dict(row) if row else None


def mark_workflow_cancelled(
    session: Session,
    workflow_run_id: str,
    *,
    lease_owner: str,
    detail: str,
) -> None:
    session.execute(text("""
        UPDATE agent_workflow_run
        SET status = 'cancelled', error_code = 'workflow_cancelled',
            error_detail = :detail, completed_at = now(), lease_owner = NULL,
            lease_expires_at = NULL, updated_at = now()
        WHERE id = :id AND lease_owner = :lease_owner
    """), {"id": workflow_run_id, "lease_owner": lease_owner, "detail": detail[:2000]})
    update_ai_usage(
        session,
        workflow_run_id,
        status="cancelled",
        error_code="workflow_cancelled",
    )
    session.commit()


def schedule_workflow_retry(
    session: Session,
    workflow_run_id: str,
    *,
    lease_owner: str,
    error_code: str,
    error_detail: str,
    delay_seconds: int,
) -> bool:
    updated = session.execute(text("""
        UPDATE agent_workflow_run
        SET status = 'queued', next_retry_at = now() + (:delay_seconds * interval '1 second'),
            lease_owner = NULL, lease_expires_at = NULL,
            error_code = :error_code, error_detail = :error_detail, updated_at = now()
        WHERE id = :id AND status = 'running' AND lease_owner = :lease_owner
          AND attempt_no < max_attempts AND cancel_requested_at IS NULL
        RETURNING id
    """), {
        "id": workflow_run_id,
        "lease_owner": lease_owner,
        "error_code": error_code[:80],
        "error_detail": error_detail[:2000],
        "delay_seconds": max(1, min(int(delay_seconds), 3600)),
    }).first()
    session.commit()
    return bool(updated)


def persist_workflow_step_started(
    session: Session,
    run: dict[str, Any],
    *,
    agent_code: str,
    agent_version: str,
    contract_version: str,
    lease_owner: str,
) -> bool:
    updated = session.execute(text("""
        INSERT INTO agent_workflow_step(
            id, workflow_run_id, institution_id, project_id, owner_id,
            agent_code, agent_version, contract_version, attempt_no,
            status, started_at, updated_at
        ) SELECT
            CAST(:id AS varchar), CAST(:workflow_run_id AS varchar),
            CAST(:institution_id AS varchar), CAST(:project_id AS varchar),
            CAST(:owner_id AS varchar), CAST(:agent_code AS varchar),
            CAST(:agent_version AS varchar), CAST(:contract_version AS varchar),
            CAST(:attempt_no AS integer),
            'running', now(), now()
        WHERE EXISTS (
            SELECT 1 FROM agent_workflow_run
            WHERE id = CAST(:workflow_run_id AS varchar) AND status = 'running'
              AND lease_owner = CAST(:lease_owner AS varchar) AND lease_expires_at > now()
        )
        ON CONFLICT (workflow_run_id, agent_code, attempt_no) DO UPDATE SET
            status = 'running', started_at = COALESCE(agent_workflow_step.started_at, now()),
            agent_version = EXCLUDED.agent_version,
            contract_version = EXCLUDED.contract_version, updated_at = now()
        WHERE EXISTS (
            SELECT 1 FROM agent_workflow_run
            WHERE id = CAST(:workflow_run_id AS varchar) AND status = 'running'
              AND lease_owner = CAST(:lease_owner AS varchar) AND lease_expires_at > now()
        )
    """), {
        "id": str(uuid4()),
        "workflow_run_id": run["id"],
        "institution_id": run["institution_id"],
        "project_id": run.get("project_id"),
        "owner_id": run["owner_id"],
        "agent_code": agent_code,
        "agent_version": agent_version,
        "contract_version": contract_version,
        "attempt_no": int(run.get("attempt_no") or 1),
        "lease_owner": lease_owner,
    }).rowcount
    session.commit()
    return bool(updated)


def persist_workflow_step_completed(
    session: Session,
    run: dict[str, Any],
    *,
    agent_code: str,
    artifact: dict[str, Any],
    lease_owner: str,
) -> bool:
    updated = session.execute(text("""
        UPDATE agent_workflow_step
        SET status = 'completed', completed_at = now(), artifact_id = :artifact_id,
            tool_results = CAST(:tool_results AS jsonb), error_code = NULL,
            error_detail = NULL, updated_at = now()
        WHERE workflow_run_id = :workflow_run_id AND agent_code = :agent_code
          AND attempt_no = :attempt_no
          AND EXISTS (
              SELECT 1 FROM agent_workflow_run
              WHERE id = :workflow_run_id AND status = 'running'
                AND lease_owner = :lease_owner AND lease_expires_at > now()
          )
        RETURNING id
    """), {
        "workflow_run_id": run["id"],
        "agent_code": agent_code,
        "attempt_no": int(run.get("attempt_no") or 1),
        "artifact_id": artifact.get("id"),
        "tool_results": json.dumps(artifact.get("tool_results", []), ensure_ascii=False),
        "lease_owner": lease_owner,
    }).first()
    if not updated:
        session.rollback()
        return False
    session.execute(text("""
        INSERT INTO agent_artifact(
            id, workflow_run_id, institution_id, project_id, owner_id,
            agent_code, agent_name, agent_version, content,
            evidence_ids, artifact_metadata, created_at
        ) VALUES (
            :id, :workflow_run_id, :institution_id, :project_id, :owner_id,
            :agent_code, :agent_name, :agent_version, :content,
            CAST(:evidence_ids AS jsonb), CAST(:artifact_metadata AS jsonb),
            CAST(:created_at AS timestamptz)
        ) ON CONFLICT (id) DO NOTHING
    """), {
        "id": artifact["id"],
        "workflow_run_id": run["id"],
        "institution_id": run["institution_id"],
        "project_id": run.get("project_id"),
        "owner_id": run["owner_id"],
        "agent_code": artifact["agent_code"],
        "agent_name": artifact["agent_name"],
        "agent_version": artifact["agent_version"],
        "content": artifact["content"],
        "evidence_ids": json.dumps(artifact.get("evidence_ids", []), ensure_ascii=False),
        "artifact_metadata": json.dumps({
            "model_alias": artifact.get("model_alias"),
            "contract_version": artifact.get("contract_version"),
            "structured_output": artifact.get("structured_output") or {},
            "tool_results": artifact.get("tool_results") or [],
        }, ensure_ascii=False),
        "created_at": artifact.get("created_at") or datetime.now(timezone.utc).isoformat(),
    })
    session.commit()
    return True


def append_workflow_event(
    session: Session,
    run: dict[str, Any],
    *,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    session.execute(text("""
        INSERT INTO workflow_event(
            id, workflow_run_id, institution_id, project_id, owner_id,
            event_type, event_payload, created_at
        ) VALUES (
            :id, :workflow_run_id, :institution_id, :project_id, :owner_id,
            :event_type, CAST(:event_payload AS jsonb), now()
        )
    """), {
        "id": str(uuid4()),
        "workflow_run_id": run["id"],
        "institution_id": run["institution_id"],
        "project_id": run.get("project_id"),
        "owner_id": run["owner_id"],
        "event_type": event_type[:80],
        "event_payload": json.dumps(payload or {}, ensure_ascii=False),
    })
    session.commit()


def persist_workflow_step_failed(
    session: Session,
    run: dict[str, Any],
    *,
    agent_code: str,
    error_code: str,
    error_detail: str,
    lease_owner: str,
) -> bool:
    updated = session.execute(text("""
        UPDATE agent_workflow_step
        SET status = 'failed', completed_at = now(), error_code = :error_code,
            error_detail = :error_detail, updated_at = now()
        WHERE workflow_run_id = :workflow_run_id AND agent_code = :agent_code
          AND attempt_no = :attempt_no
          AND EXISTS (
              SELECT 1 FROM agent_workflow_run
              WHERE id = :workflow_run_id AND lease_owner = :lease_owner
          )
    """), {
        "workflow_run_id": run["id"],
        "agent_code": agent_code,
        "attempt_no": int(run.get("attempt_no") or 1),
        "error_code": error_code[:80],
        "error_detail": error_detail[:2000],
        "lease_owner": lease_owner,
    }).rowcount
    session.commit()
    return bool(updated)


def persist_workflow_result(
    session: Session,
    run: dict[str, Any],
    state: dict[str, Any],
    *,
    lease_owner: str | None = None,
) -> bool:
    institution_id = str(run["institution_id"])
    owner_id = str(run["owner_id"])
    project_id = run.get("project_id")
    if lease_owner is not None:
        active_lease = session.execute(text("""
            SELECT id FROM agent_workflow_run
            WHERE id = :id AND status = 'running' AND lease_owner = :lease_owner
              AND lease_expires_at > now()
            FOR UPDATE
        """), {"id": run["id"], "lease_owner": lease_owner}).first()
        if not active_lease:
            session.rollback()
            return False
    for artifact in state.get("artifacts", []):
        session.execute(
            text(
                """
                INSERT INTO agent_artifact(
                    id, workflow_run_id, institution_id, project_id, owner_id,
                    agent_code, agent_name, agent_version, content,
                    evidence_ids, artifact_metadata, created_at
                ) VALUES (
                    :id, :workflow_run_id, :institution_id, :project_id, :owner_id,
                    :agent_code, :agent_name, :agent_version, :content,
                    CAST(:evidence_ids AS jsonb), CAST(:artifact_metadata AS jsonb),
                    CAST(:created_at AS timestamptz)
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": artifact["id"],
                "workflow_run_id": run["id"],
                "institution_id": institution_id,
                "project_id": project_id,
                "owner_id": owner_id,
                "agent_code": artifact["agent_code"],
                "agent_name": artifact["agent_name"],
                "agent_version": artifact["agent_version"],
                "content": artifact["content"],
                "evidence_ids": json.dumps(artifact.get("evidence_ids", []), ensure_ascii=False),
                "artifact_metadata": json.dumps({
                    "model_alias": artifact.get("model_alias"),
                    "contract_version": artifact.get("contract_version"),
                    "structured_output": artifact.get("structured_output") or {},
                    "tool_results": artifact.get("tool_results") or [],
                }, ensure_ascii=False),
                "created_at": artifact.get("created_at") or datetime.now(timezone.utc).isoformat(),
            },
        )
    for event in state.get("events", []):
        session.execute(
            text(
                """
                INSERT INTO workflow_event(
                    id, workflow_run_id, institution_id, project_id, owner_id,
                    event_type, event_payload, created_at
                ) VALUES (
                    :id, :workflow_run_id, :institution_id, :project_id, :owner_id,
                    :event_type, CAST(:event_payload AS jsonb), CAST(:created_at AS timestamptz)
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": event["id"],
                "workflow_run_id": run["id"],
                "institution_id": institution_id,
                "project_id": project_id,
                "owner_id": owner_id,
                "event_type": event["type"],
                "event_payload": json.dumps(event.get("payload", {}), ensure_ascii=False),
                "created_at": event.get("created_at") or datetime.now(timezone.utc).isoformat(),
            },
        )

    usage_records = state.get("usage_records", [])
    totals = {
        key: sum(int(item.get(key) or 0) for item in usage_records)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    updated = session.execute(
        text(
            """
            UPDATE agent_workflow_run
            SET status = 'completed', plan = CAST(:plan AS jsonb),
                final_content = :final_content, model_alias = :model_alias,
                usage = CAST(:usage AS jsonb), completed_at = now(),
                lease_owner = NULL, lease_expires_at = NULL,
                next_retry_at = NULL, error_code = NULL, error_detail = NULL,
                updated_at = now()
            WHERE id = :id
              AND (CAST(:lease_owner AS text) IS NULL OR lease_owner = CAST(:lease_owner AS text))
            """
        ),
        {
            "id": run["id"],
            "plan": json.dumps(state.get("plan", []), ensure_ascii=False),
            "final_content": state.get("final_content") or "",
            "model_alias": state.get("model_alias") or "longyun-research",
            "usage": json.dumps({"totals": totals, "records": usage_records}, ensure_ascii=False),
            "lease_owner": lease_owner,
        },
    ).rowcount
    if updated:
        update_ai_usage(
            session,
            str(run["id"]),
            status="completed",
            usage=totals,
            latency_ms=utc_milliseconds_since(run["created_at"]),
            metadata={"usage_record_count": len(usage_records)},
        )
    session.commit()
    return bool(updated)


def fail_workflow_run(
    session: Session,
    workflow_run_id: str,
    *,
    error_code: str,
    error_detail: str,
    lease_owner: str | None = None,
) -> None:
    row = session.execute(
        text(
            """
            UPDATE agent_workflow_run
            SET status = 'failed', error_code = :error_code,
                error_detail = :error_detail, completed_at = now(),
                lease_owner = NULL, lease_expires_at = NULL, updated_at = now()
            WHERE id = :id AND status NOT IN ('completed', 'cancelled')
              AND (CAST(:lease_owner AS text) IS NULL OR lease_owner = CAST(:lease_owner AS text))
            RETURNING created_at
            """
        ),
        {
            "id": workflow_run_id,
            "error_code": error_code[:80],
            "error_detail": error_detail[:2000],
            "lease_owner": lease_owner,
        },
    ).mappings().first()
    if row:
        update_ai_usage(
            session,
            workflow_run_id,
            status="failed",
            error_code=error_code,
            latency_ms=utc_milliseconds_since(row["created_at"]),
        )
    session.commit()
