import json
import os
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import research_agent as agent
from app.research_search import PublicSearchResult, build_public_web_context


CONTEXT = build_public_web_context([PublicSearchResult("赣晚籼35号", "https://www.ricedata.cn/variety/35", "赣晚籼35号公开资料", "user_specified_site")], None)


class NativeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_only_contract_and_valid_answer(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "检索到赣晚籼35号相关资料。来源：[公开记录](https://www.ricedata.cn/variety/35)。"}}]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("httpx.AsyncClient", return_value=client):
            result = await agent._native_public_evidence_answer(api_key="test", base_url="https://model.example", model_name="test", user_prompt="找到赣晚籼35号", evidence_context="无本地数据", public_web_context=CONTEXT)
        self.assertIn("赣晚籼35号", result)
        self.assertNotIn("tools", requests[0])
        self.assertIn("Tavily", requests[0]["messages"][0]["content"])

    async def test_empty_and_protocol_rejected(self):
        for content in ["", "...", "<tool>search</tool>"]:
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})))
            with patch("httpx.AsyncClient", return_value=client):
                result = await agent._native_public_evidence_answer(api_key="test", base_url="https://model.example", model_name="test", user_prompt="搜索", evidence_context="", public_web_context=CONTEXT)
            self.assertIsNone(result)


class EmptyReactTests(unittest.IsolatedAsyncioTestCase):
    async def run_reply(self, context, native_answer=None):
        class Msg:
            def __init__(self, name, content, role):
                self.name, self.content, self.role = name, content, role
            def get_text_content(self):
                return self.content if isinstance(self.content, str) else ""
        class Memory:
            def __init__(self): self.messages = []
            async def add(self, message): self.messages.append(message)
            def state_dict(self): return {"content": [{"role": m.role, "content": m.content} for m in self.messages]}
            def load_state_dict(self, state, strict=False): pass
        class Agent:
            class CompressionConfig:
                def __init__(self, **kwargs): pass
            def __init__(self, **kwargs): pass
            def set_console_output_enabled(self, value): pass
            def set_msg_queue_enabled(self, value, queue): pass
            async def _reasoning(self, choice): return Msg("assistant", "", "assistant")
        def toolkit(**kwargs):
            kwargs["tool_trace"].update(verified_evidence_read=True, public_references_read=True)
        modules = {}
        for name, values in {
            "agentscope.agent": {"ReActAgent": Agent},
            "agentscope.memory": {"InMemoryMemory": Memory},
            "agentscope.message": {"Msg": Msg},
            "agentscope.formatter": {"OpenAIChatFormatter": lambda **kwargs: None},
            "agentscope.model": {"OpenAIChatModel": lambda **kwargs: None},
            "agentscope.token": {"CharTokenCounter": lambda: None},
        }.items():
            module = types.ModuleType(name)
            module.__dict__.update(values)
            modules[name] = module
        with patch.dict(sys.modules, modules), patch.dict(os.environ, {"SHENNONG_API_KEY": "test"}), patch.object(agent, "_build_react_toolkit", side_effect=toolkit), patch.object(agent, "_execute_controlled_react_action", new=AsyncMock()), patch.object(agent, "_native_public_evidence_answer", new=AsyncMock(return_value=native_answer)):
            return [event async for event in agent.stream_research_reply(user_prompt="找到赣晚籼35号", evidence_context="", memory_state={}, public_web_context=context)]

    async def test_empty_provider_preserves_sources_and_clean_memory(self):
        events = await self.run_reply(CONTEXT)
        complete = events[-1]
        self.assertEqual(complete["response_mode"], "public_search_evidence")
        self.assertIn("https://www.ricedata.cn/variety/35", complete["content"])
        self.assertEqual(len(complete["memory_state"]["content"]), 2)
        self.assertNotIn("System correction", json.dumps(complete["memory_state"]))

    async def test_native_recovery(self):
        events = await self.run_reply(CONTEXT, "检索到赣晚籼35号公开记录，请参照对应来源核实。")
        self.assertEqual(events[-1]["response_mode"], "public_search_native")

    async def test_no_results_status_is_not_lost(self):
        events = await self.run_reply(build_public_web_context([], "Tavily 额度受限"))
        self.assertIn("Tavily 额度受限", events[-1]["content"])

    async def test_non_web_empty_answers_still_fail(self):
        with self.assertRaises(agent.EmptyResearchAnswerError):
            await self.run_reply("")


if __name__ == "__main__":
    unittest.main()
