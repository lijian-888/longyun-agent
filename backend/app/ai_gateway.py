"""Server-side AI gateway configuration and outbound data controls.

The HTTP route owns durable queueing/audit state because it already owns the
SQLAlchemy transaction and authenticated institution/user context.  This
module deliberately contains no browser-facing configuration and is the only
place that resolves upstream provider credentials.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable, Literal


class AIGatewayConfigurationError(RuntimeError):
    """Raised when the selected server-side model provider is incomplete."""


class AIEgressBlockedError(RuntimeError):
    """Raised before an external model request when sensitive data is found."""


@dataclass(frozen=True)
class AIProviderSettings:
    provider: Literal["shennong", "vllm"]
    base_url: str
    model: str
    api_key: str
    external: bool


@dataclass(frozen=True)
class AIEgressDecision:
    classification: Literal["public", "desensitized", "private_local"]
    texts: tuple[str, ...]
    redactions: int


class AIGatewayConcurrencyGate:
    """One process-wide FIFO-compatible gate with namespace observability."""

    def __init__(self, limit: int = 4) -> None:
        if limit != 4:
            raise ValueError("隆耘 AI 网关必须配置为总计 4 个并发槽。")
        self.limit = limit
        self._semaphore = asyncio.Semaphore(limit)
        self._active: dict[str, str] = {}
        self.max_observed = 0

    async def acquire(self, task_id: str, namespace: str, timeout_seconds: float) -> None:
        await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout_seconds)
        self._active[task_id] = namespace
        self.max_observed = max(self.max_observed, len(self._active))

    def release(self, task_id: str) -> None:
        if self._active.pop(task_id, None) is not None:
            self._semaphore.release()

    def active_namespaces(self) -> dict[str, str]:
        return dict(self._active)


def provider_settings(*, require_key: bool = True) -> AIProviderSettings:
    """Resolve one OpenAI-compatible provider using server environment only."""
    provider = (os.getenv("AI_PROVIDER") or "shennong").strip().lower()
    if provider == "shennong":
        settings = AIProviderSettings(
            provider="shennong",
            base_url=(os.getenv("SHENNONG_API_BASE_URL") or "https://api.agent-tech.cc/api/v1").rstrip("/"),
            model=(os.getenv("SHENNONG_MODEL") or "sn").strip() or "sn",
            api_key=(os.getenv("SHENNONG_API_KEY") or "").strip(),
            external=True,
        )
    elif provider == "vllm":
        settings = AIProviderSettings(
            provider="vllm",
            base_url=(os.getenv("VLLM_API_BASE_URL") or "http://vllm:8000/v1").rstrip("/"),
            model=(os.getenv("VLLM_MODEL") or "local-model").strip() or "local-model",
            # vLLM commonly runs without authentication.  An optional token is
            # still supported for deployments fronted by an authenticated proxy.
            api_key=(os.getenv("VLLM_API_KEY") or "").strip(),
            external=False,
        )
    else:
        raise AIGatewayConfigurationError("AI_PROVIDER 仅支持 shennong 或 vllm。")
    if require_key and settings.external and not settings.api_key:
        raise AIGatewayConfigurationError("尚未在服务器配置大模型 API Key。")
    return settings


_BLOCK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("credential", re.compile(
        r"(?i)(?:api[_-]?key|access[_-]?key|secret|password|passwd|authorization|bearer|私钥|密码|密钥)"
        r"\s*(?:[:=：]|为)\s*[^\s,，;；]{4,}"
    )),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE)),
    ("confidential-marker", re.compile(
        r"(?:国家秘密|商业秘密|机密资料|内部资料|未公开数据|不得外传|仅限内部|confidential|do not distribute)",
        re.IGNORECASE,
    )),
)

_REDACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mobile", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("email", re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9.-]+\.[a-z]{2,}(?![\w.-])")),
    ("identity-number", re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")),
)


def prepare_egress(
    texts: Iterable[str],
    *,
    provider: AIProviderSettings,
    contains_private_material: bool = False,
) -> AIEgressDecision:
    """Block secrets/private binary data and redact direct identifiers.

    Local vLLM is within the deployment boundary and may receive private
    content.  External providers receive only public text or the redacted
    representation returned here.  Callers must use ``decision.texts`` rather
    than their original strings.
    """
    values = tuple(str(value or "") for value in texts)
    if not provider.external:
        return AIEgressDecision("private_local", values, 0)
    if contains_private_material:
        raise AIEgressBlockedError(
            "当前任务包含私人附件或私人知识库内容，按出站安全规则不能发送到外部模型。"
            "请移除该附件，或切换到服务器内的本地 vLLM 后再试。"
        )
    configured_secrets = tuple(filter(None, (
        provider.api_key,
        (os.getenv("TAVILY_API_KEY") or "").strip(),
        (os.getenv("MINIO_SECRET_KEY") or "").strip(),
        (os.getenv("POSTGRES_PASSWORD") or "").strip(),
    )))
    for value in values:
        for rule_name, pattern in _BLOCK_PATTERNS:
            if pattern.search(value):
                raise AIEgressBlockedError(f"内容命中敏感出站规则（{rule_name}），本次 AI 调用已阻断。")
        if any(len(secret) >= 8 and secret in value for secret in configured_secrets):
            raise AIEgressBlockedError("内容包含服务器凭据，本次 AI 调用已阻断。")

    redactions = 0
    sanitized: list[str] = []
    for value in values:
        current = value
        for label, pattern in _REDACT_PATTERNS:
            current, count = pattern.subn(f"[已脱敏:{label}]", current)
            redactions += count
        sanitized.append(current)
    return AIEgressDecision(
        "desensitized" if redactions else "public",
        tuple(sanitized),
        redactions,
    )


def redact_secrets(value: object) -> str:
    """Return a log-safe rendering without leaking configured credentials."""
    rendered = str(value)
    for secret in (
        (os.getenv("SHENNONG_API_KEY") or "").strip(),
        (os.getenv("VLLM_API_KEY") or "").strip(),
        (os.getenv("TAVILY_API_KEY") or "").strip(),
        (os.getenv("MINIO_SECRET_KEY") or "").strip(),
        (os.getenv("POSTGRES_PASSWORD") or "").strip(),
    ):
        if secret and len(secret) >= 4:
            rendered = rendered.replace(secret, "***")
    return re.sub(
        r"(?i)((?:bearer|api[_-]?key|secret|password|token)\s*(?:[:=]\s*|\s+))[^\s,;]+",
        r"\1***",
        rendered,
    )


class SecretRedactionFilter(logging.Filter):
    """Sanitize log records before any configured handler renders them."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_secrets(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "日志内容已因安全过滤异常而隐藏"
            record.args = ()
        return True


def install_secret_log_filter() -> None:
    filter_instance = SecretRedactionFilter()
    for logger_name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        target = logging.getLogger(logger_name)
        if not any(isinstance(item, SecretRedactionFilter) for item in target.filters):
            target.addFilter(filter_instance)
        for handler in target.handlers:
            if not any(isinstance(item, SecretRedactionFilter) for item in handler.filters):
                handler.addFilter(filter_instance)
