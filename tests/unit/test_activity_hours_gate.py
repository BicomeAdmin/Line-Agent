"""Tests for the activity-hours gate (default 10:00-22:00 Asia/Taipei).

Outside the window, autonomous fires (watcher, patrol) skip without
running codex / navigating LINE. Operator-driven flows are unaffected
(those don't go through the gate)."""

import unittest
from datetime import datetime, time as day_time, timezone, timedelta
from unittest.mock import patch

from app.core.risk_control import RiskControl


def _at(hour, minute=0):
    """Return a TPE-aware datetime for the given local hour."""
    tpe = timezone(timedelta(hours=8))
    return datetime(2026, 4, 28, hour, minute, tzinfo=tpe)


class ActivityHoursTests(unittest.TestCase):
    def test_default_window_10_to_22(self):
        rc = RiskControl(
            activity_start=day_time(10, 0),
            activity_end=day_time(22, 0),
        )
        self.assertFalse(rc.is_activity_time(_at(9, 30)))   # before window
        self.assertTrue(rc.is_activity_time(_at(10, 0)))    # boundary in
        self.assertTrue(rc.is_activity_time(_at(15, 0)))    # mid-day
        self.assertTrue(rc.is_activity_time(_at(22, 0)))    # boundary in
        self.assertFalse(rc.is_activity_time(_at(22, 30)))  # after window
        self.assertFalse(rc.is_activity_time(_at(2, 0)))    # late night
        self.assertFalse(rc.is_activity_time(_at(7, 0)))    # early morning

    def test_custom_window(self):
        rc = RiskControl(
            activity_start=day_time(8, 0),
            activity_end=day_time(20, 0),
        )
        self.assertTrue(rc.is_activity_time(_at(8, 0)))
        self.assertTrue(rc.is_activity_time(_at(20, 0)))
        self.assertFalse(rc.is_activity_time(_at(7, 59)))
        self.assertFalse(rc.is_activity_time(_at(20, 1)))

    def test_uses_taipei_tz_when_no_arg(self):
        rc = RiskControl(
            activity_start=day_time(10, 0),
            activity_end=day_time(22, 0),
        )
        # Mock taipei_now to return inside-window
        with patch("app.core.timezone.taipei_now", return_value=_at(15, 0)):
            self.assertTrue(rc.is_activity_time())
        with patch("app.core.timezone.taipei_now", return_value=_at(3, 0)):
            self.assertFalse(rc.is_activity_time())


class _OffHoursStub:
    """Frozen dataclass means we can't patch methods on the singleton —
    swap in a stub object that always reports outside-hours."""

    def __init__(self):
        self.activity_start = day_time(10, 0)
        self.activity_end = day_time(22, 0)

    def is_activity_time(self, now=None):
        return False


class WatchTickGateTests(unittest.TestCase):
    """Verify the in-process tick short-circuits outside activity hours."""

    def test_skips_outside_window(self):
        from app.workflows import watch_tick_inproc
        from app.core import risk_control

        watch = {
            "customer_id": "customer_a",
            "community_id": "openchat_003",
            "watch_id": "watch-test",
            "cooldown_seconds": 300,
            "last_draft_epoch": 0,
            "last_seen_signature": "",
        }
        with patch.object(risk_control, "default_risk_control", _OffHoursStub()):
            result = watch_tick_inproc.tick_one_inprocess(watch)
        self.assertFalse(result["acted"])
        self.assertEqual(result["reason"], "outside_activity_hours")
        self.assertIn("activity_window", result)


class PatrolGateTests(unittest.TestCase):
    def test_skips_all_patrols_outside_window(self):
        from app.workflows import scheduler
        from app.core import risk_control

        with patch.object(risk_control, "default_risk_control", _OffHoursStub()):
            result = scheduler.enqueue_due_patrols()
        self.assertEqual(result["enqueued_count"], 0)
        # All communities should appear in skipped with outside_activity_hours.
        self.assertGreater(result["skipped_count"], 0)
        for s in result["skipped"]:
            self.assertEqual(s["reason"], "outside_activity_hours")


if __name__ == "__main__":
    unittest.main()
