import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.workflows import scheduled_posts as sp


class ScheduledPostsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._tmp_path = Path(self._tmp.name)

        self._path_patch = patch(
            "app.workflows.scheduled_posts.scheduled_posts_path",
            side_effect=lambda c, g: self._tmp_path / f"{c}_{g}.json",
        )
        self._audit_patch = patch("app.workflows.scheduled_posts.append_audit_event", lambda *a, **k: None)
        self._path_patch.start()
        self._audit_patch.start()
        self.addCleanup(self._path_patch.stop)
        self.addCleanup(self._audit_patch.stop)

    def test_add_then_list_round_trip(self) -> None:
        future = "2099-01-01T12:00:00+00:00"
        record = sp.add_scheduled_post("customer_a", "openchat_001", future, "晚安")
        self.assertEqual(record["status"], "scheduled")
        self.assertEqual(record["text"], "晚安")
        items = sp.list_scheduled_posts("customer_a", "openchat_001")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["post_id"], record["post_id"])

    def test_rejects_empty_text(self) -> None:
        with self.assertRaises(ValueError):
            sp.add_scheduled_post("customer_a", "openchat_001", "2099-01-01T12:00:00+00:00", "  ")

    def test_rejects_past_send_at(self) -> None:
        with self.assertRaises(ValueError):
            sp.add_scheduled_post("customer_a", "openchat_001", "2000-01-01T00:00:00+00:00", "hi")

    def test_rejects_naive_datetime(self) -> None:
        with self.assertRaises(ValueError):
            sp.add_scheduled_post("customer_a", "openchat_001", "2099-01-01T00:00:00", "hi")

    def test_cancel_changes_status(self) -> None:
        record = sp.add_scheduled_post(
            "customer_a", "openchat_001", "2099-01-01T12:00:00+00:00", "hi"
        )
        updated = sp.cancel_scheduled_post("customer_a", "openchat_001", record["post_id"])
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "cancelled")
        self.assertEqual(updated["skip_reason"], "operator_cancelled")

    def test_find_due_uses_now(self) -> None:
        record = sp.add_scheduled_post(
            "customer_a", "openchat_001", "2099-01-01T12:00:00+00:00", "hi"
        )
        # before send_at: not due
        with patch("app.workflows.scheduled_posts.list_all_scheduled_posts", return_value=[record]):
            self.assertEqual(sp.find_due_posts(now=0), [])
            # at/after send_at: due
            due = sp.find_due_posts(now=record["send_at_epoch"] + 1)
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0]["post_id"], record["post_id"])

    def test_status_lifecycle_helpers(self) -> None:
        record = sp.add_scheduled_post(
            "customer_a", "openchat_001", "2099-01-01T12:00:00+00:00", "hi"
        )
        sp.mark_post_due("customer_a", "openchat_001", record["post_id"], job_id="job-x")
        sp.mark_post_reviewing("customer_a", "openchat_001", record["post_id"], review_id="job-x")
        sp.mark_post_sent("customer_a", "openchat_001", record["post_id"], send_result={"status": "sent"})

        final = sp.get_post("customer_a", "openchat_001", record["post_id"])
        self.assertEqual(final["status"], "sent")
        self.assertEqual(final["job_id"], "job-x")
        self.assertEqual(final["review_id"], "job-x")
        self.assertIsNotNone(final["sent_at_epoch"])


class ScheduledPostStatusTests(unittest.TestCase):
    def test_aggregates_counts_and_upcoming(self) -> None:
        from app.workflows.scheduled_post_status import get_scheduled_post_status

        now = time.time()
        items = [
            {
                "customer_id": "customer_a",
                "community_id": "openchat_001",
                "post_id": "post-1",
                "status": "scheduled",
                "send_at_epoch": now + 3600,
                "send_at_iso": "future",
                "updated_at_epoch": now,
            },
            {
                "customer_id": "customer_a",
                "community_id": "openchat_001",
                "post_id": "post-2",
                "status": "sent",
                "send_at_epoch": now - 86400,
                "send_at_iso": "past",
                "updated_at_epoch": now - 60,
            },
            {
                "customer_id": "customer_a",
                "community_id": "openchat_001",
                "post_id": "post-3",
                "status": "cancelled",
                "send_at_epoch": now + 7200,
                "send_at_iso": "future",
                "updated_at_epoch": now - 120,
            },
        ]
        with patch("app.workflows.scheduled_post_status.list_all_scheduled_posts", return_value=items):
            result = get_scheduled_post_status()

        self.assertEqual(result["total_count"], 3)
        self.assertEqual(result["counts"]["scheduled"], 1)
        self.assertEqual(result["counts"]["sent"], 1)
        self.assertEqual(result["counts"]["cancelled"], 1)
        self.assertEqual(result["active_count"], 1)
        self.assertEqual(len(result["upcoming"]), 1)
        self.assertEqual(result["upcoming"][0]["post_id"], "post-1")
        self.assertEqual(len(result["recent"]), 2)


if __name__ == "__main__":
    unittest.main()
