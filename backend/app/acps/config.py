"""Environment-backed configuration for the ACPs boundary."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE_VALUES


@dataclass(frozen=True)
class AcpsIdentityBinding:
    """Authorization scope assigned to one verified Leader AIC.

    The mTLS certificate establishes the remote agent identity.  This binding
    turns that identity into the existing Longyun tenant/owner context without
    changing Longyun's user or project data model.
    """

    institution_id: str
    owner_id: str
    project_id: str
    allowed_skill_ids: frozenset[str] = frozenset()
    external_data_acknowledged: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AcpsIdentityBinding":
        institution_id = str(
            data.get("institutionId") or data.get("institution_id") or ""
        ).strip()
        owner_id = str(data.get("ownerId") or data.get("owner_id") or "").strip()
        project_id = str(
            data.get("projectId") or data.get("project_id") or ""
        ).strip()
        if not institution_id or not owner_id or not project_id:
            raise ValueError(
                "每个 AIC 绑定都必须包含 institutionId、ownerId 和 projectId"
            )
        raw_skills = data.get("allowedSkillIds", data.get("allowed_skill_ids", []))
        if not isinstance(raw_skills, list):
            raise ValueError("allowedSkillIds 必须是字符串数组")
        return cls(
            institution_id=institution_id,
            owner_id=owner_id,
            project_id=project_id,
            allowed_skill_ids=frozenset(
                str(item).strip() for item in raw_skills if str(item).strip()
            ),
            external_data_acknowledged=_as_bool(
                data.get(
                    "externalDataAcknowledged",
                    data.get("external_data_acknowledged"),
                )
            ),
        )


@dataclass(frozen=True)
class AcpsSettings:
    enabled: bool
    partner_aic: str
    identity_bindings: dict[str, AcpsIdentityBinding]
    require_mtls_proxy: bool = True
    rpc_path: str = "/acps/aip/rpc"
    max_timeout_ms: int = 3_600_000

    @classmethod
    def from_env(cls) -> "AcpsSettings":
        raw_bindings = os.getenv("ACPS_IDENTITY_BINDINGS_JSON", "{}").strip() or "{}"
        try:
            decoded = json.loads(raw_bindings)
        except json.JSONDecodeError as exc:
            raise ValueError("ACPS_IDENTITY_BINDINGS_JSON 不是合法 JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("ACPS_IDENTITY_BINDINGS_JSON 必须是以 AIC 为键的 JSON 对象")
        bindings = {
            str(aic).strip(): AcpsIdentityBinding.from_dict(value)
            for aic, value in decoded.items()
            if str(aic).strip() and isinstance(value, dict)
        }
        path = os.getenv("ACPS_AIP_RPC_PATH", "/acps/aip/rpc").strip()
        if not path.startswith("/"):
            raise ValueError("ACPS_AIP_RPC_PATH 必须以 / 开头")
        return cls(
            enabled=_as_bool(os.getenv("ACPS_AIP_ENABLED")),
            partner_aic=os.getenv("LONGYUN_AIC", "").strip(),
            identity_bindings=bindings,
            require_mtls_proxy=_as_bool(
                os.getenv("ACPS_REQUIRE_MTLS_PROXY"), default=True
            ),
            rpc_path=path,
            max_timeout_ms=max(
                1_000,
                min(int(os.getenv("ACPS_MAX_TASK_TIMEOUT_MS", "3600000")), 86_400_000),
            ),
        )

    def binding_for(self, leader_aic: str) -> AcpsIdentityBinding | None:
        return self.identity_bindings.get(leader_aic)

    def assert_ready(self) -> None:
        if not self.enabled:
            return
        if not self.partner_aic:
            raise ValueError("启用 AIP 时必须配置 LONGYUN_AIC")
        if not self.identity_bindings:
            raise ValueError("启用 AIP 时必须配置至少一个可信 Leader AIC 绑定")
