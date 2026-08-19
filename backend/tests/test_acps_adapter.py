import asyncio
import uuid
import unittest
from dataclasses import replace
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI

from acps_sdk.acs import AgentCapabilitySpec
from acps_sdk.aip import TaskCommand, TaskCommandType, TaskState, TextDataItem
from acps_sdk.aip.aip_rpc_server import TaskManager

from app.acps_adapter import (
    AcpsExecutionResult,
    AcpsSettings,
    build_acs_document,
    mount_acps_routes,
)


def _settings(**changes):
    return replace(
        AcpsSettings.from_env(),
        enabled=True,
        role="partner",
        aic="partner-aic",
        public_base_url="https://longyun.example.cn",
        documentation_url="https://longyun.example.cn/acps/health",
        rpc_url="https://longyun.example.cn:9443/acps/rpc",
        **changes,
    )


def _command(command_type: TaskCommandType, task_id: str, text: str | None = None) -> TaskCommand:
    return TaskCommand(
        id=f"command-{uuid.uuid4()}",
        sentAt=datetime.now(timezone.utc).isoformat(),
        senderRole="leader",
        senderId="leader-aic",
        sessionId="session-1",
        taskId=task_id,
        command=command_type,
        dataItems=[TextDataItem(text=text)] if text is not None else None,
    )


def _rpc_body(command: TaskCommand) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": f"rpc-{uuid.uuid4()}",
        "method": "rpc",
        "params": {"command": command.model_dump(mode="json", exclude_none=True)},
    }


class AcpsAcsTests(unittest.TestCase):
    def test_partner_and_leader_acs_validate_with_sdk(self):
        settings = _settings(mtls_enabled=True, certificate_dns_names=("longyun.example.cn",))
        partner = build_acs_document(settings, "partner")
        leader = build_acs_document(settings, "leader")

        AgentCapabilitySpec.from_dict(partner)
        AgentCapabilitySpec.from_dict(leader)
        self.assertEqual(partner["protocolVersion"], "02.01")
        self.assertEqual(partner["endPoints"][0]["transport"], "JSONRPC")
        self.assertEqual(partner["certificate"]["altNames"]["dns"], ["longyun.example.cn"])
        self.assertEqual(leader["endPoints"], [])
        self.assertEqual(leader["skills"], [])


class AcpsPartnerRpcTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        TaskManager._tasks.clear()

    async def asyncTearDown(self):
        await asyncio.sleep(0)
        TaskManager._tasks.clear()

    async def test_full_direct_aip_state_machine(self):
        calls = []

        async def execute(prompt: str, caller_aic: str) -> AcpsExecutionResult:
            calls.append((prompt, caller_aic))
            await asyncio.sleep(0.02)
            return AcpsExecutionResult(
                text=f"已分析：{prompt}",
                structured_data={"dataBoundary": "published-standard-data-only"},
            )

        app = FastAPI()
        mount_acps_routes(app, _settings(), execute)
        transport = httpx.ASGITransport(app=app)
        task_id = "task-complete"

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            start = await client.post(
                "/acps/rpc",
                json=_rpc_body(_command(TaskCommandType.Start, task_id, "比较候选材料产量")),
            )
            self.assertEqual(start.status_code, 200)
            self.assertIn(start.json()["result"]["status"]["state"], {"accepted", "working"})

            state = "accepted"
            result = None
            for _ in range(30):
                await asyncio.sleep(0.01)
                response = await client.post(
                    "/acps/rpc",
                    json=_rpc_body(_command(TaskCommandType.Get, task_id)),
                )
                result = response.json()["result"]
                state = result["status"]["state"]
                if state == "awaiting-completion":
                    break

            self.assertEqual(state, "awaiting-completion")
            self.assertEqual(result["products"][0]["dataItems"][0]["text"], "已分析：比较候选材料产量")
            self.assertEqual(result["senderId"], "partner-aic")

            complete = await client.post(
                "/acps/rpc",
                json=_rpc_body(_command(TaskCommandType.Complete, task_id)),
            )
            self.assertEqual(complete.json()["result"]["status"]["state"], "completed")

        self.assertEqual(calls, [("比较候选材料产量", "leader-aic")])

    async def test_missing_input_continue_and_cancel(self):
        gate = asyncio.Event()

        async def execute(prompt: str, caller_aic: str) -> AcpsExecutionResult:
            await gate.wait()
            return AcpsExecutionResult(text=prompt)

        app = FastAPI()
        mount_acps_routes(app, _settings(), execute)
        transport = httpx.ASGITransport(app=app)
        task_id = "task-cancel"

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            start = await client.post(
                "/acps/rpc",
                json=_rpc_body(_command(TaskCommandType.Start, task_id)),
            )
            self.assertEqual(start.json()["result"]["status"]["state"], "awaiting-input")

            continued = await client.post(
                "/acps/rpc",
                json=_rpc_body(_command(TaskCommandType.Continue, task_id, "补充问题")),
            )
            self.assertEqual(continued.json()["result"]["status"]["state"], "accepted")

            canceled = await client.post(
                "/acps/rpc",
                json=_rpc_body(_command(TaskCommandType.Cancel, task_id)),
            )
            self.assertEqual(canceled.json()["result"]["status"]["state"], "canceled")

    async def test_mtls_proxy_identity_must_match_sender(self):
        async def execute(prompt: str, caller_aic: str) -> AcpsExecutionResult:
            return AcpsExecutionResult(text=prompt)

        app = FastAPI()
        mount_acps_routes(app, _settings(require_verified_client=True), execute)
        transport = httpx.ASGITransport(app=app)
        body = _rpc_body(_command(TaskCommandType.Start, "task-identity", "问题"))

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.post("/acps/rpc", json=body)
            self.assertEqual(missing.status_code, 401)

            mismatch = await client.post(
                "/acps/rpc",
                json=body,
                headers={"X-ACPS-Client-AIC": "different-leader"},
            )
            self.assertEqual(mismatch.status_code, 403)

            accepted = await client.post(
                "/acps/rpc",
                json=body,
                headers={"X-ACPS-Client-AIC": "leader-aic"},
            )
            self.assertEqual(accepted.status_code, 200)

            other_command = _command(TaskCommandType.Get, "task-identity")
            other_command.senderId = "other-leader"
            forbidden = await client.post(
                "/acps/rpc",
                json=_rpc_body(other_command),
                headers={"X-ACPS-Client-AIC": "other-leader"},
            )
            self.assertEqual(forbidden.status_code, 403)


if __name__ == "__main__":
    unittest.main()
