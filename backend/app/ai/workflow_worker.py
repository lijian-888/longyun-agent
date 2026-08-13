"""Celery worker entry point for LangGraph workflow execution."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import suppress
from urllib.parse import quote
from uuid import uuid4

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import text

from ..celery_app import celery_app
from ..tenancy import TenantAccessError, TenantConfigurationError, tenant_database_manager
from .provider import LLMProviderError, build_default_provider
from .runtime import DatabaseWorkflowObserver, set_tool_tenant_context
from .tools import build_rice_tool_registry
from .workflow import WorkflowCancelled, build_workflow_graph
from .workflow_store import (
    claim_workflow_run,
    fail_workflow_run,
    finalize_unclaimable_workflow_run,
    get_workflow_run,
    heartbeat_workflow_run,
    mark_workflow_cancelled,
    persist_workflow_result,
    schedule_workflow_retry,
)


logger = logging.getLogger(__name__)
CHECKPOINT_SCHEMA = os.getenv("LANGGRAPH_CHECKPOINT_SCHEMA", "agent_runtime")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", CHECKPOINT_SCHEMA):
    raise RuntimeError("LANGGRAPH_CHECKPOINT_SCHEMA must be a simple PostgreSQL identifier")
LEASE_SECONDS = max(60, int(os.getenv("AGENT_WORKFLOW_LEASE_SECONDS", "300")))
HEARTBEAT_SECONDS = max(10, min(int(os.getenv("AGENT_WORKFLOW_HEARTBEAT_SECONDS", "30")), LEASE_SECONDS // 2))
_checkpoint_setup_complete: set[str] = set()


def _psycopg_url(url: str) -> str:
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    separator = "&" if "?" in normalized else "?"
    option = quote(f"-csearch_path={CHECKPOINT_SCHEMA}", safe="")
    return f"{normalized}{separator}options={option}"


def _ensure_private_checkpoint_schema(checkpoint_admin_engine) -> None:
    with checkpoint_admin_engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}"))
        connection.execute(text(f"REVOKE ALL ON SCHEMA {CHECKPOINT_SCHEMA} FROM PUBLIC"))


def _set_worker_context(session, run: dict) -> None:
    session.execute(
        text("SELECT set_config('app.research_user_id', :value, true)"),
        {"value": str(run["owner_id"])},
    )
    session.execute(
        text("SELECT set_config('app.institution_id', :value, true)"),
        {"value": str(run["institution_id"])},
    )
    session.execute(text("SELECT set_config('app.institution_admin', 'false', true)"))
    session.execute(
        text("SELECT set_config('app.project_id', :value, true)"),
        {"value": str(run.get("project_id") or "")},
    )


def _heartbeat_once(run: dict, lease_owner: str, worker_session_factory) -> bool:
    with worker_session_factory() as session:
        _set_worker_context(session, run)
        return heartbeat_workflow_run(
            session,
            str(run["id"]),
            lease_owner=lease_owner,
            lease_seconds=LEASE_SECONDS,
        )


async def _heartbeat_loop(run: dict, lease_owner: str, stop: asyncio.Event, worker_session_factory) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            active = await asyncio.to_thread(
                _heartbeat_once, run, lease_owner, worker_session_factory
            )
            if not active:
                return


async def _ensure_checkpoint_tables_once(
    checkpointer: AsyncPostgresSaver,
    institution_id: str,
) -> None:
    if institution_id in _checkpoint_setup_complete:
        return
    await checkpointer.setup()
    _checkpoint_setup_complete.add(institution_id)


async def _execute(
    run: dict,
    lease_owner: str,
    worker_session_factory,
    checkpoint_database_url: str,
    checkpoint_admin_engine,
) -> dict:
    provider = build_default_provider()
    _ensure_private_checkpoint_schema(checkpoint_admin_engine)
    async with AsyncPostgresSaver.from_conn_string(
        _psycopg_url(checkpoint_database_url),
        pipeline=False,
    ) as checkpointer:
        await _ensure_checkpoint_tables_once(checkpointer, str(run["institution_id"]))
        observer = DatabaseWorkflowObserver(
            session_factory=worker_session_factory,
            set_context=_set_worker_context,
            run=run,
            lease_owner=lease_owner,
        )
        tool_registry = build_rice_tool_registry(
            worker_session_factory,
            set_tool_tenant_context(_set_worker_context),
        )
        graph = build_workflow_graph(
            provider,
            tool_executor=tool_registry,
            observer=observer,
            checkpointer=checkpointer,
        )
        initial_state = {
            "workflow_run_id": str(run["id"]),
            "thread_id": str(run["thread_id"]),
            "institution_id": str(run["institution_id"]),
            "project_id": run.get("project_id"),
            "owner_user_id": str(run["owner_id"]),
            "user_request": str(run["user_request"]),
            "requested_agents": list(run.get("requested_agents") or []),
            "evidence_context": list(run.get("evidence_context") or []),
            "external_transfer_acknowledged": bool(
                run.get("external_transfer_acknowledged")
            ),
            "artifacts": [],
            "events": [],
            "usage_records": [],
            "status": "queued",
        }
        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(run, lease_owner, stop_heartbeat, worker_session_factory)
        )
        try:
            return await graph.ainvoke(
                initial_state,
                config={
                    "configurable": {"thread_id": str(run["thread_id"])},
                    "recursion_limit": 24,
                },
            )
        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task


@celery_app.task(
    name="longyun.agent_workflow.execute",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def execute_agent_workflow(
    self,
    workflow_run_id: str,
    institution_id: str | None = None,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, str]:
    """Claim one tenant-scoped run with a renewable lease and execute it."""
    if not institution_id or not owner_id or not project_id:
        # Looking up an unscoped run across all institution databases would be
        # both ambiguous and a cross-tenant disclosure risk.
        return {"status": "invalid_tenant_context", "workflow_run_id": workflow_run_id}
    try:
        tenant_database_manager.ensure_control_schema()
        binding = tenant_database_manager.resolve(institution_id)
        tenant_database_manager.verify_tenant_database(institution_id)
        worker_session_factory = tenant_database_manager.session_factory(institution_id)
        checkpoint_admin_engine = tenant_database_manager.engine_for(institution_id, migration=True)
        checkpoint_database_url = (
            binding.migration_database_url
            if tenant_database_manager.mode == "multi"
            else os.getenv("LANGGRAPH_DATABASE_URL", binding.migration_database_url)
        )
    except (TenantAccessError, TenantConfigurationError) as exc:
        logger.error("Rejected tenant workflow run_id=%s: %s", workflow_run_id, exc)
        return {"status": "invalid_tenant_context", "workflow_run_id": workflow_run_id}

    lease_owner = f"celery:{self.request.id or uuid4()}"
    with worker_session_factory() as session:
        _set_worker_context(session, {
            "institution_id": institution_id,
            "owner_id": owner_id,
            "project_id": project_id,
        })
        existing = get_workflow_run(session, workflow_run_id, project_id)
        if not existing:
            return {"status": "not_found", "workflow_run_id": workflow_run_id}
        if existing["institution_id"] != institution_id or existing["owner_id"] != owner_id:
            return {"status": "not_found", "workflow_run_id": workflow_run_id}
        _set_worker_context(session, existing)
        run = claim_workflow_run(
            session,
            workflow_run_id,
            lease_owner=lease_owner,
            lease_seconds=LEASE_SECONDS,
        )
        if not run:
            terminal = finalize_unclaimable_workflow_run(session, workflow_run_id)
            if terminal:
                return {"status": "failed", "workflow_run_id": workflow_run_id}
            return {"status": "already_claimed", "workflow_run_id": workflow_run_id}

    try:
        state = asyncio.run(_execute(
            run,
            lease_owner,
            worker_session_factory,
            checkpoint_database_url,
            checkpoint_admin_engine,
        ))
        with worker_session_factory() as session:
            _set_worker_context(session, run)
            persisted = persist_workflow_result(session, run, state, lease_owner=lease_owner)
        if not persisted:
            return {"status": "lease_lost", "workflow_run_id": workflow_run_id}
        return {"status": "completed", "workflow_run_id": workflow_run_id}
    except WorkflowCancelled as exc:
        with worker_session_factory() as session:
            _set_worker_context(session, run)
            mark_workflow_cancelled(
                session,
                workflow_run_id,
                lease_owner=lease_owner,
                detail=str(exc),
            )
        return {"status": "cancelled", "workflow_run_id": workflow_run_id}
    except LLMProviderError as exc:
        logger.warning("Workflow provider failure run_id=%s: %s", workflow_run_id, exc)
        retry_delay_seconds = 10 * max(1, int(run.get("attempt_no") or 1))
        with worker_session_factory() as session:
            _set_worker_context(session, run)
            retry_scheduled = schedule_workflow_retry(
                session,
                workflow_run_id,
                lease_owner=lease_owner,
                error_code="model_service_unavailable",
                error_detail=str(exc),
                delay_seconds=retry_delay_seconds,
            )
        if not retry_scheduled:
            with worker_session_factory() as session:
                _set_worker_context(session, run)
                fail_workflow_run(
                    session,
                    workflow_run_id,
                    error_code="model_service_unavailable",
                    error_detail=str(exc),
                    lease_owner=lease_owner,
                )
        if retry_scheduled:
            # Celery may redeliver slightly before the database retry timestamp.
            # The margin prevents an early delivery from being rejected and lost.
            raise self.retry(
                exc=exc,
                countdown=retry_delay_seconds + 2,
                max_retries=max(0, int(run.get("max_attempts") or 3) - 1),
            )
        return {"status": "failed", "workflow_run_id": workflow_run_id}
    except Exception as exc:
        logger.exception("Workflow execution failed run_id=%s", workflow_run_id)
        with worker_session_factory() as session:
            _set_worker_context(session, run)
            fail_workflow_run(
                session,
                workflow_run_id,
                error_code="workflow_execution_failed",
                error_detail="工作流执行失败，请联系管理员并提供任务编号。",
                lease_owner=lease_owner,
            )
        return {"status": "failed", "workflow_run_id": workflow_run_id}
