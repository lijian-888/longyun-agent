"""Dependency-aware LangGraph orchestration for the Longyun agent matrix."""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, Protocol, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from .contracts import (
    AgentArtifact,
    ToolResult,
    WorkflowPlanItem,
    json_schema_instruction,
    parse_structured_output,
    render_structured_output,
    utc_now_iso,
)
from .model_policy import (
    assert_model_payload_allowed,
    evidence_classifications,
    get_model_data_policy,
)
from .provider import LLMProvider, build_default_provider
from .registry import AGENT_SPECS, get_agent_spec, route_question
from .tools.core import AgentToolContext, AgentToolExecutor


class WorkflowCancelled(RuntimeError):
    """Raised at safe node boundaries after a cancellation request."""


class WorkflowState(TypedDict, total=False):
    workflow_run_id: str
    thread_id: str
    institution_id: str
    project_id: str | None
    owner_user_id: str
    user_request: str
    requested_agents: list[str]
    evidence_context: list[dict[str, Any]]
    external_transfer_acknowledged: bool
    plan: list[str]
    plan_items: list[dict[str, Any]]
    artifacts: Annotated[list[dict[str, Any]], operator.add]
    events: Annotated[list[dict[str, Any]], operator.add]
    usage_records: Annotated[list[dict[str, Any]], operator.add]
    final_content: str
    model_alias: str
    status: str


class WorkflowObserver(Protocol):
    def assert_active(self, node_code: str) -> None: ...
    def node_started(self, node_code: str, agent_version: str, contract_version: str) -> None: ...
    def node_completed(self, node_code: str, artifact: dict[str, Any]) -> None: ...
    def node_failed(self, node_code: str, error: Exception) -> None: ...


class NullWorkflowObserver:
    def assert_active(self, node_code: str) -> None:
        return None

    def node_started(self, node_code: str, agent_version: str, contract_version: str) -> None:
        return None

    def node_completed(self, node_code: str, artifact: dict[str, Any]) -> None:
        return None

    def node_failed(self, node_code: str, error: Exception) -> None:
        return None


class EmptyToolExecutor:
    """Safe fallback for isolated tests; production always injects a registry."""

    async def execute_for_agent(
        self,
        *,
        agent_code: str,
        allowed_tool_codes: tuple[str, ...],
        context: AgentToolContext,
    ) -> list[ToolResult]:
        return [
            ToolResult(
                tool_code=code,
                status="unavailable",
                summary="当前运行未配置数据库工具执行器。",
            )
            for code in allowed_tool_codes
        ]


def _event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "type": event_type,
        "created_at": utc_now_iso(),
        "payload": payload,
    }


def _evidence_text(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return "未提供已授权附件证据。"
    safe_items = [
        {
            "evidence_id": str(item.get("evidence_id") or item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "source": str(item.get("source") or ""),
            "content": str(item.get("content") or item.get("excerpt") or "")[:6000],
        }
        for item in evidence[:40]
    ]
    return json.dumps(safe_items, ensure_ascii=False, indent=2)


def _tool_result_text(results: list[ToolResult]) -> str:
    payload = []
    for result in results:
        item = result.model_dump(mode="json")
        serialized = json.dumps(item.get("data"), ensure_ascii=False, default=str)
        if len(serialized) > 12000:
            item["data"] = serialized[:12000] + "…（工具结果已按提示词预算截断，原始结果仍保留在产物元数据中）"
        payload.append(item)
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _prior_artifact_text(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts:
        return "暂无前序子智能体产物。"
    summaries = []
    for item in artifacts:
        summaries.append({
            "artifact_id": item.get("id"),
            "agent_code": item.get("agent_code"),
            "agent_name": item.get("agent_name"),
            "structured_output": item.get("structured_output") or {},
            "evidence_ids": item.get("evidence_ids") or [],
        })
    return json.dumps(summaries, ensure_ascii=False, indent=2, default=str)


def _validate_context(state: WorkflowState) -> dict[str, Any]:
    required = ("workflow_run_id", "thread_id", "institution_id", "owner_user_id", "user_request")
    missing = [field for field in required if not str(state.get(field) or "").strip()]
    if missing:
        raise ValueError(f"工作流上下文缺少字段：{', '.join(missing)}")
    return {
        "status": "running",
        "events": [_event("workflow_started", workflow_run_id=state["workflow_run_id"])],
    }


def _plan_workflow(state: WorkflowState) -> dict[str, Any]:
    plan = route_question(state["user_request"], state.get("requested_agents"))
    items = [
        WorkflowPlanItem(
            agent_code=code,
            agent_version=get_agent_spec(code).version,
            contract_version=get_agent_spec(code).contract_version,
            dependencies=list(get_agent_spec(code).dependencies),
            tool_codes=list(get_agent_spec(code).tool_codes),
        ).model_dump(mode="json")
        for code in plan
    ]
    return {
        "plan": plan,
        "plan_items": items,
        "events": [_event("workflow_planned", plan_items=items)],
    }


def _allowed_evidence_ids(
    state: WorkflowState,
    tool_results: list[ToolResult],
) -> set[str]:
    identifiers = {
        str(item.get("evidence_id") or item.get("id"))
        for item in state.get("evidence_context", [])
        if item.get("evidence_id") or item.get("id")
    }
    for result in tool_results:
        identifiers.update(item.evidence_id for item in result.evidence)
    return identifiers


def _specialist_node(
    agent_code: str,
    provider: LLMProvider,
    tool_executor: AgentToolExecutor,
    observer: WorkflowObserver,
):
    spec = get_agent_spec(agent_code)

    async def run(state: WorkflowState) -> dict[str, Any]:
        if agent_code not in state.get("plan", []):
            return {}
        observer.assert_active(agent_code)
        observer.node_started(agent_code, spec.version, spec.contract_version)
        try:
            tool_context = AgentToolContext(
                workflow_run_id=state["workflow_run_id"],
                institution_id=state["institution_id"],
                project_id=state.get("project_id"),
                owner_user_id=state["owner_user_id"],
                user_request=state["user_request"],
                evidence_context=tuple(state.get("evidence_context", [])),
                prior_artifacts=tuple(state.get("artifacts", [])),
            )
            tool_results = await tool_executor.execute_for_agent(
                agent_code=agent_code,
                allowed_tool_codes=spec.tool_codes,
                context=tool_context,
            )
            classifications = evidence_classifications(state.get("evidence_context", []))
            classifications.add("desensitized")
            classifications.update(
                result.data_classification
                for result in tool_results
                if result.status == "completed" and (result.data or result.evidence)
            )
            assert_model_payload_allowed(
                get_model_data_policy(),
                acknowledged=bool(state.get("external_transfer_acknowledged")),
                classifications=classifications,
                context_label=f"{spec.name}的模型输入",
            )
            allowed_evidence_ids = _allowed_evidence_ids(state, tool_results)
            prompt = (
                f"机构隔离标识：{state['institution_id']}\n"
                f"课题标识：{state.get('project_id') or '机构级任务'}\n"
                f"用户任务：{state['user_request']}\n\n"
                "【已授权附件证据】\n"
                f"{_evidence_text(state.get('evidence_context', []))}\n\n"
                "【受控工具执行结果】\n"
                f"{_tool_result_text(tool_results)}\n\n"
                "【前序智能体产物（结构化）】\n"
                f"{_prior_artifact_text(state.get('artifacts', []))}\n\n"
                "只允许引用上面出现的证据编号。统计值只能引用受控工具结果。"
                "不得把 unavailable、not_applicable 或 failed 的工具当成已完成。\n"
                "仅返回一个JSON对象，不要输出Markdown围栏。JSON结构必须为：\n"
                f"{json_schema_instruction()}"
            )
            reply = await provider.complete(
                system_prompt=spec.system_prompt,
                user_prompt=prompt,
                temperature=0.1,
                max_tokens=3200,
            )
            structured = parse_structured_output(reply.content, allowed_evidence_ids)
            artifact = AgentArtifact(
                agent_code=agent_code,
                agent_name=spec.name,
                agent_version=spec.version,
                contract_version=spec.contract_version,
                content=render_structured_output(structured),
                structured_output=structured,
                tool_results=tool_results,
                evidence_ids=structured.evidence_ids,
                model_alias=reply.model,
            ).model_dump(mode="json")
            observer.node_completed(agent_code, artifact)
            return {
                "artifacts": [artifact],
                "usage_records": [{"agent_code": agent_code, **reply.usage}],
                "events": [_event(
                    "agent_completed",
                    agent_code=agent_code,
                    agent_version=spec.version,
                    contract_version=spec.contract_version,
                    artifact_id=artifact["id"],
                    tool_statuses={item.tool_code: item.status for item in tool_results},
                )],
            }
        except Exception as exc:
            observer.node_failed(agent_code, exc)
            raise

    return run


async def _synthesize(
    state: WorkflowState,
    provider: LLMProvider,
    observer: WorkflowObserver,
) -> dict[str, Any]:
    observer.assert_active("synthesize")
    artifacts = state.get("artifacts", [])
    if not artifacts:
        return {
            "final_content": "当前任务没有得到可用的子智能体分析结果，请检查工具和模型服务。",
            "status": "completed",
            "events": [_event("workflow_completed", artifact_count=0)],
        }
    if len(artifacts) == 1:
        return {
            "final_content": str(artifacts[0].get("content") or ""),
            "status": "completed",
            "model_alias": str(artifacts[0].get("model_alias") or ""),
            "usage_records": [],
            "events": [_event("workflow_completed", artifact_count=1)],
        }
    synthesis_classifications = {"desensitized"}
    for artifact in artifacts:
        for result in artifact.get("tool_results") or []:
            if result.get("status") == "completed" and (result.get("data") or result.get("evidence")):
                synthesis_classifications.add(
                    str(result.get("data_classification") or "institution_private")
                )
    assert_model_payload_allowed(
        get_model_data_policy(),
        acknowledged=bool(state.get("external_transfer_acknowledged")),
        classifications=synthesis_classifications,  # type: ignore[arg-type]
        context_label="总控智能体汇总输入",
    )
    reply = await provider.complete(
        system_prompt=(
            "你是隆耘总控智能体。只汇总子智能体的结构化产物，不新增材料、数值或来源。"
            "冲突结论必须并列展示；明确缺失数据、不确定性和下一步验证。"
        ),
        user_prompt=(
            f"用户任务：{state['user_request']}\n\n"
            f"子智能体结构化产物：\n{_prior_artifact_text(artifacts)}\n\n"
            "请以清晰的中文Markdown输出综合答复，并保留智能体名称和证据编号。"
        ),
        temperature=0.05,
        max_tokens=4200,
    )
    return {
        "final_content": reply.content,
        "status": "completed",
        "model_alias": reply.model,
        "usage_records": [{"agent_code": "orchestrator", **reply.usage}],
        "events": [_event("workflow_completed", artifact_count=len(artifacts))],
    }


def build_workflow_graph(
    provider: LLMProvider | None = None,
    *,
    tool_executor: AgentToolExecutor | None = None,
    observer: WorkflowObserver | None = None,
    checkpointer: Any | None = None,
):
    """Build the audited graph while keeping route choice deterministic."""
    runtime_provider = provider or build_default_provider()
    runtime_tools = tool_executor or EmptyToolExecutor()
    runtime_observer = observer or NullWorkflowObserver()
    builder = StateGraph(WorkflowState)
    builder.add_node("validate_context", _validate_context)
    builder.add_node("plan_workflow", _plan_workflow)
    for code in AGENT_SPECS:
        builder.add_node(
            code,
            _specialist_node(code, runtime_provider, runtime_tools, runtime_observer),
        )

    async def synthesize_node(state: WorkflowState) -> dict[str, Any]:
        return await _synthesize(state, runtime_provider, runtime_observer)

    builder.add_node("synthesize", synthesize_node)
    builder.add_edge(START, "validate_context")
    builder.add_edge("validate_context", "plan_workflow")
    builder.add_edge("plan_workflow", "germplasm_analysis")
    builder.add_edge("plan_workflow", "research_intelligence")
    builder.add_edge(["germplasm_analysis", "research_intelligence"], "parent_combination")
    builder.add_edge("parent_combination", "trial_analysis")
    builder.add_edge("trial_analysis", "synthesize")
    builder.add_edge("synthesize", END)
    return builder.compile(checkpointer=checkpointer)


def agent_versions() -> dict[str, str]:
    return {code: spec.version for code, spec in AGENT_SPECS.items()}
