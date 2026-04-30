import unittest
from unittest.mock import MagicMock, patch

from app.workflows.scheduler import enqueue_due_scheduled_posts


class SchedulerScheduledPostsTests(unittest.TestCase):
    @patch("app.workflows.scheduler.append_audit_event", lambda *a, **k: None)
    @patch("app.workflows.scheduler.mark_post_due")
    @patch("app.workflows.scheduler.load_community_config")
    @patch("app.workflows.scheduler.find_due_posts")
    @patch("app.workflows.scheduler.job_registry")
    def test_due_post_is_enqueued_and_marked(
        self,
        mock_job_registry,
        mock_find_due,
        mock_load_community,
        mock_mark_due,
    ) -> None:
        mock_find_due.return_value = [
            {
                "customer_id": "customer_a",
                "community_id": "openchat_001",
                "post_id": "post-abc",
                "send_at_iso": "2026-04-28T20:00:00+08:00",
                "text": "hello",
                "pre_approved": False,
            }
        ]
        mock_load_community.return_value = MagicMock(device_id="emulator-5554")
        mock_job = MagicMock()
        mock_job.job_id = "job-xyz"
        mock_job.payload = {}
        mock_job_registry.enqueue.return_value = mock_job

        result = enqueue_due_scheduled_posts(now=1234567890.0)

        self.assertEqual(result["enqueued_count"], 1)
        self.assertEqual(result["enqueued"][0]["post_id"], "post-abc")
        self.assertEqual(result["enqueued"][0]["job_id"], "job-xyz")
        mock_job_registry.enqueue.assert_called_once()
        args, _ = mock_job_registry.enqueue.call_args
        self.assertEqual(args[0], "scheduled_post")
        self.assertEqual(args[1]["post_id"], "post-abc")
        self.assertEqual(args[1]["draft_text"], "hello")
        self.assertEqual(args[1]["device_id"], "emulator-5554")
        mock_mark_due.assert_called_once_with("customer_a", "openchat_001", "post-abc", job_id="job-xyz")

    @patch("app.workflows.scheduler.append_audit_event", lambda *a, **k: None)
    @patch("app.workflows.scheduler.mark_post_due")
    @patch("app.workflows.scheduler.load_community_config", side_effect=KeyError("missing"))
    @patch("app.workflows.scheduler.find_due_posts")
    @patch("app.workflows.scheduler.job_registry")
    def test_skips_when_community_missing(
        self,
        mock_job_registry,
        mock_find_due,
        mock_load_community,
        mock_mark_due,
    ) -> None:
        mock_find_due.return_value = [
            {
                "customer_id": "customer_a",
                "community_id": "ghost",
                "post_id": "post-1",
                "send_at_iso": "x",
                "text": "y",
            }
        ]

        result = enqueue_due_scheduled_posts()

        self.assertEqual(result["enqueued_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        mock_job_registry.enqueue.assert_not_called()
        mock_mark_due.assert_not_called()


class SchedulerComposeModeTests(unittest.TestCase):
    """compose_mode posts pass brief + compose_mode flag through the job payload."""

    @patch("app.workflows.scheduler.append_audit_event", lambda *a, **k: None)
    @patch("app.workflows.scheduler.mark_post_due")
    @patch("app.workflows.scheduler.load_community_config")
    @patch("app.workflows.scheduler.find_due_posts")
    @patch("app.workflows.scheduler.job_registry")
    def test_compose_mode_payload_includes_brief_and_send_at(
        self,
        mock_job_registry,
        mock_find_due,
        mock_load_community,
        mock_mark_due,
    ) -> None:
        mock_find_due.return_value = [
            {
                "customer_id": "customer_a",
                "community_id": "openchat_004",
                "post_id": "post-llm",
                "send_at_iso": "2026-05-04T20:00:00+08:00",
                "text": "",
                "brief": "靜坐入門引子",
                "compose_mode": True,
                "pre_approved": False,
            }
        ]
        mock_load_community.return_value = MagicMock(device_id="emulator-5554")
        mock_job = MagicMock()
        mock_job.job_id = "job-llm-1"
        mock_job.payload = {}
        mock_job_registry.enqueue.return_value = mock_job

        result = enqueue_due_scheduled_posts(now=1234567890.0)

        self.assertEqual(result["enqueued_count"], 1)
        args, _ = mock_job_registry.enqueue.call_args
        payload = args[1]
        self.assertTrue(payload["compose_mode"])
        self.assertEqual(payload["brief"], "靜坐入門引子")
        self.assertEqual(payload["send_at_iso"], "2026-05-04T20:00:00+08:00")
        self.assertEqual(payload["draft_text"], "")  # Empty until composer runs


if __name__ == "__main__":
    unittest.main()
