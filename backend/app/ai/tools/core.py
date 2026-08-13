"""Tool contracts and the only execution gateway available to sub-agents."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import Any, Protocol

from pydantic import BaseModel

from ..contracts import ToolResult, utc_now_iso


logger = logging.getLogger(__name__)


class ToolExecutionError(RuntimeError):
    """A safe, user-displayable tool failure without infrastructure details."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AgentToolContext:
    workflow_run_id: str
    institution_id: str
    project_id: str | None
    owner_user_id: str
    user_request: str
    evidence_context: tuple[dict[str, Any], ...] = ()
    prior_artifacts: tuple[dict[str, Any], ...] = ()


class ToolInput(BaseModel):
    query: str
    limit: int = 10


ToolHandler = Callable[[AgentToolContext, BaseModel, list[ToolResult]], ToolResult]
ArgumentBuilder = Callable[[AgentToolContext, list[ToolResult]], dict[str, Any]]


@dataclass(frozen=True)
class ControlledTool:
    code: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    build_arguments: ArgumentBuilder
    timeout_seconds: float = 60.0


class AgentToolExecutor(Protocol):
    async def execute_for_agent(
        self,
        *,
        agent_code: str,
        allowed_tool_codes: tuple[str, ...],
        context: AgentToolContext,
    ) -> list[ToolResult]: ...


@dataclass
class ControlledToolRegistry:
    """Whitelist registry; undeclared tools cannot be executed by an agent."""

    tools: dict[str, ControlledTool] = field(default_factory=dict)

    def register(self, tool: ControlledTool) -> None:
        if tool.code in self.tools:
            raise ValueError(f"工具代码重复：{tool.code}")
        self.tools[tool.code] = tool

    def validate_agent_tools(self, agent_code: str, allowed_tool_codes: tuple[str, ...]) -> None:
        missing = [code for code in allowed_tool_codes if code not in self.tools]
        if missing:
            raise ValueError(f"智能体 {agent_code} 引用了未注册工具：{', '.join(missing)}")

    async def execute_for_agent(
        self,
        *,
        agent_code: str,
        allowed_tool_codes: tuple[str, ...],
        context: AgentToolContext,
    ) -> list[ToolResult]:
        self.validate_agent_tools(agent_code, allowed_tool_codes)
        results: list[ToolResult] = []
        for code in allowed_tool_codes:
            tool = self.tools[code]
            started_at = utc_now_iso()
            try:
                arguments = tool.input_model.model_validate(
                    tool.build_arguments(context, results)
                )
                result = await asyncio.wait_for(
                    asyncio.to_thread(tool.handler, context, arguments, list(results)),
                    timeout=tool.timeout_seconds,
                )
                if result.tool_code != code:
                    raise ToolExecutionError("invalid_tool_result", "工具返回了不匹配的工具代码。")
                result.started_at = started_at
                result.completed_at = utc_now_iso()
            except asyncio.TimeoutError:
                result = ToolResult(
                    tool_code=code,
                    status="failed",
                    error_code="tool_timeout",
                    summary=f"工具 {code} 执行超时。",
                    started_at=started_at,
                    completed_at=utc_now_iso(),
                )
            except ToolExecutionError as exc:
                result = ToolResult(
                    tool_code=code,
                    status="failed",
                    error_code=exc.code,
                    summary=str(exc),
                    started_at=started_at,
                    completed_at=utc_now_iso(),
                )
            except Exception as exc:
                # Never expose SQL, credentials, file paths or driver errors to
                # the model, end user or logs.  Keep only safe diagnostic
                # dimensions and the exception class for operators.
                original_error = getattr(exc, "orig", None)
                logger.error(
                    "Controlled agent tool failed",
                    extra={
                        "agent_code": agent_code,
                        "tool_code": code,
                        "workflow_run_id": context.workflow_run_id,
                        "institution_id": context.institution_id,
                        "project_id": context.project_id,
                        "exception_type": type(exc).__name__,
                        "database_error_code": getattr(original_error, "sqlstate", None),
                    },
                )
                result = ToolResult(
                    tool_code=code,
                    status="failed",
                    error_code="tool_execution_failed",
                    summary=f"工具 {code} 暂时无法完成，请检查数据状态或联系管理员。",
                    started_at=started_at,
                    completed_at=utc_now_iso(),
                )
            results.append(result)
        return results


def default_arguments(context: AgentToolContext, _results: list[ToolResult]) -> dict[str, Any]:
    return {"query": context.user_request, "limit": 10}
