"""Tests for member lifecycle tagging (Tier 2.2 — new / active / silent / churned).

Stage classification rules (relative to reference_date):
  new      : first_seen ≤ 7 days ago  (regardless of count)
  active   : last_seen  ≤ 7 days ago  AND count ≥ 3  (and not new)
  silent   : last_seen  7-30 days ago
  churned  : last_seen  > 30 days ago
  operator : sender matches community.operator_nickname
"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.workflows import lifecycle_tagging as lt
from app.storage.config_loader import CommunityConfig


REF_DATE = datetime(2026, 4, 29)


def _export(lines: list[tuple[str, str, str]]) -> str:
    """Build a minimal LINE chat export from (date, time, sender_msg)
    triples. date format: YYYY-MM-DD; rendered as YYYY.MM.DD 星期X header."""

    out: list[str] = []
    last_date = None
    for date_str, time_str, sender_msg in lines:
        if date_str != last_date:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            out.append(d.strftime("%Y.%m.%d 星期一"))
            last_date = date_str
        out.append(f"{time_str} {sender_msg}")
    return "\n".join(out) + "\n"


def _fake_community(operator_nickname: str | None = None) -> CommunityConfig:
    return CommunityConfig(
        customer_id="customer_a",
        community_id="openchat_test",
        display_name="測試群",
        persona="default",
        device_id="emulator-5554",
        patrol_interval_minutes=60,
        operator_nickname=operator_nickname,
    )


class LifecyclePathTests(unittest.TestCase):
    def test_path_under_member_lifecycle_dir(self):
        p = lt.lifecycle_path("customer_a", "openchat_007")
        self.assertEqual(p.name, "openchat_007.json")
        self.assertEqual(p.parent.name, "member_lifecycle")


class LoadLifecycleTagsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_returns_none_when_missing(self):
        with patch.object(lt, "customer_data_root", lambda *_: self.data_root):
            self.assertIsNone(lt.load_lifecycle_tags("customer_a", "openchat_x"))

    def test_reads_existing_json(self):
        path = self.data_root / "member_lifecycle" / "openchat_x.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"members": [{"sender": "alice", "stage": "active"}]}))
        with patch.object(lt, "customer_data_root", lambda *_: self.data_root):
            snap = lt.load_lifecycle_tags("customer_a", "openchat_x")
        self.assertEqual(snap["members"][0]["sender"], "alice")

    def test_get_member_stage_lookup(self):
        path = self.data_root / "member_lifecycle" / "openchat_x.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "members": [
                {"sender": "alice", "stage": "active"},
                {"sender": "bob", "stage": "churned"},
            ]
        }))
        with patch.object(lt, "customer_data_root", lambda *_: self.data_root):
            self.assertEqual(lt.get_member_stage("customer_a", "openchat_x", "alice"), "active")
            self.assertEqual(lt.get_member_stage("customer_a", "openchat_x", "bob"), "churned")
            self.assertIsNone(lt.get_member_stage("customer_a", "openchat_x", "ghost"))


class ComputeLifecycleTagsTests(unittest.TestCase):
    """End-to-end: build a chat export, run compute, check classifications."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.export_path = self.data_root / "chat_exports" / "openchat_test.txt"
        self.export_path.parent.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, export_text: str, *, operator_nickname: str | None = None):
        self.export_path.write_text(export_text, encoding="utf-8")
        with patch.object(lt, "load_community_config", lambda *_: _fake_community(operator_nickname)), \
             patch.object(lt, "latest_export_path", lambda *_: self.export_path), \
             patch.object(lt, "customer_data_root", lambda *_: self.data_root), \
             patch.object(lt, "append_audit_event", lambda *_a, **_k: None):
            return lt.compute_lifecycle_tags("customer_a", "openchat_test", reference_date=REF_DATE)

    def _stage_of(self, snap, sender):
        for m in snap["members"]:
            if m["sender"] == sender:
                return m["stage"]
        return None

    def test_new_member_first_seen_within_7_days(self):
        # alice first appeared 2026-04-25 (4 days ago) → "new" regardless of count
        snap = self._run(_export([
            ("2026-04-25", "10:00", "alice 嗨"),
            ("2026-04-26", "11:00", "alice 大家好"),
        ]))
        self.assertEqual(self._stage_of(snap, "alice"), "new")

    def test_active_member_recent_and_frequent(self):
        # bob first 2025-11-01 (>7 days), last 2026-04-28 (1 day), count 5 → "active"
        snap = self._run(_export([
            ("2025-11-01", "10:00", "bob hi"),
            ("2026-04-25", "10:00", "bob 在嗎"),
            ("2026-04-26", "10:00", "bob ok"),
            ("2026-04-27", "10:00", "bob 收到"),
            ("2026-04-28", "10:00", "bob 謝謝"),
        ]))
        self.assertEqual(self._stage_of(snap, "bob"), "active")

    def test_silent_member_last_seen_in_window(self):
        # carol first 2025-11-01, last 2026-04-15 (14 days ago) → "silent"
        snap = self._run(_export([
            ("2025-11-01", "10:00", "carol hi"),
            ("2026-04-15", "10:00", "carol bye"),
        ]))
        self.assertEqual(self._stage_of(snap, "carol"), "silent")

    def test_churned_member_last_seen_over_30_days(self):
        # dave first 2025-01-01, last 2026-01-01 (118 days ago) → "churned"
        snap = self._run(_export([
            ("2025-01-01", "10:00", "dave hi"),
            ("2026-01-01", "10:00", "dave bye"),
        ]))
        self.assertEqual(self._stage_of(snap, "dave"), "churned")

    def test_active_threshold_count_below_minimum_falls_to_silent(self):
        # eve recent (last 1 day) but count 2 (< ACTIVE_MIN_MSGS=3) → "silent"
        # Plus first_seen old enough not to be "new".
        snap = self._run(_export([
            ("2025-11-01", "10:00", "eve hi"),
            ("2026-04-28", "10:00", "eve again"),
        ]))
        self.assertEqual(self._stage_of(snap, "eve"), "silent")

    def test_operator_nickname_classified_separately(self):
        snap = self._run(
            _export([
                ("2026-04-28", "10:00", "比利 大家好"),
                ("2026-04-28", "10:01", "alice 嗨"),
            ]),
            operator_nickname="比利",
        )
        self.assertEqual(self._stage_of(snap, "比利"), "operator")
        # alice is new (first_seen 1 day ago)
        self.assertEqual(self._stage_of(snap, "alice"), "new")

    def test_distribution_aggregates_correctly(self):
        snap = self._run(_export([
            # alice = new
            ("2026-04-26", "10:00", "alice hi"),
            # bob = active (>7 days first, ≤7 days last, count 3)
            ("2025-11-01", "10:00", "bob a"),
            ("2026-04-26", "10:00", "bob b"),
            ("2026-04-28", "10:00", "bob c"),
            # carol = churned (last > 30 days ago)
            ("2025-01-01", "10:00", "carol x"),
            ("2025-12-01", "10:00", "carol y"),
        ]))
        dist = snap["distribution"]
        self.assertEqual(dist.get("new"), 1)
        self.assertEqual(dist.get("active"), 1)
        self.assertEqual(dist.get("churned"), 1)
        self.assertEqual(snap["total_distinct_members"], 3)


if __name__ == "__main__":
    unittest.main()
