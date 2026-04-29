"""Tests for the unapprove (recall) workflow."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.reviews import ReviewRecord, ReviewStore
from app.workflows import unapprove as ua


class UnapproveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "reviews.jsonl"
        self.store = ReviewStore(state_path=self.state_path, persist=True)

        self.audit_calls: list[tuple] = []

        def fake_audit(customer_id, event_type, payload):
            self.audit_calls.append((customer_id, event_type, payload))

        self.audit_patch = patch.object(ua, "append_audit_event", side_effect=fake_audit)
        self.audit_patch.start()

    def tearDown(self) -> None:
        self.audit_patch.stop()
        self.tmp.cleanup()

    def _seed(self, status: str = "pending") -> ReviewRecord:
        rec = ReviewRecord(
            review_id="rev-1",
            source_job_id="job-1",
            customer_id="customer_a",
            customer_name="Test",
            community_id="openchat_test",
            community_name="Test",
            device_id="emulator-5554",
            draft_text="hello there friend",
            status=status,
        )
        return self.store.upsert(rec)

    def test_recall_active_review(self) -> None:
        self._seed(status="pending")
        result = ua.unapprove_review("rev-1", reason="wrong audience", store=self.store)
        self.assertEqual(result.previous_status, "pending")
        self.assertEqual(result.new_status, "recalled")
        self.assertFalse(result.sent_message_irreversible)
        # Store reflects new status
        self.assertEqual(self.store.get("rev-1").status, "recalled")
        # Audit event written
        self.assertEqual(self.audit_calls[-1][1], "review_unapproved")
        self.assertEqual(self.audit_calls[-1][2]["previous_status"], "pending")

    def test_recall_sent_review_flags_irreversible(self) -> None:
        self._seed(status="sent")
        result = ua.unapprove_review("rev-1", reason="too late", store=self.store)
        self.assertTrue(result.sent_message_irreversible)
        self.assertEqual(self.audit_calls[-1][2]["sent_message_irreversible"], True)

    def test_unknown_review_raises(self) -> None:
        with self.assertRaises(ua.UnapproveError):
            ua.unapprove_review("does-not-exist", store=self.store)

    def test_already_recalled_raises(self) -> None:
        self._seed(status="recalled")
        with self.assertRaises(ua.UnapproveError):
            ua.unapprove_review("rev-1", store=self.store)

    def test_ignored_review_cannot_be_recalled(self) -> None:
        self._seed(status="ignored")
        with self.assertRaises(ua.UnapproveError):
            ua.unapprove_review("rev-1", store=self.store)

    def test_pending_reapproval_can_be_recalled(self) -> None:
        self._seed(status="pending_reapproval")
        result = ua.unapprove_review("rev-1", store=self.store)
        self.assertEqual(result.new_status, "recalled")


if __name__ == "__main__":
    unittest.main()
