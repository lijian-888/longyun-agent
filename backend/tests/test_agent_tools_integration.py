import asyncio
import os
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.ai.registry import AGENT_SPECS
from app.ai.tools import AgentToolContext, build_rice_tool_registry


@unittest.skipUnless(
    os.getenv("RUN_AGENT_TOOL_INTEGRATION") == "1",
    "set RUN_AGENT_TOOL_INTEGRATION=1 to validate real PostgreSQL tools",
)
class AgentToolIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        url = os.environ["TEST_DATABASE_URL"]
        cls.institution_id = os.getenv("TEST_INSTITUTION_ID", "longyun-demo")
        cls.owner_id = os.getenv("TEST_OWNER_ID", "integration-test-user")
        cls.project_id = os.getenv("TEST_PROJECT_ID") or str(uuid4())
        cls.engine = create_engine(url, pool_pre_ping=True)
        cls.session_factory = sessionmaker(
            bind=cls.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        cls.migration_engine = create_engine(
            os.environ["TEST_MIGRATION_DATABASE_URL"], pool_pre_ping=True
        )
        with cls.migration_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO research_project(
                        id, institution_id, project_name, status, created_by
                    ) VALUES (
                        :id, :institution_id, '智能体工具集成测试课题', 'active', :owner_id
                    ) ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": cls.project_id,
                    "institution_id": cls.institution_id,
                    "owner_id": cls.owner_id,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO project_membership(
                        project_id, institution_id, user_id, project_role
                    ) VALUES (
                        :project_id, :institution_id, :owner_id, 'member'
                    ) ON CONFLICT (project_id, user_id) DO NOTHING
                    """
                ),
                {
                    "project_id": cls.project_id,
                    "institution_id": cls.institution_id,
                    "owner_id": cls.owner_id,
                },
            )

    @classmethod
    def tearDownClass(cls) -> None:
        with cls.migration_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM project_membership WHERE project_id=:project_id"),
                {"project_id": cls.project_id},
            )
            connection.execute(
                text("DELETE FROM research_project WHERE id=:project_id"),
                {"project_id": cls.project_id},
            )
        cls.migration_engine.dispose()
        cls.engine.dispose()

    @staticmethod
    def set_context(session, context: AgentToolContext) -> None:
        session.execute(
            text("SELECT set_config('app.research_user_id', :value, true)"),
            {"value": context.owner_user_id},
        )
        session.execute(
            text("SELECT set_config('app.institution_id', :value, true)"),
            {"value": context.institution_id},
        )
        session.execute(text("SELECT set_config('app.institution_admin', 'false', true)"))
        session.execute(
            text("SELECT set_config('app.project_id', :value, true)"),
            {"value": context.project_id or ""},
        )

    def test_every_declared_tool_executes_against_current_schema(self) -> None:
        registry = build_rice_tool_registry(self.session_factory, self.set_context)
        context = AgentToolContext(
            workflow_run_id="integration-run",
            institution_id=self.institution_id,
            project_id=self.project_id,
            owner_user_id=self.owner_id,
            user_request="测试当前数据结构和受控工具可用性",
        )
        failures: list[str] = []
        for agent_code, spec in AGENT_SPECS.items():
            results = asyncio.run(
                registry.execute_for_agent(
                    agent_code=agent_code,
                    allowed_tool_codes=spec.tool_codes,
                    context=context,
                )
            )
            failures.extend(
                f"{agent_code}/{result.tool_code}: {result.summary}"
                for result in results
                if result.status == "failed"
            )
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
