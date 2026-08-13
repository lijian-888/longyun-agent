import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ai.workflow_store import (
    claim_workflow_run,
    create_workflow_run,
    ensure_agent_workflow_schema,
    finalize_unclaimable_workflow_run,
    get_workflow_run,
    persist_workflow_result,
    persist_workflow_step_completed,
    persist_workflow_step_started,
    request_workflow_cancellation,
)


@unittest.skipUnless(
    os.getenv("RUN_AGENT_DB_INTEGRATION") == "1",
    "set RUN_AGENT_DB_INTEGRATION=1 to run workflow reliability checks",
)
class AgentWorkflowReliabilityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.institution_id = f"agent-reliability-{uuid4().hex[:10]}"
        cls.project_id = str(uuid4())
        cls.owner_id = f"owner-{uuid4().hex[:10]}"
        cls.engine = create_engine(os.environ["TEST_MIGRATION_DATABASE_URL"], pool_pre_ping=True)
        cls.session_factory = sessionmaker(bind=cls.engine, expire_on_commit=False)
        with cls.session_factory() as session:
            ensure_agent_workflow_schema(session)
            session.execute(
                text("INSERT INTO institution(id, name, status) VALUES (:id, :name, 'active')"),
                {"id": cls.institution_id, "name": "智能体可靠性测试机构"},
            )
            session.execute(text("""
                INSERT INTO research_project(
                    id, institution_id, project_name, status, created_by
                ) VALUES (
                    :id, :institution_id, 'workflow reliability', 'active', :owner_id
                )
            """), {
                "id": cls.project_id,
                "institution_id": cls.institution_id,
                "owner_id": cls.owner_id,
            })
            session.commit()

    @classmethod
    def tearDownClass(cls) -> None:
        with cls.session_factory() as session:
            for table_name in (
                "ai_usage_log",
                "workflow_event",
                "agent_artifact",
                "agent_workflow_step",
                "agent_workflow_run",
            ):
                session.execute(
                    text(f"DELETE FROM {table_name} WHERE institution_id = :institution_id"),
                    {"institution_id": cls.institution_id},
                )
            session.execute(
                text("DELETE FROM research_project WHERE id = :project_id"),
                {"project_id": cls.project_id},
            )
            session.execute(
                text("DELETE FROM institution WHERE id = :institution_id"),
                {"institution_id": cls.institution_id},
            )
            session.commit()
        cls.engine.dispose()

    def create_run(self, **overrides):
        values = {
            "institution_id": self.institution_id,
            "project_id": self.project_id,
            "owner_id": self.owner_id,
            "user_request": "验证任务可靠性",
            "requested_agents": ["trial_analysis"],
            "max_attempts": 3,
            "deadline_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        }
        values.update(overrides)
        with self.session_factory() as session:
            return create_workflow_run(session, **values)

    def test_idempotency_lease_and_stale_worker_protection(self) -> None:
        key = f"idem-{uuid4()}"
        first = self.create_run(idempotency_key=key)
        second = self.create_run(idempotency_key=key)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["was_created"])
        self.assertFalse(second["was_created"])

        with self.session_factory() as session:
            claimed = claim_workflow_run(
                session,
                first["id"],
                lease_owner="worker-a",
                lease_seconds=300,
            )
        self.assertEqual(claimed["attempt_no"], 1)

        with self.session_factory() as session:
            started = persist_workflow_step_started(
                session,
                claimed,
                agent_code="trial_analysis",
                agent_version="1.0.0",
                contract_version="1.0.0",
                lease_owner="worker-a",
            )
        self.assertTrue(started)

        artifact = {
            "id": str(uuid4()),
            "agent_code": "trial_analysis",
            "agent_name": "试验分析智能体",
            "agent_version": "1.0.0",
            "contract_version": "1.0.0",
            "content": "验证结果",
            "evidence_ids": [],
            "structured_output": {"conclusions": ["验证结果"]},
            "tool_results": [],
            "model_alias": "test-model",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self.session_factory() as session:
            completed = persist_workflow_step_completed(
                session,
                claimed,
                agent_code="trial_analysis",
                artifact=artifact,
                lease_owner="worker-a",
            )
        self.assertTrue(completed)

        state = {
            "plan": ["trial_analysis"],
            "artifacts": [artifact],
            "events": [],
            "usage_records": [],
            "final_content": "验证结果",
            "model_alias": "test-model",
        }
        with self.session_factory() as session:
            session.execute(
                text("UPDATE agent_workflow_run SET lease_owner = 'worker-b' WHERE id = :id"),
                {"id": first["id"]},
            )
            session.commit()
            stale_write = persist_workflow_result(
                session,
                claimed,
                state,
                lease_owner="worker-a",
            )
        self.assertFalse(stale_write)

    def test_cancel_queued_and_finalize_expired(self) -> None:
        queued = self.create_run()
        with self.session_factory() as session:
            cancelled = request_workflow_cancellation(session, queued["id"])
        self.assertEqual(cancelled["status"], "cancelled")

        expired = self.create_run(
            deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        with self.session_factory() as session:
            finalized = finalize_unclaimable_workflow_run(session, expired["id"])
            current = get_workflow_run(session, expired["id"])
        self.assertEqual(finalized["error_code"], "workflow_deadline_exceeded")
        self.assertEqual(current["status"], "failed")


if __name__ == "__main__":
    unittest.main()
