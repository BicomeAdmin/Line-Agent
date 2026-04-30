"""Tests for post-send verification — the belt that catches "ADB
said sent" but message never landed in the LINE chat."""

import unittest
from unittest.mock import MagicMock, patch

from app.workflows.send_verification import (
    SendVerification,
    _matches,
    _normalize,
    verify_send,
)


class NormalizeTests(unittest.TestCase):
    def test_collapses_whitespace(self) -> None:
        self.assertEqual(_normalize("我  覺得   啊"), "我覺得啊")

    def test_strips_leading_trailing(self) -> None:
        self.assertEqual(_normalize("  我覺得  "), "我覺得")

    def test_empty(self) -> None:
        self.assertEqual(_normalize(""), "")


class MatchesTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        self.assertTrue(_matches("我覺得", _normalize("我覺得")))

    def test_substring_match_either_direction(self) -> None:
        # Landed contains expected
        self.assertTrue(_matches("我覺得不錯啊", _normalize("我覺得")))
        # Expected contains landed (unusual but covered)
        self.assertTrue(_matches("我覺得", _normalize("我覺得不錯啊")))

    def test_whitespace_difference_tolerated(self) -> None:
        self.assertTrue(_matches("我 覺 得 不錯", _normalize("我覺得不錯")))

    def test_completely_different_fails(self) -> None:
        self.assertFalse(_matches("你好嗎", _normalize("我覺得")))


class VerifySendTests(unittest.TestCase):
    def test_match_on_first_read(self) -> None:
        msgs = [
            {"sender": "Alice", "text": "你呢", "is_self": False},
            {"sender": "__operator__", "text": "我覺得不錯啊", "is_self": True},
        ]
        with patch("app.workflows.send_verification.read_recent_chat", return_value=msgs):
            v = verify_send(MagicMock(), "/tmp/x", "我覺得不錯啊")
        self.assertTrue(v.ok)
        self.assertEqual(v.reason, "match")

    def test_polls_until_bubble_appears(self) -> None:
        # First read: no self bubble. Second read: bubble appears.
        first = [{"sender": "Alice", "text": "你呢", "is_self": False}]
        second = first + [{"sender": "__operator__", "text": "我覺得不錯", "is_self": True}]
        with patch("app.workflows.send_verification.read_recent_chat", side_effect=[first, second]), \
             patch("app.workflows.send_verification.time.sleep"):
            v = verify_send(MagicMock(), "/tmp/x", "我覺得不錯", max_attempts=3, sleep_seconds=0.0)
        self.assertTrue(v.ok)

    def test_no_self_bubble_after_send(self) -> None:
        msgs = [{"sender": "Alice", "text": "x", "is_self": False}]
        with patch("app.workflows.send_verification.read_recent_chat", return_value=msgs), \
             patch("app.workflows.send_verification.time.sleep"):
            v = verify_send(MagicMock(), "/tmp/x", "我覺得", max_attempts=2, sleep_seconds=0.0)
        self.assertFalse(v.ok)
        self.assertEqual(v.reason, "no_self_bubble_after_send")

    def test_self_bubble_does_not_match(self) -> None:
        # Latest self-bubble is OLD content (operator typed something else
        # earlier), draft never landed.
        msgs = [
            {"sender": "__operator__", "text": "舊訊息", "is_self": True},
            {"sender": "Alice", "text": "回應", "is_self": False},
        ]
        with patch("app.workflows.send_verification.read_recent_chat", return_value=msgs), \
             patch("app.workflows.send_verification.time.sleep"):
            v = verify_send(MagicMock(), "/tmp/x", "我這次想說的話", max_attempts=2, sleep_seconds=0.0)
        self.assertFalse(v.ok)
        self.assertEqual(v.reason, "latest_self_bubble_does_not_match")
        self.assertIn("舊訊息", v.matched_text)

    def test_read_failure_short_circuits(self) -> None:
        with patch("app.workflows.send_verification.read_recent_chat",
                   side_effect=RuntimeError("ADB drop")):
            v = verify_send(MagicMock(), "/tmp/x", "我覺得")
        self.assertFalse(v.ok)
        self.assertTrue(v.reason.startswith("read_failed"))

    def test_empty_expected_fails_safe(self) -> None:
        v = verify_send(MagicMock(), "/tmp/x", "")
        self.assertFalse(v.ok)
        self.assertEqual(v.reason, "empty_expected")


if __name__ == "__main__":
    unittest.main()
