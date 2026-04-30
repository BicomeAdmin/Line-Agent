"""Tests for daemon-startup orphan-state recovery."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows import scheduled_posts as sp
from app.workflows.orphan_recovery import (
    DUE_ORPHAN_GRACE_SECONDS,
    REVIEWING_ORPHAN_GRACE_SECONDS,
    recover_orphan_state,
)


class OrphanRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._tmp_path = Path(self._tmp.name)

        self._path_patch = patch(
            "app.workflows.scheduled_posts.scheduled_posts_path",
            side_effect=lambda c, g: self._tmp_path / f"{c}_{g}.json",
        )
        self._audit_sp = patch("app.workflows.scheduled_posts.append_audit_event", lambda *a, **k: None)
        self._audit_or = patch("app.workflows.orphan_recovery.append_audit_event", lambda *a, **k: None)
        self._path_patch.start()
        self._audit_sp.start()
        self._audit_or.start()
        self.addCleanup(self._path_patch.stop)
        self.addCleanup(self._audit_sp.stop)
        self.addCleanup(self._audit_or.stop)

        # Empty review store for predictable behavior.
        from app.core.reviews import review_store
        with review_store._lock:
            review_store._reviews.clear()

    def _add_post(self, **overrides) -> dict:
        """Create a post and back-date its updated_at by writing the
        JSON directly — _update_post always bumps updated_at to now."""
        record = sp.add_scheduled_post(
            "customer_a", "openchat_004",
            "2099-01-01T12:00:00+08:00", "test text",
        )
        # Apply overrides via direct file edit so updated_at_epoch sticks.
        path = self._tmp_path / "customer_a_openchat_004.json"
        import json
        posts = json.loads(path.read_text(encoding="utf-8"))
        for entry in posts:
            if entry["post_id"] == record["post_id"]:
                entry.update(overrides)
        path.write_text(json.dumps(posts), encoding="utf-8")
        return sp.get_post("customer_a", "openchat_004", record["post_id"])

    def test_due_orphan_past_grace_resets_to_scheduled(self) -> None:
        old_updated = time.time() - (DUE_ORPHAN_GRACE_SECONDS + 60)
        post = self._add_post(status="due", updated_at_epoch=old_updated, job_id="j1")

        summary = recover_orphan_state()

        self.assertEqual(summary.due_orphans_reset, 1)
        refreshed = sp.get_post("customer_a", "openchat_004", post["post_id"])
        self.assertEqual(refreshed["status"], "scheduled")
        self.assertIsNone(refreshed["job_id"])

    def test_due_within_grace_left_alone(self) -> None:
        recent = time.time() - 30  # 30s — well within grace
        post = self._add_post(status="due", updated_at_epoch=recent, job_id="j1")

        summary = recover_orphan_state()

        self.assertEqual(summary.due_orphans_reset, 0)
        refreshed = sp.get_post("customer_a", "openchat_004", post["post_id"])
        self.assertEqual(refreshed["status"], "due")

    def test_reviewing_with_no_review_record_marked_skipped(self) -> None:
        old = time.time() - (REVIEWING_ORPHAN_GRACE_SECONDS + 60)
        post = self._add_post(
            status="reviewing", updated_at_epoch=old, review_id="ghost-id",
        )

        summary = recover_orphan_state()

        self.assertEqual(summary.reviewing_orphans_marked, 1)
        refreshed = sp.get_post("customer_a", "openchat_004", post["post_id"])
        self.assertEqual(refreshed["status"], "skipped")
        self.assertEqual(refreshed["skip_reason"], "orphaned_no_review_record")

    def test_reviewing_with_real_review_left_alone(self) -> None:
        from app.core.reviews import ReviewRecord, review_store
        old = time.time() - (REVIEWING_ORPHAN_GRACE_SECONDS + 60)
        post = self._add_post(
            status="reviewing", updated_at_epoch=old, review_id="real-id",
        )
        review_store.upsert(ReviewRecord(
            review_id="real-id", source_job_id="real-id",
            customer_id="customer_a", customer_name="C",
            community_id="openchat_004", community_name="X",
            device_id="emulator-5554", draft_text="hi",
            reason="test", confidence=None, status="pending",
        ))

        summary = recover_orphan_state()

        self.assertEqual(summary.reviewing_orphans_marked, 0)
        refreshed = sp.get_post("customer_a", "openchat_004", post["post_id"])
        self.assertEqual(refreshed["status"], "reviewing")

    def test_terminal_states_unaffected(self) -> None:
        old = time.time() - (DUE_ORPHAN_GRACE_SECONDS + 1000)
        post_sent = self._add_post(status="sent", updated_at_epoch=old)
        post_cancelled = self._add_post(status="cancelled", updated_at_epoch=old)

        summary = recover_orphan_state()

        # Neither terminal state was touched
        self.assertEqual(summary.due_orphans_reset, 0)
        self.assertEqual(summary.reviewing_orphans_marked, 0)
        self.assertEqual(
            sp.get_post("customer_a", "openchat_004", post_sent["post_id"])["status"],
            "sent",
        )
        self.assertEqual(
            sp.get_post("customer_a", "openchat_004", post_cancelled["post_id"])["status"],
            "cancelled",
        )

    def test_idempotent(self) -> None:
        old = time.time() - (DUE_ORPHAN_GRACE_SECONDS + 60)
        self._add_post(status="due", updated_at_epoch=old)

        recover_orphan_state()
        second = recover_orphan_state()

        # Already-recovered post shouldn't recover again
        self.assertEqual(second.due_orphans_reset, 0)


if __name__ == "__main__":
    unittest.main()
