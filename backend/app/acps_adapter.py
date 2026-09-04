"""ACPs v2.1 adapter for running Longyun as a Leader, Partner, or both.

This module deliberately keeps ACPs transport/state management separate from
the research business logic in ``main.py``.  The Partner callback receives only
plain text and returns a text product plus non-sensitive structured metadata.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, cast
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from acps_sdk.acs import AgentCapabilitySpec
from acps_sdk.adp import DiscoveryRequest, DiscoveryResponse
from acps_sdk.aip import (
    AipRpcClient,
    Product,
    StructuredDataItem,
    TaskCommand,
    TaskResult,
    TaskState,
    TextDataItem,
)
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers,
    DefaultHandlers,
    TaskManager,
    handle_rpc_request,
)
from acps_sdk.aip.aip_group_leader import GroupLeader
from acps_sdk.aip.aip_group_model import ACSObject
from acps_sdk.aip.aip_group_runtime import validate_aic_format


AcpsRole = Literal["leader", "partner", "hybrid"]
AcpsDirectCommand = Literal["get", "continue", "complete", "cancel"]
AcpsGroupTaskCommand = Literal["continue", "complete", "cancel"]
AcpsGroupMemberCommand = Literal["status", "leave", "force-remove", "mute", "unmute"]
PartnerExecutor = Callable[[str, str], Awaitable["AcpsExecutionResult"]]
logger = logging.getLogger("uvicorn.error")

try:
    ACPS_SDK_VERSION = package_version("acps-sdk")
except PackageNotFoundError:  # pragma: no cover - dependency is mandatory in deployed images
    ACPS_SDK_VERSION = "unavailable"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, "").split(",") if value.strip())


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _base_url(value: str) -> str:
    return value.strip().rstrip("/")


@dataclass(frozen=True)
class AcpsSettings:
    """Environment-backed ACPs runtime settings."""

    enabled: bool
    role: AcpsRole
    aic: str
    name: str
    version: str
    public_base_url: str
    documentation_url: str
    rpc_url: str
    discovery_base_url: str | None
    provider_organization: str
    provider_department: str | None
    provider_url: str | None
    provider_license: str | None
    provider_name: str | None
    provider_email: str | None
    provider_country_code: str
    provider_domain: str | None
    provider_domain_registration_number: str | None
    provider_domain_registration_type: str | None
    mtls_enabled: bool
    require_verified_client: bool
    verified_client_header: str
    client_certificate_file: str | None
    client_private_key_file: str | None
    trust_bundle_file: str | None
    certificate_dns_names: tuple[str, ...]
    certificate_ip_addresses: tuple[str, ...]
    poll_interval_seconds: float
    task_timeout_seconds: float
    leader_task_retention_seconds: int
    require_registered_leader: bool
    allowed_partner_hosts: tuple[str, ...]
    group_enabled: bool
    rabbitmq_host: str | None
    rabbitmq_port: int
    rabbitmq_vhost: str
    rabbitmq_user: str | None
    rabbitmq_password: str | None
    group_auth_service_url: str | None
    group_invitation_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "AcpsSettings":
        role = os.getenv("ACPS_ROLE", "hybrid").strip().lower()
        if role not in {"leader", "partner", "hybrid"}:
            raise ValueError("ACPS_ROLE 必须是 leader、partner 或 hybrid。")
        public_base_url = _base_url(os.getenv("ACPS_PUBLIC_BASE_URL", "http://localhost:5183"))
        return cls(
            enabled=_env_bool("ACPS_ENABLED", True),
            role=cast(AcpsRole, role),
            aic=os.getenv("ACPS_AIC", "").strip(),
            name=os.getenv("ACPS_AGENT_NAME", "隆耘 Agent 育种智能体").strip(),
            version=os.getenv("ACPS_AGENT_VERSION", "1.0.0").strip(),
            public_base_url=public_base_url,
            documentation_url=os.getenv(
                "ACPS_DOCUMENTATION_URL",
                "https://github.com/lijian-888/longyun-agent/blob/codex/main-rework/docs/ACPS-INTEGRATION.md",
            ).strip(),
            rpc_url=os.getenv("ACPS_RPC_URL", f"{public_base_url}/acps/rpc").strip(),
            discovery_base_url=_optional_env("ACPS_DISCOVERY_BASE_URL"),
            provider_organization=os.getenv("ACPS_PROVIDER_ORGANIZATION", "隆耘智能体项目").strip(),
            provider_department=_optional_env("ACPS_PROVIDER_DEPARTMENT"),
            provider_url=_optional_env("ACPS_PROVIDER_URL")
            or "https://github.com/lijian-888/longyun-agent",
            provider_license=_optional_env("ACPS_PROVIDER_LICENSE"),
            provider_name=_optional_env("ACPS_PROVIDER_NAME"),
            provider_email=_optional_env("ACPS_PROVIDER_EMAIL"),
            provider_country_code=os.getenv("ACPS_PROVIDER_COUNTRY_CODE", "CN").strip().upper(),
            provider_domain=_optional_env("ACPS_PROVIDER_DOMAIN"),
            provider_domain_registration_number=_optional_env("ACPS_PROVIDER_DOMAIN_REGISTRATION_NUMBER"),
            provider_domain_registration_type=_optional_env("ACPS_PROVIDER_DOMAIN_REGISTRATION_TYPE"),
            mtls_enabled=_env_bool("ACPS_MTLS_ENABLED", False),
            require_verified_client=_env_bool("ACPS_REQUIRE_VERIFIED_CLIENT", False),
            verified_client_header=os.getenv("ACPS_VERIFIED_CLIENT_HEADER", "X-ACPS-Client-AIC").strip(),
            client_certificate_file=_optional_env("ACPS_CLIENT_CERT_FILE"),
            client_private_key_file=_optional_env("ACPS_CLIENT_KEY_FILE"),
            trust_bundle_file=_optional_env("ACPS_TRUST_BUNDLE_FILE"),
            certificate_dns_names=_env_list("ACPS_CERTIFICATE_DNS_NAMES"),
            certificate_ip_addresses=_env_list("ACPS_CERTIFICATE_IP_ADDRESSES"),
            poll_interval_seconds=max(float(os.getenv("ACPS_POLL_INTERVAL_SECONDS", "0.5")), 0.05),
            task_timeout_seconds=max(float(os.getenv("ACPS_TASK_TIMEOUT_SECONDS", "180")), 1.0),
            leader_task_retention_seconds=max(
                int(os.getenv("ACPS_LEADER_TASK_RETENTION_SECONDS", "3600")), 60
            ),
            require_registered_leader=_env_bool("ACPS_REQUIRE_REGISTERED_LEADER", False),
            allowed_partner_hosts=tuple(
                item.lower() for item in _env_list("ACPS_ALLOWED_PARTNER_HOSTS")
            ),
            group_enabled=_env_bool("ACPS_GROUP_ENABLED", False),
            rabbitmq_host=_optional_env("ACPS_RABBITMQ_HOST"),
            rabbitmq_port=max(int(os.getenv("ACPS_RABBITMQ_PORT", "5671")), 1),
            rabbitmq_vhost=os.getenv("ACPS_RABBITMQ_VHOST", "acps").strip() or "acps",
            rabbitmq_user=_optional_env("ACPS_RABBITMQ_USER"),
            rabbitmq_password=_optional_env("ACPS_RABBITMQ_PASSWORD"),
            group_auth_service_url=_optional_env("ACPS_GROUP_AUTH_SERVICE_URL"),
            group_invitation_timeout_seconds=max(
                int(os.getenv("ACPS_GROUP_INVITATION_TIMEOUT_SECONDS", "300")), 1
            ),
        )

    @property
    def supports_leader(self) -> bool:
        return self.enabled and self.role in {"leader", "hybrid"}

    @property
    def supports_partner(self) -> bool:
        return self.enabled and self.role in {"partner", "hybrid"}

    @property
    def runtime_roles(self) -> list[str]:
        if self.role == "hybrid":
            return ["leader", "partner"]
        return [self.role]

    @property
    def registered(self) -> bool:
        return bool(self.aic)

    @property
    def group_blockers(self) -> list[str]:
        """Return non-secret reasons why Group Leader mode cannot be started."""
        blockers: list[str] = []
        if not self.group_enabled:
            blockers.append("ACPS_GROUP_ENABLED=false")
        if not self.supports_leader:
            blockers.append("当前角色未启用 Leader")
        if not self.aic:
            blockers.append("缺少 Registry 分配的 ACPS_AIC")
        elif not validate_aic_format(self.aic)[0]:
            blockers.append("ACPS_AIC 格式不符合 ACPs v2.1 规范")
        if self.require_registered_leader and not self.allowed_partner_hosts:
            blockers.append("缺少 ACPS_ALLOWED_PARTNER_HOSTS 出站白名单")
        if not self.rabbitmq_host:
            blockers.append("缺少 ACPS_RABBITMQ_HOST")
        if not self.group_auth_service_url:
            blockers.append("缺少 ACPS_GROUP_AUTH_SERVICE_URL")
        if self.mtls_enabled:
            for name, value in {
                "ACPS_CLIENT_CERT_FILE": self.client_certificate_file,
                "ACPS_CLIENT_KEY_FILE": self.client_private_key_file,
                "ACPS_TRUST_BUNDLE_FILE": self.trust_bundle_file,
            }.items():
                if not value:
                    blockers.append(f"缺少 {name}")
                elif not Path(value).is_file():
                    blockers.append(f"{name} 文件不存在")
        elif not (self.rabbitmq_user and self.rabbitmq_password):
            blockers.append("未启用 mTLS，且缺少 RabbitMQ 用户名或密码")
        return blockers

    @property
    def group_ready(self) -> bool:
        return not self.group_blockers

    def outbound_ssl_context(self) -> ssl.SSLContext | None:
        """Build the Leader's client-auth TLS context when mTLS is enabled."""
        if not self.mtls_enabled:
            return None
        required = {
            "ACPS_CLIENT_CERT_FILE": self.client_certificate_file,
            "ACPS_CLIENT_KEY_FILE": self.client_private_key_file,
            "ACPS_TRUST_BUNDLE_FILE": self.trust_bundle_file,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"ACPs mTLS 缺少配置：{', '.join(missing)}")
        for name, value in required.items():
            if not Path(str(value)).is_file():
                raise RuntimeError(f"{name} 指向的文件不存在：{value}")
        context = ssl.create_default_context(cafile=self.trust_bundle_file)
        context.load_cert_chain(
            certfile=str(self.client_certificate_file),
            keyfile=str(self.client_private_key_file),
        )
        return context


class AcpsExecutionResult(BaseModel):
    """Business result converted into an AIP Product by the Partner adapter."""

    text: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    product_name: str = "隆耘育种分析结果"
    product_description: str = "基于平台已发布标准数据生成的只读科研分析"


class AcpsLeaderDispatchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=12_000)
    partner_url: str | None = Field(default=None, max_length=2_048)
    partner_aic: str | None = Field(default=None, max_length=512)
    task_id: str | None = Field(default=None, min_length=1, max_length=256)
    session_id: str | None = Field(default=None, min_length=1, max_length=160)
    auto_complete: bool = True


class AcpsLeaderTaskCommandRequest(BaseModel):
    command: AcpsDirectCommand
    query: str | None = Field(default=None, min_length=1, max_length=12_000)
    wait_for_result: bool = True
    auto_complete: bool = False


class AcpsGroupCreateRequest(BaseModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=160)


class AcpsGroupPartnerRequest(BaseModel):
    partner_aic: str = Field(min_length=1, max_length=512)
    partner_url: str | None = Field(default=None, max_length=2_048)
    partner_acs: dict[str, Any] | None = None


class AcpsGroupTaskRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12_000)
    task_id: str | None = Field(default=None, min_length=1, max_length=256)
    target_partners: list[str] | None = Field(default=None, max_length=100)


class AcpsGroupTaskCommandRequest(BaseModel):
    command: AcpsGroupTaskCommand
    content: str | None = Field(default=None, min_length=1, max_length=12_000)
    reason: str | None = Field(default=None, max_length=2_000)
    target_partner: str | None = Field(default=None, max_length=512)


class AcpsGroupMemberCommandRequest(BaseModel):
    command: AcpsGroupMemberCommand


def _now_beijing() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _provider(settings: AcpsSettings) -> dict[str, Any]:
    provider: dict[str, Any] = {
        "countryCode": settings.provider_country_code,
        "organization": settings.provider_organization,
    }
    optional = {
        "department": settings.provider_department,
        "url": settings.provider_url,
        "license": settings.provider_license,
        "name": settings.provider_name,
        "email": settings.provider_email,
    }
    provider.update({key: value for key, value in optional.items() if value})
    registration = (
        settings.provider_domain,
        settings.provider_domain_registration_number,
        settings.provider_domain_registration_type,
    )
    if any(registration) and not all(registration):
        raise ValueError(
            "ACPs 域名备案配置必须同时提供 ACPS_PROVIDER_DOMAIN、"
            "ACPS_PROVIDER_DOMAIN_REGISTRATION_NUMBER 和 ACPS_PROVIDER_DOMAIN_REGISTRATION_TYPE。"
        )
    if all(registration):
        registration_type = str(settings.provider_domain_registration_type).upper()
        if registration_type not in {"ICP", "WHOIS"}:
            raise ValueError("ACPS_PROVIDER_DOMAIN_REGISTRATION_TYPE 必须是 ICP 或 WHOIS。")
        provider["domainRegistrations"] = [{
            "domain": settings.provider_domain,
            "registrationNumber": settings.provider_domain_registration_number,
            "registrationType": registration_type,
        }]
    return provider


def _partner_skills() -> list[dict[str, Any]]:
    return [
        {
            "id": "longyun.rice.published-data-analysis",
            "name": "水稻已发布标准数据分析",
            "description": (
                "查询并分析隆耘平台中已经发布的标准化水稻品种、性状和区域试验数据。"
                "只读访问，不处理未发布数据、私有知识库或会话附件。"
            ),
            "version": "1.0.0",
            "tags": ["水稻", "育种", "性状", "区域试验", "已发布数据", "只读"],
            "examples": [
                "比较候选材料 A 与对照品种三年区域试验的产量和稳定性。",
                "查询某品种的已发布株高、千粒重和生育期性状。",
            ],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain", "application/json"],
        },
        {
            "id": "longyun.rice.breeding-research",
            "name": "水稻育种科研辅助",
            "description": (
                "结合已发布标准数据给出育种材料比较、试验解释与基因型研究建议；"
                "结果用于科研辅助，不替代专家审查或田间验证。"
            ),
            "version": "1.0.0",
            "tags": ["水稻育种", "材料比较", "基因型", "科研分析", "决策辅助"],
            "examples": [
                "根据已发布试验数据分析候选材料的主要优势与风险。",
                "解释目标性状与候选基因研究时需要关注的验证步骤。",
            ],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain", "application/json"],
        },
    ]


def build_acs_document(settings: AcpsSettings, role: AcpsRole | None = None) -> dict[str, Any]:
    """Build and validate an ACS 02.01 document for the selected runtime role."""
    selected_role = role or settings.role
    if selected_role not in {"leader", "partner", "hybrid"}:
        raise ValueError("ACS role must be leader, partner, or hybrid")
    exposes_partner = selected_role in {"partner", "hybrid"}
    security_schemes: dict[str, Any] = {}
    endpoint_security: list[dict[str, list[str]]] | None = None
    if settings.mtls_enabled:
        security_schemes["mtls"] = {
            "type": "mutualTLS",
            "description": "使用 ACPs CA 签发的客户端和服务端身份证书进行双向 TLS 认证。",
        }
        endpoint_security = [{"mtls": []}]

    group_capable = selected_role in {"leader", "hybrid"} and settings.group_ready
    document: dict[str, Any] = {
        "aic": settings.aic,
        "active": settings.enabled,
        "lastModifiedTime": _now_beijing(),
        "protocolVersion": "02.01",
        "name": settings.name,
        "description": (
            "面向水稻育种科研与数据治理的隆耘智能体。可作为 ACPs Leader 调度协作智能体，"
            "也可作为 Partner 提供基于已发布标准数据的只读分析能力。"
        ),
        "version": settings.version,
        "documentationUrl": settings.documentation_url,
        "webAppUrl": settings.public_base_url,
        "provider": _provider(settings),
        "securitySchemes": security_schemes,
        "endPoints": ([{
            "url": settings.rpc_url,
            "transport": "JSONRPC",
            **({"security": endpoint_security} if endpoint_security else {}),
        }] if exposes_partner else []),
        "capabilities": {
            "streaming": False,
            "notification": False,
            "messageQueue": ["amqp:0.9.1"] if group_capable else [],
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": _partner_skills() if exposes_partner else [],
        "entityMeta": {
            "runtimeRoles": ["leader", "partner"] if selected_role == "hybrid" else [selected_role],
            "dataBoundary": "published-standard-data-only",
            "aipTransport": (
                "direct-jsonrpc+amqp-group" if group_capable else "direct-jsonrpc"
            ),
            "interactionModes": ["direct", "group"] if group_capable else ["direct"],
        },
    }
    if settings.certificate_dns_names or settings.certificate_ip_addresses:
        document["certificate"] = {
            "altNames": {
                "dns": list(settings.certificate_dns_names),
                "ip": list(settings.certificate_ip_addresses),
            }
        }

    # The SDK validates the normative ACS fields.  Registry-specific extension
    # fields such as certificate are retained in the original dictionary.
    AgentCapabilitySpec.from_dict(document)
    return document


def _command_text(command: TaskCommand) -> str:
    return "\n".join(
        item.text.strip()
        for item in command.dataItems or []
        if isinstance(item, TextDataItem) and item.text.strip()
    )


def _partner_sender_id(settings: AcpsSettings) -> str:
    return settings.aic or "longyun-unregistered-partner"


def build_partner_handlers(
    settings: AcpsSettings,
    executor: PartnerExecutor,
) -> CommandHandlers:
    """Create the Direct AIP Partner state machine and background job runner."""
    jobs: dict[str, asyncio.Task[None]] = {}

    def stamp_sender(task: TaskResult) -> TaskResult:
        task.senderId = _partner_sender_id(settings)
        task.sentAt = datetime.now(timezone.utc).isoformat()
        return task

    async def run_task(task_id: str, prompt: str, caller_aic: str) -> None:
        current = TaskManager.get_task(task_id)
        if not current or current.status.state == TaskState.Canceled:
            return
        TaskManager.update_task_status(task_id, TaskState.Working)
        try:
            result = await executor(prompt, caller_aic)
            current = TaskManager.get_task(task_id)
            if not current or current.status.state == TaskState.Canceled:
                return
            product = Product(
                id=f"product-{uuid.uuid4()}",
                name=result.product_name,
                description=result.product_description,
                dataItems=[
                    TextDataItem(text=result.text, metadata={"mimeType": "text/plain; charset=utf-8"}),
                    StructuredDataItem(
                        data=result.structured_data,
                        metadata={"mimeType": "application/json", "dataBoundary": "published-standard-data-only"},
                    ),
                ],
            )
            TaskManager.set_products(task_id, [product])
            TaskManager.update_task_status(
                task_id,
                TaskState.AwaitingCompletion,
                [TextDataItem(text="隆耘分析已完成，请由 Leader 确认接收结果。")],
            )
            updated = TaskManager.get_task(task_id)
            if updated:
                stamp_sender(updated)
        except asyncio.CancelledError:
            current = TaskManager.get_task(task_id)
            if current and current.status.state != TaskState.Canceled:
                TaskManager.update_task_status(task_id, TaskState.Canceled)
            raise
        except Exception:
            logger.exception("Longyun ACPs Partner task %s failed", task_id)
            current = TaskManager.get_task(task_id)
            if current and current.status.state != TaskState.Canceled:
                TaskManager.update_task_status(
                    task_id,
                    TaskState.Failed,
                    [TextDataItem(text="隆耘任务执行失败，请稍后重试或联系服务管理员。")],
                )
                stamp_sender(current)
        finally:
            jobs.pop(task_id, None)

    def schedule(task_id: str, prompt: str, caller_aic: str) -> None:
        previous = jobs.get(task_id)
        if previous and not previous.done():
            previous.cancel()
        jobs[task_id] = asyncio.create_task(run_task(task_id, prompt, caller_aic))

    async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
        if not command.taskId:
            command.taskId = f"task-{uuid.uuid4()}"
        if task:
            TaskManager.add_command_to_history(task.taskId, command)
            return stamp_sender(task)
        prompt = _command_text(command)
        task = TaskManager.create_task(command, TaskState.Accepted)
        stamp_sender(task)
        if not prompt:
            return stamp_sender(TaskManager.update_task_status(
                task.taskId,
                TaskState.AwaitingInput,
                [TextDataItem(text="请提供需要隆耘分析的水稻育种或已发布标准数据问题。")],
            ))
        schedule(task.taskId, prompt, command.senderId)
        return task

    async def on_get(command: TaskCommand, task: TaskResult) -> TaskResult:
        return stamp_sender(await DefaultHandlers.get(command, task))

    async def on_continue(command: TaskCommand, task: TaskResult) -> TaskResult:
        prompt = _command_text(command)
        if task.status.state not in {TaskState.AwaitingInput, TaskState.AwaitingCompletion} or not prompt:
            return stamp_sender(await DefaultHandlers.continue_(command, task))
        TaskManager.add_command_to_history(task.taskId, command)
        TaskManager.set_products(task.taskId, [])
        updated = TaskManager.update_task_status(task.taskId, TaskState.Accepted)
        schedule(task.taskId, prompt, command.senderId)
        return stamp_sender(updated)

    async def on_complete(command: TaskCommand, task: TaskResult) -> TaskResult:
        return stamp_sender(await DefaultHandlers.complete(command, task))

    async def on_cancel(command: TaskCommand, task: TaskResult) -> TaskResult:
        job = jobs.get(task.taskId)
        if job and not job.done():
            job.cancel()
        return stamp_sender(await DefaultHandlers.cancel(command, task))

    return CommandHandlers(
        on_start=on_start,
        on_get=on_get,
        on_continue=on_continue,
        on_complete=on_complete,
        on_cancel=on_cancel,
    )


def _rpc_endpoint(acs: dict[str, Any]) -> str | None:
    for endpoint in acs.get("endPoints") or []:
        if str(endpoint.get("transport", "")).upper() == "JSONRPC" and endpoint.get("url"):
            return str(endpoint["url"])
    return None


def _host_matches_allowlist(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    normalized = host.rstrip(".").lower()
    for pattern in allowed_hosts:
        candidate = pattern.rstrip(".").lower()
        if candidate.startswith("*."):
            suffix = candidate[1:]
            if normalized.endswith(suffix) and normalized != suffix[1:]:
                return True
        elif normalized == candidate:
            return True
    return False


def validate_partner_url(url: str, settings: AcpsSettings) -> str:
    """Validate an outbound ACPs endpoint before the API can make a request."""
    normalized = url.strip()
    parsed = urlsplit(normalized)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Partner URL 端口无效。") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Partner URL 必须是有效的 HTTP 或 HTTPS 地址。")
    if any(ord(char) < 32 for char in normalized) or "\\" in normalized:
        raise ValueError("Partner URL 包含不安全字符。")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Partner URL 不得包含用户凭据或片段。")
    if settings.mtls_enabled and parsed.scheme.lower() != "https":
        raise RuntimeError("启用 ACPs mTLS 时不允许调用明文 HTTP Partner。")
    if settings.allowed_partner_hosts and not _host_matches_allowlist(
        parsed.hostname, settings.allowed_partner_hosts
    ):
        raise ValueError(
            f"Partner 主机 {parsed.hostname} 不在 ACPS_ALLOWED_PARTNER_HOSTS 允许列表中。"
        )
    return normalized


async def discover_partner(
    query: str,
    settings: AcpsSettings,
    requested_aic: str | None = None,
) -> dict[str, Any]:
    """Use ADP explicit discovery and return the best callable JSON-RPC Partner."""
    if not settings.discovery_base_url:
        raise RuntimeError("尚未配置 ACPS_DISCOVERY_BASE_URL，且请求中没有指定 partner_url。")
    discovery_url = _base_url(settings.discovery_base_url)
    if not discovery_url.endswith("/discover"):
        discovery_url = f"{discovery_url}/discover"
    request = DiscoveryRequest(type="explicit", query=query, limit=10)
    verify: bool | ssl.SSLContext = settings.outbound_ssl_context() or True
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

    candidates = sorted(
        discovery.result.iter_agent_skills(),
        key=lambda item: item[2].ranking,
    )
    seen: set[str] = set()
    for aic, acs, skill, group in candidates:
        if aic in seen or (requested_aic and aic != requested_aic):
            continue
        seen.add(aic)
        rpc_url = _rpc_endpoint(acs)
        if rpc_url:
            rpc_url = validate_partner_url(rpc_url, settings)
            return {
                "aic": aic,
                "name": acs.get("name") or aic,
                "rpcUrl": rpc_url,
                "skillId": skill.skill_id,
                "ranking": skill.ranking,
                "group": group,
            }
    raise RuntimeError("ACPs Discovery 没有找到带 JSONRPC 端点且符合条件的 Partner。")


def _task_snapshot(task: TaskResult) -> dict[str, Any]:
    return task.model_dump(by_alias=True, exclude_none=True, mode="json")


def _rpc_client(partner_url: str, settings: AcpsSettings) -> AipRpcClient:
    if settings.require_registered_leader and not settings.aic:
        raise RuntimeError("当前环境要求正式 Leader 身份，但尚未配置 Registry 分配的 ACPS_AIC。")
    if settings.require_registered_leader and not validate_aic_format(settings.aic)[0]:
        raise RuntimeError("ACPS_AIC 格式不符合 ACPs v2.1 规范，不能作为正式 Leader 调用。")
    if settings.require_registered_leader and not settings.allowed_partner_hosts:
        raise RuntimeError("正式 Leader 必须配置 ACPS_ALLOWED_PARTNER_HOSTS 出站白名单。")
    validated_url = validate_partner_url(partner_url, settings)
    return AipRpcClient(
        partner_url=validated_url,
        leader_id=settings.aic or "longyun-unregistered-leader",
        ssl_context=(
            settings.outbound_ssl_context()
            if validated_url.lower().startswith("https://")
            else None
        ),
    )


async def _wait_for_direct_task(
    client: AipRpcClient,
    task: TaskResult,
    session_id: str,
    settings: AcpsSettings,
) -> TaskResult:
    deadline = asyncio.get_running_loop().time() + settings.task_timeout_seconds
    while task.status.state in {TaskState.Accepted, TaskState.Working}:
        if asyncio.get_running_loop().time() >= deadline:
            try:
                await client.cancel_task(task.taskId, session_id)
            except Exception:
                logger.warning(
                    "ACPs Direct task %s timed out and the cancellation request failed",
                    task.taskId,
                    exc_info=True,
                )
            raise TimeoutError(
                f"Partner 任务超过 {settings.task_timeout_seconds:g} 秒，已尝试请求取消。"
            )
        await asyncio.sleep(settings.poll_interval_seconds)
        task = await client.get_task(task.taskId, session_id)
    return task


async def dispatch_partner_task(
    request: AcpsLeaderDispatchRequest,
    settings: AcpsSettings,
) -> dict[str, Any]:
    """Run the Leader side of a Direct AIP task to completion or a wait state."""
    if not settings.supports_leader:
        raise RuntimeError("当前 ACPS_ROLE 未启用 Leader 能力。")
    if (
        settings.require_registered_leader
        and request.partner_aic
        and not validate_aic_format(request.partner_aic)[0]
    ):
        raise ValueError("partner_aic 格式不符合 ACPs v2.1 规范。")
    selected = {
        "aic": request.partner_aic or "explicit-partner",
        "name": request.partner_aic or "显式 Partner",
        "rpcUrl": request.partner_url,
        "skillId": None,
        "ranking": None,
        "group": None,
    }
    if not request.partner_url:
        selected = await discover_partner(request.query, settings, request.partner_aic)
    partner_url = str(selected["rpcUrl"] or "")
    if not partner_url:
        raise RuntimeError("Partner 没有可调用的 JSONRPC 端点。")
    partner_url = validate_partner_url(partner_url, settings)
    selected["rpcUrl"] = partner_url
    client = _rpc_client(partner_url, settings)
    session_id = request.session_id or f"session-{uuid.uuid4()}"
    try:
        task = await client.start_task(session_id, request.query, request.task_id)
        task = await _wait_for_direct_task(client, task, session_id, settings)
        if task.status.state == TaskState.AwaitingCompletion and request.auto_complete:
            task = await client.complete_task(task.taskId, session_id)
        return {
            "selectedPartner": selected,
            "sessionId": session_id,
            "task": _task_snapshot(task),
        }
    finally:
        await client.close()


@dataclass
class AcpsDirectTaskBinding:
    task_id: str
    session_id: str
    partner_url: str
    partner_aic: str
    owner_id: str
    project_id: str
    touched_at: datetime


class AcpsDirectLeaderRuntime:
    """Own Direct Leader task handles without exposing Partner endpoints to later calls."""

    def __init__(self, settings: AcpsSettings):
        self.settings = settings
        self._tasks: dict[str, AcpsDirectTaskBinding] = {}

    def _purge_expired(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self.settings.leader_task_retention_seconds
        )
        self._tasks = {
            task_id: binding
            for task_id, binding in self._tasks.items()
            if binding.touched_at >= cutoff
        }

    def _binding(self, task_id: str, owner_id: str, project_id: str) -> AcpsDirectTaskBinding:
        self._purge_expired()
        binding = self._tasks.get(task_id)
        # Deliberately return the same error for missing and foreign tasks so a
        # researcher cannot use this endpoint to enumerate another user's IDs.
        if not binding or binding.owner_id != owner_id or binding.project_id != project_id:
            raise LookupError("未找到当前账号在本课题创建的 ACPs Direct 任务。")
        binding.touched_at = datetime.now(timezone.utc)
        return binding

    async def dispatch(
        self,
        request: AcpsLeaderDispatchRequest,
        *,
        owner_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        result = await dispatch_partner_task(request, self.settings)
        task = result.get("task") or {}
        selected = result.get("selectedPartner") or {}
        task_id = str(task.get("taskId") or "").strip()
        session_id = str(result.get("sessionId") or "").strip()
        partner_url = str(selected.get("rpcUrl") or "").strip()
        if not (task_id and session_id and partner_url):
            raise RuntimeError("Partner 返回的任务缺少 taskId、sessionId 或 RPC endpoint。")
        existing = self._tasks.get(task_id)
        if existing and (existing.owner_id != owner_id or existing.project_id != project_id):
            raise RuntimeError("Partner 返回了一个已经属于其他调用方的 taskId。")
        self._tasks[task_id] = AcpsDirectTaskBinding(
            task_id=task_id,
            session_id=session_id,
            partner_url=partner_url,
            partner_aic=str(selected.get("aic") or "explicit-partner"),
            owner_id=owner_id,
            project_id=project_id,
            touched_at=datetime.now(timezone.utc),
        )
        return result

    async def command(
        self,
        task_id: str,
        request: AcpsLeaderTaskCommandRequest,
        *,
        owner_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        binding = self._binding(task_id, owner_id, project_id)
        if request.command == "continue" and not request.query:
            raise ValueError("continue 命令必须提供 query。")
        if request.command != "continue" and request.query:
            raise ValueError(f"{request.command} 命令不接受 query。")

        client = _rpc_client(binding.partner_url, self.settings)
        try:
            if request.command == "get":
                task = await client.get_task(task_id, binding.session_id)
            elif request.command == "continue":
                task = await client.continue_task(
                    task_id, binding.session_id, str(request.query)
                )
                if request.wait_for_result:
                    task = await _wait_for_direct_task(
                        client, task, binding.session_id, self.settings
                    )
            elif request.command == "complete":
                task = await client.complete_task(task_id, binding.session_id)
            else:
                task = await client.cancel_task(task_id, binding.session_id)

            if task.status.state == TaskState.AwaitingCompletion and request.auto_complete:
                task = await client.complete_task(task_id, binding.session_id)
            binding.touched_at = datetime.now(timezone.utc)
            return {
                "selectedPartner": {
                    "aic": binding.partner_aic,
                    "rpcUrl": binding.partner_url,
                },
                "sessionId": binding.session_id,
                "task": _task_snapshot(task),
            }
        finally:
            await client.close()


@dataclass(frozen=True)
class AcpsGroupOwner:
    owner_id: str
    project_id: str


class AcpsGroupLeaderRuntime:
    """Secure, owner-scoped wrapper around the ACPs v2.1 GroupLeader SDK."""

    def __init__(
        self,
        settings: AcpsSettings,
        leader_factory: Callable[..., Any] = GroupLeader,
    ) -> None:
        self.settings = settings
        self._leader_factory = leader_factory
        self._leader: Any | None = None
        self._leader_lock = asyncio.Lock()
        self._owners: dict[str, AcpsGroupOwner] = {}

    def readiness(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.group_enabled,
            "ready": self.settings.group_ready,
            "blockers": self.settings.group_blockers,
            "activeSessions": len(self._owners),
        }

    async def _get_leader(self) -> Any:
        blockers = self.settings.group_blockers
        if blockers:
            raise RuntimeError("ACPs Group Leader 尚未就绪：" + "；".join(blockers))
        if self._leader is not None:
            return self._leader
        async with self._leader_lock:
            if self._leader is None:
                ssl_context = (
                    self.settings.outbound_ssl_context()
                    if self.settings.mtls_enabled
                    else None
                )
                ssl_cert = None
                if self.settings.mtls_enabled:
                    ssl_cert = (
                        str(self.settings.client_certificate_file),
                        str(self.settings.client_private_key_file),
                    )
                self._leader = self._leader_factory(
                    leader_aic=self.settings.aic,
                    rabbitmq_config={
                        "host": self.settings.rabbitmq_host,
                        "port": self.settings.rabbitmq_port,
                        "vhost": self.settings.rabbitmq_vhost,
                        "user": self.settings.rabbitmq_user,
                        "password": self.settings.rabbitmq_password,
                        "auth_service_url": self.settings.group_auth_service_url,
                    },
                    ssl_context=ssl_context,
                    ssl_cert=ssl_cert,
                    invitation_timeout_seconds=(
                        self.settings.group_invitation_timeout_seconds
                    ),
                )
        return self._leader

    async def _owned_session(
        self,
        session_id: str,
        owner_id: str,
        project_id: str,
    ) -> Any:
        leader = await self._get_leader()
        owner = self._owners.get(session_id)
        if owner != AcpsGroupOwner(owner_id=owner_id, project_id=project_id):
            raise LookupError("未找到当前账号在本课题创建的 ACPs Group 会话。")
        if session_id not in leader.group_sessions:
            self._owners.pop(session_id, None)
            raise LookupError("ACPs Group 会话已过期或运行时已重启，请重新建组。")
        return leader

    async def create_group(
        self,
        request: AcpsGroupCreateRequest,
        *,
        owner_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        leader = await self._get_leader()
        session_id = request.session_id or f"session-{uuid.uuid4()}"
        if session_id in self._owners or session_id in leader.group_sessions:
            raise ValueError("该 ACPs Group sessionId 已存在。")
        await leader.create_group_session(session_id, [])
        self._owners[session_id] = AcpsGroupOwner(owner_id, project_id)
        return leader.get_group_runtime(session_id)

    async def get_group(
        self, session_id: str, *, owner_id: str, project_id: str
    ) -> dict[str, Any]:
        leader = await self._owned_session(session_id, owner_id, project_id)
        return leader.get_group_runtime(session_id)

    async def invite_partner(
        self,
        session_id: str,
        request: AcpsGroupPartnerRequest,
        *,
        owner_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        leader = await self._owned_session(session_id, owner_id, project_id)
        if not validate_aic_format(request.partner_aic)[0]:
            raise ValueError("partner_aic 格式不符合 ACPs v2.1 规范。")
        partner_acs_data = request.partner_acs
        if partner_acs_data is not None:
            AgentCapabilitySpec.from_dict(partner_acs_data)
            acs_aic = str(partner_acs_data.get("aic") or "").strip()
            if acs_aic != request.partner_aic:
                raise ValueError("partner_acs.aic 与 partner_aic 不一致。")
            acs_rpc_url = _rpc_endpoint(partner_acs_data)
            if acs_rpc_url:
                acs_rpc_url = validate_partner_url(acs_rpc_url, self.settings)
        else:
            acs_rpc_url = None
        partner_url = (
            validate_partner_url(request.partner_url, self.settings)
            if request.partner_url
            else acs_rpc_url
        )
        if partner_acs_data is None and partner_url is None:
            raise ValueError("邀请 Partner 必须提供 partner_acs 或 partner_url。")
        await leader.invite_partner(
            session_id,
            ACSObject(aic=request.partner_aic),
            partner_rpc_url=partner_url,
            partner_acs_data=partner_acs_data,
        )
        return leader.get_group_runtime(session_id)

    async def start_task(
        self,
        session_id: str,
        request: AcpsGroupTaskRequest,
        *,
        owner_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        leader = await self._owned_session(session_id, owner_id, project_id)
        for partner_aic in request.target_partners or []:
            if not validate_aic_format(partner_aic)[0]:
                raise ValueError("target_partners 包含不符合 ACPs v2.1 规范的 AIC。")
        task_id = await leader.start_task(
            session_id,
            task_content=request.content,
            task_id=request.task_id,
            target_partners=request.target_partners,
        )
        return {
            "taskId": task_id,
            "group": leader.get_group_runtime(session_id),
        }

    async def task_command(
        self,
        session_id: str,
        task_id: str,
        request: AcpsGroupTaskCommandRequest,
        *,
        owner_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        leader = await self._owned_session(session_id, owner_id, project_id)
        if request.target_partner and not validate_aic_format(request.target_partner)[0]:
            raise ValueError("target_partner 格式不符合 ACPs v2.1 规范。")
        if request.command == "continue":
            if not request.content:
                raise ValueError("continue 命令必须提供 content。")
            await leader.continue_task(
                session_id, task_id, request.content, request.target_partner
            )
        elif request.command == "complete":
            if request.content or request.reason:
                raise ValueError("complete 命令不接受 content 或 reason。")
            await leader.complete_task(session_id, task_id, request.target_partner)
        else:
            if request.content:
                raise ValueError("cancel 命令不接受 content，请使用 reason。")
            await leader.cancel_task(
                session_id, task_id, request.reason, request.target_partner
            )
        return {
            "taskId": task_id,
            "command": request.command,
            "group": leader.get_group_runtime(session_id),
        }

    async def member_command(
        self,
        session_id: str,
        partner_aic: str,
        request: AcpsGroupMemberCommandRequest,
        *,
        owner_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        leader = await self._owned_session(session_id, owner_id, project_id)
        if not validate_aic_format(partner_aic)[0]:
            raise ValueError("partner_aic 格式不符合 ACPs v2.1 规范。")
        if request.command == "status":
            await leader.check_partner_status(partner_aic, session_id)
        elif request.command == "leave":
            await leader.request_partner_leave(partner_aic, session_id)
        elif request.command == "force-remove":
            await leader.force_remove_partner(partner_aic, session_id)
        else:
            session = leader.group_sessions[session_id]
            if request.command == "mute":
                await session.leader_mq_client.mute_partner(partner_aic, session_id)
            else:
                await session.leader_mq_client.unmute_partner(partner_aic, session_id)
        return leader.get_group_runtime(session_id)

    async def dissolve_group(
        self, session_id: str, *, owner_id: str, project_id: str
    ) -> dict[str, Any]:
        leader = await self._owned_session(session_id, owner_id, project_id)
        runtime = leader.get_group_runtime(session_id)
        await leader.dissolve_group_session(session_id)
        self._owners.pop(session_id, None)
        return {
            "sessionId": session_id,
            "groupId": runtime.get("group_id"),
            "dissolved": True,
        }

    async def close(self) -> None:
        if self._leader is not None:
            await self._leader.close()
            self._leader = None
        self._owners.clear()


def mount_acps_routes(
    app: FastAPI,
    settings: AcpsSettings,
    executor: PartnerExecutor,
    group_runtime: AcpsGroupLeaderRuntime | None = None,
) -> None:
    """Mount public ACS/AIP routes. The caller mounts the authenticated Leader route."""
    handlers = build_partner_handlers(settings, executor)

    @app.get("/.well-known/acps-agent.json", include_in_schema=False)
    async def acps_agent_card() -> dict[str, Any]:
        if not settings.enabled:
            raise HTTPException(404, "ACPs adapter is disabled")
        return build_acs_document(settings)

    @app.get("/acps/health", tags=["ACPs"])
    async def acps_health() -> dict[str, Any]:
        return {
            "enabled": settings.enabled,
            "role": settings.role,
            "runtimeRoles": settings.runtime_roles,
            "aic": settings.aic or None,
            "registered": settings.registered,
            "protocolVersion": "02.01",
            "sdkVersion": ACPS_SDK_VERSION,
            "aipTransport": (
                "direct-jsonrpc+amqp-group"
                if settings.group_ready
                else "direct-jsonrpc"
            ),
            "mtls": settings.mtls_enabled,
            "directLeader": settings.supports_leader,
            "groupLeader": (
                group_runtime.readiness()
                if group_runtime is not None
                else {
                    "enabled": settings.group_enabled,
                    "ready": settings.group_ready,
                    "blockers": settings.group_blockers,
                    "activeSessions": 0,
                }
            ),
        }

    @app.get("/acps/info", tags=["ACPs"])
    async def acps_info() -> dict[str, Any]:
        return {
            "name": settings.name,
            "protocolVersion": "02.01",
            "sdkVersion": ACPS_SDK_VERSION,
            "runtimeRoles": settings.runtime_roles,
            "interfaces": {
                "acs": "/.well-known/acps-agent.json",
                "partnerRpc": "/acps/rpc" if settings.supports_partner else None,
                "leaderDispatch": "/api/acps/leader/dispatch" if settings.supports_leader else None,
                "leaderTaskControl": (
                    "/api/acps/leader/tasks/{task_id}/commands"
                    if settings.supports_leader
                    else None
                ),
                "leaderGroups": (
                    "/api/acps/leader/groups" if settings.group_enabled else None
                ),
            },
            "aipCommands": ["start", "get", "continue", "complete", "cancel"],
            "interactionModes": {
                "direct": settings.supports_leader or settings.supports_partner,
                "group": settings.group_ready,
            },
            "dataBoundary": "published-standard-data-only",
            "limitations": [
                "不读取科研人员私有知识库、浏览器会话历史或附件",
                "不提供数据写入、发布、字段变更或治理审批能力",
                "科研分析结果需要专家审查和田间验证",
            ],
        }

    @app.post("/acps/rpc", tags=["ACPs"])
    async def acps_rpc(request: Request):
        if not settings.supports_partner:
            raise HTTPException(404, "当前运行模式未启用 ACPs Partner。")
        caller_aic = request.headers.get(settings.verified_client_header, "").strip()
        if settings.require_verified_client and not caller_aic:
            raise HTTPException(401, "ACPs Partner 要求由 mTLS 网关验证调用方证书。")
        try:
            body = await request.json()
            command_body = body["params"]["command"]
            claimed_sender = str(command_body["senderId"]).strip()
            task_id = str(command_body.get("taskId") or "").strip()
        except (KeyError, TypeError, ValueError, AttributeError):
            claimed_sender = ""
            task_id = ""
        if settings.require_verified_client and claimed_sender and claimed_sender != caller_aic:
            raise HTTPException(403, "TaskCommand.senderId 与 mTLS 客户端证书 AIC 不一致。")
        # A valid Leader certificate may only inspect or mutate tasks that it
        # originally created. Check before the SDK handler, because its generic
        # exception path marks a task failed and would let another Leader cause
        # a denial of service merely by guessing a task ID.
        existing_task = TaskManager.get_task(task_id) if task_id else None
        initial_command = (existing_task.commandHistory or [None])[0] if existing_task else None
        owner_aic = initial_command.senderId if initial_command else ""
        if owner_aic and claimed_sender and owner_aic != claimed_sender:
            raise HTTPException(403, "当前 Leader 无权访问其他 Leader 创建的 ACPs 任务。")
        return await handle_rpc_request(request, handlers)
