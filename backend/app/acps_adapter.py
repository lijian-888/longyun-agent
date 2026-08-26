"""ACPs v2.1 Inbox/Group adapter for Longyun.

This module deliberately keeps ACPs transport/state management separate from
the research business logic in ``main.py``.  Leader and Partner use separate
AICs and client certificates while sharing one Longyun application deployment.
"""

from __future__ import annotations

import copy
import os
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, cast

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from acps_sdk.acs import AgentCapabilitySpec

from .acps_group import (
    AcpsGroupCommandRequest,
    AcpsGroupDispatchRequest,
    AcpsGroupPartnerTarget,
    AcpsGroupRuntime,
)


AcpsRole = Literal["leader", "partner", "hybrid"]
AcpsIdentityRole = Literal["leader", "partner"]
AcpsTransport = Literal["group"]
PartnerExecutor = Callable[[str, str, dict[str, str]], Awaitable["AcpsExecutionResult"]]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
    transport: AcpsTransport
    leader_aic: str
    partner_aic: str
    name: str
    version: str
    public_base_url: str
    documentation_url: str
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
    session_owner_username: str
    session_owner_subject: str | None
    mtls_enabled: bool
    leader_client_certificate_file: str | None
    leader_client_private_key_file: str | None
    partner_client_certificate_file: str | None
    partner_client_private_key_file: str | None
    trust_bundle_file: str | None
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
    poll_interval_seconds: float
    task_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "AcpsSettings":
        role = os.getenv("ACPS_ROLE", "hybrid").strip().lower()
        if role not in {"leader", "partner", "hybrid"}:
            raise ValueError("ACPS_ROLE 必须是 leader、partner 或 hybrid。")
        transport = os.getenv("ACPS_TRANSPORT", "group").strip().lower()
        if transport != "group":
            raise ValueError("隆耘生产适配器只支持 ACPS_TRANSPORT=group。")
        public_base_url = _base_url(os.getenv("ACPS_PUBLIC_BASE_URL", "http://localhost:5183"))
        return cls(
            enabled=_env_bool("ACPS_ENABLED", False),
            role=cast(AcpsRole, role),
            transport=cast(AcpsTransport, transport),
            leader_aic=os.getenv("ACPS_LEADER_AIC", "").strip(),
            partner_aic=os.getenv("ACPS_PARTNER_AIC", "").strip(),
            name=os.getenv("ACPS_AGENT_NAME", "隆耘 Agent 育种智能体").strip(),
            version=os.getenv("ACPS_AGENT_VERSION", "2.0.0").strip(),
            public_base_url=public_base_url,
            documentation_url=os.getenv(
                "ACPS_DOCUMENTATION_URL",
                "https://github.com/lijian-888/longyun-agent/blob/main/docs/ACPS-INTEGRATION.md",
            ).strip(),
            discovery_base_url=_optional_env("ACPS_DISCOVERY_BASE_URL"),
            provider_organization=os.getenv(
                "ACPS_PROVIDER_ORGANIZATION", "江西省亿发姆科技发展有限公司"
            ).strip(),
            provider_department=_optional_env("ACPS_PROVIDER_DEPARTMENT") or "隆耘智能体项目组",
            provider_url=_optional_env("ACPS_PROVIDER_URL")
            or "https://longyun.e-farmer.cn/",
            provider_license=_optional_env("ACPS_PROVIDER_LICENSE"),
            provider_name=_optional_env("ACPS_PROVIDER_NAME") or "李键",
            provider_email=_optional_env("ACPS_PROVIDER_EMAIL") or "13437975781@163.com",
            provider_country_code=os.getenv("ACPS_PROVIDER_COUNTRY_CODE", "CN").strip().upper(),
            provider_domain=_optional_env("ACPS_PROVIDER_DOMAIN"),
            provider_domain_registration_number=_optional_env("ACPS_PROVIDER_DOMAIN_REGISTRATION_NUMBER"),
            provider_domain_registration_type=_optional_env("ACPS_PROVIDER_DOMAIN_REGISTRATION_TYPE"),
            session_owner_username=os.getenv(
                "ACPS_SESSION_OWNER_USERNAME", "acps.researcher"
            ).strip(),
            session_owner_subject=_optional_env("ACPS_SESSION_OWNER_SUBJECT"),
            mtls_enabled=_env_bool("ACPS_MTLS_ENABLED", False),
            leader_client_certificate_file=_optional_env("ACPS_LEADER_CLIENT_CERT_FILE"),
            leader_client_private_key_file=_optional_env("ACPS_LEADER_CLIENT_KEY_FILE"),
            partner_client_certificate_file=_optional_env("ACPS_PARTNER_CLIENT_CERT_FILE"),
            partner_client_private_key_file=_optional_env("ACPS_PARTNER_CLIENT_KEY_FILE"),
            trust_bundle_file=_optional_env("ACPS_TRUST_BUNDLE_FILE"),
            rabbitmq_host=_optional_env("ACPS_RABBITMQ_HOST"),
            rabbitmq_port=int(os.getenv("ACPS_RABBITMQ_PORT", "5671")),
            rabbitmq_vhost=os.getenv("ACPS_RABBITMQ_VHOST", "acps").strip(),
            rabbitmq_user=_optional_env("ACPS_RABBITMQ_USER"),
            rabbitmq_password=_optional_env("ACPS_RABBITMQ_PASSWORD"),
            allow_plain_rabbitmq=_env_bool("ACPS_ALLOW_PLAIN_RABBITMQ", False),
            rabbitmq_auth_service_url=_optional_env("ACPS_RABBITMQ_AUTH_SERVICE_URL"),
            group_invitation_timeout_seconds=max(
                int(os.getenv("ACPS_GROUP_INVITATION_TIMEOUT_SECONDS", "300")), 10
            ),
            group_max_partner_groups=max(
                int(os.getenv("ACPS_GROUP_MAX_PARTNER_GROUPS", "16")), 1
            ),
            group_max_partners=max(int(os.getenv("ACPS_GROUP_MAX_PARTNERS", "8")), 1),
            poll_interval_seconds=max(float(os.getenv("ACPS_POLL_INTERVAL_SECONDS", "0.5")), 0.05),
            task_timeout_seconds=max(float(os.getenv("ACPS_TASK_TIMEOUT_SECONDS", "180")), 1.0),
        )

    @property
    def supports_leader(self) -> bool:
        return self.enabled and self.role in {"leader", "hybrid"}

    @property
    def supports_partner(self) -> bool:
        return self.enabled and self.role in {"partner", "hybrid"}

    @property
    def supports_group(self) -> bool:
        return self.enabled

    @property
    def aip_transport(self) -> str:
        return "group-rabbitmq-inbox"

    @property
    def runtime_roles(self) -> list[str]:
        if self.role == "hybrid":
            return ["leader", "partner"]
        return [self.role]

    @property
    def registered(self) -> bool:
        return all(self.aic_for(role) for role in self.runtime_roles)

    def aic_for(self, role: AcpsIdentityRole) -> str:
        return self.leader_aic if role == "leader" else self.partner_aic

    def amqp_url_for(self, role: AcpsIdentityRole) -> str | None:
        aic = self.aic_for(role)
        if not self.rabbitmq_host or not aic:
            return None
        return (
            f"amqps://{self.rabbitmq_host}:{self.rabbitmq_port}/"
            f"{self.rabbitmq_vhost}?inbox=inbox_{aic}"
        )

    def outbound_ssl_context(self, role: AcpsIdentityRole) -> ssl.SSLContext | None:
        """Build the selected ACPs identity's client-auth TLS context."""
        if not self.mtls_enabled:
            return None
        cert = (
            self.leader_client_certificate_file
            if role == "leader"
            else self.partner_client_certificate_file
        )
        key = (
            self.leader_client_private_key_file
            if role == "leader"
            else self.partner_client_private_key_file
        )
        required = {
            f"ACPS_{role.upper()}_CLIENT_CERT_FILE": cert,
            f"ACPS_{role.upper()}_CLIENT_KEY_FILE": key,
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
            certfile=str(cert),
            keyfile=str(key),
        )
        return context


class AcpsFileArtifact(BaseModel):
    """A file already stored behind an authorized, time-limited URL."""

    name: str
    mime_type: str
    uri: str
    size_bytes: int = Field(ge=0)
    sha256: str


class AcpsExecutionResult(BaseModel):
    """Business result converted into an AIP Product by the Partner adapter."""

    text: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    files: list[AcpsFileArtifact] = Field(default_factory=list)
    product_name: str = "隆耘育种分析结果"
    product_description: str = "基于平台已发布标准数据生成的只读科研分析"


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
            "outputModes": [
                "text/plain",
                "application/json",
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "image/png",
                "image/jpeg",
            ],
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
            "outputModes": [
                "text/plain",
                "application/json",
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "image/png",
                "image/jpeg",
            ],
        },
    ]


def _validate_acs_document(document: dict[str, Any]) -> None:
    """Validate ACS while tolerating one known acps-sdk 2.1 enum lag.

    The normative ACS 02.01 regex and the official Group demos use
    ``rabbitmq:>=4.2``.  The Python SDK 2.1.0 model still enumerates only
    RabbitMQ 3.9-3.11.  Validate the original first; when and only when every
    error points at ``capabilities.messageQueue``, validate a compatibility
    copy so all other normative fields remain SDK-checked.
    """
    try:
        AgentCapabilitySpec.from_dict(document)
        return
    except ValidationError as exc:
        errors = exc.errors()
        if not errors or any(tuple(error.get("loc", ()))[:2] != ("capabilities", "messageQueue") for error in errors):
            raise
    compatibility = copy.deepcopy(document)
    compatibility["capabilities"]["messageQueue"] = ["rabbitmq:3.11"]
    AgentCapabilitySpec.from_dict(compatibility)


def build_acs_document(
    settings: AcpsSettings,
    role: AcpsIdentityRole,
) -> dict[str, Any]:
    """Build one ACS for one independently registered Longyun identity."""
    selected_role = role
    exposes_partner = selected_role == "partner"
    security_schemes: dict[str, Any] = {}
    endpoint_security: list[dict[str, list[str]]] | None = None
    if settings.mtls_enabled:
        security_schemes["mtls"] = {
            "type": "mutualTLS",
            "description": "使用 ACPs CA 签发的 clientAuth 身份证书连接 RabbitMQ。",
        }
        endpoint_security = [{"mtls": []}]

    endpoints: list[dict[str, Any]] = []
    amqp_url = settings.amqp_url_for(selected_role)
    if settings.supports_group and amqp_url:
        endpoints.append({
            "url": amqp_url,
            "transport": "AMQP",
            **({"security": endpoint_security} if endpoint_security else {}),
        })

    document: dict[str, Any] = {
        "aic": settings.aic_for(selected_role),
        "active": settings.enabled,
        "lastModifiedTime": _now_beijing(),
        "protocolVersion": "02.01",
        "name": f"{settings.name}（{'Leader' if selected_role == 'leader' else 'Partner'}）",
        "description": (
            "面向水稻育种科研的隆耘 Group Leader。通过 Discovery 选择 Partner，创建和维护"
            "RabbitMQ Group，分发任务并汇总产出。"
            if selected_role == "leader"
            else "面向水稻育种科研的隆耘 Group Partner。通过 AMQP Inbox 接受邀请，加入"
            "RabbitMQ Group，并提供基于已发布标准数据的只读研究分析能力。"
        ),
        "version": settings.version,
        "documentationUrl": settings.documentation_url,
        "webAppUrl": settings.public_base_url,
        "provider": _provider(settings),
        "securitySchemes": security_schemes,
        "endPoints": endpoints,
        "capabilities": {
            "streaming": False,
            "notification": False,
            "messageQueue": (
                ["rabbitmq:>=4.2"]
                if settings.supports_group
                else []
            ),
        },
        "defaultInputModes": ["text/plain"] if exposes_partner else [],
        "defaultOutputModes": (
            [
                "text/plain",
                "application/json",
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "image/png",
                "image/jpeg",
            ]
            if exposes_partner
            else []
        ),
        "skills": _partner_skills() if exposes_partner else [],
        "entityMeta": {
            "runtimeRoles": [selected_role],
            "dataBoundary": "published-standard-data-only",
            "aipTransport": settings.aip_transport,
            "sharedApplication": "longyun-agent",
            "internalSessionOwner": "acps.researcher",
        },
    }

    _validate_acs_document(document)
    return document


def mount_acps_routes(
    app: FastAPI,
    settings: AcpsSettings,
    executor: PartnerExecutor,
) -> AcpsGroupRuntime:
    """Mount read-only metadata routes and start the Inbox/Group runtime."""
    group_runtime = AcpsGroupRuntime(settings, executor)

    @app.on_event("startup")
    async def start_acps_group_runtime() -> None:
        await group_runtime.startup()

    @app.on_event("shutdown")
    async def stop_acps_group_runtime() -> None:
        await group_runtime.close()

    @app.get("/.well-known/acps-agent.json", include_in_schema=False)
    async def acps_agent_card(
        role: AcpsIdentityRole = Query(default="partner"),
    ) -> dict[str, Any]:
        if not settings.enabled:
            raise HTTPException(404, "ACPs adapter is disabled")
        if role not in settings.runtime_roles:
            raise HTTPException(404, f"当前运行模式未启用 ACPs {role} 身份。")
        return build_acs_document(settings, role)

    @app.get("/acps/health", tags=["ACPs"])
    async def acps_health() -> dict[str, Any]:
        return {
            "enabled": settings.enabled,
            "role": settings.role,
            "runtimeRoles": settings.runtime_roles,
            "identities": {
                "leader": settings.leader_aic or None,
                "partner": settings.partner_aic or None,
            },
            "registered": settings.registered,
            "protocolVersion": "02.01",
            "aipTransport": settings.aip_transport,
            "mtls": settings.mtls_enabled,
            "group": group_runtime.status(),
        }

    @app.get("/acps/info", tags=["ACPs"])
    async def acps_info() -> dict[str, Any]:
        return {
            "name": settings.name,
            "protocolVersion": "02.01",
            "runtimeRoles": settings.runtime_roles,
            "identities": {
                "leader": settings.leader_aic or None,
                "partner": settings.partner_aic or None,
            },
            "interfaces": {
                "leaderAcs": "/.well-known/acps-agent.json?role=leader",
                "partnerAcs": "/.well-known/acps-agent.json?role=partner",
                "partnerInvitation": "AMQP Inbox",
                "groupLeaderDispatch": (
                    "/api/acps/leader/group/dispatch"
                    if settings.supports_leader
                    else None
                ),
            },
            "aipCommands": ["start", "get", "continue", "complete", "cancel"],
            "dataBoundary": "published-standard-data-only",
            "limitations": [
                "不读取科研人员私有知识库、浏览器会话历史或附件",
                "不提供数据写入、发布、字段变更或治理审批能力",
                "科研分析结果需要专家审查和田间验证",
            ],
        }

    return group_runtime
