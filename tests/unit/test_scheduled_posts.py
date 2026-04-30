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


class ComposeModeTests(unittest.TestCase):
    """compose_mode + brief + recurrence wiring on the storage layer."""

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

    def test_compose_mode_requires_brief(self) -> None:
        with self.assertRaises(ValueError):
            sp.add_scheduled_post(
                "customer_a", "openchat_004", "2099-01-01T12:00:00+08:00",
                None, compose_mode=True, brief="   ",
            )

    def test_compose_mode_text_starts_empty(self) -> None:
        record = sp.add_scheduled_post(
            "customer_a", "openchat_004", "2099-01-01T12:00:00+08:00",
            None, compose_mode=True, brief="靜坐入門",
        )
        self.assertTrue(record["compose_mode"])
        self.assertEqual(record["brief"], "靜坐入門")
        self.assertEqual(record["text"], "")
        # Default lead time of 4h applies when caller didn't specify
        self.assertEqual(record["compose_lead_seconds"], 4 * 3600)

    def test_compose_mode_custom_lead(self) -> None:
        record = sp.add_scheduled_post(
            "customer_a", "openchat_004", "2099-01-01T12:00:00+08:00",
            None, compose_mode=True, brief="brief",
            compose_lead_seconds=600,
        )
        self.assertEqual(record["compose_lead_seconds"], 600)

    def test_direct_text_default_no_lead(self) -> None:
        record = sp.add_scheduled_post(
            "customer_a", "openchat_004", "2099-01-01T12:00:00+08:00", "hi",
        )
        self.assertFalse(record["compose_mode"])
        self.assertEqual(record["compose_lead_seconds"], 0)

    def test_find_due_honors_compose_lead(self) -> None:
        # Post sent_at = 100, compose_lead = 30 → effective trigger = 70
        post = {
            "post_id": "post-x", "customer_id": "customer_a",
            "community_id": "g", "status": "scheduled",
            "send_at_epoch": 100.0, "compose_mode": True,
            "compose_lead_seconds": 30,
        }
        with patch("app.workflows.scheduled_posts.list_all_scheduled_posts", return_value=[post]):
            self.assertEqual(sp.find_due_posts(now=69), [])
            self.assertEqual(len(sp.find_due_posts(now=70)), 1)
            self.assertEqual(len(sp.find_due_posts(now=100)), 1)

    def test_find_due_for_direct_text_unchanged(self) -> None:
        post = {
            "post_id": "post-x", "customer_id": "customer_a",
            "community_id": "g", "status": "scheduled",
            "send_at_epoch": 100.0, "compose_mode": False,
        }
        with patch("app.workflows.scheduled_posts.list_all_scheduled_posts", return_value=[post]):
            self.assertEqual(sp.find_due_posts(now=99), [])
            self.assertEqual(len(sp.find_due_posts(now=100)), 1)

    def test_recurrence_validation_propagates(self) -> None:
        with self.assertRaises(ValueError):
            sp.add_scheduled_post(
                "customer_a", "openchat_004", "2099-01-01T12:00:00+08:00",
                "hi", recurrence={"kind": "yearly", "time_tpe": "20:00"},
            )

    def test_mark_sent_spawns_next_occurrence_for_recurring(self) -> None:
        from app.workflows.scheduled_post_recurrence import parse_recurrence_string

        future_iso = "2099-05-04T20:00:00+08:00"
        record = sp.add_scheduled_post(
            "customer_a", "openchat_004", future_iso, "weekly post",
            recurrence=parse_recurrence_string("weekly:mon@20:00"),
        )
        sp.mark_post_due("customer_a", "openchat_004", record["post_id"], job_id="j1")
        sp.mark_post_sent("customer_a", "openchat_004", record["post_id"])

        all_posts = sp.list_scheduled_posts("customer_a", "openchat_004")
        # Original sent + spawned scheduled = 2
        statuses = sorted(p["status"] for p in all_posts)
        self.assertEqual(statuses, ["scheduled", "sent"])
        spawned = next(p for p in all_posts if p["status"] == "scheduled")
        self.assertEqual(spawned["text"], "weekly post")
        self.assertEqual((spawned["recurrence"] or {}).get("occurrences_fired"), 1)
        # Spawned post is exactly 7 days later
        self.assertAlmostEqual(
            spawned["send_at_epoch"] - record["send_at_epoch"], 7 * 86400, delta=1.0,
        )

    def test_mark_sent_no_spawn_for_non_recurring(self) -> None:
        record = sp.add_scheduled_post(
            "customer_a", "openchat_004", "2099-01-01T12:00:00+08:00", "one-shot",
        )
        sp.mark_post_due("customer_a", "openchat_004", record["post_id"], job_id="j1")
        sp.mark_post_sent("customer_a", "openchat_004", record["post_id"])

        all_posts = sp.list_scheduled_posts("customer_a", "openchat_004")
        self.assertEqual(len(all_posts), 1)
        self.assertEqual(all_posts[0]["status"], "sent")

    def test_compose_mode_recurrence_preserves_brief(self) -> None:
        from app.workflows.scheduled_post_recurrence import parse_recurrence_string

        record = sp.add_scheduled_post(
            "customer_a", "openchat_004", "2099-05-04T20:00:00+08:00", None,
            compose_mode=True, brief="靜坐入門引子",
            recurrence=parse_recurrence_string("weekly:mon@20:00"),
        )
        sp.mark_post_due("customer_a", "openchat_004", record["post_id"], job_id="j1")
        sp.mark_post_sent("customer_a", "openchat_004", record["post_id"])

        all_posts = sp.list_scheduled_posts("customer_a", "openchat_004")
        spawned = next(p for p in all_posts if p["status"] == "scheduled")
        self.assertTrue(spawned["compose_mode"])
        self.assertEqual(spawned["brief"], "靜坐入門引子")
        self.assertEqual(spawned["text"], "")  # composer hasn't run on the spawned post yet


if __name__ == "__main__":
    unittest.main()
