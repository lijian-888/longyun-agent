import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ai.model_policy import (
    ModelDataPolicyError,
    assert_model_payload_allowed,
    get_model_data_policy,
)


class ModelDataPolicyTests(unittest.TestCase):
    def policy(self, **values: str):
        environment = {
            "LONGYUN_LLM_DEPLOYMENT_MODE": "external_api",
            "LONGYUN_DATA_ENVIRONMENT": "sandbox_desensitized",
            "LONGYUN_LLM_BASE_URL": "https://open.cherryin.ai/v1",
            "LONGYUN_LLM_PROVIDER_NAME": "CherryIn",
            "LONGYUN_ALLOW_EXTERNAL_PRIVATE_EVIDENCE": "false",
            "LONGYUN_ALLOW_EXTERNAL_WEB_SEARCH": "false",
            **values,
        }
        with patch.dict(os.environ, environment, clear=False):
            return get_model_data_policy()

    def test_external_sandbox_uses_environment_level_acknowledgement(self) -> None:
        assert_model_payload_allowed(
            self.policy(),
            acknowledged=False,
            classifications=("desensitized",),
        )

    def test_desensitized_sandbox_allows_access_scoped_material_after_ack(self) -> None:
        assert_model_payload_allowed(
            self.policy(),
            acknowledged=True,
            classifications=("institution_private",),
        )

    def test_non_sandbox_external_mode_blocks_private_material(self) -> None:
        with self.assertRaises(ModelDataPolicyError):
            assert_model_payload_allowed(
                self.policy(LONGYUN_DATA_ENVIRONMENT="institution_private"),
                acknowledged=True,
                classifications=("institution_private",),
            )

    def test_non_sandbox_external_mode_still_requires_acknowledgement(self) -> None:
        with self.assertRaises(ModelDataPolicyError):
            assert_model_payload_allowed(
                self.policy(LONGYUN_DATA_ENVIRONMENT="institution_private"),
                acknowledged=False,
                classifications=("public",),
            )

    def test_local_model_allows_private_material_without_external_ack(self) -> None:
        assert_model_payload_allowed(
            self.policy(
                LONGYUN_LLM_DEPLOYMENT_MODE="local",
                LONGYUN_DATA_ENVIRONMENT="institution_private",
            ),
            acknowledged=False,
            classifications=("institution_private",),
        )

    def test_public_policy_does_not_expose_api_key(self) -> None:
        with patch.dict(os.environ, {"LONGYUN_LLM_API_KEY": "must-not-leak"}):
            public = self.policy().public_view()
        self.assertNotIn("api_key", public)
        self.assertEqual(public["provider_host"], "open.cherryin.ai")

    def test_current_cherryin_api_host_is_identified_without_exposing_url_path(self) -> None:
        policy = self.policy(
            LONGYUN_LLM_BASE_URL="https://open.cherryin.net/v1",
            LONGYUN_LLM_PROVIDER_NAME="",
        )
        self.assertEqual(policy.provider_name, "CherryIn")
        self.assertEqual(policy.provider_host, "open.cherryin.net")


class LegacyChatPolicyRegressionTests(unittest.TestCase):
    def test_legacy_chat_checks_policy_before_evidence_and_model_calls(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "app" / "main.py"
        ).read_text(encoding="utf-8")
        start = source.index("async def research_chat_stream(")
        end = source.index("@app.get(\"/api/research/messages/", start)
        function_source = source[start:end]
        first_policy_check = function_source.index("assert_model_payload_allowed(")
        first_evidence_build = function_source.index("build_published_evidence_context(")
        self.assertLess(first_policy_check, first_evidence_build)
        self.assertIn("if not model_policy.history_allowed", function_source)
        self.assertIn("external_data_acknowledged", function_source)


if __name__ == "__main__":
    unittest.main()
