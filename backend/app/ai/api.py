"""HTTP boundary for the four-agent workflow application."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..auth import CurrentUser, require_researcher
from ..tenancy import tenant_database_manager
from .api_models import AgentWorkflowCreate
from .model_policy import get_model_data_policy
from .registry import public_agent_catalog, route_question
from .workflow_store import (
    create_workflow_run,
    fail_workflow_run,
    get_workflow_run,
    get_workflow_run_by_idempotency,
    list_workflow_artifacts,
    list_workflow_events,
    list_workflow_runs,
    list_workflow_steps,
    request_workflow_cancellation,
)


@dataclass(frozen=True)
class AgentApiDependencies:
    get_session: Callable[..., Session]
    get_owned_research_session: Callable[[Session, str], Any]
    research_attachment_model: Any
    set_research_owner: Callable[..., None]


def _serialize_workflow(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "institution_id": row["institution_id"],
        "project_id": row.get("project_id"),
        "research_session_id": row.get("research_session_id"),
        "user_request": row["user_request"],
        "requested_agents": list(row.get("requested_agents") or []),
        "plan": list(row.get("plan") or []),
        "status": row["status"],
        "final_content": row.get("final_content"),
        "model_alias": row.get("model_alias"),
        "usage": row.get("usage") or {},
        "external_transfer_acknowledged": bool(row.get("external_transfer_acknowledged")),
        "attempt_no": int(row.get("attempt_no") or 0),
        "max_attempts": int(row.get("max_attempts") or 0),
        "heartbeat_at": row["heartbeat_at"].isoformat() if row.get("heartbeat_at") else None,
        "lease_expires_at": row["lease_expires_at"].isoformat() if row.get("lease_expires_at") else None,
        "cancel_requested_at": row["cancel_requested_at"].isoformat() if row.get("cancel_requested_at") else None,
        "deadline_at": row["deadline_at"].isoformat() if row.get("deadline_at") else None,
        "error_code": row.get("error_code"),
        "error_detail": row.get("error_detail"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "started_at": row["started_at"].isoformat() if row.get("started_at") else None,
        "completed_at": row["completed_at"].isoformat() if row.get("completed_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def _assert_institution_active(session: Session, user: CurrentUser) -> None:
    institution = session.execute(text("""
        SELECT status, trial_expires_at, retention_until
        FROM institution WHERE id = :institution_id
    """), {"institution_id": user.institution_id}).mappings().first()
    if not institution:
        raise HTTPException(403, "当前账号所属机构尚未完成平台开通。")
    if institution["status"] not in {"active", "trial"}:
        raise HTTPException(423, "机构环境当前已冻结，请由机构管理员联系平台续期。")
    expires_at = institution.get("trial_expires_at")
    if expires_at and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(423, "机构试用期已到期，环境已进入冻结保留期。")


def _assert_project_access(session: Session, user: CurrentUser, project_id: str | None) -> None:
    if not project_id:
        return
    project = session.execute(text("""
        SELECT id FROM research_project
        WHERE id = :project_id AND institution_id = :institution_id AND status = 'active'
    """), {"project_id": project_id, "institution_id": user.institution_id}).first()
    if not project:
        raise HTTPException(404, "未找到当前机构内的有效课题。")
    session.info["project_id"] = project_id
    session.execute(
        text("SELECT set_config('app.project_id', :project_id, true)"),
        {"project_id": project_id},
    )
    if {"data_processor", "field_admin"}.intersection(user.roles):
        return
    membership = session.execute(text("""
        SELECT 1 FROM project_membership
        WHERE project_id = :project_id AND institution_id = :institution_id
          AND user_id = :user_id
    """), {
        "project_id": project_id,
        "institution_id": user.institution_id,
        "user_id": user.id,
    }).first()
    if not membership:
        raise HTTPException(403, "当前账号不是该课题成员，不能使用该课题数据。")


def _model_policy() -> dict[str, Any]:
    return get_model_data_policy().public_view()


def build_agent_router(dependencies: AgentApiDependencies) -> APIRouter:
    router = APIRouter()
    logger = logging.getLogger(__name__)

    def collect_evidence(
        session: Session,
        *,
        research_session_id: str | None,
        attachment_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not attachment_ids:
            return []
        if not research_session_id:
            raise HTTPException(422, "提交附件时必须同时指定所属智能体会话。")
        research_session = dependencies.get_owned_research_session(session, research_session_id)
        attachment_model = dependencies.research_attachment_model
        attachments = session.scalars(
            select(attachment_model).where(
                attachment_model.session_id == research_session_id,
                attachment_model.id.in_(attachment_ids),
            )
        ).all()
        if len(attachments) != len(set(attachment_ids)):
            raise HTTPException(422, "存在不属于当前账号或当前会话的附件。")
        evidence: list[dict[str, Any]] = []
        total_chars = 0
        max_evidence_chars = int(os.getenv("AGENT_MAX_EVIDENCE_CHARS", "120000"))
        for attachment in attachments:
            if attachment.parsing_status != "parsed" or not attachment.parsed_markdown:
                raise HTTPException(409, f"附件《{attachment.file_name}》尚未完成可用的本地解析。")
            total_chars += len(attachment.parsed_markdown)
            if total_chars > max_evidence_chars:
                raise HTTPException(
                    413,
                    f"本次附件解析文本超过 {max_evidence_chars} 字符。"
                    "请拆分任务或减少附件，平台不会静默截断科研材料。",
                )
            evidence.append({
                "evidence_id": f"attachment:{attachment.id}",
                "title": attachment.file_name,
                "source": "private_attachment",
                "data_classification": "institution_private",
                "content": attachment.parsed_markdown,
            })
        return evidence

    @router.get("/api/agents")
    def list_available_agents(
        user: CurrentUser = Depends(require_researcher),
    ) -> dict[str, Any]:
        return {
            "institution_id": user.institution_id,
            "orchestrator": {"code": "longyun_orchestrator", "version": "2.0.0"},
            "model_policy": _model_policy(),
            "agents": public_agent_catalog(),
        }

    @router.get("/api/agent-workflows")
    def get_workflows(
        limit: int = Query(default=50, ge=1, le=100),
        project_id: str = Query(..., min_length=36, max_length=36),
        user: CurrentUser = Depends(require_researcher),
        session: Session = Depends(dependencies.get_session),
    ) -> list[dict[str, Any]]:
        _assert_institution_active(session, user)
        _assert_project_access(session, user, project_id)
        return [
            _serialize_workflow(row)
            for row in list_workflow_runs(session, limit=limit, project_id=project_id)
        ]

    @router.post("/api/agent-workflows", status_code=202)
    def submit_workflow(
        payload: AgentWorkflowCreate,
        user: CurrentUser = Depends(require_researcher),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        _assert_institution_active(session, user)
        _assert_project_access(session, user, payload.project_id)
        if payload.research_session_id:
            research_session = dependencies.get_owned_research_session(
                session, payload.research_session_id
            )
            if research_session.project_id != payload.project_id:
                raise HTTPException(
                    409,
                    "智能体任务、会话和附件必须属于同一课题，不能跨课题拼接上下文。",
                )
        try:
            selected_agents = route_question(payload.content, payload.agent_codes)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        idempotency_key = (payload.idempotency_key or "").strip() or None
        if idempotency_key:
            existing = get_workflow_run_by_idempotency(
                session,
                owner_id=user.id,
                project_id=payload.project_id,
                idempotency_key=idempotency_key,
            )
            if existing:
                return _serialize_workflow(existing)

        if os.getenv("AGENT_QUEUE_LIMITS_ENABLED", "false").lower() == "true":
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": f"longyun:agent-submit:{user.institution_id}"},
            )
            user_active = session.execute(text("""
                SELECT count(*) FROM agent_workflow_run
                WHERE owner_id = :owner_id AND status IN ('queued', 'running')
            """), {"owner_id": user.id}).scalar_one()
            institution_active = session.execute(
                text("SELECT current_institution_active_agent_workflow_count()")
            ).scalar_one()
            if user_active >= int(os.getenv("AGENT_MAX_ACTIVE_PER_USER", "2")):
                raise HTTPException(429, "当前账号已有较多分析任务，请等待其中一个完成后再提交。")
            if institution_active >= int(os.getenv("AGENT_MAX_ACTIVE_PER_INSTITUTION", "16")):
                raise HTTPException(429, "当前机构分析队列已满，请稍后重试。")

        evidence = collect_evidence(
            session,
            research_session_id=payload.research_session_id,
            attachment_ids=payload.attachment_ids,
        )
        policy = _model_policy()
        if policy["external_data_acknowledgement_required"] and not payload.external_data_acknowledged:
            raise HTTPException(422, "当前任务将调用外部模型 API。请先确认问题正文仅包含公开或已脱敏信息。")
        if evidence and not policy["private_evidence_allowed"]:
            raise HTTPException(
                403,
                "当前使用外部模型 API，平台安全策略禁止发送私有附件。"
                "请移除附件，或在切换到院内本地模型后再分析。",
            )

        run = create_workflow_run(
            session,
            institution_id=user.institution_id,
            project_id=payload.project_id,
            owner_id=user.id,
            user_request=payload.content.strip(),
            requested_agents=selected_agents,
            evidence_context=evidence,
            research_session_id=payload.research_session_id,
            external_transfer_acknowledged=payload.external_data_acknowledged,
            idempotency_key=idempotency_key,
            max_attempts=int(os.getenv("AGENT_WORKFLOW_MAX_ATTEMPTS", "3")),
            deadline_at=datetime.now(timezone.utc) + timedelta(seconds=payload.deadline_seconds),
        )
        try:
            from .workflow_worker import execute_agent_workflow

            if run.get("was_created", True):
                binding = tenant_database_manager.resolve(user.institution_id)
                execute_agent_workflow.apply_async(
                    args=[run["id"], user.institution_id, user.id, payload.project_id],
                    queue=binding.workflow_queue,
                    headers={
                        "institution_id": user.institution_id,
                        "owner_user_id": user.id,
                        "project_id": payload.project_id,
                    },
                )
        except Exception as exc:
            logger.exception("Unable to enqueue agent workflow run_id=%s", run["id"])
            dependencies.set_research_owner(
                session,
                user.id,
                user.institution_id,
                institution_admin=bool({"data_processor", "field_admin"}.intersection(user.roles)),
            )
            fail_workflow_run(
                session,
                run["id"],
                error_code="workflow_queue_unavailable",
                error_detail="智能体任务队列暂不可用，请稍后重新提交。",
            )
            raise HTTPException(503, "智能体任务队列暂不可用，请稍后重试。") from exc
        return _serialize_workflow(run)

    @router.get("/api/agent-workflows/{workflow_run_id}")
    def get_workflow_detail(
        workflow_run_id: str,
        project_id: str = Query(..., min_length=36, max_length=36),
        user: CurrentUser = Depends(require_researcher),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        _assert_project_access(session, user, project_id)
        row = get_workflow_run(session, workflow_run_id, project_id)
        if not row:
            raise HTTPException(404, "未找到该智能体任务，或当前账号无权访问。")
        result = _serialize_workflow(row)
        result["artifacts"] = list_workflow_artifacts(session, workflow_run_id)
        result["steps"] = [{
            **step,
            "started_at": step["started_at"].isoformat() if step.get("started_at") else None,
            "completed_at": step["completed_at"].isoformat() if step.get("completed_at") else None,
            "updated_at": step["updated_at"].isoformat() if step.get("updated_at") else None,
        } for step in list_workflow_steps(session, workflow_run_id)]
        return result

    @router.post("/api/agent-workflows/{workflow_run_id}/cancel")
    def cancel_workflow(
        workflow_run_id: str,
        project_id: str = Query(..., min_length=36, max_length=36),
        user: CurrentUser = Depends(require_researcher),
        session: Session = Depends(dependencies.get_session),
    ) -> dict[str, Any]:
        _assert_project_access(session, user, project_id)
        if not get_workflow_run(session, workflow_run_id, project_id):
            raise HTTPException(404, "未找到该智能体任务，或当前账号无权访问。")
        row = request_workflow_cancellation(session, workflow_run_id)
        if not row:
            raise HTTPException(409, "任务已结束，不能再取消。")
        return _serialize_workflow(row)

    @router.get("/api/agent-workflows/{workflow_run_id}/events")
    def get_workflow_events(
        workflow_run_id: str,
        project_id: str = Query(..., min_length=36, max_length=36),
        user: CurrentUser = Depends(require_researcher),
        session: Session = Depends(dependencies.get_session),
    ) -> list[dict[str, Any]]:
        _assert_project_access(session, user, project_id)
        if not get_workflow_run(session, workflow_run_id, project_id):
            raise HTTPException(404, "未找到该智能体任务，或当前账号无权访问。")
        return [{
            **event,
            "created_at": event["created_at"].isoformat() if event.get("created_at") else None,
        } for event in list_workflow_events(session, workflow_run_id)]

    return router
