"""Fail-closed policy for every call that can leave the Longyun boundary.

The selected model implementation is deliberately provider-neutral.  An
OpenAI-compatible relay is still an external processor: the prompt can pass
through the relay and one or more upstream model vendors.  Callers therefore
classify the material they intend to send before invoking a provider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Literal
from urllib.parse import urlparse


DataClassification = Literal["public", "desensitized", "institution_private"]


class ModelDataPolicyError(RuntimeError):
    """Safe policy rejection suitable for an API or workflow error."""


def _as_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ModelDataPolicy:
    deployment_mode: str
    data_environment: str
    provider_name: str
    provider_host: str
    allow_external_private_evidence: bool
    allow_external_web_search: bool

    @property
    def is_local(self) -> bool:
        return self.deployment_mode in {"local", "on_prem", "private"}

    @property
    def acknowledgement_required(self) -> bool:
        # The sandbox contract already limits intake to public or desensitized
        # material.  Treat that as an environment-level acknowledgement rather
        # than recording a user confirmation that the UI no longer asks for.
        return not self.is_local and not self.is_desensitized_sandbox

    @property
    def is_desensitized_sandbox(self) -> bool:
        return self.data_environment == "sandbox_desensitized"

    @property
    def private_evidence_allowed(self) -> bool:
        # "Private" here is an access scope.  A sandbox-scoped object may be
        # sent only after the user attests that its contents are desensitized.
        return (
            self.is_local
            or self.is_desensitized_sandbox
            or self.allow_external_private_evidence
        )

    @property
    def history_allowed(self) -> bool:
        # Conversation history and working memory may contain older private
        # turns for which no per-transfer acknowledgement exists.
        return self.is_local or self.allow_external_private_evidence

    def public_view(self) -> dict[str, object]:
        return {
            "deployment_mode": self.deployment_mode,
            "data_environment": self.data_environment,
            "provider_name": self.provider_name,
            "provider_host": self.provider_host,
            "external_data_acknowledgement_required": self.acknowledgement_required,
            "private_evidence_allowed": self.private_evidence_allowed,
            "conversation_history_forwarded": self.history_allowed,
            "external_web_search_enabled": self.allow_external_web_search,
        }


def get_model_data_policy() -> ModelDataPolicy:
    deployment_mode = os.getenv("LONGYUN_LLM_DEPLOYMENT_MODE", "external_api").strip().lower()
    base_url = (
        os.getenv("LONGYUN_LLM_BASE_URL")
        or os.getenv("SHENNONG_API_BASE_URL")
        or ""
    ).strip()
    provider_host = (urlparse(base_url).hostname or "").lower()
    provider_name = (
        os.getenv("LONGYUN_LLM_PROVIDER_NAME")
        or (
            "CherryIn"
            if provider_host.endswith(("cherryin.ai", "cherryin.net"))
            else "OpenAI-compatible model service"
        )
    ).strip()
    return ModelDataPolicy(
        deployment_mode=deployment_mode,
        data_environment=os.getenv(
            "LONGYUN_DATA_ENVIRONMENT", "sandbox_desensitized"
        ).strip().lower(),
        provider_name=provider_name,
        provider_host=provider_host,
        allow_external_private_evidence=_as_bool(
            "LONGYUN_ALLOW_EXTERNAL_PRIVATE_EVIDENCE", False
        ),
        allow_external_web_search=_as_bool("LONGYUN_ALLOW_EXTERNAL_WEB_SEARCH", False),
    )


def require_external_acknowledgement(
    policy: ModelDataPolicy,
    *,
    acknowledged: bool,
) -> None:
    if policy.acknowledgement_required and not acknowledged:
        raise ModelDataPolicyError(
            "当前请求将调用外部模型 API。请先确认本轮问题仅包含公开或已脱敏信息。"
        )


def assert_model_payload_allowed(
    policy: ModelDataPolicy,
    *,
    acknowledged: bool,
    classifications: Iterable[DataClassification],
    context_label: str = "本轮模型上下文",
) -> None:
    """Reject private provider payloads before any network request is made."""
    require_external_acknowledgement(policy, acknowledged=acknowledged)
    levels = set(classifications)
    if (
        not policy.is_local
        and "institution_private" in levels
        and not policy.is_desensitized_sandbox
        and not policy.allow_external_private_evidence
    ):
        raise ModelDataPolicyError(
            f"{context_label}包含机构私有数据，当前外部模型通道禁止传输。"
            "请移除私有材料，或切换到院内本地模型后再分析。"
        )


def evidence_classifications(items: Iterable[dict[str, object]]) -> set[DataClassification]:
    """Classify legacy evidence cards; unknown types fail closed as private."""
    public_types = {"published_standard_data", "public_knowledge", "trusted_public_web"}
    classifications: set[DataClassification] = set()
    for item in items:
        explicit = str(item.get("data_classification") or "").strip()
        if explicit in {"public", "desensitized", "institution_private"}:
            classifications.add(explicit)  # type: ignore[arg-type]
            continue
        evidence_type = str(item.get("type") or item.get("source") or "").strip()
        classifications.add("public" if evidence_type in public_types else "institution_private")
    return classifications
