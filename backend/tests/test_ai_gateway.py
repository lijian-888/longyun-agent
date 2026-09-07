import asyncio
import logging
import os
import unittest
from unittest.mock import patch

from app.ai_gateway import (
    AIEgressBlockedError,
    AIGatewayConcurrencyGate,
    SecretRedactionFilter,
    prepare_egress,
    provider_settings,
)


class AIProviderSettingsTests(unittest.TestCase):
    def test_shennong_credentials_are_read_only_from_server_environment(self):
        with patch.dict(os.environ, {
            "AI_PROVIDER": "shennong",
            "SHENNONG_API_KEY": "server-only-secret",
            "SHENNONG_MODEL": "sn-test",
        }, clear=False):
            settings = provider_settings()
        self.assertTrue(settings.external)
        self.assertEqual(settings.model, "sn-test")
        self.assertEqual(settings.api_key, "server-only-secret")

    def test_vllm_uses_same_interface_without_requiring_external_key(self):
        with patch.dict(os.environ, {
            "AI_PROVIDER": "vllm",
            "VLLM_API_BASE_URL": "http://model.internal/v1",
            "VLLM_MODEL": "rice-local",
            "VLLM_API_KEY": "",
        }, clear=False):
            settings = provider_settings()
        self.assertFalse(settings.external)
        self.assertEqual(settings.base_url, "http://model.internal/v1")
        self.assertEqual(settings.model, "rice-local")


class AIEgressPolicyTests(unittest.TestCase):
    def setUp(self):
        self.external = type("Provider", (), {
            "external": True,
            "api_key": "upstream-secret-123",
        })()
        self.local = type("Provider", (), {"external": False, "api_key": ""})()

    def test_direct_identifiers_are_desensitized_before_external_call(self):
        decision = prepare_egress(
            ["联系人 13437975781，邮箱 owner@example.com，证件 11010519491231002X"],
            provider=self.external,
        )
        self.assertEqual(decision.classification, "desensitized")
        self.assertEqual(decision.redactions, 3)
        self.assertNotIn("13437975781", decision.texts[0])
        self.assertNotIn("owner@example.com", decision.texts[0])

    def test_credentials_and_confidential_markers_are_blocked(self):
        for value in ("password=plain-secret", "这是内部资料，不得外传", "upstream-secret-123"):
            with self.subTest(value=value), self.assertRaises(AIEgressBlockedError):
                prepare_egress([value], provider=self.external)

    def test_private_attachment_is_blocked_for_external_but_allowed_locally(self):
        with self.assertRaises(AIEgressBlockedError):
            prepare_egress(["普通问题"], provider=self.external, contains_private_material=True)
        decision = prepare_egress(["私人试验记录"], provider=self.local, contains_private_material=True)
        self.assertEqual(decision.classification, "private_local")

    def test_log_filter_removes_configured_key(self):
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed upstream-secret-123", (), None)
        with patch.dict(os.environ, {"SHENNONG_API_KEY": "upstream-secret-123"}, clear=False):
            self.assertTrue(SecretRedactionFilter().filter(record))
        self.assertNotIn("upstream-secret-123", record.getMessage())


class AIConcurrencyGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_parallelism_is_four_and_namespaces_do_not_mix(self):
        gate = AIGatewayConcurrencyGate(4)
        running = 0
        observed = 0
        lock = asyncio.Lock()

        async def work(index: int) -> tuple[str, str]:
            nonlocal running, observed
            task_id = f"task-{index}"
            namespace = "institution-a" if index % 2 == 0 else "institution-b"
            await gate.acquire(task_id, namespace, 2)
            try:
                self.assertEqual(gate.active_namespaces()[task_id], namespace)
                async with lock:
                    running += 1
                    observed = max(observed, running)
                await asyncio.sleep(0.02)
                return task_id, namespace
            finally:
                async with lock:
                    running -= 1
                gate.release(task_id)

        results = await asyncio.gather(*(work(index) for index in range(12)))
        self.assertEqual(len(results), 12)
        self.assertEqual(observed, 4)
        self.assertEqual(gate.max_observed, 4)
        self.assertEqual(gate.active_namespaces(), {})


if __name__ == "__main__":
    unittest.main()
