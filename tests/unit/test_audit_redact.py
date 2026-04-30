"""Tests for audit-event redaction (member privacy on external sharing)."""

import unittest

from app.core.audit_redact import redact_event, redact_events


class DefaultLevelTests(unittest.TestCase):
    def test_strips_draft_text(self) -> None:
        ev = {
            "event_type": "send_attempt",
            "timestamp": "2026-04-30T12:00:00Z",
            "customer_id": "customer_a",
            "payload": {
                "community_id": "openchat_004",
                "draft_text": "我覺得不錯啊",
                "status": "sent",
            },
        }
        out = redact_event(ev)
        self.assertEqual(out["payload"]["draft_text"], "[redacted 6 chars]")
        # Non-content fields preserved
        self.assertEqual(out["payload"]["community_id"], "openchat_004")
        self.assertEqual(out["payload"]["status"], "sent")
        self.assertEqual(out["customer_id"], "customer_a")
        self.assertTrue(out["redacted"])
        self.assertEqual(out["redaction_level"], "default")

    def test_strips_target_message(self) -> None:
        ev = {
            "event_type": "mcp_compose_review_created",
            "timestamp": "2026-04-30T12:00:00Z",
            "customer_id": "c", "payload": {
                "target_sender": "Alice",
                "target_message": "原文敏感內容",
                "community_id": "g",
            },
        }
        out = redact_event(ev)
        self.assertEqual(out["payload"]["target_message"], "[redacted 6 chars]")
        self.assertEqual(out["payload"]["target_sender"], "[sender]")
        self.assertEqual(out["payload"]["community_id"], "g")  # default keeps community

    def test_strips_text_preview_inside_nested_dict(self) -> None:
        ev = {
            "event_type": "send_safety_blocked", "timestamp": "x",
            "customer_id": "c", "payload": {
                "community_id": "g",
                "verdict": {
                    "issues": [
                        {"code": "url_in_draft", "matched": "https://shopee.tw/abc"},
                    ],
                },
            },
        }
        out = redact_event(ev)
        # Nested matched field redacted
        issue = out["payload"]["verdict"]["issues"][0]
        self.assertEqual(issue["code"], "url_in_draft")
        self.assertTrue(issue["matched"].startswith("[redacted"))

    def test_recent_lines_list_redacted(self) -> None:
        ev = {
            "event_type": "x", "timestamp": "x", "customer_id": "c",
            "payload": {
                "recent_lines": ["我也是耶", "感覺差不多吧"],
            },
        }
        out = redact_event(ev)
        self.assertTrue(out["payload"]["recent_lines"].startswith("[redacted list"))


class MinimalLevelTests(unittest.TestCase):
    def test_strips_community_id(self) -> None:
        ev = {
            "event_type": "x", "timestamp": "x", "customer_id": "customer_a",
            "payload": {
                "community_id": "openchat_004",
                "community_name": "水月觀音道場",
                "draft_text": "x",
            },
        }
        out = redact_event(ev, level="minimal")
        self.assertEqual(out["payload"]["community_id"], "[community]")
        self.assertEqual(out["payload"]["community_name"], "[community]")
        # Customer also redacted at minimal level
        self.assertEqual(out["customer_id"], "[redacted]")


class RobustnessTests(unittest.TestCase):
    def test_empty_string_marker(self) -> None:
        ev = {"event_type": "x", "timestamp": "x", "customer_id": "c",
              "payload": {"draft_text": ""}}
        self.assertEqual(redact_event(ev)["payload"]["draft_text"], "[empty]")

    def test_none_marker(self) -> None:
        ev = {"event_type": "x", "timestamp": "x", "customer_id": "c",
              "payload": {"draft_text": None}}
        self.assertEqual(redact_event(ev)["payload"]["draft_text"], "[empty]")

    def test_invalid_level_raises(self) -> None:
        with self.assertRaises(ValueError):
            redact_event({"event_type": "x", "timestamp": "x",
                          "customer_id": "c", "payload": {}}, level="aggressive")

    def test_redact_events_batch(self) -> None:
        evs = [
            {"event_type": "x", "timestamp": "x", "customer_id": "c",
             "payload": {"draft_text": "abc"}}
            for _ in range(5)
        ]
        out = redact_events(evs)
        self.assertEqual(len(out), 5)
        self.assertTrue(all(e["redacted"] for e in out))

    def test_unrelated_payload_unchanged(self) -> None:
        ev = {"event_type": "x", "timestamp": "x", "customer_id": "c",
              "payload": {"count": 5, "ratio": 0.3, "reason": "ok"}}
        out = redact_event(ev)
        self.assertEqual(out["payload"]["count"], 5)
        self.assertEqual(out["payload"]["ratio"], 0.3)
        self.assertEqual(out["payload"]["reason"], "ok")


if __name__ == "__main__":
    unittest.main()
