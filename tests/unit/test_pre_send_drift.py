"""Pre-send temporal drift guard — verifies that approving a stale
review aborts when the group context has shifted since composition.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from app.workflows.job_processor import _check_pre_send_drift


def _make_review(*, age_minutes: float):
    """Build a fake ReviewRecord-like object with `created_at`."""
    return MagicMock(created_at=time.time() - age_minutes * 60)


class FreshReviewSkipsDriftTests(unittest.TestCase):
    """If review was just created, drift check is a no-op."""

    def test_returns_none_for_fresh_review(self) -> None:
        review = _make_review(age_minutes=5)
        with patch("app.workflows.read_chat.read_recent_chat") as mock_read:
            result = _check_pre_send_drift(
                customer_id="customer_a",
                community_id="g",
                device_id="emulator-5554",
                existing_review=review,
            )
        self.assertIsNone(result)
        # Should NOT have read the chat — fresh reviews don't drift
        mock_read.assert_not_called()


class StaleReviewHotChatTests(unittest.TestCase):
    """Scenario 1: review > 30min old AND chat is now 熱絡 → abort."""

    def test_aborts_when_stale_review_and_group_now_hot(self) -> None:
        review = _make_review(age_minutes=45)
        now = time.time()
        # 3 non-self messages in last 30min → 熱絡
        msgs = [
            {"sender": "A", "text": "聊", "ts_epoch": now - 5 * 60, "is_self": False},
            {"sender": "B", "text": "聊", "ts_epoch": now - 10 * 60, "is_self": False},
            {"sender": "C", "text": "聊", "ts_epoch": now - 15 * 60, "is_self": False},
        ]
        with patch("app.workflows.read_chat.read_recent_chat", return_value=msgs):
            result = _check_pre_send_drift(
                customer_id="customer_a",
                community_id="g",
                device_id="emulator-5554",
                existing_review=review,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["reason"], "stale_review_group_now_hot")
        self.assertIn("熱絡", result["current_temperature"])

    def test_no_abort_when_stale_review_but_chat_quiet(self) -> None:
        review = _make_review(age_minutes=45)
        now = time.time()
        # No recent activity (last msg 200min ago, only 1)
        msgs = [
            {"sender": "A", "text": "舊", "ts_epoch": now - 200 * 60, "is_self": False},
        ]
        with patch("app.workflows.read_chat.read_recent_chat", return_value=msgs):
            result = _check_pre_send_drift(
                customer_id="customer_a",
                community_id="g",
                device_id="emulator-5554",
                existing_review=review,
            )
        # 45min old + sleepy chat → not stale enough for scenario 2,
        # not hot enough for scenario 1
        self.assertIsNone(result)


class VeryStaleReviewTests(unittest.TestCase):
    """Scenario 2: review > 3h old AND chat had non-self activity since."""

    def test_aborts_when_very_stale_and_chat_advanced(self) -> None:
        review_age_min = 200
        review = _make_review(age_minutes=review_age_min)
        review_created = review.created_at
        now = time.time()
        # Non-self message AFTER review was created
        msgs = [
            {"sender": "A", "text": "新訊息", "ts_epoch": review_created + 60 * 30, "is_self": False},
        ]
        with patch("app.workflows.read_chat.read_recent_chat", return_value=msgs):
            result = _check_pre_send_drift(
                customer_id="customer_a",
                community_id="g",
                device_id="emulator-5554",
                existing_review=review,
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["reason"], "very_stale_review_chat_advanced")
        self.assertEqual(result["non_self_msgs_since_review"], 1)

    def test_no_abort_when_very_stale_but_only_self_activity(self) -> None:
        review = _make_review(age_minutes=200)
        review_created = review.created_at
        # Only the operator typing since — doesn't count as drift
        msgs = [
            {"sender": "妍", "text": "我自己又發了一句", "ts_epoch": review_created + 60 * 30, "is_self": True},
        ]
        with patch("app.workflows.read_chat.read_recent_chat", return_value=msgs):
            result = _check_pre_send_drift(
                customer_id="customer_a",
                community_id="g",
                device_id="emulator-5554",
                existing_review=review,
            )
        self.assertIsNone(result)


class FailureSafetyTests(unittest.TestCase):
    """Drift check is best-effort — read failures should not block sends."""

    def test_no_existing_review_returns_none(self) -> None:
        result = _check_pre_send_drift(
            customer_id="customer_a",
            community_id="g",
            device_id="emulator-5554",
            existing_review=None,
        )
        self.assertIsNone(result)

    def test_chat_read_failure_does_not_block(self) -> None:
        review = _make_review(age_minutes=60)
        with patch(
            "app.workflows.read_chat.read_recent_chat",
            side_effect=RuntimeError("ADB timeout"),
        ), patch("app.workflows.job_processor.append_audit_event") as audit:
            result = _check_pre_send_drift(
                customer_id="customer_a",
                community_id="g",
                device_id="emulator-5554",
                existing_review=review,
            )
        self.assertIsNone(result)
        # Failure is audited so we can debug later
        types = [c[0][1] for c in audit.call_args_list]
        self.assertIn("approve_send_drift_read_failed", types)


if __name__ == "__main__":
    unittest.main()
