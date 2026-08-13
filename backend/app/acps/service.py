"""Translate native AIP task commands to the existing Longyun workflow."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from acps_sdk.aip import (
    FileDataItem,
    Product,
    StructuredDataItem,
    TaskCommand,
    TaskCommandType,
    TaskResult,
    TaskState,
    TaskStatus,
    TextDataItem,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..ai.registry import route_question
from ..ai.workflow_store import (
    create_workflow_run,
    fail_workflow_run,
    get_workflow_run,
    list_workflow_artifacts,
    request_workflow_cancellation,
)
from .config import AcpsIdentityBinding, AcpsSettings
from .store import (
    acknowledge_task,
    create_or_get_task_binding,
    get_task_binding,
    mark_task_enqueued,
    record_task_command,
    update_protocol_state,
)


SKILL_TO_AGENT_CODE = {
    "longyun.germplasm-analysis": "germplasm_analysis",
    "longyun.parent-combination": "parent_combination",
    "longyun.trial-analysis": "trial_analysis",
    "longyun.research-intelligence": "research_intelligence",
}

WORKFLOW_TO_AIP_STATE = {
    "queued": TaskState.Accepted,
    "running": TaskState.Working,
    "failed": TaskState.Failed,
    "cancelled": TaskState.Canceled,
}


class AipApplicationError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class AipTaskNotFound(AipApplicationError):
    def __init__(self, task_id: str):
        super().__init__(-32001, "Task not found", {"taskId": task_id})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | str | None = None) -> str:
    if isinstance(value, str):
        return value
    return (value or _now()).isoformat()


def _idempotency_key(leader_aic: str, task_id: str) -> str:
    digest = hashlib.sha256(f"{leader_aic}\0{task_id}".encode("utf-8")).hexdigest()
    return f"acps:{digest}"


def _default_enqueue(workflow_run_id: str, institution_id: str, owner_id: str) -> None:
    from ..ai.workflow_worker import execute_agent_workflow

    execute_agent_workflow.delay(workflow_run_id, institution_id, owner_id)


class LongyunAipService:
    """AIP-facing application service backed by Longyun's durable workflow."""

    def __init__(
        self,
        settings: AcpsSettings,
        *,
        enqueue: Callable[[str, str, str], None] = _default_enqueue,
    ) -> None:
        self.settings = settings
        self.enqueue = enqueue

    def handle(
        self,
        session: Session,
        *,
        leader_aic: str,
        binding: AcpsIdentityBinding,
        command: TaskCommand,
    ) -> TaskResult:
        if not command.taskId:
            raise AipApplicationError(-32602, "Invalid params", "taskId is required")
        if len(command.taskId) > 240 or len(leader_aic) > 240:
            raise AipApplicationError(
                -32602,
                "Invalid params",
                "taskId and senderId must not exceed 240 characters",
            )
        if command.command == TaskCommandType.Start:
            return self._start(session, leader_aic, binding, command)

        task_binding = get_task_binding(
            session,
            leader_aic=leader_aic,
            external_task_id=command.taskId,
        )
        if not task_binding:
            raise AipTaskNotFound(command.taskId)
        task_binding = record_task_command(
            session,
            task_binding["id"],
            command.model_dump(mode="json", exclude_none=True),
        )

        if command.command == TaskCommandType.Cancel:
            run = get_workflow_run(session, task_binding["workflow_run_id"])
            if run and run["status"] not in {"completed", "failed", "cancelled"}:
                request_workflow_cancellation(session, run["id"])
            return self._snapshot(session, task_binding, command=command)
        if command.command == TaskCommandType.Complete:
            snapshot_state = self._desired_state(
                task_binding,
                self._require_workflow(session, task_binding),
            )
            if snapshot_state == TaskState.AwaitingCompletion:
                task_binding = acknowledge_task(
                    session,
                    task_binding["id"],
                    acknowledged_at=_now(),
                )
            return self._snapshot(session, task_binding, command=command)
        if command.command == TaskCommandType.Continue:
            return self._snapshot(
                session,
                task_binding,
                command=command,
                status_note="隆耘当前任务不会进入 awaiting-input；continue 命令未改变任务。",
            )
        if command.command == TaskCommandType.Get:
            return self._snapshot(session, task_binding, command=command)
        raise AipApplicationError(
            -32602,
            "Invalid params",
            f"Unsupported command: {command.command.value}",
        )

    def _start(
        self,
        session: Session,
        leader_aic: str,
        identity: AcpsIdentityBinding,
        command: TaskCommand,
    ) -> TaskResult:
        existing = get_task_binding(
            session,
            leader_aic=leader_aic,
            external_task_id=command.taskId or "",
        )
        if existing:
            existing = record_task_command(
                session,
                existing["id"],
                command.model_dump(mode="json", exclude_none=True),
            )
            existing = self._ensure_enqueued(session, existing)
            return self._snapshot(session, existing, command=command)

        self._assert_scope(session, identity)
        user_request, skill_ids = self._parse_start_input(command)
        if identity.allowed_skill_ids and not set(skill_ids).issubset(
            identity.allowed_skill_ids
        ):
            denied = sorted(set(skill_ids).difference(identity.allowed_skill_ids))
            raise AipApplicationError(
                -32003,
                "Skill not authorized",
                {"skillIds": denied},
            )
        unknown = sorted(set(skill_ids).difference(SKILL_TO_AGENT_CODE))
        if unknown:
            raise AipApplicationError(
                -32602,
                "Unknown Longyun skill",
                {"skillIds": unknown},
            )
        requested_agents = [SKILL_TO_AGENT_CODE[item] for item in skill_ids]
        try:
            selected_agents = route_question(user_request, requested_agents)
        except ValueError as exc:
            raise AipApplicationError(-32602, "Invalid task request", str(exc)) from exc
        if identity.allowed_skill_ids:
            allowed_agents = {
                SKILL_TO_AGENT_CODE[skill_id]
                for skill_id in identity.allowed_skill_ids
                if skill_id in SKILL_TO_AGENT_CODE
            }
            denied_agents = [code for code in selected_agents if code not in allowed_agents]
            if denied_agents:
                raise AipApplicationError(
                    -32003,
                    "Routed skill is not authorized",
                    {"agentCodes": denied_agents},
                )

        deployment_mode = os.getenv(
            "LONGYUN_LLM_DEPLOYMENT_MODE", "external_api"
        ).strip().lower()
        if deployment_mode != "local" and not identity.external_data_acknowledged:
            raise AipApplicationError(
                -32003,
                "External model transfer is not authorized",
                "该 AIC 未获授权将任务正文发送给隆耘当前配置的外部模型服务。",
            )
        self._assert_capacity(session, identity)

        timeout_ms, max_products_bytes = self._start_limits(command)
        workflow = create_workflow_run(
            session,
            institution_id=identity.institution_id,
            project_id=identity.project_id,
            owner_id=identity.owner_id,
            user_request=user_request,
            requested_agents=selected_agents,
            evidence_context=[],
            external_transfer_acknowledged=identity.external_data_acknowledged,
            idempotency_key=_idempotency_key(leader_aic, command.taskId or ""),
            max_attempts=int(os.getenv("AGENT_WORKFLOW_MAX_ATTEMPTS", "3")),
            deadline_at=_now() + timedelta(milliseconds=timeout_ms),
        )
        initial_status = TaskStatus(
            state=TaskState.Accepted,
            stateChangedAt=_iso(workflow.get("created_at")),
        ).model_dump(mode="json", exclude_none=True)
        task_binding = create_or_get_task_binding(
            session,
            institution_id=identity.institution_id,
            owner_id=identity.owner_id,
            leader_aic=leader_aic,
            external_task_id=command.taskId or "",
            session_id=command.sessionId,
            workflow_run_id=workflow["id"],
            initial_command=command.model_dump(mode="json", exclude_none=True),
            initial_status=initial_status,
            max_products_bytes=max_products_bytes,
        )
        task_binding = self._ensure_enqueued(session, task_binding)
        return self._snapshot(session, task_binding, command=command)

    def _ensure_enqueued(
        self,
        session: Session,
        task_binding: dict[str, Any],
    ) -> dict[str, Any]:
        """At-least-once enqueue with a durable marker.

        A crash after the database commit but before broker publication leaves
        ``enqueued_at`` empty.  A repeated idempotent Start repairs that gap.
        A crash after publication can cause one duplicate message, which the
        existing workflow lease/claim logic safely rejects.
        """
        if task_binding.get("enqueued_at"):
            return task_binding
        workflow = self._require_workflow(session, task_binding)
        if workflow["status"] != "queued":
            return task_binding
        try:
            self.enqueue(
                workflow["id"],
                task_binding["institution_id"],
                task_binding["owner_id"],
            )
        except Exception as exc:
            fail_workflow_run(
                session,
                workflow["id"],
                error_code="workflow_queue_unavailable",
                error_detail="智能体任务队列暂不可用，请稍后重新提交。",
            )
            raise AipApplicationError(
                -32050,
                "Longyun workflow queue unavailable",
                {"taskId": task_binding["external_task_id"]},
            ) from exc
        return mark_task_enqueued(
            session,
            task_binding["id"],
            enqueued_at=_now(),
        )

    def _parse_start_input(self, command: TaskCommand) -> tuple[str, list[str]]:
        texts: list[str] = []
        skill_ids: list[str] = []
        for item in command.dataItems or []:
            if isinstance(item, TextDataItem) and item.text.strip():
                texts.append(item.text.strip())
            elif isinstance(item, StructuredDataItem):
                raw_skills = item.data.get("skillIds") or item.data.get("skill_ids") or []
                if isinstance(raw_skills, list):
                    skill_ids.extend(str(value).strip() for value in raw_skills)
            elif isinstance(item, FileDataItem):
                raise AipApplicationError(
                    -32602,
                    "File input is not supported on this boundary",
                    "文件必须先进入隆耘现有的受控附件流程；AIP 边界当前只接收文本和结构化参数。",
                )
        params = command.commandParams or {}
        raw_param_skills = params.get("skillIds") or params.get("skill_ids") or []
        if isinstance(raw_param_skills, list):
            skill_ids.extend(str(value).strip() for value in raw_param_skills)
        user_request = "\n\n".join(texts).strip()
        if not user_request:
            raise AipApplicationError(
                -32602,
                "Task text is required",
                "start 命令至少需要一个非空 TextDataItem。",
            )
        return user_request, list(dict.fromkeys(item for item in skill_ids if item))

    def _start_limits(self, command: TaskCommand) -> tuple[int, int | None]:
        params = command.commandParams or {}
        raw_timeout = params.get("timeout")
        default_timeout = int(os.getenv("AGENT_DEFAULT_DEADLINE_SECONDS", "900")) * 1000
        try:
            timeout_ms = int(raw_timeout) if raw_timeout is not None else default_timeout
        except (TypeError, ValueError) as exc:
            raise AipApplicationError(-32602, "Invalid timeout") from exc
        timeout_ms = max(1_000, min(timeout_ms, self.settings.max_timeout_ms))
        raw_limit = params.get("maxProductsBytes")
        if raw_limit is None:
            return timeout_ms, None
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise AipApplicationError(-32602, "Invalid maxProductsBytes") from exc
        if limit <= 0:
            raise AipApplicationError(-32602, "Invalid maxProductsBytes")
        return timeout_ms, limit

    def _assert_scope(self, session: Session, identity: AcpsIdentityBinding) -> None:
        institution = session.execute(text("""
            SELECT status, trial_expires_at FROM institution WHERE id = :id
        """), {"id": identity.institution_id}).mappings().first()
        if not institution or institution["status"] not in {"active", "trial"}:
            raise AipApplicationError(-32003, "Institution is not active")
        expires_at = institution.get("trial_expires_at")
        if expires_at and expires_at <= _now():
            raise AipApplicationError(-32003, "Institution trial has expired")
        project = session.execute(text("""
            SELECT 1 FROM research_project project
            WHERE project.id = :project_id
              AND project.institution_id = :institution_id
              AND project.status = 'active'
              AND EXISTS (
                  SELECT 1 FROM project_membership membership
                  WHERE membership.project_id = project.id
                    AND membership.institution_id = project.institution_id
                    AND membership.user_id = :owner_id
              )
        """), {
            "project_id": identity.project_id,
            "institution_id": identity.institution_id,
            "owner_id": identity.owner_id,
        }).first()
        if not project:
            raise AipApplicationError(
                -32003,
                "Configured project is not active or the AIC owner is not a project member",
            )

    def _assert_capacity(self, session: Session, identity: AcpsIdentityBinding) -> None:
        if os.getenv("AGENT_QUEUE_LIMITS_ENABLED", "false").lower() != "true":
            return
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"longyun:agent-submit:{identity.institution_id}"},
        )
        owner_active = session.execute(text("""
            SELECT count(*) FROM agent_workflow_run
            WHERE owner_id = :owner_id AND status IN ('queued', 'running')
        """), {"owner_id": identity.owner_id}).scalar_one()
        institution_active = session.execute(
            text("SELECT current_institution_active_agent_workflow_count()")
        ).scalar_one()
        if owner_active >= int(os.getenv("AGENT_MAX_ACTIVE_PER_USER", "2")):
            raise AipApplicationError(-32029, "Leader task limit reached")
        if institution_active >= int(
            os.getenv("AGENT_MAX_ACTIVE_PER_INSTITUTION", "16")
        ):
            raise AipApplicationError(-32029, "Institution task limit reached")

    def _require_workflow(
        self,
        session: Session,
        task_binding: dict[str, Any],
    ) -> dict[str, Any]:
        run = get_workflow_run(session, task_binding["workflow_run_id"])
        if not run:
            raise AipTaskNotFound(task_binding["external_task_id"])
        return run

    def _desired_state(
        self,
        task_binding: dict[str, Any],
        run: dict[str, Any],
    ) -> TaskState:
        if task_binding.get("protocol_error") and task_binding.get("protocol_state") in {
            TaskState.Failed.value,
            TaskState.Rejected.value,
        }:
            return TaskState(task_binding["protocol_state"])
        if run["status"] == "completed":
            return (
                TaskState.Completed
                if task_binding.get("acknowledged_at")
                else TaskState.AwaitingCompletion
            )
        return WORKFLOW_TO_AIP_STATE.get(run["status"], TaskState.Working)

    def _snapshot(
        self,
        session: Session,
        task_binding: dict[str, Any],
        *,
        command: TaskCommand,
        status_note: str | None = None,
    ) -> TaskResult:
        run = self._require_workflow(session, task_binding)
        state = self._desired_state(task_binding, run)
        changed_at = _iso(
            task_binding.get("acknowledged_at")
            if state == TaskState.Completed
            else run.get("updated_at")
        )
        task_binding = update_protocol_state(
            session,
            task_binding["id"],
            state=state.value,
            state_changed_at=changed_at,
            error=task_binding.get("protocol_error"),
        )
        products = self._products(session, task_binding, run) if run["status"] == "completed" else []
        product_error = self._product_limit_error(task_binding, products)
        if product_error:
            state = TaskState.Failed
            changed_at = _iso()
            task_binding = update_protocol_state(
                session,
                task_binding["id"],
                state=state.value,
                state_changed_at=changed_at,
                error=product_error,
            )
            products = []

        status_items = []
        detail = product_error or run.get("error_detail") or task_binding.get("protocol_error")
        if detail:
            status_items.append(TextDataItem(text=str(detail)))
        if status_note:
            status_items.append(TextDataItem(text=status_note))
        status = TaskStatus(
            state=state,
            stateChangedAt=changed_at,
            dataItems=status_items or None,
        )
        command_history = [
            TaskCommand.model_validate(item)
            for item in list(task_binding.get("command_history") or [])
        ]
        status_history = [
            TaskStatus.model_validate(item)
            for item in list(task_binding.get("status_history") or [])
        ]
        if command.command == TaskCommandType.Get:
            command_history, status_history = self._filter_histories(
                command,
                command_history,
                status_history,
            )
        return TaskResult(
            id=f"result-{task_binding['id']}",
            sentAt=_iso(),
            senderRole="partner",
            senderId=self.settings.partner_aic,
            taskId=task_binding["external_task_id"],
            sessionId=task_binding.get("session_id") or command.sessionId,
            status=status,
            products=products or None,
            commandHistory=command_history or None,
            statusHistory=status_history or None,
        )

    def _products(
        self,
        session: Session,
        task_binding: dict[str, Any],
        run: dict[str, Any],
    ) -> list[Product]:
        task_id = task_binding["external_task_id"]
        products = [Product(
            id=f"{task_id}:final",
            name="隆耘农业分析结果",
            description="隆耘总控智能体汇总结果",
            dataItems=[
                TextDataItem(text=str(run.get("final_content") or "")),
                StructuredDataItem(data={
                    "modelAlias": run.get("model_alias"),
                    "usage": run.get("usage") or {},
                }),
            ],
        )]
        for artifact in list_workflow_artifacts(session, run["id"]):
            metadata = artifact.get("artifact_metadata") or {}
            products.append(Product(
                id=str(artifact["id"]),
                name=str(artifact.get("agent_name") or artifact.get("agent_code") or "隆耘产物"),
                description=f"隆耘子智能体 {artifact.get('agent_code') or ''} 的结构化产物",
                dataItems=[
                    TextDataItem(text=str(artifact.get("content") or "")),
                    StructuredDataItem(data={
                        "agentCode": artifact.get("agent_code"),
                        "agentVersion": artifact.get("agent_version"),
                        "structuredOutput": metadata.get("structured_output") or {},
                        "evidenceIds": list(artifact.get("evidence_ids") or []),
                    }),
                ],
            ))
        return products

    def _product_limit_error(
        self,
        task_binding: dict[str, Any],
        products: list[Product],
    ) -> str | None:
        limit = task_binding.get("max_products_bytes")
        if not limit or not products:
            return None
        size = len(json.dumps(
            [item.model_dump(mode="json", exclude_none=True) for item in products],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"))
        if size <= int(limit):
            return None
        return f"Products size {size} bytes exceeds maxProductsBytes={limit}."

    @staticmethod
    def _filter_histories(
        command: TaskCommand,
        commands: list[TaskCommand],
        statuses: list[TaskStatus],
    ) -> tuple[list[TaskCommand], list[TaskStatus]]:
        params = command.commandParams or {}
        last_command = params.get("lastCommandSentAt")
        last_status = params.get("lastStateChangedAt")
        if last_command:
            commands = [item for item in commands if item.sentAt > str(last_command)]
        if last_status:
            statuses = [
                item for item in statuses if item.stateChangedAt > str(last_status)
            ]
        return commands, statuses
