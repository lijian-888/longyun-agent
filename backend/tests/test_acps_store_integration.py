"""Opt-in PostgreSQL checks for durable AIP task mapping and RLS."""

from __future__ import annotations

import os
import re
import unittest
from uuid import uuid4


@unittest.skipUnless(
    os.getenv("RUN_ACPS_DB_INTEGRATION") == "1",
    "set RUN_ACPS_DB_INTEGRATION=1 to run ACPs persistence checks",
)
class AcpsStoreIntegrationTests(unittest.TestCase):
    def test_task_binding_survives_sessions_and_is_owner_private(self) -> None:
        from sqlalchemy import create_engine, text

        from app.acps.store import ensure_acps_schema
        from app.ai.workflow_store import ensure_agent_workflow_schema

        migration_engine = create_engine(os.environ["MIGRATION_DATABASE_URL"])
        application_engine = create_engine(os.environ["DATABASE_URL"])
        application_role = os.getenv("APP_DATABASE_ROLE", "rice_app")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", application_role):
            self.fail("APP_DATABASE_ROLE is not a safe PostgreSQL identifier")
        application_password = os.getenv(
            "TEST_APP_DATABASE_PASSWORD", "rice_app_test_password"
        ).replace("'", "''")
        institution_id = f"acps-test-{uuid4().hex[:10]}"
        run_a, run_b = str(uuid4()), str(uuid4())
        binding_a, binding_b = str(uuid4()), str(uuid4())
        leader_aic = f"test-leader-{uuid4().hex}"
        try:
            with migration_engine.begin() as connection:
                connection.execute(text("""
                    DO $$ BEGIN
                        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{application_role}') THEN
                            CREATE ROLE {application_role} LOGIN NOSUPERUSER NOBYPASSRLS
                                PASSWORD '{application_password}';
                        END IF;
                    END $$
                """.format(
                    application_role=application_role,
                    application_password=application_password,
                )))
            from sqlalchemy.orm import Session

            with Session(migration_engine) as session:
                ensure_agent_workflow_schema(session)
                ensure_acps_schema(session)
                session.execute(text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON acps_task_binding TO {application_role}"
                ))
                session.execute(text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON agent_workflow_run TO {application_role}"
                ))
                session.execute(text("""
                    INSERT INTO institution(id, name, status)
                    VALUES (:institution_id, 'ACPs test', 'active')
                """), {"institution_id": institution_id})
                for run_id, owner_id in ((run_a, "owner-a"), (run_b, "owner-b")):
                    session.execute(text("""
                        INSERT INTO agent_workflow_run(
                            id, thread_id, institution_id, owner_id, user_request, status
                        ) VALUES (:id, :thread_id, :institution_id, :owner_id, 'test', 'queued')
                    """), {
                        "id": run_id,
                        "thread_id": f"acps-test:{run_id}",
                        "institution_id": institution_id,
                        "owner_id": owner_id,
                    })
                for binding_id, run_id, owner_id, task_id in (
                    (binding_a, run_a, "owner-a", "task-a"),
                    (binding_b, run_b, "owner-b", "task-b"),
                ):
                    session.execute(text("""
                        INSERT INTO acps_task_binding(
                            id, institution_id, owner_id, leader_aic,
                            external_task_id, workflow_run_id
                        ) VALUES (
                            :id, :institution_id, :owner_id, :leader_aic,
                            :task_id, :workflow_run_id
                        )
                    """), {
                        "id": binding_id,
                        "institution_id": institution_id,
                        "owner_id": owner_id,
                        "leader_aic": leader_aic,
                        "task_id": task_id,
                        "workflow_run_id": run_id,
                    })
                session.commit()

            with application_engine.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.institution_id', :value, true)"),
                    {"value": institution_id},
                )
                connection.execute(
                    text("SELECT set_config('app.research_user_id', 'owner-a', true)")
                )
                visible = connection.execute(text("""
                    SELECT external_task_id FROM acps_task_binding ORDER BY external_task_id
                """)).scalars().all()
            self.assertEqual(visible, ["task-a"])

            # A new database connection proves this is not SDK in-memory state.
            with application_engine.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.institution_id', :value, true)"),
                    {"value": institution_id},
                )
                connection.execute(
                    text("SELECT set_config('app.research_user_id', 'owner-a', true)")
                )
                task = connection.execute(text("""
                    SELECT external_task_id FROM acps_task_binding
                    WHERE leader_aic = :leader_aic AND external_task_id = 'task-a'
                """), {"leader_aic": leader_aic}).scalar_one()
            self.assertEqual(task, "task-a")
        finally:
            with migration_engine.begin() as connection:
                connection.execute(text(
                    "DELETE FROM acps_task_binding WHERE id IN (:a, :b)"
                ), {"a": binding_a, "b": binding_b})
                connection.execute(text(
                    "DELETE FROM agent_workflow_run WHERE id IN (:a, :b)"
                ), {"a": run_a, "b": run_b})
                connection.execute(text(
                    "DELETE FROM institution WHERE id = :institution_id"
                ), {"institution_id": institution_id})
            application_engine.dispose()
            migration_engine.dispose()


if __name__ == "__main__":
    unittest.main()
