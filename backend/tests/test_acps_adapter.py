import asyncio
import uuid
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from acps_sdk.acs import AgentCapabilitySpec
from acps_sdk.aip import (
    TaskCommand,
    TaskCommandType,
    TaskResult,
    TaskState,
    TaskStatus,
    TextDataItem,
)
from acps_sdk.aip.aip_rpc_server import TaskManager

from app.acps_adapter import (
    AcpsExecutionResult,
    AcpsDirectLeaderRuntime,
    AcpsGroupCreateRequest,
    AcpsGroupLeaderRuntime,
    AcpsGroupMemberCommandRequest,
    AcpsGroupPartnerRequest,
    AcpsGroupTaskCommandRequest,
    AcpsGroupTaskRequest,
    AcpsLeaderDispatchRequest,
    AcpsLeaderTaskCommandRequest,
    AcpsSettings,
    build_acs_document,
    mount_acps_routes,
    validate_partner_url,
)


def _settings(**changes):
    values = {
        "enabled": True,
        "role": "partner",
        "aic": "partner-aic",
        "public_base_url": "https://longyun.example.cn",
        "documentation_url": "https://longyun.example.cn/acps/health",
        "rpc_url": "https://longyun.example.cn:9443/acps/rpc",
    }
    values.update(changes)
    return replace(
        AcpsSettings.from_env(),
        **values,
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


def _task_result(task_id: str, session_id: str, state: TaskState) -> TaskResult:
    now = datetime.now(timezone.utc).isoformat()
    return TaskResult(
        id=f"result-{uuid.uuid4()}",
        sentAt=now,
        senderRole="partner",
        senderId="partner-aic",
        sessionId=session_id,
        taskId=task_id,
        status=TaskStatus(state=state, stateChangedAt=now),
    )


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


class FakeAipRpcClient:
    calls: list[tuple] = []

    def __init__(self, partner_url: str, leader_id: str, ssl_context=None):
        self.partner_url = partner_url
        self.leader_id = leader_id

    async def start_task(self, session_id: str, query: str, task_id: str | None = None):
        task_id = task_id or "task-generated"
        self.calls.append(("start", task_id, session_id, query))
        return _task_result(task_id, session_id, TaskState.AwaitingInput)

    async def get_task(self, task_id: str, session_id: str):
        self.calls.append(("get", task_id, session_id))
        return _task_result(task_id, session_id, TaskState.AwaitingInput)

    async def continue_task(self, task_id: str, session_id: str, query: str):
        self.calls.append(("continue", task_id, session_id, query))
        return _task_result(task_id, session_id, TaskState.AwaitingCompletion)

    async def complete_task(self, task_id: str, session_id: str):
        self.calls.append(("complete", task_id, session_id))
        return _task_result(task_id, session_id, TaskState.Completed)

    async def cancel_task(self, task_id: str, session_id: str):
        self.calls.append(("cancel", task_id, session_id))
        return _task_result(task_id, session_id, TaskState.Canceled)

    async def close(self):
        self.calls.append(("close",))


class AcpsDirectLeaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeAipRpcClient.calls.clear()
        self.settings = _settings(
            role="leader",
            aic="leader-aic",
            allowed_partner_hosts=("partner.example.cn",),
        )

    def test_partner_url_allowlist_and_credentials(self):
        self.assertEqual(
            validate_partner_url("https://partner.example.cn/acps/rpc", self.settings),
            "https://partner.example.cn/acps/rpc",
        )
        with self.assertRaises(ValueError):
            validate_partner_url("https://other.example.cn/acps/rpc", self.settings)
        with self.assertRaises(ValueError):
            validate_partner_url("https://user:secret@partner.example.cn/acps/rpc", self.settings)

    async def test_dispatch_continue_complete_and_owner_isolation(self):
        runtime = AcpsDirectLeaderRuntime(self.settings)
        with patch("app.acps_adapter.AipRpcClient", FakeAipRpcClient):
            dispatched = await runtime.dispatch(
                AcpsLeaderDispatchRequest(
                    query="初始任务",
                    partner_url="https://partner.example.cn/acps/rpc",
                    partner_aic="partner-aic",
                    task_id="task-1",
                    session_id="session-1",
                    auto_complete=False,
                ),
                owner_id="user-1",
                project_id="project-1",
            )
            self.assertEqual(
                dispatched["task"]["status"]["state"], "awaiting-input"
            )

            fetched = await runtime.command(
                "task-1",
                AcpsLeaderTaskCommandRequest(command="get"),
                owner_id="user-1",
                project_id="project-1",
            )
            self.assertEqual(fetched["task"]["status"]["state"], "awaiting-input")

            continued = await runtime.command(
                "task-1",
                AcpsLeaderTaskCommandRequest(
                    command="continue", query="补充信息", auto_complete=True
                ),
                owner_id="user-1",
                project_id="project-1",
            )
            self.assertEqual(continued["task"]["status"]["state"], "completed")
            self.assertIn(
                ("continue", "task-1", "session-1", "补充信息"),
                FakeAipRpcClient.calls,
            )
            self.assertIn(("complete", "task-1", "session-1"), FakeAipRpcClient.calls)

            canceled = await runtime.command(
                "task-1",
                AcpsLeaderTaskCommandRequest(command="cancel"),
                owner_id="user-1",
                project_id="project-1",
            )
            self.assertEqual(canceled["task"]["status"]["state"], "canceled")

            with self.assertRaises(LookupError):
                await runtime.command(
                    "task-1",
                    AcpsLeaderTaskCommandRequest(command="get"),
                    owner_id="user-2",
                    project_id="project-1",
                )


class FakeGroupMqClient:
    def __init__(self, calls: list[tuple]):
        self.calls = calls

    async def mute_partner(self, partner_aic: str, session_id: str):
        self.calls.append(("mute", session_id, partner_aic))

    async def unmute_partner(self, partner_aic: str, session_id: str):
        self.calls.append(("unmute", session_id, partner_aic))


class FakeGroupLeader:
    def __init__(self, **kwargs):
        self.calls: list[tuple] = []
        self.group_sessions: dict[str, SimpleNamespace] = {}

    async def create_group_session(self, session_id: str, initial_partners: list):
        self.calls.append(("create", session_id, initial_partners))
        self.group_sessions[session_id] = SimpleNamespace(
            leader_mq_client=FakeGroupMqClient(self.calls)
        )

    def get_group_runtime(self, session_id: str):
        return {
            "session_id": session_id,
            "group_id": f"group-{session_id}",
            "members": [],
        }

    async def invite_partner(self, session_id: str, partner_acs, **kwargs):
        self.calls.append(("invite", session_id, partner_acs.aic, kwargs))
        return True

    async def start_task(self, session_id: str, **kwargs):
        self.calls.append(("start", session_id, kwargs))
        return kwargs.get("task_id") or "group-task-1"

    async def continue_task(self, session_id: str, task_id: str, content: str, target=None):
        self.calls.append(("continue", session_id, task_id, content, target))

    async def complete_task(self, session_id: str, task_id: str, target=None):
        self.calls.append(("complete", session_id, task_id, target))

    async def cancel_task(self, session_id: str, task_id: str, reason=None, target=None):
        self.calls.append(("cancel", session_id, task_id, reason, target))

    async def check_partner_status(self, partner_aic: str, session_id: str):
        self.calls.append(("status", session_id, partner_aic))

    async def request_partner_leave(self, partner_aic: str, session_id: str):
        self.calls.append(("leave", session_id, partner_aic))

    async def force_remove_partner(self, partner_aic: str, session_id: str):
        self.calls.append(("force-remove", session_id, partner_aic))
        return {}

    async def dissolve_group_session(self, session_id: str):
        self.calls.append(("dissolve", session_id))
        self.group_sessions.pop(session_id, None)

    async def close(self):
        self.calls.append(("close",))


class AcpsGroupLeaderTests(unittest.IsolatedAsyncioTestCase):
    def _runtime(self):
        settings = _settings(
            role="leader",
            aic="1.2.156.3088.A.B.C.D.E.ABCD",
            group_enabled=True,
            rabbitmq_host="mq.example.cn",
            rabbitmq_user="leader-aic",
            rabbitmq_password="secret",
            group_auth_service_url="https://mq-auth.example.cn",
            allowed_partner_hosts=("partner.example.cn",),
        )
        return AcpsGroupLeaderRuntime(settings, leader_factory=FakeGroupLeader)

    async def test_group_lifecycle_and_owner_isolation(self):
        runtime = self._runtime()
        leader_acs = build_acs_document(runtime.settings, "leader")
        self.assertEqual(leader_acs["capabilities"]["messageQueue"], ["amqp:0.9.1"])
        self.assertEqual(
            leader_acs["entityMeta"]["interactionModes"], ["direct", "group"]
        )
        group = await runtime.create_group(
            AcpsGroupCreateRequest(session_id="session-1"),
            owner_id="user-1",
            project_id="project-1",
        )
        self.assertEqual(group["group_id"], "group-session-1")

        await runtime.invite_partner(
            "session-1",
            AcpsGroupPartnerRequest(
                partner_aic="1.2.156.3088.A.B.C.D.E.BCDE",
                partner_url="https://partner.example.cn/acps/rpc",
            ),
            owner_id="user-1",
            project_id="project-1",
        )
        task = await runtime.start_task(
            "session-1",
            AcpsGroupTaskRequest(
                content="协作分析",
                target_partners=["1.2.156.3088.A.B.C.D.E.BCDE"],
            ),
            owner_id="user-1",
            project_id="project-1",
        )
        self.assertEqual(task["taskId"], "group-task-1")
        await runtime.task_command(
            "session-1",
            "group-task-1",
            AcpsGroupTaskCommandRequest(
                command="continue",
                content="补充信息",
                target_partner="1.2.156.3088.A.B.C.D.E.BCDE",
            ),
            owner_id="user-1",
            project_id="project-1",
        )
        await runtime.task_command(
            "session-1",
            "group-task-1",
            AcpsGroupTaskCommandRequest(command="complete"),
            owner_id="user-1",
            project_id="project-1",
        )
        await runtime.task_command(
            "session-1",
            "group-task-2",
            AcpsGroupTaskCommandRequest(command="cancel", reason="用户终止"),
            owner_id="user-1",
            project_id="project-1",
        )
        for command in ("status", "mute", "unmute", "leave", "force-remove"):
            await runtime.member_command(
                "session-1",
                "1.2.156.3088.A.B.C.D.E.BCDE",
                AcpsGroupMemberCommandRequest(command=command),
                owner_id="user-1",
                project_id="project-1",
            )

        with self.assertRaises(LookupError):
            await runtime.get_group(
                "session-1", owner_id="user-2", project_id="project-1"
            )

        dissolved = await runtime.dissolve_group(
            "session-1", owner_id="user-1", project_id="project-1"
        )
        self.assertTrue(dissolved["dissolved"])


if __name__ == "__main__":
    unittest.main()
