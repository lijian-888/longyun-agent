import json
import unittest
from unittest.mock import patch

from app.ai.provider import ProviderSettings
from app.research_agent import (
    _build_plain_evidence_messages,
    _extract_openai_answer_text,
    _stream_plain_evidence_reply,
)


class ResearchAgentCompatibleResponseTests(unittest.TestCase):
    def test_extracts_text_from_string_and_block_content(self) -> None:
        self.assertEqual(
            _extract_openai_answer_text(
                {"choices": [{"delta": {"content": "流式正文"}}]},
            ),
            "流式正文",
        )
        self.assertEqual(
            _extract_openai_answer_text(
                {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "text", "text": "最终"},
                                    {"type": "text", "text": {"value": "正文"}},
                                ],
                            },
                        },
                    ],
                },
            ),
            "最终正文",
        )

    def test_plain_evidence_request_contains_no_synthetic_tool_roles(self) -> None:
        messages = _build_plain_evidence_messages(
            user_prompt="土壤 pH 与产量有什么关联？",
            evidence_context="课题 A 的已核验表型记录。",
            public_web_context="",
            conversation_history=[
                {"role": "user", "content": "上一问"},
                {"role": "assistant", "content": "上一答"},
                {"role": "system", "content": "不得转发的内部消息"},
            ],
        )
        self.assertEqual([item["role"] for item in messages], ["system", "user", "assistant", "user"])
        self.assertNotIn("tool", {item["role"] for item in messages})
        self.assertIn("土壤 pH 与产量有什么关联", messages[-1]["content"])
        self.assertIn("课题 A 的已核验表型记录", messages[-1]["content"])
        self.assertIn("不得声称已经联网检索", messages[-1]["content"])


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _FakeClient:
    def __init__(self, *, fallback_text: str) -> None:
        self.fallback_text = fallback_text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def stream(self, *args, **kwargs):
        placeholder = json.dumps(
            {"choices": [{"delta": {"content": "..."}}]},
            ensure_ascii=False,
        )
        return _FakeStreamResponse([f"data: {placeholder}", "data: [DONE]"])

    async def post(self, *args, **kwargs):
        fallback_text = self.fallback_text

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"choices": [{"message": {"content": fallback_text}}]}

        return _Response()


class ResearchAgentFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_placeholder_stream_retries_with_non_streaming_completion(self) -> None:
        answer = "土壤 pH 会影响有效磷的形态与根系吸收，需结合降雨和试验设计验证其对产量构成的影响。"
        settings = ProviderSettings(
            base_url="https://model.example/v1",
            api_key="test-key",
            model="test-model",
            logical_model_alias="test",
            timeout_seconds=30,
        )
        fake_client = _FakeClient(fallback_text=answer)
        with (
            patch("app.research_agent.ProviderSettings.from_environment", return_value=settings),
            patch("httpx.AsyncClient", return_value=fake_client),
        ):
            events = [
                item
                async for item in _stream_plain_evidence_reply(
                    api_key="test-key",
                    user_prompt="土壤 pH 与产量有什么关联？",
                    evidence_context="无课题观测记录。",
                    public_web_context="",
                    conversation_history=[],
                )
            ]

        self.assertEqual(events, [{"type": "token", "text": answer}])


if __name__ == "__main__":
    unittest.main()
