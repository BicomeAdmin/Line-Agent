"""Regression tests for extract_card_action — must accept both the v1
webhook schema (action at top level) and the v2 long-connection schema
(action nested under event)."""

import unittest

from app.lark.events import extract_card_action


def _make_value(action="approve", job_id="job-test-1"):
    return {
        "action": action,
        "job_id": job_id,
        "customer_id": "customer_a",
        "community_id": "openchat_001",
        "device_id": "emulator-5554",
        "draft_text": "測試草稿",
    }


class ExtractCardActionTests(unittest.TestCase):
    def test_v1_webhook_top_level_action(self):
        payload = {
            "type": "event_callback",
            "action": {"value": _make_value()},
        }
        result = extract_card_action(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result["job_id"], "job-test-1")
        self.assertEqual(result["action"], "approve")
        self.assertEqual(result["customer_id"], "customer_a")

    def test_v2_long_connection_nested_under_event(self):
        # Shape captured live from lark-oapi WsClient on 2026-04-28.
        payload = {
            "schema": "2.0",
            "header": {"event_type": "card.action.trigger"},
            "event": {
                "operator": {"open_id": "ou_xxx"},
                "token": "c-xyz",
                "action": {"value": _make_value(action="ignore", job_id="job-v2")},
            },
        }
        result = extract_card_action(payload)
        self.assertIsNotNone(result)
        self.assertEqual(result["job_id"], "job-v2")
        self.assertEqual(result["action"], "ignore")

    def test_v1_takes_precedence_when_both_present(self):
        # Defensive: if a payload somehow has both, prefer top-level.
        payload = {
            "action": {"value": _make_value(job_id="job-top")},
            "event": {"action": {"value": _make_value(job_id="job-nested")}},
        }
        result = extract_card_action(payload)
        self.assertEqual(result["job_id"], "job-top")

    def test_returns_none_when_no_action_anywhere(self):
        self.assertIsNone(extract_card_action({}))
        self.assertIsNone(extract_card_action({"event": {}}))
        self.assertIsNone(extract_card_action({"event": {"action": "not-a-dict"}}))

    def test_returns_none_when_value_missing_job_id(self):
        payload = {"event": {"action": {"value": {"action": "approve"}}}}
        self.assertIsNone(extract_card_action(payload))

    def test_returns_none_when_value_missing_action_name(self):
        payload = {"event": {"action": {"value": {"job_id": "job-x"}}}}
        self.assertIsNone(extract_card_action(payload))


if __name__ == "__main__":
    unittest.main()
