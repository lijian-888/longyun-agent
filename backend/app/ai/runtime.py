"""Runtime observers that persist node lifecycle without coupling the graph to SQL."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from .orchestration import WorkflowCancelled
from .tools.core import AgentToolContext
from .workflow_store import (
    append_workflow_event,
    persist_workflow_step_completed,
    persist_workflow_step_failed,
    persist_workflow_step_started,
    workflow_should_stop,
)


SessionFactory = Callable[[], Session]
RunContextSetter = Callable[[Session, dict[str, Any]], None]


class DatabaseWorkflowObserver:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        set_context: RunContextSetter,
        run: dict[str, Any],
        lease_owner: str,
    ) -> None:
        self._session_factory = session_factory
        self._set_context = set_context
        self._run = run
        self._lease_owner = lease_owner

    def _session(self) -> Session:
        session = self._session_factory()
        self._set_context(session, self._run)
        return session

    def assert_active(self, node_code: str) -> None:
        with self._session() as session:
            reason = workflow_should_stop(
                session,
                str(self._run["id"]),
                lease_owner=self._lease_owner,
            )
        if reason:
            raise WorkflowCancelled(reason)

    def node_started(self, node_code: str, agent_version: str, contract_version: str) -> None:
        with self._session() as session:
            persisted = persist_workflow_step_started(
                session,
                self._run,
                agent_code=node_code,
                agent_version=agent_version,
                contract_version=contract_version,
                lease_owner=self._lease_owner,
            )
        if not persisted:
            raise WorkflowCancelled("任务执行租约已失效。")
        with self._session() as session:
            append_workflow_event(
                session,
                self._run,
                event_type="agent_started",
                payload={
                    "agent_code": node_code,
                    "agent_version": agent_version,
                    "contract_version": contract_version,
                    "attempt_no": self._run.get("attempt_no"),
                },
            )

    def node_completed(self, node_code: str, artifact: dict[str, Any]) -> None:
        with self._session() as session:
            persisted = persist_workflow_step_completed(
                session,
                self._run,
                agent_code=node_code,
                artifact=artifact,
                lease_owner=self._lease_owner,
            )
        if not persisted:
            raise WorkflowCancelled("任务执行租约已失效，结果未写入。")
        with self._session() as session:
            append_workflow_event(
                session,
                self._run,
                event_type="agent_result_persisted",
                payload={
                    "agent_code": node_code,
                    "artifact_id": artifact.get("id"),
                    "evidence_ids": artifact.get("evidence_ids") or [],
                },
            )

    def node_failed(self, node_code: str, error: Exception) -> None:
        error_code = "workflow_cancelled" if isinstance(error, WorkflowCancelled) else "agent_execution_failed"
        detail = str(error) if isinstance(error, WorkflowCancelled) else "子智能体执行失败，请联系管理员并提供任务编号。"
        with self._session() as session:
            persist_workflow_step_failed(
                session,
                self._run,
                agent_code=node_code,
                error_code=error_code,
                error_detail=detail,
                lease_owner=self._lease_owner,
            )
        with self._session() as session:
            append_workflow_event(
                session,
                self._run,
                event_type="agent_failed",
                payload={"agent_code": node_code, "error_code": error_code},
            )


def set_tool_tenant_context(
    set_run_context: RunContextSetter,
) -> Callable[[Session, AgentToolContext], None]:
    def apply(session: Session, context: AgentToolContext) -> None:
        set_run_context(session, {
            "institution_id": context.institution_id,
            "owner_id": context.owner_user_id,
            "project_id": context.project_id,
        })

    return apply
