"""Tests for the cold-spell heartbeat alert."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.storage.config_loader import CommunityConfig
from app.workflows import cold_spell_alert as cs


def _make_community(community_id: str, **overrides) -> CommunityConfig:
    defaults = dict(
        customer_id="customer_a",
        community_id=community_id,
        display_name=community_id,
        persona="default",
        device_id="emulator-5554",
        patrol_interval_minutes=120,
    )
    defaults.update(overrides)
    return CommunityConfig(**defaults)


def _analyze_event(community_id: str, state: str, hours_ago: float, *, now: datetime) -> dict:
    ts = (now - timedelta(hours=hours_ago)).isoformat()
    return {
        "timestamp": ts,
        "event_type": "community_chat_analyzed",
        "payload": {"community_id": community_id, "active_state": state},
    }


class ColdSpellAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 4, 29, 12, 0, 0, tzinfo=timezone.utc)
        self.audit_calls: list[tuple] = []

        def fake_audit(customer_id, event_type, payload):
            self.audit_calls.append((customer_id, event_type, payload))

        # Redirect marker dir into temp
        self.marker_patch = patch.object(
            cs, "customer_data_root", return_value=self.root / "customer_a" / "data"
        )
        self.marker_patch.start()

        self.audit_patch = patch.object(cs, "append_audit_event", side_effect=fake_audit)
        self.audit_patch.start()

    def tearDown(self) -> None:
        self.marker_patch.stop()
        self.audit_patch.stop()
        self.tmp.cleanup()

    def test_alerts_on_fresh_cold_spell(self) -> None:
        events = [_analyze_event("openchat_001", "cold_spell", 2.0, now=self.now)]
        with patch.object(cs, "read_all_audit_events", return_value=events):
            result = cs.run_heartbeat(
                now=self.now,
                push_lark=False,
                communities=[_make_community("openchat_001")],
            )
        self.assertEqual(len(result.alerted), 1)
        self.assertEqual(result.alerted[0].community_id, "openchat_001")
        self.assertEqual(result.alerted[0].state, "cold_spell")
        # Audit event recorded
        self.assertTrue(any(c[1] == "cold_spell_alert_marked" for c in self.audit_calls))

    def test_alerts_on_quiet_state(self) -> None:
        events = [_analyze_event("openchat_002", "quiet", 3.0, now=self.now)]
        with patch.object(cs, "read_all_audit_events", return_value=events):
            result = cs.run_heartbeat(
                now=self.now,
                push_lark=False,
                communities=[_make_community("openchat_002")],
            )
        self.assertEqual(len(result.alerted), 1)

    def test_skips_active_state(self) -> None:
        events = [_analyze_event("openchat_003", "active", 1.0, now=self.now)]
        with patch.object(cs, "read_all_audit_events", return_value=events):
            result = cs.run_heartbeat(
                now=self.now,
                push_lark=False,
                communities=[_make_community("openchat_003")],
            )
        self.assertEqual(len(result.alerted), 0)
        # Candidate is reported but won't_alert
        self.assertEqual(result.candidates[0].state, "active")
        self.assertFalse(result.candidates[0].will_alert)

    def test_skips_trickle_state(self) -> None:
        # Trickle means SOMETHING is happening; not silence.
        events = [_analyze_event("openchat_004", "trickle", 1.0, now=self.now)]
        with patch.object(cs, "read_all_audit_events", return_value=events):
            result = cs.run_heartbeat(
                now=self.now,
                push_lark=False,
                communities=[_make_community("openchat_004")],
            )
        self.assertEqual(len(result.alerted), 0)

    def test_marks_stale_signal_when_analyze_too_old(self) -> None:
        # 24h-old analyze, our threshold is 12h
        events = [_analyze_event("openchat_005", "cold_spell", 24.0, now=self.now)]
        with patch.object(cs, "read_all_audit_events", return_value=events):
            result = cs.run_heartbeat(
                now=self.now,
                push_lark=False,
                communities=[_make_community("openchat_005")],
            )
        self.assertEqual(len(result.alerted), 0)
        self.assertEqual(result.candidates[0].state, "stale_signal")

    def test_no_signal_when_no_analyze_event(self) -> None:
        with patch.object(cs, "read_all_audit_events", return_value=[]):
            result = cs.run_heartbeat(
                now=self.now,
                push_lark=False,
                communities=[_make_community("openchat_006")],
            )
        self.assertEqual(len(result.alerted), 0)
        self.assertEqual(result.candidates[0].state, "no_signal")

    def test_cooldown_skips_repeat(self) -> None:
        events = [_analyze_event("openchat_007", "cold_spell", 1.0, now=self.now)]
        community = _make_community("openchat_007")
        with patch.object(cs, "read_all_audit_events", return_value=events):
            cs.run_heartbeat(now=self.now, push_lark=False, communities=[community])
            # Second call within cooldown window
            result2 = cs.run_heartbeat(
                now=self.now + timedelta(hours=1),
                push_lark=False,
                communities=[community],
                alert_cooldown_hours=24.0,
            )
        self.assertEqual(len(result2.alerted), 0)
        self.assertEqual(len(result2.skipped_cooldown), 1)

    def test_cooldown_clears_after_window(self) -> None:
        events = [_analyze_event("openchat_008", "cold_spell", 1.0, now=self.now)]
        community = _make_community("openchat_008")
        with patch.object(cs, "read_all_audit_events", return_value=events):
            cs.run_heartbeat(now=self.now, push_lark=False, communities=[community])
            # 25h later — outside 24h cooldown
            later_events = [_analyze_event("openchat_008", "cold_spell", 1.0, now=self.now + timedelta(hours=25))]
            with patch.object(cs, "read_all_audit_events", return_value=later_events):
                result2 = cs.run_heartbeat(
                    now=self.now + timedelta(hours=25),
                    push_lark=False,
                    communities=[community],
                    alert_cooldown_hours=24.0,
                )
        self.assertEqual(len(result2.alerted), 1)


if __name__ == "__main__":
    unittest.main()
