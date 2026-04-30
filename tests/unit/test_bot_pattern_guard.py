"""Tests for cumulative bot-fingerprint detection."""

import time
import unittest
from datetime import datetime, timezone

from app.workflows.bot_pattern_guard import (
    BLOCK_DAILY_COUNT,
    REPEAT_THRESHOLD,
    WARN_DAILY_COUNT,
    _opening_phrase,
    assess_bot_pattern_risk,
)


def _ev(event_type: str, *, ts: float, community_id: str, text_preview: str = "") -> dict:
    return {
        "event_type": event_type,
        "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
        "payload": {"community_id": community_id, "text_preview": text_preview},
    }


class OpeningPhraseTests(unittest.TestCase):
    def test_extracts_first_two_han_chars(self) -> None:
        self.assertEqual(_opening_phrase("我覺得這個不錯"), "我覺")

    def test_skips_leading_punctuation_and_emoji(self) -> None:
        self.assertEqual(_opening_phrase("✨我也是這樣想"), "我也")

    def test_skips_leading_whitespace(self) -> None:
        self.assertEqual(_opening_phrase("   感覺不錯"), "感覺")

    def test_returns_empty_for_no_han_chars(self) -> None:
        self.assertEqual(_opening_phrase("hello world"), "")

    def test_returns_empty_for_empty(self) -> None:
        self.assertEqual(_opening_phrase(""), "")


class DailyCountThresholdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_700_000_000.0

    def _events(self, count: int, *, community_id: str = "g") -> list[dict]:
        # All events within 12h of now → counted in the rolling 24h window.
        # Use DIVERSE openings so repetition heuristic doesn't bump risk
        # — these tests target daily-count thresholds in isolation.
        diverse_openings = ["我覺", "感覺", "其實", "好像", "我自", "對啊", "可能", "原來", "想說", "今天"]
        return [
            _ev("mcp_compose_review_created",
                ts=self.now - i * 60 * 60 * 0.5,  # spread over 12h
                community_id=community_id,
                text_preview=f"{diverse_openings[i % len(diverse_openings)]}得 draft #{i}")
            for i in range(count)
        ]

    def test_below_warn_returns_ok(self) -> None:
        events = self._events(WARN_DAILY_COUNT - 1)
        v = assess_bot_pattern_risk("c", "g", now=self.now, audit_events=events)
        self.assertEqual(v.risk, "ok")
        self.assertEqual(v.daily_draft_count, WARN_DAILY_COUNT - 1)

    def test_at_warn_threshold_warns(self) -> None:
        events = self._events(WARN_DAILY_COUNT)
        v = assess_bot_pattern_risk("c", "g", now=self.now, audit_events=events)
        # WARN_DAILY_COUNT count + same opening across all → bumped to warn
        self.assertIn(v.risk, ("warn", "block"))

    def test_at_block_threshold_blocks(self) -> None:
        events = self._events(BLOCK_DAILY_COUNT)
        v = assess_bot_pattern_risk("c", "g", now=self.now, audit_events=events)
        self.assertEqual(v.risk, "block")
        self.assertEqual(v.daily_draft_count, BLOCK_DAILY_COUNT)

    def test_outside_24h_window_excluded(self) -> None:
        # Drafts more than 24h ago don't count
        old_events = [
            _ev("mcp_compose_review_created",
                ts=self.now - 30 * 3600,  # 30h ago
                community_id="g",
                text_preview="x")
            for _ in range(BLOCK_DAILY_COUNT)
        ]
        v = assess_bot_pattern_risk("c", "g", now=self.now, audit_events=old_events)
        self.assertEqual(v.risk, "ok")
        self.assertEqual(v.daily_draft_count, 0)

    def test_other_communities_excluded(self) -> None:
        # Drafts in DIFFERENT communities don't pollute this assessment
        events = [
            _ev("mcp_compose_review_created", ts=self.now - 100,
                community_id="other", text_preview="x")
            for _ in range(BLOCK_DAILY_COUNT)
        ]
        v = assess_bot_pattern_risk("c", "g", now=self.now, audit_events=events)
        self.assertEqual(v.risk, "ok")


class OpeningRepetitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_700_000_000.0

    def test_repeated_opening_warns(self) -> None:
        # 3 of last 5 drafts open with "我覺" → repetition tip
        events = [
            _ev("mcp_compose_review_created", ts=self.now - 60,
                community_id="g", text_preview="我覺得不錯啊"),
            _ev("mcp_compose_review_created", ts=self.now - 120,
                community_id="g", text_preview="我覺得也是耶"),
            _ev("mcp_compose_review_created", ts=self.now - 180,
                community_id="g", text_preview="我覺得這個喔"),
            _ev("mcp_compose_review_created", ts=self.now - 240,
                community_id="g", text_preview="感覺差不多吧"),
        ]
        v = assess_bot_pattern_risk("c", "g", now=self.now, audit_events=events)
        # 4 drafts (under daily warn = 5) but 3× "我覺" → warn
        self.assertEqual(v.risk, "warn")
        self.assertTrue(any(p[0] == "我覺" and p[1] >= REPEAT_THRESHOLD for p in v.repeated_openings))

    def test_diverse_openings_no_repetition_warn(self) -> None:
        events = [
            _ev("mcp_compose_review_created", ts=self.now - 60,
                community_id="g", text_preview="我覺得 X"),
            _ev("mcp_compose_review_created", ts=self.now - 120,
                community_id="g", text_preview="感覺 Y"),
            _ev("mcp_compose_review_created", ts=self.now - 180,
                community_id="g", text_preview="不過 Z"),
            _ev("mcp_compose_review_created", ts=self.now - 240,
                community_id="g", text_preview="我自己也"),
        ]
        v = assess_bot_pattern_risk("c", "g", now=self.now, audit_events=events)
        self.assertEqual(v.risk, "ok")
        self.assertEqual(v.repeated_openings, ())


class EventTypeFilteringTests(unittest.TestCase):
    def test_unrelated_event_types_ignored(self) -> None:
        now = 1_700_000_000.0
        events = [
            _ev("send_attempt", ts=now - 60, community_id="g", text_preview="x")
            for _ in range(BLOCK_DAILY_COUNT)
        ]
        v = assess_bot_pattern_risk("c", "g", now=now, audit_events=events)
        self.assertEqual(v.daily_draft_count, 0)
        self.assertEqual(v.risk, "ok")

    def test_scheduled_post_compose_succeeded_counts(self) -> None:
        now = 1_700_000_000.0
        events = [
            _ev("scheduled_post_compose_succeeded",
                ts=now - i * 60, community_id="g",
                text_preview=f"draft {i}")
            for i in range(BLOCK_DAILY_COUNT)
        ]
        # text_preview key check — code reads "text_preview" OR "draft_preview"
        v = assess_bot_pattern_risk("c", "g", now=now, audit_events=events)
        self.assertEqual(v.daily_draft_count, BLOCK_DAILY_COUNT)
        self.assertEqual(v.risk, "block")


if __name__ == "__main__":
    unittest.main()
