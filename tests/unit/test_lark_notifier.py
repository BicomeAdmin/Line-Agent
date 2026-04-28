"""Tests for the operator review-card notifier.

Skips actual Lark calls — verifies env-gating, error swallowing, and
title resolution by reason."""

import os
import unittest
from unittest.mock import patch, MagicMock

from app.core.reviews import ReviewRecord
from app.lark import notifier


def _make_record(reason="mcp_compose:operator", review_id="job-test-1"):
    return ReviewRecord(
        review_id=review_id,
        source_job_id=review_id,
        customer_id="customer_a",
        customer_name="客戶 A",
        community_id="openchat_test",
        community_name="測試群",
        device_id="emulator-5554",
        draft_text="測試草稿",
        reason=reason,
    )


class NotifyOperatorOfNewReviewTests(unittest.TestCase):
    def test_no_op_when_chat_id_unset(self):
        with patch.dict(os.environ, {"OPERATOR_DAILY_DIGEST_CHAT_ID": ""}, clear=False):
            result = notifier.notify_operator_of_new_review(_make_record())
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_operator_chat_id")

    def test_pushes_card_when_chat_id_set(self):
        fake_client = MagicMock()
        with patch.dict(os.environ, {"OPERATOR_DAILY_DIGEST_CHAT_ID": "oc_abc123"}, clear=False), \
             patch("app.lark.client.LarkClient", return_value=fake_client), \
             patch.object(notifier, "append_audit_event"):
            result = notifier.notify_operator_of_new_review(_make_record())
        self.assertEqual(result["status"], "ok")
        fake_client.send_card.assert_called_once()
        # Verify chat_id passed through, receive_id_type set:
        _, kwargs = fake_client.send_card.call_args
        args = fake_client.send_card.call_args.args
        self.assertEqual(args[0], "oc_abc123")
        self.assertEqual(kwargs.get("receive_id_type"), "chat_id")

    def test_swallows_lark_error_and_audits(self):
        from app.lark.client import LarkClientError

        fake_client = MagicMock()
        fake_client.send_card.side_effect = LarkClientError("boom")
        audit_calls = []

        def fake_audit(customer_id, event_type, payload):
            audit_calls.append((event_type, payload))

        with patch.dict(os.environ, {"OPERATOR_DAILY_DIGEST_CHAT_ID": "oc_abc"}, clear=False), \
             patch("app.lark.client.LarkClient", return_value=fake_client), \
             patch.object(notifier, "append_audit_event", side_effect=fake_audit):
            result = notifier.notify_operator_of_new_review(_make_record())
        self.assertEqual(result["status"], "error")
        self.assertIn("lark_error", result["reason"])
        # Failure path audited — operator can spot the miss in dashboard.
        self.assertTrue(any(et == "operator_review_card_failed" for et, _ in audit_calls))

    def test_title_resolution_by_reason(self):
        cases = {
            "mcp_compose:operator": "操作員擬稿",
            "mcp_compose:auto_watch": "自動追蹤擬稿",
            "mcp_compose": "LLM 擬稿",
            "patrol": "巡邏擬稿",
            "scheduled_post": "排程擬稿",
            "edit_required": "編輯後重審",
            "totally_unknown_reason": "待審核",
        }
        for reason, expected_substr in cases.items():
            title = notifier._resolve_card_title(_make_record(reason=reason))
            self.assertIn(
                expected_substr,
                title,
                f"reason={reason!r} expected substring {expected_substr!r} in title {title!r}",
            )


if __name__ == "__main__":
    unittest.main()
