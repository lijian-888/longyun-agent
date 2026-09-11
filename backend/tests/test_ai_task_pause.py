import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import main


class FakeSession:
    def __init__(self, task=None, message=None):
        self.task = task
        self.message = message
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, model, item_id):
        if model is main.AIGatewayTask:
            return self.task if self.task and self.task.id == item_id else None
        if model is main.ResearchMessage:
            return self.message if self.message and self.message.id == item_id else None
        return None

    def commit(self):
        self.committed = True


class RunningCoroutine:
    def __init__(self):
        self.cancelled = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True


def task(status="running"):
    return SimpleNamespace(
        id="task-1",
        institution_id="institution-1",
        project_id="project-1",
        owner_id="user-1",
        session_id="session-1",
        request_message_id="message-1",
        idempotency_key="idem-12345678",
        status=status,
        cancel_requested=False,
        completed_at=None,
        updated_at=None,
        result_payload={
            "request": {"knowledge_scope": "both", "attachment_ids": ["attachment-1"]},
        },
        error_code=None,
        error_message=None,
    )


class AITaskPauseTests(unittest.TestCase):
    def setUp(self):
        main.AI_TASK_CANCEL_EVENTS.clear()
        main.AI_TASK_RUNNING_COROUTINES.clear()

    def tearDown(self):
        main.AI_TASK_CANCEL_EVENTS.clear()
        main.AI_TASK_RUNNING_COROUTINES.clear()

    def test_running_task_is_paused_and_coroutine_is_stopped(self):
        item = task()
        session = FakeSession(item)
        running = RunningCoroutine()
        main.AI_TASK_RUNNING_COROUTINES[item.id] = running
        user = SimpleNamespace(id="user-1", roles={"researcher"})
        account = SimpleNamespace(institution_id="institution-1")
        with (
            patch.object(main, "sync_platform_account", return_value=account),
            patch.object(main, "_record_ai_audit") as audit,
            patch.object(main, "_serialize_ai_task", side_effect=lambda value: {"status": value.status}),
        ):
            result = asyncio.run(main.pause_ai_task(item.id, user, session))
        self.assertEqual(result["status"], "paused")
        self.assertEqual(item.status, "paused")
        self.assertFalse(item.cancel_requested)
        self.assertTrue(running.cancelled)
        self.assertTrue(session.committed)
        audit.assert_called_once()

    def test_pause_checkpoint_preserves_request_and_partial_text(self):
        item = task("paused")
        session = FakeSession(item)
        with (
            patch.object(main, "SessionLocal", return_value=session),
            patch.object(main, "_record_ai_audit") as audit,
        ):
            main._store_paused_ai_checkpoint(item.id, "已经生成的部分回答")
        self.assertEqual(item.result_payload["request"]["knowledge_scope"], "both")
        self.assertEqual(item.result_payload["checkpoint"]["partial_text"], "已经生成的部分回答")
        self.assertEqual(item.result_payload["checkpoint"]["strategy"], "restart_from_durable_request")
        self.assertTrue(session.committed)
        audit.assert_called_once()

    def test_paused_task_can_be_explicitly_discarded(self):
        item = task("paused")
        session = FakeSession(item)
        user = SimpleNamespace(id="user-1", roles={"researcher"})
        account = SimpleNamespace(institution_id="institution-1")
        with (
            patch.object(main, "sync_platform_account", return_value=account),
            patch.object(main, "_record_ai_audit"),
            patch.object(main, "_serialize_ai_task", side_effect=lambda value: {"status": value.status}),
        ):
            result = asyncio.run(main.cancel_ai_task(item.id, user, session))
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(item.status, "cancelled")
        self.assertIsNotNone(item.completed_at)

    def test_resume_request_uses_durable_message_and_checkpoint(self):
        item = task("paused")
        item.result_payload["checkpoint"] = {
            "partial_text": "部分回答",
            "strategy": "restart_from_durable_request",
        }
        message = SimpleNamespace(id="message-1", session_id="session-1", content="原始科研问题")
        result = main._serialize_ai_resume_request(FakeSession(item, message), item)
        self.assertEqual(result["content"], "原始科研问题")
        self.assertEqual(result["attachment_ids"], ["attachment-1"])
        self.assertEqual(result["partial_text"], "部分回答")
        self.assertEqual(result["idempotency_key"], item.idempotency_key)


if __name__ == "__main__":
    unittest.main()
