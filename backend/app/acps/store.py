"""Persistent mapping between external AIP tasks and Longyun workflows."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session


def ensure_acps_schema(session: Session) -> None:
    """Create only the integration table; existing Longyun tables stay intact."""
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS acps_task_binding (
            id VARCHAR(36) PRIMARY KEY,
            institution_id VARCHAR(64) NOT NULL REFERENCES institution(id),
            owner_id VARCHAR(120) NOT NULL,
            leader_aic VARCHAR(240) NOT NULL,
            external_task_id VARCHAR(240) NOT NULL,
            session_id VARCHAR(240),
            workflow_run_id VARCHAR(36) NOT NULL
                REFERENCES agent_workflow_run(id) ON DELETE CASCADE,
            protocol_state VARCHAR(40) NOT NULL DEFAULT 'accepted',
            protocol_error TEXT,
            command_history JSONB NOT NULL DEFAULT '[]'::jsonb,
            status_history JSONB NOT NULL DEFAULT '[]'::jsonb,
            max_products_bytes BIGINT,
            enqueued_at TIMESTAMPTZ,
            acknowledged_at TIMESTAMPTZ,
            last_state_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (leader_aic, external_task_id),
            UNIQUE (workflow_run_id)
        )
    """))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_acps_task_owner "
        "ON acps_task_binding(institution_id, owner_id, updated_at DESC)"
    ))
    session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_acps_task_external "
        "ON acps_task_binding(leader_aic, external_task_id)"
    ))
    session.execute(text(
        "ALTER TABLE acps_task_binding ADD COLUMN IF NOT EXISTS enqueued_at TIMESTAMPTZ"
    ))
    session.execute(text("ALTER TABLE acps_task_binding ENABLE ROW LEVEL SECURITY"))
    session.execute(text("ALTER TABLE acps_task_binding FORCE ROW LEVEL SECURITY"))
    session.execute(text(
        "DROP POLICY IF EXISTS acps_task_private_owner ON acps_task_binding"
    ))
    session.execute(text("""
        CREATE POLICY acps_task_private_owner ON acps_task_binding FOR ALL
        USING (
            institution_id = current_setting('app.institution_id', true)
            AND owner_id = current_setting('app.research_user_id', true)
        )
        WITH CHECK (
            institution_id = current_setting('app.institution_id', true)
            AND owner_id = current_setting('app.research_user_id', true)
        )
    """))
    session.commit()


def get_task_binding(
    session: Session,
    *,
    leader_aic: str,
    external_task_id: str,
) -> dict[str, Any] | None:
    row = session.execute(text("""
        SELECT * FROM acps_task_binding
        WHERE leader_aic = :leader_aic AND external_task_id = :external_task_id
    """), {
        "leader_aic": leader_aic,
        "external_task_id": external_task_id,
    }).mappings().first()
    return dict(row) if row else None


def create_or_get_task_binding(
    session: Session,
    *,
    institution_id: str,
    owner_id: str,
    leader_aic: str,
    external_task_id: str,
    session_id: str | None,
    workflow_run_id: str,
    initial_command: dict[str, Any],
    initial_status: dict[str, Any],
    max_products_bytes: int | None,
) -> dict[str, Any]:
    row = session.execute(text("""
        INSERT INTO acps_task_binding(
            id, institution_id, owner_id, leader_aic, external_task_id,
            session_id, workflow_run_id, protocol_state, command_history,
            status_history, max_products_bytes, last_state_changed_at
        ) VALUES (
            :id, :institution_id, :owner_id, :leader_aic, :external_task_id,
            :session_id, :workflow_run_id, 'accepted',
            CAST(:command_history AS jsonb), CAST(:status_history AS jsonb),
            :max_products_bytes, CAST(:state_changed_at AS timestamptz)
        )
        ON CONFLICT (leader_aic, external_task_id) DO UPDATE
        SET updated_at = acps_task_binding.updated_at
        RETURNING *
    """), {
        "id": str(uuid4()),
        "institution_id": institution_id,
        "owner_id": owner_id,
        "leader_aic": leader_aic,
        "external_task_id": external_task_id,
        "session_id": session_id,
        "workflow_run_id": workflow_run_id,
        "command_history": json.dumps([initial_command], ensure_ascii=False),
        "status_history": json.dumps([initial_status], ensure_ascii=False),
        "max_products_bytes": max_products_bytes,
        "state_changed_at": initial_status["stateChangedAt"],
    }).mappings().one()
    session.commit()
    return dict(row)


def record_task_command(
    session: Session,
    binding_id: str,
    command: dict[str, Any],
) -> dict[str, Any]:
    row = session.execute(text("""
        SELECT * FROM acps_task_binding WHERE id = :id FOR UPDATE
    """), {"id": binding_id}).mappings().one()
    history = list(row.get("command_history") or [])
    command_id = str(command.get("id") or "")
    if not any(str(item.get("id") or "") == command_id for item in history):
        history.append(command)
        row = session.execute(text("""
            UPDATE acps_task_binding
            SET command_history = CAST(:history AS jsonb), updated_at = now()
            WHERE id = :id RETURNING *
        """), {
            "id": binding_id,
            "history": json.dumps(history, ensure_ascii=False),
        }).mappings().one()
        session.commit()
    return dict(row)


def update_protocol_state(
    session: Session,
    binding_id: str,
    *,
    state: str,
    state_changed_at: str,
    error: str | None = None,
) -> dict[str, Any]:
    row = session.execute(text("""
        SELECT * FROM acps_task_binding WHERE id = :id FOR UPDATE
    """), {"id": binding_id}).mappings().one()
    if row["protocol_state"] == state and (row.get("protocol_error") or None) == error:
        return dict(row)
    history = list(row.get("status_history") or [])
    history.append({"state": state, "stateChangedAt": state_changed_at})
    updated = session.execute(text("""
        UPDATE acps_task_binding
        SET protocol_state = :state,
            protocol_error = :error,
            status_history = CAST(:history AS jsonb),
            last_state_changed_at = CAST(:state_changed_at AS timestamptz),
            updated_at = now()
        WHERE id = :id RETURNING *
    """), {
        "id": binding_id,
        "state": state,
        "error": error,
        "history": json.dumps(history, ensure_ascii=False),
        "state_changed_at": state_changed_at,
    }).mappings().one()
    session.commit()
    return dict(updated)


def acknowledge_task(
    session: Session,
    binding_id: str,
    *,
    acknowledged_at: datetime,
) -> dict[str, Any]:
    row = session.execute(text("""
        UPDATE acps_task_binding
        SET acknowledged_at = COALESCE(acknowledged_at, :acknowledged_at),
            updated_at = now()
        WHERE id = :id RETURNING *
    """), {"id": binding_id, "acknowledged_at": acknowledged_at}).mappings().one()
    session.commit()
    return dict(row)


def mark_task_enqueued(
    session: Session,
    binding_id: str,
    *,
    enqueued_at: datetime,
) -> dict[str, Any]:
    row = session.execute(text("""
        UPDATE acps_task_binding
        SET enqueued_at = COALESCE(enqueued_at, :enqueued_at), updated_at = now()
        WHERE id = :id RETURNING *
    """), {"id": binding_id, "enqueued_at": enqueued_at}).mappings().one()
    session.commit()
    return dict(row)
