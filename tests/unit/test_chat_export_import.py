"""Tests for LINE chat export parser + import workflow.

Fixture mirrors the actual format observed in the operator's
[LINE]特殊支援群.txt export (2026-04 era), including:
  - date headers in YYYY.MM.DD 星期X format
  - sender names with role badges ("阿樂 本尊")
  - sender names without spaces ("阿樂2", "鹿哥威廉")
  - multi-line message continuations
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows import chat_export_import as cei


# Minimal fixture covering the structural variations.
SAMPLE_EXPORT = """2025.11.11 星期二
20:17 阿樂 本尊 ☕️ 歡迎  @12 @10-3  加入《里響咖啡俱樂部》！

這裡你可以：
1️⃣ 學風味 → 每週咖啡知識與手沖技巧分享
2026.01.27 星期二
10:36 阿樂 本尊 222
10:36 阿樂 本尊 222
2026.02.12 星期四
17:35 阿樂 本尊 威廉你點看看
17:35 阿樂 本尊 我看還是會在line裡面
17:36 鹿哥威廉 暗樁（安卓）正常
自機（IOS）跳轉
2026.04.28 星期二
11:59 阿樂2 午安，大家好啊！
12:01 阿樂2 今天大家有看到什麼時事讓你印象深刻嗎？
12:13 阿樂2 台股 4 萬點這個我會先保守看
"""


class ParseLineExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
        self.tmp.write(SAMPLE_EXPORT)
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_parses_all_messages(self):
        msgs = cei.parse_line_export(self.path)
        self.assertEqual(len(msgs), 9)

    def test_sender_with_role_badge(self):
        msgs = cei.parse_line_export(self.path)
        # "阿樂 本尊" should be one sender, not two
        ale = [m for m in msgs if m.sender == "阿樂 本尊"]
        self.assertEqual(len(ale), 5)

    def test_sender_without_space(self):
        msgs = cei.parse_line_export(self.path)
        ale2 = [m for m in msgs if m.sender == "阿樂2"]
        self.assertEqual(len(ale2), 3)
        luge = [m for m in msgs if m.sender == "鹿哥威廉"]
        self.assertEqual(len(luge), 1)

    def test_multiline_continuation_concatenates(self):
        msgs = cei.parse_line_export(self.path)
        # Welcome message has continuation lines after the time prefix.
        welcome = [m for m in msgs if "歡迎" in m.text][0]
        self.assertIn("學風味", welcome.text)  # continuation absorbed
        self.assertIn("這裡你可以", welcome.text)

        # 鹿哥威廉's message has IOS continuation.
        luge = [m for m in msgs if m.sender == "鹿哥威廉"][0]
        self.assertIn("暗樁", luge.text)
        self.assertIn("自機", luge.text)

    def test_date_assigned_correctly(self):
        msgs = cei.parse_line_export(self.path)
        # First message is on 2025-11-11
        self.assertEqual(msgs[0].date, "2025-11-11")
        # Last messages on 2026-04-28
        self.assertTrue(all(m.date == "2026-04-28" for m in msgs[-3:]))

    def test_time_extracted(self):
        msgs = cei.parse_line_export(self.path)
        self.assertEqual(msgs[0].time, "20:17")
        self.assertEqual(msgs[-1].time, "12:13")


class AggregatePerSenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False)
        self.tmp.write(SAMPLE_EXPORT)
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_top_sender_is_the_most_active(self):
        msgs = cei.parse_line_export(self.path)
        stats = cei.aggregate_per_sender(msgs)
        self.assertEqual(stats[0].sender, "阿樂 本尊")  # 5 messages
        self.assertEqual(stats[0].message_count, 5)

    def test_avg_length_calculated(self):
        msgs = cei.parse_line_export(self.path)
        stats = cei.aggregate_per_sender(msgs)
        for s in stats:
            self.assertGreater(s.message_count, 0)
            d = s.to_dict()
            self.assertIn("avg_length", d)
            self.assertIsInstance(d["avg_length"], (int, float))


class ImportChatExportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

        self.export_file = self.tmp_path / "export.txt"
        self.export_file.write_text(SAMPLE_EXPORT, encoding="utf-8")

        self.profile_path = self.tmp_path / "voice_profile.md"
        self.profile_path.write_text(
            "# Voice profile — test\n\nOperator section preserved.\n",
            encoding="utf-8",
        )

        self.data_root = self.tmp_path / "data"

    def tearDown(self):
        self.tmp.cleanup()

    def _patches(self):
        community_stub = type("C", (), {
            "customer_id": "customer_a",
            "community_id": "openchat_test",
            "device_id": "emulator-5554",
        })()
        return [
            patch.object(cei, "load_community_config", return_value=community_stub),
            patch.object(cei, "voice_profile_path", lambda *a, **k: self.profile_path),
            patch.object(cei, "customer_data_root", lambda *a, **k: self.data_root),
            patch.object(cei, "append_audit_event", lambda *a, **k: None),
        ]

    def _enter(self, ps):
        for p in ps: p.start()

    def _stop(self, ps):
        for p in ps: p.stop()

    def test_imports_and_updates_voice_profile(self):
        ps = self._patches()
        self._enter(ps)
        try:
            r = cei.import_chat_export("customer_a", "openchat_test", self.export_file)
        finally:
            self._stop(ps)

        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["messages_parsed"], 9)
        self.assertGreaterEqual(r["distinct_senders"], 3)  # 阿樂 本尊 + 阿樂2 + 鹿哥威廉
        self.assertGreater(r["new_samples_added"], 0)
        self.assertIn("sender_stats", r)
        self.assertIsInstance(r["sender_stats"], list)

        # Voice profile got a managed block injected.
        text = self.profile_path.read_text(encoding="utf-8")
        self.assertIn("BEGIN auto-harvested", text)
        self.assertIn("Operator section preserved.", text)

        # Local copy made.
        self.assertIsNotNone(r["stored_at"])
        self.assertTrue(Path(r["stored_at"]).exists())

    def test_missing_export_file_returns_error(self):
        ps = self._patches()
        self._enter(ps)
        try:
            r = cei.import_chat_export("customer_a", "openchat_test", "/does/not/exist.txt")
        finally:
            self._stop(ps)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason"], "export_file_not_found")

    def test_missing_voice_profile_returns_error(self):
        self.profile_path.unlink()
        ps = self._patches()
        self._enter(ps)
        try:
            r = cei.import_chat_export("customer_a", "openchat_test", self.export_file)
        finally:
            self._stop(ps)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason"], "voice_profile_missing")


if __name__ == "__main__":
    unittest.main()
