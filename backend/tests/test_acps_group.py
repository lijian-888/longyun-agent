import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from acps_sdk.aip import TaskCommand, TaskCommandType, TaskState, TextDataItem
from acps_sdk.aip.aip_group_model import ACSObject

from app.acps_adapter import (
    AcpsExecutionResult,
    AcpsFileArtifact,
    AcpsSettings,
    build_acs_document,
)
from app.acps_group import (
    AcpsGroupDispatchRequest,
    AcpsGroupPartnerTarget,
    AcpsGroupRuntime,
)


PARTNER_AIC = "1.2.156.3088.1.1.CIQJUQ.HELDGD.1.03TO"
LEADER_AIC = "1.2.156.3088.1.1.CIQJUQ.HELDGD.1.03TP"


def _settings(**changes):
    values = {
        "enabled": True,
        "role": "hybrid",
        "transport": "group",
        "leader_aic": LEADER_AIC,
        "partner_aic": PARTNER_AIC,
        "rabbitmq_host": "mq.example.cn",
        "rabbitmq_port": 5671,
        "rabbitmq_vhost": "acps",
        "rabbitmq_user": "group-user",
        "rabbitmq_password": "group-password",
        "allow_plain_rabbitmq": True,
        "rabbitmq_auth_service_url": "https://mq-auth.example.cn:9007",
        "mtls_enabled": False,
    }
    values.update(changes)
    return replace(AcpsSettings.from_env(), **values)


def _command(command_type, task_id="task-1", text=None):
    return TaskCommand(
        id=f"command-{task_id}-{command_type.value}",
        sentAt=datetime.now(timezone.utc).isoformat(),
        senderRole="leader",
        senderId=LEADER_AIC,
        sessionId="session-1",
        groupId="group-session-1",
        taskId=task_id,
        command=command_type,
        mentions=[PARTNER_AIC],
        dataItems=[TextDataItem(text=text)] if text is not None else None,
    )


class FakePartnerClient:
    def __init__(self):
        self.results = []

    async def send_task_result(
        self,
        task_id,
        session_id,
        state,
        products=None,
        status_data_items=None,
    ):
        self.results.append({
            "task_id": task_id,
            "session_id": session_id,
            "state": state,
            "products": products or [],
            "status_data_items": status_data_items or [],
        })

    async def reject_task(self, task_id, session_id, reason):
        self.results.append({
            "task_id": task_id,
            "session_id": session_id,
            "state": TaskState.Rejected,
            "reason": reason,
        })


class AcpsGroupPartnerTests(unittest.IsolatedAsyncioTestCase):
    def test_hybrid_runtime_requires_distinct_aics_and_disallows_plain_by_default(self):
        runtime = AcpsGroupRuntime(
            _settings(
                leader_aic=PARTNER_AIC,
                allow_plain_rabbitmq=False,
            ),
            lambda prompt, caller_aic, context: None,
        )

        errors = runtime.configuration_errors()

        self.assertIn("Leader AIC 与 Partner AIC 必须不同", errors)
        self.assertIn("生产 Inbox/Group 禁止 RabbitMQ PLAIN 认证", errors)

    async def test_group_partner_state_machine_and_group_id(self):
        calls = []

        async def execute(prompt, caller_aic, context):
            calls.append((prompt, caller_aic))
            self.assertEqual(context["groupId"], "group-session-1")
            self.assertEqual(context["taskId"], "task-1")
            await asyncio.sleep(0)
            return AcpsExecutionResult(
                text=f"已分析：{prompt}",
                structured_data={"dataBoundary": "published-standard-data-only"},
                files=[
                    AcpsFileArtifact(
                        name="analysis.pdf",
                        mime_type="application/pdf",
                        uri="https://longyun.e-farmer.cn/files/signed/analysis.pdf",
                        size_bytes=1024,
                        sha256="a" * 64,
                    )
                ],
            )

        runtime = AcpsGroupRuntime(_settings(), execute)
        client = FakePartnerClient()

        await runtime._handle_partner_command(
            client,
            "group-session-1",
            LEADER_AIC,
            _command(TaskCommandType.Start, text="比较候选材料"),
            True,
        )
        for _ in range(20):
            await asyncio.sleep(0.01)
            if client.results[-1]["state"] == TaskState.AwaitingCompletion:
                break

        self.assertEqual(
            [result["state"] for result in client.results],
            [TaskState.Accepted, TaskState.Working, TaskState.AwaitingCompletion],
        )
        self.assertEqual(calls, [("比较候选材料", LEADER_AIC)])
        self.assertEqual(client.results[-1]["products"][0].dataItems[0].text, "已分析：比较候选材料")
        file_item = client.results[-1]["products"][0].dataItems[2]
        self.assertEqual(file_item.name, "analysis.pdf")
        self.assertEqual(file_item.mimeType, "application/pdf")
        self.assertEqual(file_item.metadata["delivery"], "time-limited-authorized-url")

        await runtime._handle_partner_command(
            client,
            "group-session-1",
            LEADER_AIC,
            _command(TaskCommandType.Complete),
            True,
        )
        self.assertEqual(client.results[-1]["state"], TaskState.Completed)

    async def test_mentions_and_wrong_group_are_ignored(self):
        async def execute(prompt, caller_aic, context):
            return AcpsExecutionResult(text=prompt)

        runtime = AcpsGroupRuntime(_settings(), execute)
        client = FakePartnerClient()
        await runtime._handle_partner_command(
            client,
            "group-session-1",
            LEADER_AIC,
            _command(TaskCommandType.Start, text="问题"),
            False,
        )
        wrong_group = _command(TaskCommandType.Start, text="问题")
        wrong_group.groupId = "other-group"
        await runtime._handle_partner_command(
            client,
            "group-session-1",
            LEADER_AIC,
            wrong_group,
            True,
        )
        self.assertEqual(client.results, [])

class FakeGroupSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.group_id = f"group-{session_id}"
        self.state_update_event = asyncio.Event()
        self.task_states = {}
        self.task_products = {}
        self.task_prompts = {}
        self.message_history = []


class FakeGroupLeader:
    def __init__(self):
        self.group_sessions = {}
        self.invited = []

    async def create_group_session(self, session_id, initial_partners):
        session = FakeGroupSession(session_id)
        self.group_sessions[session_id] = session
        return session

    async def invite_partner(self, session_id, partner_acs, partner_rpc_url=None, partner_acs_data=None):
        self.invited.append((session_id, partner_acs.aic, partner_rpc_url))
        return True

    async def start_task(self, session_id, *, task_content, task_id=None, target_partners=None):
        resolved_task_id = task_id or "task-generated"
        session = self.group_sessions[session_id]
        session.task_states[resolved_task_id] = {
            aic: TaskState.AwaitingCompletion for aic in target_partners or []
        }
        session.task_products[resolved_task_id] = {
            aic: f"result:{task_content}" for aic in target_partners or []
        }
        session.state_update_event.set()
        return resolved_task_id

    async def complete_task(self, session_id, task_id, target_partner=None):
        session = self.group_sessions[session_id]
        session.task_states[task_id][target_partner] = TaskState.Completed
        session.state_update_event.set()

    async def cancel_task(self, session_id, task_id, reason=None, target_partner=None):
        session = self.group_sessions.get(session_id)
        if session and task_id in session.task_states:
            targets = [target_partner] if target_partner else list(session.task_states[task_id])
            for aic in targets:
                session.task_states[task_id][aic] = TaskState.Canceled
            session.state_update_event.set()

    def get_group_runtime(self, session_id):
        session = self.group_sessions[session_id]
        return {
            "session_id": session_id,
            "group_id": session.group_id,
            "state": "ready",
            "members": [],
        }

    async def dissolve_group_session(self, session_id):
        self.group_sessions.pop(session_id, None)


class AcpsGroupLeaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_partner_aic_must_match_acs(self):
        async def execute(prompt, caller_aic, context):
            return AcpsExecutionResult(text=prompt)

        runtime = AcpsGroupRuntime(_settings(), execute)
        runtime._leader = FakeGroupLeader()
        request = AcpsGroupDispatchRequest(
            query="联合分析候选材料",
            partners=[
                AcpsGroupPartnerTarget(
                    aic=PARTNER_AIC,
                    acs=build_acs_document(
                        _settings(role="partner", partner_aic=LEADER_AIC),
                        "partner",
                    ),
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "ACS 中的 aic 不一致"):
            await runtime.dispatch(request, "researcher-1")

    async def test_group_dispatch_invites_completes_and_dissolves(self):
        async def execute(prompt, caller_aic, context):
            return AcpsExecutionResult(text=prompt)

        runtime = AcpsGroupRuntime(_settings(), execute)
        fake_leader = FakeGroupLeader()
        runtime._leader = fake_leader
        request = AcpsGroupDispatchRequest(
            query="联合分析候选材料",
            session_id="session-owned",
            task_id="task-owned",
            partners=[
                AcpsGroupPartnerTarget(
                    aic=LEADER_AIC,
                    acs=build_acs_document(
                        _settings(role="partner", leader_aic="", partner_aic=LEADER_AIC),
                        "partner",
                    ),
                )
            ],
            auto_complete=True,
            auto_dissolve=True,
        )

        result = await runtime.dispatch(request, "researcher-1")

        self.assertEqual(result["groupId"], "group-session-owned")
        self.assertEqual(result["states"], {LEADER_AIC: "completed"})
        self.assertTrue(result["dissolved"])
        self.assertNotIn("session-owned", fake_leader.group_sessions)

    async def test_group_session_owner_isolation(self):
        async def execute(prompt, caller_aic, context):
            return AcpsExecutionResult(text=prompt)

        runtime = AcpsGroupRuntime(_settings(), execute)
        runtime._leader = FakeGroupLeader()
        runtime._leader_session_owners["session-private"] = "researcher-1"
        with self.assertRaises(PermissionError):
            runtime._assert_owner("session-private", "researcher-2")


if __name__ == "__main__":
    unittest.main()
