import unittest

from app.main import app


class AgentApiRouteTests(unittest.TestCase):
    def test_agent_routes_are_registered_once_from_split_router(self) -> None:
        actual = {
            (route.path, method): route.endpoint.__module__
            for route in app.routes
            for method in getattr(route, "methods", set())
            if route.path.startswith("/api/agent")
        }
        expected = {
            ("/api/agents", "GET"),
            ("/api/agent-workflows", "GET"),
            ("/api/agent-workflows", "POST"),
            ("/api/agent-workflows/{workflow_run_id}", "GET"),
            ("/api/agent-workflows/{workflow_run_id}/cancel", "POST"),
            ("/api/agent-workflows/{workflow_run_id}/events", "GET"),
        }
        self.assertEqual(set(actual), expected)
        self.assertEqual(set(actual.values()), {"app.ai.api"})


if __name__ == "__main__":
    unittest.main()
