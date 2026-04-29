"""Tests for event_health_report — read-only health surface for the day's
ignition events (09:00 daily digest + 10:00 watcher cycle).
"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.workflows import event_health_report as ehr


TPE = ZoneInfo("Asia/Taipei")


class DigestHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _patch_paths(self, marker_value: str | None, log_lines: list[str]):
        marker_file = self.root / "marker.txt"
        if marker_value is not None:
            marker_file.write_text(marker_value, encoding="utf-8")
        log_file = self.root / "scheduler.log"
        log_file.write_text("\n".join(log_lines), encoding="utf-8")
        return (
            patch.object(ehr, "daily_digest_marker_path", return_value=marker_file),
            patch.object(ehr, "SCHEDULER_LOG", log_file),
            patch.object(ehr, "collect_dashboard_data", return_value={}),
            patch.object(ehr, "format_text_report", return_value=(
                "🩺 系統健康\n"
                "📨 24h 送發統計（Asia/Taipei）\n"
                "🌐 社群\n"
                "📥 待審 inbox\n"
                "🛎  最近 auto-fire\n"
            )),
        )

    def test_marker_today_means_sent(self) -> None:
        now = datetime(2026, 4, 29, 9, 10, tzinfo=TPE)
        patches = self._patch_paths("2026-04-29", ["[scheduler] daily_digest pushed to oc_xxx…"])
        for p in patches:
            p.start()
        try:
            h = ehr.collect_digest_health(now=now)
        finally:
            for p in patches:
                p.stop()
        self.assertTrue(h.sent_today)
        self.assertEqual(len(h.log_push_lines), 1)
        self.assertEqual(h.sections_present["system_health"], True)
        self.assertEqual(h.sections_present["communities"], True)

    def test_no_marker_means_not_sent(self) -> None:
        now = datetime(2026, 4, 29, 8, 30, tzinfo=TPE)
        patches = self._patch_paths(None, [])
        for p in patches:
            p.start()
        try:
            h = ehr.collect_digest_health(now=now)
        finally:
            for p in patches:
                p.stop()
        self.assertFalse(h.sent_today)
        self.assertFalse(h.marker_present)

    def test_yesterdays_marker_means_not_sent_today(self) -> None:
        now = datetime(2026, 4, 29, 8, 30, tzinfo=TPE)
        patches = self._patch_paths("2026-04-28", [])
        for p in patches:
            p.start()
        try:
            h = ehr.collect_digest_health(now=now)
        finally:
            for p in patches:
                p.stop()
        self.assertFalse(h.sent_today)
        self.assertTrue(h.marker_present)

    def test_error_lines_surfaced(self) -> None:
        now = datetime(2026, 4, 29, 9, 10, tzinfo=TPE)
        patches = self._patch_paths(None, ["[scheduler] daily_digest lark error=timeout"])
        for p in patches:
            p.start()
        try:
            h = ehr.collect_digest_health(now=now)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(len(h.log_error_lines), 1)


class WatcherHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _setup_watches(self, watches: list[dict]) -> None:
        path = self.root / "customers" / "customer_a" / "data" / "watches.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(watches), encoding="utf-8")

    def test_only_active_unexpired_watches_counted(self) -> None:
        now = datetime(2026, 4, 29, 10, 30, tzinfo=TPE)
        now_epoch = now.timestamp()
        self._setup_watches([
            {"watch_id": "w1", "status": "active", "end_at_epoch": now_epoch + 600, "community_id": "openchat_001"},
            {"watch_id": "w2", "status": "active", "end_at_epoch": now_epoch - 100, "community_id": "openchat_002"},  # expired by time
            {"watch_id": "w3", "status": "expired", "end_at_epoch": now_epoch + 600, "community_id": "openchat_003"},  # status expired
            {"watch_id": "w4", "status": "cancelled", "end_at_epoch": now_epoch + 600, "community_id": "openchat_004"},
        ])
        with patch("app.workflows.event_health_report.Path", side_effect=lambda p: self.root / p), \
             patch.object(ehr, "read_recent_audit_events", return_value=[]):
            h = ehr.collect_watcher_health(now=now)
        self.assertEqual(len(h.watches_active), 1)
        self.assertEqual(h.watches_active[0]["watch_id"], "w1")

    def test_audit_event_window_filter(self) -> None:
        now = datetime(2026, 4, 29, 10, 30, tzinfo=TPE)
        # Cutoff is 2h before now in UTC iso. 08:30 TPE = 00:30 UTC; 09:30 TPE = 01:30 UTC (within window).
        events = [
            {"timestamp": "2026-04-29T01:30:00+00:00", "event_type": "watch_tick_fired", "payload": {}},   # in
            {"timestamp": "2026-04-28T22:00:00+00:00", "event_type": "watch_tick_fired", "payload": {}},   # too old
            {"timestamp": "2026-04-29T01:45:00+00:00", "event_type": "operator_review_card_pushed", "payload": {}},
            {"timestamp": "2026-04-29T01:50:00+00:00", "event_type": "mcp_compose_review_created", "payload": {}},
        ]
        with patch("app.workflows.event_health_report.Path", side_effect=lambda p: self.root / p), \
             patch.object(ehr, "read_recent_audit_events", return_value=events):
            h = ehr.collect_watcher_health(now=now)
        self.assertEqual(len(h.recent_tick_events), 1)
        self.assertEqual(len(h.recent_review_cards), 1)
        self.assertEqual(len(h.recent_compose_reviews), 1)


if __name__ == "__main__":
    unittest.main()
