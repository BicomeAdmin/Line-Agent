"""Tests for auto_watch — per-community opt-in, idempotent start/stop, audit."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.workflows import auto_watch as aw
from app.storage.config_loader import CommunityConfig


TPE = ZoneInfo("Asia/Taipei")


def _make_community(community_id: str, *, enabled: bool, start_hour: int = 10, end_hour: int = 22) -> CommunityConfig:
    return CommunityConfig(
        customer_id="customer_a",
        community_id=community_id,
        display_name=community_id,
        persona="default",
        device_id="emulator-5554",
        patrol_interval_minutes=120,
        enabled=True,
        auto_watch_enabled=enabled,
        auto_watch_start_hour_tpe=start_hour,
        auto_watch_end_hour_tpe=end_hour,
        auto_watch_duration_minutes=720,
        auto_watch_cooldown_seconds=600,
        auto_watch_poll_interval_seconds=60,
    )


class AutoWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cust_root = self.root / "customers" / "customer_a"
        (self.cust_root / "data" / "auto_watches").mkdir(parents=True)
        (self.cust_root / "data").mkdir(exist_ok=True)
        # Patch path resolution so module reads/writes inside tmp
        self.path_patches = [
            patch("app.workflows.auto_watch.customer_root", return_value=self.cust_root),
            patch("app.storage.watches.watches_state_path", return_value=self.cust_root / "data" / "watches.json"),
        ]
        for p in self.path_patches:
            p.start()
        # Stub audit so tests don't write to real audit log
        self.audit_calls: list[tuple] = []

        def fake_audit(customer_id, event_type, payload):
            self.audit_calls.append((customer_id, event_type, payload))

        self.audit_patch_aw = patch("app.workflows.auto_watch.append_audit_event", side_effect=fake_audit)
        self.audit_patch_w = patch("app.storage.watches.append_audit_event", side_effect=fake_audit)
        self.audit_patch_aw.start()
        self.audit_patch_w.start()

    def tearDown(self) -> None:
        self.audit_patch_aw.stop()
        self.audit_patch_w.stop()
        for p in self.path_patches:
            p.stop()
        self.tmp.cleanup()

    def _patch_communities(self, communities: list[CommunityConfig]):
        return patch("app.workflows.auto_watch.load_all_communities", return_value=communities)

    def _read_watches(self) -> list[dict]:
        path = self.cust_root / "data" / "watches.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    # ── Start phase ──

    def test_disabled_community_never_starts(self) -> None:
        c = _make_community("openchat_001", enabled=False)
        now = datetime(2026, 4, 29, 10, 2, tzinfo=TPE)
        with self._patch_communities([c]):
            r = aw.run_auto_watch_cycle(now=now)
        self.assertEqual(r.started, [])
        self.assertEqual(self._read_watches(), [])

    def test_enabled_starts_within_window(self) -> None:
        c = _make_community("openchat_003", enabled=True, start_hour=10)
        now = datetime(2026, 4, 29, 10, 3, tzinfo=TPE)
        with self._patch_communities([c]):
            r = aw.run_auto_watch_cycle(now=now)
        self.assertEqual(len(r.started), 1)
        self.assertEqual(r.started[0]["community_id"], "openchat_003")
        # marker written
        marker = self.cust_root / "data" / "auto_watches" / "openchat_003__2026-04-29.txt"
        self.assertTrue(marker.exists())
        # audit event written
        types = [c[1] for c in self.audit_calls]
        self.assertIn("watch_auto_started", types)

    def test_does_not_start_outside_window(self) -> None:
        c = _make_community("openchat_003", enabled=True, start_hour=10)
        # 09:30 — too early
        now = datetime(2026, 4, 29, 9, 30, tzinfo=TPE)
        with self._patch_communities([c]):
            r = aw.run_auto_watch_cycle(now=now)
        self.assertEqual(r.started, [])

    def test_idempotent_within_same_day(self) -> None:
        c = _make_community("openchat_003", enabled=True, start_hour=10)
        now1 = datetime(2026, 4, 29, 10, 1, tzinfo=TPE)
        now2 = datetime(2026, 4, 29, 10, 4, tzinfo=TPE)
        with self._patch_communities([c]):
            aw.run_auto_watch_cycle(now=now1)
            r2 = aw.run_auto_watch_cycle(now=now2)
        self.assertEqual(r2.started, [])
        self.assertEqual(len(r2.skipped), 1)
        self.assertEqual(r2.skipped[0]["reason"], "already_started_today")

    # ── Stop phase ──

    def test_stops_at_end_window(self) -> None:
        c = _make_community("openchat_003", enabled=True, start_hour=10, end_hour=22)
        # First, start it
        with self._patch_communities([c]):
            aw.run_auto_watch_cycle(now=datetime(2026, 4, 29, 10, 1, tzinfo=TPE))
        # Then jump to 22:01
        with self._patch_communities([c]):
            r = aw.run_auto_watch_cycle(now=datetime(2026, 4, 29, 22, 1, tzinfo=TPE))
        self.assertEqual(len(r.stopped), 1)
        self.assertEqual(r.stopped[0]["community_id"], "openchat_003")
        types = [c[1] for c in self.audit_calls]
        self.assertIn("watch_auto_stopped", types)

    def test_stop_only_targets_auto_watches_not_manual(self) -> None:
        # Manually start a watch (no auto_watch prefix in note)
        from app.storage import watches as ws
        ws.add_watch(
            customer_id="customer_a",
            community_id="openchat_003",
            duration_minutes=720,
            note="operator: manual session",
        )
        c = _make_community("openchat_003", enabled=True, end_hour=22)
        with self._patch_communities([c]):
            r = aw.run_auto_watch_cycle(now=datetime(2026, 4, 29, 22, 5, tzinfo=TPE))
        self.assertEqual(r.stopped, [])

    def test_two_communities_independent(self) -> None:
        c003 = _make_community("openchat_003", enabled=True, start_hour=10)
        c004 = _make_community("openchat_004", enabled=False, start_hour=10)
        with self._patch_communities([c003, c004]):
            r = aw.run_auto_watch_cycle(now=datetime(2026, 4, 29, 10, 2, tzinfo=TPE))
        self.assertEqual(len(r.started), 1)
        self.assertEqual(r.started[0]["community_id"], "openchat_003")


if __name__ == "__main__":
    unittest.main()
