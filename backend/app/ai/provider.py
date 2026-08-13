"""Provider-neutral LLM access used by every Longyun sub-agent.

Business code refers to a logical model alias and never receives an upstream
API key.  The same interface can point at the current OpenAI-compatible API or
at an institute-local vLLM endpoint later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class LLMProviderError(RuntimeError):
    """Safe provider failure; the upstream credential is never included."""


@dataclass(frozen=True)
class LLMReply:
    content: str
    model: str
    usage: dict[str, int]


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 3000,
    ) -> LLMReply: ...


@dataclass(frozen=True)
class ProviderSettings:
    base_url: str
    api_key: str
    model: str
    logical_model_alias: str
    timeout_seconds: float

    @classmethod
    def from_environment(cls) -> "ProviderSettings":
        return cls(
            base_url=(
                os.getenv("LONGYUN_LLM_BASE_URL")
                or os.getenv("SHENNONG_API_BASE_URL")
                or "http://127.0.0.1:8000/v1"
            ).rstrip("/"),
            api_key=(
                os.getenv("LONGYUN_LLM_API_KEY")
                or os.getenv("SHENNONG_API_KEY")
                or ""
            ).strip(),
            model=(
                os.getenv("LONGYUN_LLM_MODEL")
                or os.getenv("SHENNONG_MODEL")
                or "longyun-default"
            ).strip(),
            logical_model_alias=(
                os.getenv("LONGYUN_LLM_ALIAS") or "longyun-research"
            ).strip(),
            timeout_seconds=float(os.getenv("LONGYUN_LLM_TIMEOUT_SECONDS", "180")),
        )


class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible adapter shared by API and future vLLM."""

    def __init__(self, settings: ProviderSettings | None = None) -> None:
        self.settings = settings or ProviderSettings.from_environment()

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 3000,
    ) -> LLMReply:
        deployment_mode = os.getenv("LONGYUN_LLM_DEPLOYMENT_MODE", "external_api").strip().lower()
        if not self.settings.api_key and deployment_mode != "local":
            raise LLMProviderError("尚未配置隆耘统一模型服务凭证。")

        # Some compatible gateways do not consistently honor the system role.
        # Repeating the contract in the user turn keeps behavior portable to
        # the current upstream service and to a later vLLM deployment.
        payload = {
            "model": self.settings.model,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"【必须遵守的任务约束】\n{system_prompt}\n\n【本次任务】\n{user_prompt}",
                },
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
                headers = {"Content-Type": "application/json"}
                if self.settings.api_key:
                    headers["Authorization"] = f"Bearer {self.settings.api_key}"
                response = await client.post(
                    f"{self.settings.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                body: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                detail = "统一模型服务鉴权失败。"
            elif status == 429:
                detail = "统一模型服务当前请求过多，请稍后重试。"
            else:
                detail = f"统一模型服务返回异常状态 {status}。"
            raise LLMProviderError(detail) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMProviderError("暂时无法连接统一模型服务。") from exc

        choices = body.get("choices") or []
        content = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            content = str(message.get("content") or "").strip()
        if not content:
            raise LLMProviderError("模型没有返回可用的分析结果。")

        raw_usage = body.get("usage") or {}
        usage = {
            key: int(raw_usage.get(key) or 0)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        return LLMReply(
            content=content,
            model=self.settings.logical_model_alias,
            usage=usage,
        )


def build_default_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider()
