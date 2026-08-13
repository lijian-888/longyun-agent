"""Opt-in PostgreSQL integration checks for production workflow isolation."""

from __future__ import annotations

import os
import re
import unittest
from uuid import uuid4


@unittest.skipUnless(
    os.getenv("RUN_AGENT_DB_INTEGRATION") == "1",
    "set RUN_AGENT_DB_INTEGRATION=1 to run PostgreSQL RLS checks",
)
class AgentWorkflowRlsIntegrationTests(unittest.TestCase):
    def test_private_runs_and_institution_queue_count(self) -> None:
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import Session

        from app.ai.workflow_store import ensure_agent_workflow_schema

        migration_url = os.environ["MIGRATION_DATABASE_URL"]
        application_url = os.environ["DATABASE_URL"]
        application_role = os.getenv("APP_DATABASE_ROLE", "rice_app")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", application_role):
            self.fail("APP_DATABASE_ROLE is not a safe PostgreSQL identifier")

        migration_engine = create_engine(migration_url)
        application_engine = create_engine(application_url)
        institution_a = f"rls-test-a-{uuid4().hex[:8]}"
        institution_b = f"rls-test-b-{uuid4().hex[:8]}"
        project_a = str(uuid4())
        project_a_other = str(uuid4())
        project_b = str(uuid4())
        runs = [str(uuid4()) for _ in range(4)]

        try:
            with Session(migration_engine) as session:
                ensure_agent_workflow_schema(session)
                session.execute(
                    text(
                        "GRANT EXECUTE ON FUNCTION "
                        f"current_institution_active_agent_workflow_count() TO {application_role}"
                    )
                )
                session.execute(
                    text(
                        "INSERT INTO institution(id, name, status) VALUES "
                        "(:a, 'RLS A', 'active'), (:b, 'RLS B', 'active')"
                    ),
                    {"a": institution_a, "b": institution_b},
                )
                session.execute(
                    text(
                        "INSERT INTO research_project("
                        "id, institution_id, project_name, created_by"
                        ") VALUES "
                        "(:project_a, :institution_a, 'RLS Project A', 'user-a'), "
                        "(:project_a_other, :institution_a, 'RLS Project A Other', 'user-a'), "
                        "(:project_b, :institution_b, 'RLS Project B', 'user-a')"
                    ),
                    {
                        "project_a": project_a,
                        "project_a_other": project_a_other,
                        "institution_a": institution_a,
                        "project_b": project_b,
                        "institution_b": institution_b,
                    },
                )
                session.execute(
                    text(
                        "INSERT INTO project_membership("
                        "project_id, institution_id, user_id, project_role"
                        ") VALUES "
                        "(:project_a, :institution_a, 'user-a', 'member'), "
                        "(:project_a, :institution_a, 'user-b', 'member'), "
                        "(:project_a_other, :institution_a, 'user-a', 'member'), "
                        "(:project_b, :institution_b, 'user-a', 'member')"
                    ),
                    {
                        "project_a": project_a,
                        "project_a_other": project_a_other,
                        "institution_a": institution_a,
                        "project_b": project_b,
                        "institution_b": institution_b,
                    },
                )
                fixtures = (
                    (runs[0], institution_a, project_a, "user-a"),
                    (runs[1], institution_a, project_a, "user-b"),
                    (runs[2], institution_a, project_a_other, "user-a"),
                    (runs[3], institution_b, project_b, "user-a"),
                )
                for run_id, institution_id, project_id, owner_id in fixtures:
                    session.execute(
                        text(
                            "INSERT INTO agent_workflow_run("
                            "id, thread_id, institution_id, project_id, owner_id, user_request, status"
                            ") VALUES ("
                            ":id, :thread_id, :institution_id, :project_id, :owner_id, 'test', 'queued'"
                            ")"
                        ),
                        {
                            "id": run_id,
                            "thread_id": f"test:{run_id}",
                            "institution_id": institution_id,
                            "project_id": project_id,
                            "owner_id": owner_id,
                        },
                    )
                session.commit()

            with application_engine.connect() as connection:
                transaction = connection.begin()
                connection.execute(
                    text("SELECT set_config('app.institution_id', :value, true)"),
                    {"value": institution_a},
                )
                connection.execute(
                    text("SELECT set_config('app.research_user_id', 'user-a', true)")
                )
                connection.execute(
                    text("SELECT set_config('app.project_id', :value, true)"),
                    {"value": project_a},
                )
                # Even an institution data administrator cannot inspect a
                # member's private conversation or workflow artifacts.
                connection.execute(
                    text("SELECT set_config('app.institution_admin', 'true', true)")
                )
                visible_owners = connection.execute(
                    text("SELECT owner_id FROM agent_workflow_run ORDER BY owner_id")
                ).scalars().all()
                institution_active = connection.execute(
                    text("SELECT current_institution_active_agent_workflow_count()")
                ).scalar_one()
                transaction.rollback()

            self.assertEqual(visible_owners, ["user-a"])
            self.assertEqual(institution_active, 3)
        finally:
            with migration_engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM agent_workflow_run "
                        "WHERE id IN (:run_a, :run_b, :run_c, :run_d)"
                    ),
                    {
                        "run_a": runs[0],
                        "run_b": runs[1],
                        "run_c": runs[2],
                        "run_d": runs[3],
                    },
                )
                connection.execute(
                    text(
                        "DELETE FROM project_membership "
                        "WHERE project_id IN (:a, :a_other, :b)"
                    ),
                    {"a": project_a, "a_other": project_a_other, "b": project_b},
                )
                connection.execute(
                    text("DELETE FROM research_project WHERE id IN (:a, :a_other, :b)"),
                    {"a": project_a, "a_other": project_a_other, "b": project_b},
                )
                connection.execute(
                    text("DELETE FROM institution WHERE id IN (:a, :b)"),
                    {"a": institution_a, "b": institution_b},
                )


if __name__ == "__main__":
    unittest.main()
