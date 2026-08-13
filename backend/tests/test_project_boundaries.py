import unittest

from pydantic import ValidationError

from app.ai.api_models import AgentWorkflowCreate


class ProjectBoundaryContractTests(unittest.TestCase):
    def test_multi_agent_workflow_cannot_be_created_without_project(self) -> None:
        with self.assertRaises(ValidationError):
            AgentWorkflowCreate(content="分析当前试验数据")

    def test_multi_agent_workflow_keeps_explicit_project(self) -> None:
        project_id = "11111111-1111-1111-1111-111111111111"
        payload = AgentWorkflowCreate(
            content="分析当前试验数据",
            project_id=project_id,
        )
        self.assertEqual(payload.project_id, project_id)


if __name__ == "__main__":
    unittest.main()
