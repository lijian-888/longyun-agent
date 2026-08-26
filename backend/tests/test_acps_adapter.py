import unittest
from dataclasses import replace

import httpx
from fastapi import FastAPI

from app.acps_adapter import (
    AcpsExecutionResult,
    AcpsSettings,
    build_acs_document,
    mount_acps_routes,
)


LEADER_AIC = "1.2.156.3088.1.1.CIQJUQ.HELDGD.1.03LE"
PARTNER_AIC = "1.2.156.3088.1.1.CIQJUQ.HELDGD.1.03PA"


def _settings(**changes):
    values = {
        "enabled": True,
        "role": "hybrid",
        "transport": "group",
        "leader_aic": LEADER_AIC,
        "partner_aic": PARTNER_AIC,
        "public_base_url": "https://longyun.e-farmer.cn",
        "documentation_url": "https://longyun.e-farmer.cn/acps/info",
        "rabbitmq_host": "acps-mq.internal.example.cn",
        "rabbitmq_port": 5671,
        "rabbitmq_vhost": "acps",
        "rabbitmq_user": "local-test",
        "rabbitmq_password": "local-test-password",
        "allow_plain_rabbitmq": True,
        "rabbitmq_auth_service_url": "https://acps-mq-auth.internal.example.cn:9007",
        "mtls_enabled": False,
    }
    values.update(changes)
    return replace(AcpsSettings.from_env(), **values)


class AcpsAcsTests(unittest.TestCase):
    def test_leader_and_partner_have_independent_aics_and_amqp_only(self):
        settings = _settings()
        leader = build_acs_document(settings, "leader")
        partner = build_acs_document(settings, "partner")

        self.assertEqual(leader["aic"], LEADER_AIC)
        self.assertEqual(partner["aic"], PARTNER_AIC)
        self.assertNotEqual(leader["aic"], partner["aic"])
        self.assertEqual([item["transport"] for item in leader["endPoints"]], ["AMQP"])
        self.assertEqual([item["transport"] for item in partner["endPoints"]], ["AMQP"])
        self.assertNotIn("groupInvitationUrl", partner["entityMeta"])
        self.assertEqual(leader["skills"], [])
        self.assertEqual(len(partner["skills"]), 2)
        self.assertEqual(
            partner["capabilities"]["messageQueue"],
            ["rabbitmq:>=4.2"],
        )
        self.assertIn("application/pdf", partner["defaultOutputModes"])
        self.assertIn(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            partner["defaultOutputModes"],
        )

    def test_provider_defaults_match_longyun_registration(self):
        provider = build_acs_document(_settings(), "partner")["provider"]

        self.assertEqual(provider["organization"], "江西省亿发姆科技发展有限公司")
        self.assertEqual(provider["department"], "隆耘智能体项目组")
        self.assertEqual(provider["name"], "李键")
        self.assertEqual(provider["email"], "13437975781@163.com")


class AcpsMetadataRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_specific_cards_and_no_rpc_ingress(self):
        async def execute(prompt, caller_aic, context):
            return AcpsExecutionResult(text=prompt)

        app = FastAPI()
        mount_acps_routes(app, _settings(), execute)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            partner = await client.get("/.well-known/acps-agent.json?role=partner")
            leader = await client.get("/.well-known/acps-agent.json?role=leader")
            health = await client.get("/acps/health")
            direct = await client.post("/acps/rpc", json={})
            rpc_invitation = await client.post("/acps/group/rpc", json={})

        self.assertEqual(partner.status_code, 200)
        self.assertEqual(partner.json()["aic"], PARTNER_AIC)
        self.assertEqual(leader.status_code, 200)
        self.assertEqual(leader.json()["aic"], LEADER_AIC)
        self.assertEqual(
            health.json()["identities"],
            {"leader": LEADER_AIC, "partner": PARTNER_AIC},
        )
        self.assertEqual(direct.status_code, 404)
        self.assertEqual(rpc_invitation.status_code, 404)

    async def test_disabled_role_card_is_hidden(self):
        async def execute(prompt, caller_aic, context):
            return AcpsExecutionResult(text=prompt)

        app = FastAPI()
        mount_acps_routes(app, _settings(role="partner", leader_aic=""), execute)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/.well-known/acps-agent.json?role=leader")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
