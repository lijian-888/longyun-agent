"""ACPs AIP Group-mode runtime for Longyun.

The ACPs SDK owns RabbitMQ transport and wire models.  This module owns the
Longyun-specific lifecycle around those primitives: invitation policy,
per-user Leader sessions, Partner task execution, stable-state waiting and
cleanup.  It intentionally does not import ``main.py`` so the protocol layer
remains testable without the research application's database bootstrap.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from acps_sdk.acs import AgentCapabilitySpec
from acps_sdk.adp import DiscoveryRequest, DiscoveryResponse
from acps_sdk.aip import (
    ACSObject,
    FileDataItem,
    GroupLeader,
    GroupPartnerMqClient,
    Product,
    StructuredDataItem,
    TaskCommand,
    TaskCommandType,
    TaskResult,
    TaskState,
    TextDataItem,
)
from acps_sdk.aip.aip_group_model import (
    InboxGroupInvitation,
)


logger = logging.getLogger("uvicorn.error")
GroupCommand = Literal["continue", "complete", "cancel"]


class GroupSettings(Protocol):
    enabled: bool
    role: str
    leader_aic: str
    partner_aic: str
    discovery_base_url: str | None
    mtls_enabled: bool
    rabbitmq_host: str | None
    rabbitmq_port: int
    rabbitmq_vhost: str
    rabbitmq_user: str | None
    rabbitmq_password: str | None
    allow_plain_rabbitmq: bool
    rabbitmq_auth_service_url: str | None
    group_invitation_timeout_seconds: int
    group_max_partner_groups: int
    group_max_partners: int
    task_timeout_seconds: float

    @property
    def supports_group(self) -> bool: ...

    @property
    def supports_leader(self) -> bool: ...

    @property
    def supports_partner(self) -> bool: ...

    def amqp_url_for(self, role: Literal["leader", "partner"]) -> str | None: ...

    def outbound_ssl_context(self, role: Literal["leader", "partner"]) -> ssl.SSLContext | None: ...


class ExecutionResult(Protocol):
    text: str
    structured_data: dict[str, Any]
    product_name: str
    product_description: str
    files: list[Any]


PartnerExecutor = Callable[[str, str, dict[str, str]], Awaitable[ExecutionResult]]


class AcpsGroupPartnerTarget(BaseModel):
    """An explicit Group Partner, or an AIC that Discovery must resolve."""

    aic: str = Field(min_length=1, max_length=256)
    acs: dict[str, Any] | None = None


class AcpsGroupDispatchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=12_000)
    partners: list[AcpsGroupPartnerTarget] = Field(default_factory=list, max_length=16)
    target_partner_aics: list[str] | None = Field(default=None, max_length=16)
    max_partners: int = Field(default=3, ge=1, le=16)
    task_id: str | None = None
    session_id: str | None = None
    auto_complete: bool = True
    auto_dissolve: bool = True
    timeout_seconds: float | None = Field(default=None, ge=1, le=3600)


class AcpsGroupCommandRequest(BaseModel):
    command: GroupCommand
    content: str | None = Field(default=None, max_length=12_000)
    partner_aic: str | None = None
    auto_dissolve: bool = True
    timeout_seconds: float | None = Field(default=None, ge=1, le=3600)


@dataclass
class PartnerTaskRecord:
    leader_aic: str
    session_id: str
    state: TaskState
    products: list[Product] = field(default_factory=list)
    status_data_items: list[Any] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _endpoint(acs: dict[str, Any], transport: str) -> str | None:
    target = transport.upper()
    for endpoint in acs.get("endPoints") or []:
        if str(endpoint.get("transport", "")).upper() == target and endpoint.get("url"):
            return str(endpoint["url"])
    return None


def _supports_rabbitmq(acs: dict[str, Any]) -> bool:
    queues = (acs.get("capabilities") or {}).get("messageQueue") or []
    return bool(_endpoint(acs, "AMQP")) or any(
        str(item).lower().startswith("rabbitmq:") for item in queues
    )


def _validate_discovered_acs(acs: dict[str, Any]) -> None:
    try:
        AgentCapabilitySpec.from_dict(acs)
        return
    except ValidationError as exc:
        errors = exc.errors()
        if not errors or any(tuple(error.get("loc", ()))[:2] != ("capabilities", "messageQueue") for error in errors):
            raise
    compatibility = copy.deepcopy(acs)
    compatibility["capabilities"]["messageQueue"] = ["rabbitmq:3.11"]
    AgentCapabilitySpec.from_dict(compatibility)


async def discover_group_partners(
    query: str,
    settings: GroupSettings,
    requested_aics: set[str] | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Discover unique RabbitMQ-capable Partners for a Group session."""
    if not settings.discovery_base_url:
        raise RuntimeError("尚未配置 ACPS_DISCOVERY_BASE_URL，无法发现 Group Partner。")
    discovery_url = _base_url(settings.discovery_base_url)
    if not discovery_url.endswith("/discover"):
        discovery_url = f"{discovery_url}/discover"
    request = DiscoveryRequest(type="explicit", query=query, limit=max(limit * 4, 10))
    verify: bool | ssl.SSLContext = settings.outbound_ssl_context("leader") or True
    async with httpx.AsyncClient(verify=verify, timeout=30.0) as client:
        response = await client.post(
            discovery_url,
            json=request.model_dump(by_alias=True, exclude_none=True),
        )
        response.raise_for_status()
    discovery = DiscoveryResponse.from_dict(response.json())
    if discovery.error:
        raise RuntimeError(
            f"ACPs Discovery 返回错误 {discovery.error.code}: {discovery.error.message}"
        )
    if not discovery.result:
        raise RuntimeError("ACPs Discovery 未返回结果。")

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    candidates = sorted(discovery.result.iter_agent_skills(), key=lambda item: item[2].ranking)
    for aic, acs, skill, group in candidates:
        if aic in seen or (requested_aics and aic not in requested_aics):
            continue
        seen.add(aic)
        if not _supports_rabbitmq(acs):
            continue
        amqp_url = _endpoint(acs, "AMQP")
        if not amqp_url:
            continue
        selected.append({
            "aic": aic,
            "name": acs.get("name") or aic,
            "amqpUrl": amqp_url,
            "skillId": skill.skill_id,
            "ranking": skill.ranking,
            "group": group,
            "acs": acs,
        })
        if len(selected) >= limit:
            break
    if requested_aics:
        missing = requested_aics - {item["aic"] for item in selected}
        if missing:
            raise RuntimeError(f"Discovery 未找到可用的 Group Partner：{', '.join(sorted(missing))}")
    if not selected:
        raise RuntimeError("ACPs Discovery 没有找到声明 RabbitMQ Group 能力的 Partner。")
    return selected


class AcpsGroupRuntime:
    """Long-lived Group Leader/Partner runtime shared by the FastAPI app."""

    stable_states = {
        TaskState.AwaitingInput,
        TaskState.AwaitingCompletion,
        TaskState.Completed,
        TaskState.Failed,
        TaskState.Rejected,
        TaskState.Canceled,
    }
    terminal_states = {
        TaskState.Completed,
        TaskState.Failed,
        TaskState.Rejected,
        TaskState.Canceled,
    }

    def __init__(self, settings: GroupSettings, executor: PartnerExecutor):
        self.settings = settings
        self.executor = executor
        self._partner_clients: dict[str, GroupPartnerMqClient] = {}
        self._partner_tasks: dict[tuple[str, str], PartnerTaskRecord] = {}
        self._partner_jobs: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._partner_lock = asyncio.Lock()
        self._inbox_client: GroupPartnerMqClient | None = None
        self._leader: GroupLeader | None = None
        self._leader_session_owners: dict[str, str] = {}
        self._startup_error: str | None = None

    def configuration_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.settings.supports_group:
            return ["ACPS_TRANSPORT 未启用 group"]
        if self.settings.supports_leader and not self.settings.leader_aic:
            errors.append("ACPS_LEADER_AIC")
        if self.settings.supports_partner and not self.settings.partner_aic:
            errors.append("ACPS_PARTNER_AIC")
        if (
            self.settings.supports_leader
            and self.settings.supports_partner
            and self.settings.leader_aic
            and self.settings.leader_aic == self.settings.partner_aic
        ):
            errors.append("Leader AIC 与 Partner AIC 必须不同")
        if not self.settings.rabbitmq_host:
            errors.append("ACPS_RABBITMQ_HOST")
        if not self.settings.rabbitmq_vhost:
            errors.append("ACPS_RABBITMQ_VHOST")
        has_user = bool(self.settings.rabbitmq_user)
        has_password = bool(self.settings.rabbitmq_password)
        if has_user != has_password:
            errors.append("ACPS_RABBITMQ_USER/ACPS_RABBITMQ_PASSWORD 必须同时配置")
        if has_user and not self.settings.allow_plain_rabbitmq:
            errors.append("生产 Inbox/Group 禁止 RabbitMQ PLAIN 认证")
        if not has_user and not self.settings.mtls_enabled:
            errors.append("RabbitMQ EXTERNAL 认证需要 ACPS_MTLS_ENABLED=true")
        if not has_user and self.settings.mtls_enabled:
            for role in ("leader", "partner"):
                if role not in self.settings.role and self.settings.role != "hybrid":
                    continue
                try:
                    self.settings.outbound_ssl_context(role)
                except RuntimeError as exc:
                    errors.append(str(exc))
        if self.settings.supports_leader and not self.settings.rabbitmq_auth_service_url:
            errors.append("ACPS_RABBITMQ_AUTH_SERVICE_URL")
        return errors

    @property
    def configured(self) -> bool:
        return not self.configuration_errors()

    def status(self) -> dict[str, Any]:
        inbox_connected = bool(
            self._inbox_client
            and self._inbox_client.connection
            and not self._inbox_client.connection.is_closed
        )
        return {
            "configured": self.configured,
            "configurationErrors": self.configuration_errors(),
            "rabbitmq": {
                "host": self.settings.rabbitmq_host,
                "port": self.settings.rabbitmq_port,
                "vhost": self.settings.rabbitmq_vhost,
                "authentication": "plain" if self.settings.rabbitmq_user else "mtls-external",
                "leaderAmqpEndpointAdvertised": bool(self.settings.amqp_url_for("leader")),
                "partnerAmqpEndpointAdvertised": bool(self.settings.amqp_url_for("partner")),
            },
            "identities": {
                "leader": self.settings.leader_aic or None,
                "partner": self.settings.partner_aic or None,
            },
            "partnerInboxConnected": inbox_connected,
            "activePartnerGroups": len(self._partner_clients),
            "activeLeaderGroups": len(self._leader.group_sessions) if self._leader else 0,
            "startupError": self._startup_error,
        }

    def _ssl_context(self, role: Literal["leader", "partner"]) -> ssl.SSLContext | None:
        return self.settings.outbound_ssl_context(role) if self.settings.mtls_enabled else None

    def _rabbitmq_config(self) -> dict[str, Any]:
        return {
            "host": self.settings.rabbitmq_host,
            "port": self.settings.rabbitmq_port,
            "vhost": self.settings.rabbitmq_vhost,
            "user": self.settings.rabbitmq_user,
            "password": self.settings.rabbitmq_password,
            "auth_service_url": self.settings.rabbitmq_auth_service_url,
        }

    def _new_partner_client(self) -> GroupPartnerMqClient:
        return GroupPartnerMqClient(
            partner_aic=self.settings.partner_aic,
            rabbitmq_host=self.settings.rabbitmq_host or "localhost",
            rabbitmq_port=self.settings.rabbitmq_port,
            rabbitmq_vhost=self.settings.rabbitmq_vhost,
            rabbitmq_user=self.settings.rabbitmq_user,
            rabbitmq_password=self.settings.rabbitmq_password,
            ssl_context=self._ssl_context("partner"),
        )

    async def startup(self) -> None:
        """Start the Partner inbox consumer when Group is fully configured."""
        if not (self.settings.supports_group and self.settings.supports_partner):
            return
        if not self.configured:
            self._startup_error = "；".join(self.configuration_errors())
            logger.warning("ACPs Group inbox 未启动：%s", self._startup_error)
            return
        try:
            client = self._new_partner_client()
            await client.start_inbox_consuming(self._handle_inbox_invitation)
            self._inbox_client = client
            self._startup_error = None
        except Exception as exc:
            self._startup_error = str(exc)
            logger.exception("ACPs Group Partner inbox 启动失败")

    async def close(self) -> None:
        for job in list(self._partner_jobs.values()):
            job.cancel()
        if self._partner_jobs:
            await asyncio.gather(*self._partner_jobs.values(), return_exceptions=True)
        self._partner_jobs.clear()
        for client in list(self._partner_clients.values()):
            await client.close()
        self._partner_clients.clear()
        if self._inbox_client:
            await self._inbox_client.close()
            self._inbox_client = None
        if self._leader:
            await self._leader.close()
            self._leader = None
        self._leader_session_owners.clear()

    def _validate_group_membership(self, group: Any) -> None:
        partner_aics = {partner.aic for partner in group.partners}
        if self.settings.partner_aic not in partner_aics:
            raise ValueError("群组邀请未把当前 Partner AIC 列为成员。")
        if not group.leader or not group.leader.aic:
            raise ValueError("群组邀请缺少 Leader AIC。")

    def _bind_partner_client(
        self,
        client: GroupPartnerMqClient,
        group_id: str,
        leader_aic: str,
    ) -> None:
        async def on_command(command: TaskCommand, is_mentioned: bool) -> None:
            await self._handle_partner_command(
                client,
                group_id,
                leader_aic,
                command,
                is_mentioned,
            )

        async def on_disconnect(disconnected: GroupPartnerMqClient, left_group_id: str | None) -> None:
            resolved_group = left_group_id or group_id
            if self._partner_clients.get(resolved_group) is disconnected:
                self._partner_clients.pop(resolved_group, None)
            for key, job in list(self._partner_jobs.items()):
                if key[0] == resolved_group:
                    job.cancel()

        client.set_command_handler(on_command)
        client.set_disconnect_handler(on_disconnect)

    async def _handle_inbox_invitation(self, invitation: InboxGroupInvitation) -> None:
        try:
            if not self.configured:
                raise RuntimeError("ACPs Group RabbitMQ 尚未完成配置。")
            self._validate_group_membership(invitation.group)
            group_id = invitation.group.groupId
            async with self._partner_lock:
                existing = self._partner_clients.get(group_id)
                if existing and existing.is_joined:
                    return
                if len(self._partner_clients) >= self.settings.group_max_partner_groups:
                    raise RuntimeError("当前 Partner 已达到最大并发群组数。")
                client = self._new_partner_client()
                self._bind_partner_client(client, group_id, invitation.group.leader.aic)
                joined = await client.join_group_from_invitation(invitation)
                if joined:
                    self._partner_clients[group_id] = client
                else:
                    await client.close()
        except Exception:
            logger.exception("处理 ACPs Group Inbox 邀请失败")

    async def _send_record(
        self,
        client: GroupPartnerMqClient,
        task_id: str,
        record: PartnerTaskRecord,
    ) -> None:
        await client.send_task_result(
            task_id,
            record.session_id,
            record.state,
            products=record.products or None,
            status_data_items=record.status_data_items or None,
        )

    async def _set_partner_state(
        self,
        client: GroupPartnerMqClient,
        key: tuple[str, str],
        record: PartnerTaskRecord,
        state: TaskState,
        *,
        products: list[Product] | None = None,
        status_data_items: list[Any] | None = None,
    ) -> None:
        record.state = state
        record.products = products or []
        record.status_data_items = status_data_items or []
        record.updated_at = datetime.now(timezone.utc).isoformat()
        self._partner_tasks[key] = record
        await self._send_record(client, key[1], record)

    async def _execute_partner_task(
        self,
        client: GroupPartnerMqClient,
        key: tuple[str, str],
        prompt: str,
    ) -> None:
        record = self._partner_tasks[key]
        try:
            await self._set_partner_state(client, key, record, TaskState.Working)
            result = await self.executor(
                prompt,
                record.leader_aic,
                {
                    "groupId": key[0],
                    "taskId": key[1],
                    "sessionId": record.session_id,
                },
            )
            if record.state == TaskState.Canceled:
                return
            data_items: list[Any] = [
                TextDataItem(text=result.text, metadata={"mimeType": "text/plain; charset=utf-8"}),
                StructuredDataItem(
                    data=result.structured_data,
                    metadata={
                        "mimeType": "application/json",
                        "dataBoundary": "published-standard-data-only",
                    },
                ),
            ]
            data_items.extend(
                FileDataItem(
                    name=artifact.name,
                    mimeType=artifact.mime_type,
                    uri=artifact.uri,
                    metadata={
                        "sizeBytes": artifact.size_bytes,
                        "sha256": artifact.sha256,
                        "delivery": "time-limited-authorized-url",
                    },
                )
                for artifact in result.files
            )
            product = Product(
                id=f"product-{uuid.uuid4()}",
                name=result.product_name,
                description=result.product_description,
                dataItems=data_items,
            )
            await self._set_partner_state(
                client,
                key,
                record,
                TaskState.AwaitingCompletion,
                products=[product],
                status_data_items=[TextDataItem(text="隆耘分析已完成，请由 Leader 确认接收结果。")],
            )
        except asyncio.CancelledError:
            if record.state != TaskState.Canceled:
                await self._set_partner_state(client, key, record, TaskState.Canceled)
            raise
        except Exception:
            logger.exception("Longyun ACPs Group Partner task %s failed", key[1])
            await self._set_partner_state(
                client,
                key,
                record,
                TaskState.Failed,
                status_data_items=[TextDataItem(text="隆耘任务执行失败，请稍后重试或联系服务管理员。")],
            )
        finally:
            self._partner_jobs.pop(key, None)

    def _schedule_partner_task(
        self,
        client: GroupPartnerMqClient,
        key: tuple[str, str],
        prompt: str,
    ) -> None:
        previous = self._partner_jobs.get(key)
        if previous and not previous.done():
            previous.cancel()
        self._partner_jobs[key] = asyncio.create_task(
            self._execute_partner_task(client, key, prompt)
        )

    async def _handle_partner_command(
        self,
        client: GroupPartnerMqClient,
        group_id: str,
        leader_aic: str,
        command: TaskCommand,
        is_mentioned: bool,
    ) -> None:
        if not is_mentioned:
            return
        if command.groupId != group_id or command.senderId != leader_aic:
            logger.warning("忽略 Group 身份或 groupId 不匹配的 TaskCommand")
            return
        if not command.taskId or not command.sessionId:
            logger.warning("忽略缺少 taskId/sessionId 的 Group TaskCommand")
            return
        key = (group_id, command.taskId)
        record = self._partner_tasks.get(key)
        prompt = "\n".join(
            item.text.strip()
            for item in command.dataItems or []
            if isinstance(item, TextDataItem) and item.text.strip()
        )

        if command.command == TaskCommandType.Start:
            if record:
                await self._send_record(client, command.taskId, record)
                return
            record = PartnerTaskRecord(
                leader_aic=leader_aic,
                session_id=command.sessionId,
                state=TaskState.Accepted,
            )
            await self._set_partner_state(client, key, record, TaskState.Accepted)
            if not prompt:
                await self._set_partner_state(
                    client,
                    key,
                    record,
                    TaskState.AwaitingInput,
                    status_data_items=[TextDataItem(text="请提供需要隆耘分析的水稻育种或已发布标准数据问题。")],
                )
            else:
                self._schedule_partner_task(client, key, prompt)
            return

        if not record:
            await client.reject_task(command.taskId, command.sessionId, "隆耘未找到该群组任务。")
            return
        if record.leader_aic != leader_aic or record.session_id != command.sessionId:
            logger.warning("忽略不属于原始 Leader/session 的 Group TaskCommand")
            return

        if command.command == TaskCommandType.Get:
            await self._send_record(client, command.taskId, record)
        elif command.command == TaskCommandType.Continue:
            if record.state not in {TaskState.AwaitingInput, TaskState.AwaitingCompletion} or not prompt:
                await self._send_record(client, command.taskId, record)
                return
            await self._set_partner_state(client, key, record, TaskState.Accepted)
            self._schedule_partner_task(client, key, prompt)
        elif command.command == TaskCommandType.Complete:
            if record.state == TaskState.AwaitingCompletion:
                await self._set_partner_state(
                    client,
                    key,
                    record,
                    TaskState.Completed,
                    products=record.products,
                )
            else:
                await self._send_record(client, command.taskId, record)
        elif command.command == TaskCommandType.Cancel:
            if record.state in self.terminal_states:
                await self._send_record(client, command.taskId, record)
                return
            job = self._partner_jobs.get(key)
            record.state = TaskState.Canceled
            if job and not job.done():
                job.cancel()
            await self._set_partner_state(client, key, record, TaskState.Canceled)

    async def _ensure_leader(self) -> GroupLeader:
        if not self.settings.supports_leader:
            raise RuntimeError("当前 ACPS_ROLE 未启用 Leader 能力。")
        errors = self.configuration_errors()
        if errors:
            raise RuntimeError(f"ACPs Group 配置不完整：{'；'.join(errors)}")
        if self._leader is None:
            self._leader = GroupLeader(
                leader_aic=self.settings.leader_aic,
                rabbitmq_config=self._rabbitmq_config(),
                ssl_context=self._ssl_context("leader"),
                invitation_timeout_seconds=self.settings.group_invitation_timeout_seconds,
            )
        return self._leader

    async def _resolve_targets(self, request: AcpsGroupDispatchRequest) -> list[dict[str, Any]]:
        explicit: list[dict[str, Any]] = []
        unresolved: set[str] = set()
        for target in request.partners:
            acs = target.acs
            if acs:
                _validate_discovered_acs(acs)
                if str(acs.get("aic") or "") != target.aic:
                    raise ValueError(
                        f"Partner 目标 AIC {target.aic} 与 ACS 中的 aic 不一致。"
                    )
            amqp_url = _endpoint(acs, "AMQP") if acs else None
            if amqp_url:
                explicit.append({
                    "aic": target.aic,
                    "name": (acs or {}).get("name") or target.aic,
                    "amqpUrl": amqp_url,
                    "skillId": None,
                    "ranking": None,
                    "group": None,
                    "acs": acs,
                })
            else:
                unresolved.add(target.aic)
        if unresolved:
            explicit.extend(await discover_group_partners(
                request.query,
                self.settings,
                unresolved,
                min(len(unresolved), self.settings.group_max_partners),
            ))
        if not request.partners:
            explicit = await discover_group_partners(
                request.query,
                self.settings,
                None,
                min(request.max_partners, self.settings.group_max_partners),
            )
        deduped: dict[str, dict[str, Any]] = {}
        for item in explicit:
            deduped.setdefault(str(item["aic"]), item)
        selected = list(deduped.values())
        if len(selected) > self.settings.group_max_partners:
            raise ValueError(f"单个群组最多允许 {self.settings.group_max_partners} 个 Partner。")
        if not selected:
            raise ValueError("Group 调度至少需要一个 Partner。")
        return selected

    def _assert_owner(self, session_id: str, owner_id: str) -> None:
        actual = self._leader_session_owners.get(session_id)
        if not actual:
            raise ValueError(f"Group session not found: {session_id}")
        if actual != owner_id:
            raise PermissionError("无权访问其他账号创建的 ACPs Group 会话。")

    async def _wait_for_states(
        self,
        session: Any,
        task_id: str,
        partner_aics: list[str],
        timeout_seconds: float,
        *,
        terminal_only: bool = False,
    ) -> dict[str, TaskState]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        accepted = self.terminal_states if terminal_only else self.stable_states
        while True:
            session.state_update_event.clear()
            states = session.task_states.get(task_id, {})
            if all(states.get(aic) in accepted for aic in partner_aics):
                return {aic: states[aic] for aic in partner_aics}
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"Group 任务超过 {timeout_seconds:g} 秒。")
            await asyncio.wait_for(session.state_update_event.wait(), timeout=remaining)

    def _task_snapshot(self, session_id: str, task_id: str) -> dict[str, Any]:
        if not self._leader or session_id not in self._leader.group_sessions:
            raise ValueError(f"Group session not found: {session_id}")
        session = self._leader.group_sessions[session_id]
        latest_results: dict[str, dict[str, Any]] = {}
        for message in reversed(session.message_history):
            if (
                isinstance(message, TaskResult)
                and message.taskId == task_id
                and message.senderId not in latest_results
            ):
                latest_results[message.senderId] = message.model_dump(
                    by_alias=True, exclude_none=True, mode="json"
                )
        return {
            "sessionId": session_id,
            "groupId": session.group_id,
            "taskId": task_id,
            "states": {
                aic: state.value
                for aic, state in session.task_states.get(task_id, {}).items()
            },
            "products": session.task_products.get(task_id, {}),
            "awaitingInput": session.task_prompts.get(task_id, {}),
            "results": latest_results,
            "runtime": self._leader.get_group_runtime(session_id),
        }

    async def dispatch(
        self,
        request: AcpsGroupDispatchRequest,
        owner_id: str,
    ) -> dict[str, Any]:
        leader = await self._ensure_leader()
        selected = await self._resolve_targets(request)
        session_id = request.session_id or f"session-{uuid.uuid4()}"
        if session_id in self._leader_session_owners:
            self._assert_owner(session_id, owner_id)
            raise ValueError("该 Group session_id 已存在，请使用 command 接口继续任务。")
        partner_aics = [str(item["aic"]) for item in selected]
        targets = request.target_partner_aics or partner_aics
        unknown_targets = set(targets) - set(partner_aics)
        if unknown_targets:
            raise ValueError(f"目标 Partner 不在群组成员中：{', '.join(sorted(unknown_targets))}")
        session = await leader.create_group_session(session_id, [])
        self._leader_session_owners[session_id] = owner_id
        timeout_seconds = request.timeout_seconds or self.settings.task_timeout_seconds
        task_id = request.task_id or f"task-{uuid.uuid4()}"
        try:
            for partner in selected:
                acs_data = partner.get("acs")
                if not acs_data or not partner.get("amqpUrl"):
                    raise ValueError(
                        f"Partner {partner['aic']} 没有可用于 Inbox 邀请的 AMQP ACS。"
                    )
                invited = await leader.invite_partner(
                    session_id,
                    ACSObject(aic=str(partner["aic"])),
                    partner_rpc_url=None,
                    partner_acs_data=acs_data,
                )
                if not invited:
                    raise RuntimeError(f"Partner {partner['aic']} 未接受 Group Inbox 邀请。")
            task_id = await leader.start_task(
                session_id,
                task_content=request.query,
                task_id=task_id,
                target_partners=targets,
            )
            states = await self._wait_for_states(session, task_id, targets, timeout_seconds)
            if request.auto_complete:
                awaiting = [aic for aic, state in states.items() if state == TaskState.AwaitingCompletion]
                for aic in awaiting:
                    await leader.complete_task(session_id, task_id, target_partner=aic)
                if awaiting:
                    await self._wait_for_states(
                        session,
                        task_id,
                        awaiting,
                        timeout_seconds,
                        terminal_only=True,
                    )
            snapshot = self._task_snapshot(session_id, task_id)
            snapshot["selectedPartners"] = [
                {key: value for key, value in item.items() if key != "acs"}
                for item in selected
            ]
            final_states = {
                TaskState(value) for value in snapshot["states"].values()
            }
            if request.auto_dissolve and final_states and final_states <= self.terminal_states:
                await self.dissolve(session_id, owner_id)
                snapshot["dissolved"] = True
            else:
                snapshot["dissolved"] = False
            return snapshot
        except Exception:
            if self._leader and session_id in self._leader.group_sessions:
                try:
                    await leader.cancel_task(session_id, task_id, reason="Leader 调度失败或超时")
                except Exception:
                    logger.warning("Group 调度失败后取消任务未成功", exc_info=True)
                await self.dissolve(session_id, owner_id)
            raise

    def status_for_owner(self, session_id: str, task_id: str, owner_id: str) -> dict[str, Any]:
        self._assert_owner(session_id, owner_id)
        return self._task_snapshot(session_id, task_id)

    async def command(
        self,
        session_id: str,
        task_id: str,
        request: AcpsGroupCommandRequest,
        owner_id: str,
    ) -> dict[str, Any]:
        self._assert_owner(session_id, owner_id)
        leader = await self._ensure_leader()
        if not self._leader or session_id not in self._leader.group_sessions:
            raise ValueError(f"Group session not found: {session_id}")
        session = self._leader.group_sessions[session_id]
        current_states = session.task_states.get(task_id, {})
        if not current_states:
            raise ValueError("该任务尚无可操作的 Partner 状态。")
        if request.partner_aic and request.partner_aic not in current_states:
            raise ValueError("指定的 Partner 不属于该 Group 任务。")
        candidates = [request.partner_aic] if request.partner_aic else list(current_states)
        if request.command == "continue":
            target_aics = [
                aic
                for aic in candidates
                if current_states[aic] in {TaskState.AwaitingInput, TaskState.AwaitingCompletion}
            ]
        elif request.command == "complete":
            target_aics = [
                aic for aic in candidates if current_states[aic] == TaskState.AwaitingCompletion
            ]
        else:
            target_aics = [
                aic for aic in candidates if current_states[aic] not in self.terminal_states
            ]
        if not target_aics:
            if request.partner_aic and current_states[request.partner_aic] not in self.terminal_states:
                raise ValueError(f"当前状态不允许执行 {request.command} 命令。")
            snapshot = self._task_snapshot(session_id, task_id)
            snapshot["dissolved"] = False
            return snapshot
        timeout_seconds = request.timeout_seconds or self.settings.task_timeout_seconds
        if request.command == "continue":
            if not request.content or not request.content.strip():
                raise ValueError("continue 命令需要 content。")
            for aic in target_aics:
                await leader.continue_task(session_id, task_id, request.content.strip(), aic)
            await self._wait_for_states(session, task_id, target_aics, timeout_seconds)
        elif request.command == "complete":
            for aic in target_aics:
                await leader.complete_task(session_id, task_id, aic)
            await self._wait_for_states(
                session, task_id, target_aics, timeout_seconds, terminal_only=True
            )
        else:
            for aic in target_aics:
                await leader.cancel_task(session_id, task_id, request.content, aic)
            await self._wait_for_states(
                session, task_id, target_aics, timeout_seconds, terminal_only=True
            )
        snapshot = self._task_snapshot(session_id, task_id)
        final_states = {TaskState(value) for value in snapshot["states"].values()}
        if request.auto_dissolve and final_states and final_states <= self.terminal_states:
            await self.dissolve(session_id, owner_id)
            snapshot["dissolved"] = True
        else:
            snapshot["dissolved"] = False
        return snapshot

    async def dissolve(self, session_id: str, owner_id: str) -> None:
        self._assert_owner(session_id, owner_id)
        if self._leader:
            await self._leader.dissolve_group_session(session_id)
        self._leader_session_owners.pop(session_id, None)
