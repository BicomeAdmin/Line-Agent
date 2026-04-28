"""Tests for dashboard pure-logic helpers — daily digest gating + aging
alert dedup. Process-health and full data collection are integration-y;
covered by the live smoke-test rather than unit tests."""

import json
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from app.workflows import dashboard


def _taipei_epoch(year, month, day, hour, minute=0):
    tz = timezone(timedelta(hours=8))
    return datetime(year, month, day, hour, minute, tzinfo=tz).timestamp()


class DailyDigestGatingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_marker(self):
        return patch.object(
            dashboard,
            "daily_digest_marker_path",
            lambda customer_id: self.tmp_path / "marker.txt",
        )

    def test_fires_at_target_hour_when_no_marker(self):
        # 09:02 Taipei, target 09 — within window, never sent.
        with self._patch_marker():
            self.assertTrue(
                dashboard.should_send_daily_digest(
                    "customer_a",
                    target_hour_taipei=9,
                    now_epoch=_taipei_epoch(2026, 4, 29, 9, 2),
                )
            )

    def test_does_not_fire_outside_window(self):
        with self._patch_marker():
            # Same target hour but minute 06 — outside 5-min window.
            self.assertFalse(
                dashboard.should_send_daily_digest(
                    "customer_a",
                    target_hour_taipei=9,
                    now_epoch=_taipei_epoch(2026, 4, 29, 9, 6),
                )
            )
            # Wrong hour entirely.
            self.assertFalse(
                dashboard.should_send_daily_digest(
                    "customer_a",
                    target_hour_taipei=9,
                    now_epoch=_taipei_epoch(2026, 4, 29, 14, 0),
                )
            )

    def test_does_not_fire_twice_same_day(self):
        marker = self.tmp_path / "marker.txt"
        marker.write_text("2026-04-29", encoding="utf-8")
        with self._patch_marker():
            self.assertFalse(
                dashboard.should_send_daily_digest(
                    "customer_a",
                    target_hour_taipei=9,
                    now_epoch=_taipei_epoch(2026, 4, 29, 9, 1),
                )
            )

    def test_fires_again_on_next_day(self):
        marker = self.tmp_path / "marker.txt"
        marker.write_text("2026-04-29", encoding="utf-8")
        with self._patch_marker():
            self.assertTrue(
                dashboard.should_send_daily_digest(
                    "customer_a",
                    target_hour_taipei=9,
                    now_epoch=_taipei_epoch(2026, 4, 30, 9, 0),
                )
            )

    def test_mark_writes_today_taipei_date(self):
        with self._patch_marker():
            dashboard.mark_daily_digest_sent(
                "customer_a",
                now_epoch=_taipei_epoch(2026, 4, 29, 9, 3),
            )
            text = (self.tmp_path / "marker.txt").read_text(encoding="utf-8")
            self.assertEqual(text, "2026-04-29")


class AgingReviewAlertDedupeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_marker(self):
        return patch.object(
            dashboard,
            "aging_alert_marker_path",
            lambda customer_id: self.tmp_path / "aging.json",
        )

    def test_alerts_when_no_marker(self):
        with self._patch_marker():
            self.assertTrue(dashboard.should_alert_aging_review("customer_a", "review-1"))

    def test_does_not_realert_after_marking(self):
        with self._patch_marker():
            self.assertTrue(dashboard.should_alert_aging_review("customer_a", "review-1"))
            dashboard.mark_aging_alert_sent("customer_a", "review-1")
            self.assertFalse(dashboard.should_alert_aging_review("customer_a", "review-1"))

    def test_distinct_reviews_alert_independently(self):
        with self._patch_marker():
            dashboard.mark_aging_alert_sent("customer_a", "review-1")
            self.assertFalse(dashboard.should_alert_aging_review("customer_a", "review-1"))
            self.assertTrue(dashboard.should_alert_aging_review("customer_a", "review-2"))

    def test_corrupt_marker_treated_as_no_history(self):
        marker = self.tmp_path / "aging.json"
        marker.write_text("not json{", encoding="utf-8")
        with self._patch_marker():
            self.assertTrue(dashboard.should_alert_aging_review("customer_a", "review-1"))


class FormatTextReportTests(unittest.TestCase):
    def test_renders_minimal_data_without_crashing(self):
        data = {
            "generated_at_taipei": "2026-04-29 09:00:00",
            "health": {"scheduler_daemon": {"running": False}},
            "send_metrics_24h": {"totals": {"drafts_created": 0, "sent": 0, "ignored": 0, "review_pending": 0}},
            "communities": [],
            "pending_reviews": [],
            "active_watches": [],
            "recent_auto_fires": [],
            "recent_audit": [],
        }
        report = dashboard.format_text_report(data)
        self.assertIn("Project Echo 狀態", report)
        self.assertIn("❌", report)  # daemon not running marker
        self.assertIn("無", report)  # empty inbox marker

    def test_compact_skips_audit_section(self):
        data = {
            "generated_at_taipei": "2026-04-29 09:00:00",
            "health": {},
            "send_metrics_24h": {"totals": {"drafts_created": 1, "sent": 0, "ignored": 0, "review_pending": 1}},
            "communities": [],
            "pending_reviews": [],
            "active_watches": [],
            "recent_auto_fires": [],
            "recent_audit": [
                {"ts_taipei": "2026-04-29 08:55", "event_type": "send_attempt", "summary": "openchat_001"},
            ],
        }
        full = dashboard.format_text_report(data, compact=False)
        compact = dashboard.format_text_report(data, compact=True)
        self.assertIn("最近事件", full)
        self.assertNotIn("最近事件", compact)


if __name__ == "__main__":
    unittest.main()
