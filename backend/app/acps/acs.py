"""Generate the Longyun ACS document registered with ACPs Registry."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from ..ai.registry import AGENT_SPECS
from .service import SKILL_TO_AGENT_CODE


def build_longyun_acs(
    *,
    aic: str,
    rpc_url: str | None,
    amqp_url: str | None = None,
    organization: str = "亿发姆",
) -> dict[str, Any]:
    if not aic.strip():
        raise ValueError("aic is required")
    endpoints: list[dict[str, Any]] = []
    if rpc_url:
        endpoints.append({
            "url": rpc_url,
            "transport": "JSONRPC",
            "security": [{"mtls": []}],
        })
    if amqp_url:
        endpoints.append({
            "url": amqp_url,
            "transport": "AMQP",
            "security": [{"mtls": []}],
        })
    reverse_skills = {agent_code: skill_id for skill_id, agent_code in SKILL_TO_AGENT_CODE.items()}
    skills = []
    for code, spec in AGENT_SPECS.items():
        skills.append({
            "id": reverse_skills[code],
            "name": spec.name,
            "description": spec.description,
            "version": spec.version,
            "tags": list(spec.capabilities),
            "examples": list(spec.keywords[:3]),
            "inputModes": ["text/plain", "application/json"],
            "outputModes": ["text/plain", "application/json"],
        })
    return {
        "aic": aic,
        "active": True,
        "lastModifiedTime": datetime.now(timezone.utc).isoformat(),
        "protocolVersion": "02.01",
        "name": "隆耘农业科研智能体",
        "description": (
            "面向农业科研的总控智能体，提供种质鉴析、亲本配组辅助、"
            "试验分析和科研情报能力。对外使用 AIP，内部继续使用隆耘现有工作流。"
        ),
        "version": "2.1.0",
        "provider": {"countryCode": "CN", "organization": organization},
        "securitySchemes": {
            "mtls": {"type": "mutualTLS", "description": "ACPs 智能体间 mTLS 双向认证"}
        },
        "endPoints": endpoints,
        "capabilities": {
            "streaming": False,
            "notification": False,
            # RabbitMQ 4.2 uses AMQP 0-9-1.  Declaring the wire protocol keeps
            # this ACS valid in acps-sdk 2.1.0 as well as Registry's schema.
            "messageQueue": ["amqp:0.9.1"] if amqp_url else [],
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": skills,
    }


def main() -> None:
    document = build_longyun_acs(
        aic=os.environ.get("LONGYUN_AIC", ""),
        rpc_url=os.environ.get("LONGYUN_ACS_RPC_URL") or None,
        amqp_url=os.environ.get("LONGYUN_ACS_AMQP_URL") or None,
        organization=os.environ.get("LONGYUN_PROVIDER_ORGANIZATION", "亿发姆"),
    )
    print(json.dumps(document, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
