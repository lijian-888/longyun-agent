import asyncio
import ssl
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import httpx
from acps_sdk.acs import AgentCapabilitySpec
from acps_sdk.aip import (
    TaskCommand,
    TaskCommandType,
    TaskResult,
    TaskState,
    TaskStatus,
    TextDataItem,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.acps.acs import build_longyun_acs
from app.acps.api import AcpsApiDependencies, build_acps_router
from app.acps.client import AipPartnerClient, DiscoveredPartner
from app.acps.config import AcpsIdentityBinding, AcpsSettings
from app.acps.service import LongyunAipService


NOW = datetime.now(timezone.utc)
LEADER_AIC = "1.2.156.3088.1.1.LEADER.TEST.1.0001"
PARTNER_AIC = "1.2.156.3088.1.1.LONGYUN.TEST.1.0001"


def command(command_type: TaskCommandType, *, task_id: str = "task-1") -> TaskCommand:
    return TaskCommand(
        id=f"command-{command_type.value}",
        sentAt=NOW.isoformat(),
        senderRole="leader",
        senderId=LEADER_AIC,
        sessionId="session-1",
        taskId=task_id,
        command=command_type,
        commandParams={"skillIds": ["longyun.trial-analysis"], "timeout": 60_000},
        dataItems=[TextDataItem(text="分析两年两点水稻区试稳定性")]
        if command_type == TaskCommandType.Start
        else None,
    )


class AcpsConfigurationTests(unittest.TestCase):
    def test_binding_reads_camel_case_authorization_scope(self) -> None:
        binding = AcpsIdentityBinding.from_dict({
            "institutionId": "institution-a",
            "ownerId": "acps:leader-a",
            "projectId": "project-a",
            "allowedSkillIds": ["longyun.trial-analysis"],
            "externalDataAcknowledged": True,
        })
        self.assertEqual(binding.institution_id, "institution-a")
        self.assertEqual(binding.owner_id, "acps:leader-a")
        self.assertTrue(binding.external_data_acknowledged)

    def test_generated_acs_uses_official_21_shape(self) -> None:
        document = build_longyun_acs(
            aic=PARTNER_AIC,
            rpc_url="https://longyun.internal/acps/aip/rpc",
            amqp_url="amqps://rabbitmq.internal/acps?inbox=inbox_longyun",
        )
        parsed = AgentCapabilitySpec.model_validate(document)
        self.assertEqual(parsed.protocol_version, "02.01")
        self.assertEqual(len(parsed.skills), 4)
        self.assertEqual(parsed.end_points[0].transport, "JSONRPC")
        self.assertEqual(parsed.capabilities.message_queue[0].value, "amqp:0.9.1")


class LongyunAipServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = AcpsSettings(
            enabled=True,
            partner_aic=PARTNER_AIC,
            identity_bindings={},
            require_mtls_proxy=True,
        )
        self.identity = AcpsIdentityBinding(
            institution_id="institution-a",
            owner_id="acps:leader-a",
            project_id="project-a",
            allowed_skill_ids=frozenset({"longyun.trial-analysis"}),
            external_data_acknowledged=True,
        )

    @patch("app.acps.service.mark_task_enqueued")
    @patch("app.acps.service.update_protocol_state")
    @patch("app.acps.service.get_workflow_run")
    @patch("app.acps.service.create_or_get_task_binding")
    @patch("app.acps.service.create_workflow_run")
    @patch("app.acps.service.get_task_binding", return_value=None)
    def test_start_maps_aip_skill_to_existing_workflow(
        self,
        _get_binding: Mock,
        create_run: Mock,
        create_binding: Mock,
        get_run: Mock,
        update_state: Mock,
        mark_enqueued: Mock,
    ) -> None:
        workflow = {
            "id": "run-1",
            "status": "queued",
            "created_at": NOW,
            "updated_at": NOW,
            "was_created": True,
        }
        binding = {
            "id": "binding-1",
            "institution_id": "institution-a",
            "owner_id": "acps:leader-a",
            "leader_aic": LEADER_AIC,
            "external_task_id": "task-1",
            "session_id": "session-1",
            "workflow_run_id": "run-1",
            "protocol_state": "accepted",
            "protocol_error": None,
            "command_history": [command(TaskCommandType.Start).model_dump(mode="json", exclude_none=True)],
            "status_history": [{"state": "accepted", "stateChangedAt": NOW.isoformat()}],
            "acknowledged_at": None,
            "max_products_bytes": None,
        }
        create_run.return_value = workflow
        create_binding.return_value = binding
        get_run.return_value = workflow
        update_state.return_value = binding
        mark_enqueued.return_value = {**binding, "enqueued_at": NOW}
        enqueue = Mock()
        service = LongyunAipService(self.settings, enqueue=enqueue)
        service._assert_scope = Mock()
        service._assert_capacity = Mock()

        with patch.dict("os.environ", {"LONGYUN_LLM_DEPLOYMENT_MODE": "external_api"}):
            result = service.handle(
                Mock(),
                leader_aic=LEADER_AIC,
                binding=self.identity,
                command=command(TaskCommandType.Start),
            )

        self.assertEqual(result.status.state, TaskState.Accepted)
        self.assertEqual(create_run.call_args.kwargs["requested_agents"], ["trial_analysis"])
        enqueue.assert_called_once_with("run-1", "institution-a", "acps:leader-a")

    @patch("app.acps.service.update_protocol_state")
    @patch("app.acps.service.mark_task_enqueued")
    @patch("app.acps.service.get_workflow_run")
    @patch("app.acps.service.record_task_command")
    @patch("app.acps.service.get_task_binding")
    def test_repeated_start_repairs_commit_before_enqueue_gap(
        self,
        get_binding: Mock,
        record_command: Mock,
        get_run: Mock,
        mark_enqueued: Mock,
        update_state: Mock,
    ) -> None:
        binding = {
            "id": "binding-repair",
            "institution_id": "institution-a",
            "owner_id": "acps:leader-a",
            "leader_aic": LEADER_AIC,
            "external_task_id": "task-1",
            "session_id": "session-1",
            "workflow_run_id": "run-repair",
            "protocol_state": "accepted",
            "protocol_error": None,
            "command_history": [],
            "status_history": [{"state": "accepted", "stateChangedAt": NOW.isoformat()}],
            "enqueued_at": None,
            "acknowledged_at": None,
            "max_products_bytes": None,
        }
        repaired = {**binding, "enqueued_at": NOW}
        workflow = {
            "id": "run-repair",
            "status": "queued",
            "created_at": NOW,
            "updated_at": NOW,
        }
        get_binding.return_value = binding
        record_command.return_value = binding
        get_run.return_value = workflow
        mark_enqueued.return_value = repaired
        update_state.return_value = repaired
        enqueue = Mock()

        result = LongyunAipService(self.settings, enqueue=enqueue).handle(
            Mock(),
            leader_aic=LEADER_AIC,
            binding=self.identity,
            command=command(TaskCommandType.Start),
        )

        enqueue.assert_called_once_with("run-repair", "institution-a", "acps:leader-a")
        self.assertEqual(result.status.state, TaskState.Accepted)

    @patch("app.acps.service.list_workflow_artifacts", return_value=[])
    @patch("app.acps.service.update_protocol_state")
    @patch("app.acps.service.acknowledge_task")
    @patch("app.acps.service.get_workflow_run")
    @patch("app.acps.service.record_task_command")
    @patch("app.acps.service.get_task_binding")
    def test_complete_acknowledges_products_without_changing_internal_run(
        self,
        get_binding: Mock,
        record_command: Mock,
        get_run: Mock,
        acknowledge: Mock,
        update_state: Mock,
        _artifacts: Mock,
    ) -> None:
        binding = {
            "id": "binding-1",
            "leader_aic": LEADER_AIC,
            "external_task_id": "task-1",
            "session_id": "session-1",
            "workflow_run_id": "run-1",
            "protocol_state": "awaiting-completion",
            "protocol_error": None,
            "command_history": [],
            "status_history": [{"state": "awaiting-completion", "stateChangedAt": NOW.isoformat()}],
            "acknowledged_at": None,
            "max_products_bytes": None,
        }
        acknowledged = {**binding, "acknowledged_at": NOW}
        run = {
            "id": "run-1",
            "status": "completed",
            "updated_at": NOW,
            "final_content": "稳定性分析完成",
            "model_alias": "local-test",
            "usage": {},
        }
        get_binding.return_value = binding
        record_command.return_value = binding
        get_run.return_value = run
        acknowledge.return_value = acknowledged
        update_state.return_value = acknowledged
        result = LongyunAipService(self.settings, enqueue=Mock()).handle(
            Mock(),
            leader_aic=LEADER_AIC,
            binding=self.identity,
            command=command(TaskCommandType.Complete),
        )
        self.assertEqual(result.status.state, TaskState.Completed)
        self.assertEqual(result.products[0].dataItems[0].text, "稳定性分析完成")


class AipApiSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        identity = AcpsIdentityBinding(
            institution_id="institution-a",
            owner_id="acps:leader-a",
            project_id="project-a",
            external_data_acknowledged=True,
        )
        settings = AcpsSettings(
            enabled=True,
            partner_aic=PARTNER_AIC,
            identity_bindings={LEADER_AIC: identity},
            require_mtls_proxy=True,
        )
        self.owner_calls = []

        def get_session():
            yield Mock()

        def set_owner(session, owner_id, institution_id, *, institution_admin=False):
            self.owner_calls.append((owner_id, institution_id, institution_admin))

        service = Mock()
        service.handle.return_value = TaskResult(
            id="result-api",
            sentAt=NOW.isoformat(),
            senderRole="partner",
            senderId=PARTNER_AIC,
            taskId="task-1",
            sessionId="session-1",
            status=TaskStatus(state=TaskState.Accepted, stateChangedAt=NOW.isoformat()),
        )
        app = FastAPI()
        app.include_router(build_acps_router(
            AcpsApiDependencies(get_session=get_session, set_research_owner=set_owner),
            settings,
            service=service,
        ))
        self.client = TestClient(app)
        self.payload = {
            "jsonrpc": "2.0",
            "id": "rpc-1",
            "method": "rpc",
            "params": {
                "command": command(TaskCommandType.Start).model_dump(
                    mode="json", exclude_none=True
                )
            },
        }

    def test_verified_aic_is_bound_to_longyun_tenant_context(self) -> None:
        response = self.client.post(
            "/acps/aip/rpc",
            json=self.payload,
            headers={
                "X-ACPS-mTLS-Verified": "SUCCESS",
                "X-ACPS-Client-AIC": LEADER_AIC,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["status"]["state"], "accepted")
        self.assertEqual(self.owner_calls, [("acps:leader-a", "institution-a", False)])

    def test_missing_mtls_verification_is_rejected(self) -> None:
        response = self.client.post(
            "/acps/aip/rpc",
            json=self.payload,
            headers={"X-ACPS-Client-AIC": LEADER_AIC},
        )
        self.assertEqual(response.status_code, 401)

    def test_sender_id_cannot_impersonate_another_aic(self) -> None:
        payload = dict(self.payload)
        payload["params"] = {"command": {
            **self.payload["params"]["command"],
            "senderId": "another-aic",
        }}
        response = self.client.post(
            "/acps/aip/rpc",
            json=payload,
            headers={
                "X-ACPS-mTLS-Verified": "SUCCESS",
                "X-ACPS-Client-AIC": LEADER_AIC,
            },
        )
        self.assertEqual(response.status_code, 403)


class OutboundAipClientTests(unittest.TestCase):
    def test_start_sends_native_task_command_and_parses_task_result(self) -> None:
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = __import__("json").loads(request.content)
            observed.update(body)
            task_command = body["params"]["command"]
            result = TaskResult(
                id="result-1",
                sentAt=NOW.isoformat(),
                senderRole="partner",
                senderId="partner-a",
                taskId=task_command["taskId"],
                sessionId=task_command["sessionId"],
                status=TaskStatus(state=TaskState.Accepted, stateChangedAt=NOW.isoformat()),
            )
            return httpx.Response(200, json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": result.model_dump(mode="json", exclude_none=True),
            })

        async def run() -> TaskResult:
            http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            client = AipPartnerClient(
                LEADER_AIC,
                ssl_context=ssl.create_default_context(),
                http_client=http_client,
            )
            try:
                return await client.start(
                    DiscoveredPartner(
                        aic="partner-a",
                        skill_id="other.crop-analysis",
                        rpc_url="https://partner.internal/rpc",
                        acs={},
                    ),
                    session_id="session-outbound",
                    text="分析作物数据",
                    task_id="task-outbound",
                )
            finally:
                await http_client.aclose()

        result = asyncio.run(run())
        self.assertEqual(result.status.state, TaskState.Accepted)
        sent = observed["params"]["command"]
        self.assertEqual(sent["type"], "task-command")
        self.assertEqual(sent["command"], "start")
        self.assertEqual(sent["senderId"], LEADER_AIC)
        self.assertEqual(sent["dataItems"][0]["type"], "text")


if __name__ == "__main__":
    unittest.main()
