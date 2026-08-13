import asyncio
import json
import os
import unittest
from unittest.mock import patch

from pydantic import BaseModel

from app.ai.contracts import EvidenceReference, ToolResult, parse_structured_output
from app.ai.model_policy import ModelDataPolicyError
from app.ai.provider import LLMReply
from app.ai.registry import route_question
from app.ai.tools.core import AgentToolContext, ControlledTool, ControlledToolRegistry
from app.ai.workflow import build_workflow_graph


class FakeProvider:
    def __init__(self, *, evidence_ids: list[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.evidence_ids = evidence_ids or []

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 3000,
    ) -> LLMReply:
        self.calls.append((system_prompt, user_prompt))
        if "总控智能体" in system_prompt:
            content = "综合答复"
        else:
            content = json.dumps(
                {
                    "conclusions": [f"受控回答-{len(self.calls)}"],
                    "recommendations": ["开展下一轮验证"],
                    "evidence_ids": self.evidence_ids,
                    "missing_data": [],
                    "uncertainties": ["仅适用于当前证据范围"],
                    "next_steps": ["人工复核"],
                    "confidence": 0.72,
                },
                ensure_ascii=False,
            )
        return LLMReply(
            content=content,
            model="test-local-model",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )


class FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def execute_for_agent(
        self,
        *,
        agent_code: str,
        allowed_tool_codes: tuple[str, ...],
        context: AgentToolContext,
    ) -> list[ToolResult]:
        self.calls.append((agent_code, allowed_tool_codes))
        return [
            ToolResult(
                tool_code=code,
                status="completed",
                summary="受控工具完成",
                data={"agent_code": agent_code},
                evidence=[
                    EvidenceReference(
                        evidence_id=f"tool:{agent_code}:{code}",
                        title=code,
                        source="test-controlled-tool",
                    )
                ],
            )
            for code in allowed_tool_codes
        ]


def initial_state(question: str, requested_agents: list[str] | None = None) -> dict:
    return {
        "workflow_run_id": "run-1",
        "thread_id": "thread-1",
        "institution_id": "institution-a",
        "project_id": "project-a",
        "owner_user_id": "user-a",
        "user_request": question,
        "requested_agents": requested_agents or [],
        "external_transfer_acknowledged": True,
        "evidence_context": [
            {
                "evidence_id": "attachment:e-1",
                "title": "trial.csv",
                "source": "private_attachment",
                "content": "受控数据",
            }
        ],
        "artifacts": [],
        "events": [],
        "usage_records": [],
    }


class AgentRoutingTests(unittest.TestCase):
    def test_parent_route_adds_germplasm_dependency(self) -> None:
        self.assertEqual(
            route_question("请根据亲缘和花期辅助推荐杂交亲本组合"),
            ["germplasm_analysis", "parent_combination"],
        )

    def test_explicit_parent_selection_cannot_bypass_dependency(self) -> None:
        self.assertEqual(
            route_question("任意问题", ["parent_combination"]),
            ["germplasm_analysis", "parent_combination"],
        )

    def test_explicit_agent_selection_is_bounded_and_stable(self) -> None:
        self.assertEqual(
            route_question("任意问题", ["trial_analysis", "germplasm_analysis"]),
            ["germplasm_analysis", "trial_analysis"],
        )


class AgentContractTests(unittest.TestCase):
    def test_unknown_model_citations_are_removed(self) -> None:
        output = parse_structured_output(
            json.dumps(
                {
                    "conclusions": ["结论"],
                    "evidence_ids": ["allowed", "hallucinated"],
                    "confidence": 0.8,
                },
                ensure_ascii=False,
            ),
            {"allowed"},
        )
        self.assertEqual(output.evidence_ids, ["allowed"])

    def test_plain_text_falls_back_to_unverified_contract(self) -> None:
        output = parse_structured_output("未结构化回答", set())
        self.assertEqual(output.conclusions, ["未结构化回答"])
        self.assertIsNone(output.confidence)
        self.assertTrue(output.uncertainties)


class AgentToolGatewayTests(unittest.TestCase):
    class Input(BaseModel):
        query: str

    def test_undeclared_tool_is_rejected_before_execution(self) -> None:
        registry = ControlledToolRegistry()
        context = AgentToolContext(
            workflow_run_id="run-1",
            institution_id="institution-a",
            project_id=None,
            owner_user_id="user-a",
            user_request="test",
        )
        with self.assertRaises(ValueError):
            asyncio.run(
                registry.execute_for_agent(
                    agent_code="test-agent",
                    allowed_tool_codes=("not-registered",),
                    context=context,
                )
            )

    def test_tool_failure_is_sanitized(self) -> None:
        registry = ControlledToolRegistry()

        def handler(_context, _input, _prior):
            raise RuntimeError("postgresql://secret-password@database")

        registry.register(
            ControlledTool(
                code="safe-tool",
                description="test",
                input_model=self.Input,
                handler=handler,
                build_arguments=lambda context, _results: {"query": context.user_request},
            )
        )
        context = AgentToolContext(
            workflow_run_id="run-1",
            institution_id="institution-a",
            project_id=None,
            owner_user_id="user-a",
            user_request="test",
        )
        with self.assertLogs("app.ai.tools.core", level="ERROR") as captured:
            results = asyncio.run(
                registry.execute_for_agent(
                    agent_code="test-agent",
                    allowed_tool_codes=("safe-tool",),
                    context=context,
                )
            )
        self.assertEqual(results[0].error_code, "tool_execution_failed")
        self.assertNotIn("secret-password", results[0].summary)
        self.assertNotIn("secret-password", "\n".join(captured.output))


class AgentWorkflowTests(unittest.TestCase):
    def test_single_agent_executes_only_its_contract_tools(self) -> None:
        provider = FakeProvider(evidence_ids=["attachment:e-1", "hallucinated"])
        tools = FakeToolExecutor()
        graph = build_workflow_graph(provider, tool_executor=tools)
        result = asyncio.run(
            graph.ainvoke(initial_state("分析多年多点试验", ["trial_analysis"]))
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["plan"], ["trial_analysis"])
        self.assertEqual(len(result["artifacts"]), 1)
        self.assertEqual(result["artifacts"][0]["agent_code"], "trial_analysis")
        self.assertEqual(result["artifacts"][0]["evidence_ids"], ["attachment:e-1"])
        self.assertEqual(tools.calls[0][0], "trial_analysis")
        self.assertEqual(len(provider.calls), 1)

    def test_cross_agent_workflow_preserves_artifacts_and_synthesizes(self) -> None:
        provider = FakeProvider()
        tools = FakeToolExecutor()
        graph = build_workflow_graph(provider, tool_executor=tools)
        result = asyncio.run(
            graph.ainvoke(
                initial_state(
                    "结合种质基因、文献、亲本组合和田间试验给出建议",
                    [
                        "germplasm_analysis",
                        "research_intelligence",
                        "parent_combination",
                        "trial_analysis",
                    ],
                )
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["artifacts"]), 4)
        self.assertEqual(
            {artifact["agent_code"] for artifact in result["artifacts"]},
            {
                "germplasm_analysis",
                "research_intelligence",
                "parent_combination",
                "trial_analysis",
            },
        )
        self.assertEqual(len(provider.calls), 5)
        parent_prompt = next(
            prompt
            for system, prompt in provider.calls
            if "亲本配组智能体" in system
        )
        self.assertIn("前序智能体产物", parent_prompt)
        self.assertIn("germplasm_analysis", parent_prompt)

    def test_private_tool_output_cannot_bypass_strict_external_policy(self) -> None:
        provider = FakeProvider()
        tools = FakeToolExecutor()
        graph = build_workflow_graph(provider, tool_executor=tools)
        state = initial_state("分析机构私有试验数据", ["trial_analysis"])
        state["evidence_context"] = [{
            "evidence_id": "public:e-1",
            "title": "published.csv",
            "source": "published_standard_data",
            "data_classification": "public",
            "content": "published data",
        }]
        strict_external = {
            "LONGYUN_LLM_DEPLOYMENT_MODE": "external_api",
            "LONGYUN_DATA_ENVIRONMENT": "institution_private",
            "LONGYUN_ALLOW_EXTERNAL_PRIVATE_EVIDENCE": "false",
        }
        with patch.dict(os.environ, strict_external, clear=False):
            with self.assertRaises(ModelDataPolicyError):
                asyncio.run(graph.ainvoke(state))
        self.assertEqual(provider.calls, [])


if __name__ == "__main__":
    unittest.main()
