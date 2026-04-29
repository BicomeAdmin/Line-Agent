"""Tests for 九宮格 KPI tracker (Tier 1.5).

Covers _looks_broadcast heuristic, _compute_single_day aggregation,
compute_community_kpis end-to-end via temp paths, and the dashboard
summary reader.
"""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.workflows import kpi_tracker as kt
from app.workflows.chat_export_import import ChatMessage
from app.storage.config_loader import CommunityConfig


def _msg(sender: str, text: str, date: str = "2026-04-28", time: str = "10:00") -> ChatMessage:
    return ChatMessage(date=date, time=time, sender=sender, text=text)


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


class LooksBroadcastTests(unittest.TestCase):
    def test_empty_text_returns_false(self):
        self.assertFalse(kt._looks_broadcast(""))
        self.assertFalse(kt._looks_broadcast(None))  # type: ignore

    def test_at_all_token_hits(self):
        self.assertTrue(kt._looks_broadcast("@All 大家注意"))
        self.assertTrue(kt._looks_broadcast("@all 注意"))

    def test_announcement_tokens_hit(self):
        for token_msg in ("公告：今天停課", "限時福利大放送", "報名連結在下方", "歡迎大家加入"):
            self.assertTrue(kt._looks_broadcast(token_msg), f"failed: {token_msg}")

    def test_normal_chat_misses(self):
        self.assertFalse(kt._looks_broadcast("今天天氣不錯"))
        self.assertFalse(kt._looks_broadcast("剛剛在喝咖啡"))


class ComputeSingleDayTests(unittest.TestCase):
    def test_message_count_total(self):
        items = [_msg("a", "x"), _msg("b", "y"), _msg("a", "z")]
        out = kt._compute_single_day("2026-04-28", items, "")
        self.assertEqual(out["message_count"], 3)

    def test_distinct_active_senders(self):
        items = [_msg("a", "x"), _msg("b", "y"), _msg("a", "z")]
        out = kt._compute_single_day("2026-04-28", items, "")
        self.assertEqual(out["distinct_active_senders"], 2)
        self.assertEqual(set(out["active_senders_list"]), {"a", "b"})

    def test_skips_unknown_sender_in_count(self):
        items = [_msg("a", "x"), _msg("unknown", "y"), _msg("b", "z")]
        out = kt._compute_single_day("2026-04-28", items, "")
        # "unknown" excluded from active senders
        self.assertEqual(out["distinct_active_senders"], 2)

    def test_operator_messages_counted_by_nickname(self):
        items = [_msg("比利", "親自回覆"), _msg("alice", "發問"), _msg("比利", "再回")]
        out = kt._compute_single_day("2026-04-28", items, "比利")
        self.assertEqual(out["operator_messages"], 2)

    def test_operator_messages_synthetic_marker_also_counted(self):
        items = [_msg("__operator__", "system-driven"), _msg("alice", "user")]
        out = kt._compute_single_day("2026-04-28", items, "比利")
        self.assertEqual(out["operator_messages"], 1)

    def test_broadcast_vs_natural_split(self):
        items = [
            _msg("operator", "公告：今天活動"),
            _msg("a", "我來"),
            _msg("b", "我也"),
        ]
        out = kt._compute_single_day("2026-04-28", items, "")
        self.assertEqual(out["broadcast_messages"], 1)
        self.assertEqual(out["natural_messages"], 2)

    def test_top_senders_ranked(self):
        items = [_msg("low", "x")] + [_msg("high", "y") for _ in range(5)]
        out = kt._compute_single_day("2026-04-28", items, "")
        # most_common(3) returns list of (sender, count) tuples
        self.assertEqual(out["top_senders"][0][0], "high")
        self.assertEqual(out["top_senders"][0][1], 5)


class ComputeCommunityKpisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.export_path = self.data_root / "chat_exports" / "openchat_test.txt"
        self.export_path.parent.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _build_export(self, lines: list[tuple[str, str, str]]) -> str:
        out: list[str] = []
        last_date = None
        for date_str, time_str, sender_msg in lines:
            if date_str != last_date:
                d = datetime.strptime(date_str, "%Y-%m-%d")
                out.append(d.strftime("%Y.%m.%d 星期一"))
                last_date = date_str
            out.append(f"{time_str} {sender_msg}")
        return "\n".join(out) + "\n"

    def _run(self, export_text: str, *, operator_nickname: str | None = None, days_back: int = 10000):
        self.export_path.write_text(export_text, encoding="utf-8")
        with patch.object(kt, "load_community_config", lambda *_: _fake_community(operator_nickname)), \
             patch.object(kt, "latest_export_path", lambda *_: self.export_path), \
             patch.object(kt, "customer_data_root", lambda *_: self.data_root), \
             patch.object(kt, "append_audit_event", lambda *_a, **_k: None):
            return kt.compute_community_kpis("customer_a", "openchat_test", days_back=days_back)

    def test_status_ok_with_basic_export(self):
        snap = self._run(self._build_export([
            ("2026-04-25", "10:00", "alice 早安"),
            ("2026-04-25", "10:01", "bob hi"),
            ("2026-04-26", "10:00", "alice 又來了"),
        ]))
        self.assertEqual(snap["status"], "ok")
        self.assertEqual(snap["days_with_data"], 2)
        self.assertEqual(snap["messages_last_30_days"], 3)

    def test_weekly_active_senders_dedupes_across_days(self):
        snap = self._run(self._build_export([
            ("2026-04-25", "10:00", "alice a"),
            ("2026-04-26", "10:00", "alice b"),
            ("2026-04-26", "10:01", "bob c"),
        ]))
        # alice + bob = 2 unique active senders in window
        self.assertEqual(snap["weekly_active_senders"], 2)

    def test_persists_snapshot_to_disk(self):
        self._run(self._build_export([("2026-04-25", "10:00", "alice hi")]))
        out = self.data_root / "kpi_snapshots" / "openchat_test.json"
        self.assertTrue(out.exists())
        loaded = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(loaded["community_id"], "openchat_test")

    def test_missing_export_returns_error(self):
        # latest_export_path returns None when no chat export imported yet
        with patch.object(kt, "load_community_config", lambda *_: _fake_community()), \
             patch.object(kt, "latest_export_path", lambda *_: None), \
             patch.object(kt, "customer_data_root", lambda *_: self.data_root), \
             patch.object(kt, "append_audit_event", lambda *_a, **_k: None):
            snap = kt.compute_community_kpis("customer_a", "openchat_test")
        self.assertEqual(snap["status"], "error")
        self.assertEqual(snap["reason"], "no_export_available")


class KpiSnapshotsPathTests(unittest.TestCase):
    def test_path_layout(self):
        p = kt.kpi_snapshots_path("customer_a", "openchat_007")
        self.assertEqual(p.name, "openchat_007.json")
        self.assertEqual(p.parent.name, "kpi_snapshots")


if __name__ == "__main__":
    unittest.main()
