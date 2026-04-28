"""Tests for style harvesting + conversation fingerprinting (Skills A & B)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows import style_harvest
from app.workflows.style_harvest import (
    _filter_natural_lines,
    _score_line,
    _splice_harvest_block,
    fingerprint_conversation,
    harvest_style_samples,
)


def _msg(text, position=0, sender="unknown"):
    return {"sender": sender, "text": text, "position": position, "source": "uiautomator"}


class FilterNaturalLinesTests(unittest.TestCase):
    def test_drops_short_links_broadcasts_pure_emoji(self):
        msgs = [
            _msg("對啊"),  # too short (2 chars)
            _msg("有人知道嗎"),  # ok, 5 chars
            _msg("欸這個我也想知道"),  # ok
            _msg("公告：本群禁止廣告"),  # broadcast
            _msg("https://line.me/ti/g2/abc"),  # link
            _msg("@張三"),  # bare mention
            _msg("🤣🤣🤣"),  # pure emoji
            _msg("這個我覺得可以再看一下"),  # ok
            _msg("a" * 100),  # too long
        ]
        kept = _filter_natural_lines(msgs)
        self.assertEqual(
            kept,
            ["有人知道嗎", "欸這個我也想知道", "這個我覺得可以再看一下"],
        )

    def test_drops_ui_chrome_member_badges_counts(self):
        # Regression: live harvest pulled these in.
        msgs = [
            _msg("(74)"),
            _msg("（120）"),
            _msg("阿樂 本尊"),
            _msg("瞭解詳情"),
            _msg("查看更多"),
            _msg("欸這個我也想知道"),  # legit, must survive
        ]
        kept = _filter_natural_lines(msgs)
        self.assertEqual(kept, ["欸這個我也想知道"])

    def test_drops_ui_timestamps_and_date_headers(self):
        # Regression: live harvest on openchat_002 pulled in these as samples.
        msgs = [
            _msg("下午7:47"),
            _msg("上午11:59"),
            _msg("19:47"),
            _msg("4月2日 週四"),
            _msg("週四"),
            _msg("昨天"),
            _msg("已讀 3"),
            _msg("欸這個我也想知道"),  # legit, must survive
        ]
        kept = _filter_natural_lines(msgs)
        self.assertEqual(kept, ["欸這個我也想知道"])

    def test_drops_empty_and_whitespace(self):
        kept = _filter_natural_lines([_msg(""), _msg("   "), _msg("有人在嗎")])
        self.assertEqual(kept, ["有人在嗎"])


class ScoreLineTests(unittest.TestCase):
    def test_mid_length_with_particle_scores_highest(self):
        sweet = _score_line("欸這個我覺得可以啊")  # 9 chars, ends 啊
        long_no_particle = _score_line("這個我覺得我們可以再多看一陣子再決定也不遲")  # 21 chars
        too_short = _score_line("對啊哈")  # 3 chars, but len<6 penalty kicks in
        self.assertGreater(sweet, long_no_particle)
        self.assertGreater(sweet, too_short)

    def test_repeated_punctuation_penalized(self):
        clean = _score_line("這個有點意思耶")
        spammy = _score_line("這個有點意思耶!!!!!")
        self.assertGreater(clean, spammy)


class SpliceHarvestBlockTests(unittest.TestCase):
    def test_appends_when_no_existing_block(self):
        existing = "# Voice profile\n\nSome content.\n"
        result = _splice_harvest_block(existing, ["欸這個", "對啊", "有人知道嗎"])
        self.assertIn("BEGIN auto-harvested", result)
        self.assertIn("END auto-harvested", result)
        self.assertIn("- 欸這個", result)
        self.assertIn("Some content.", result)  # original preserved

    def test_replaces_existing_block_in_place(self):
        existing = (
            "# Voice profile\n\n"
            "Operator notes.\n\n"
            "<!-- BEGIN auto-harvested community lines -->\n"
            "## Observed community lines\n\n"
            "- 舊樣本一\n"
            "- 舊樣本二\n"
            "<!-- END auto-harvested community lines -->\n"
            "\nMore operator notes.\n"
        )
        result = _splice_harvest_block(existing, ["新樣本一", "新樣本二"])
        self.assertNotIn("舊樣本一", result)
        self.assertIn("- 新樣本一", result)
        self.assertIn("- 新樣本二", result)
        self.assertIn("Operator notes.", result)
        self.assertIn("More operator notes.", result)
        # Exactly one block:
        self.assertEqual(result.count("BEGIN auto-harvested"), 1)
        self.assertEqual(result.count("END auto-harvested"), 1)


class FingerprintConversationTests(unittest.TestCase):
    def test_too_few_samples_returns_neutral(self):
        fp = fingerprint_conversation([_msg("a"), _msg("b")])
        self.assertIsNone(fp["median_length"])
        self.assertEqual(fp["top_opening_words"], [])
        self.assertIn("樣本不足", fp["summary_zh"])

    def test_typical_chat_fingerprint(self):
        msgs = [
            _msg("欸這個我也想知道"),
            _msg("對啊"),
            _msg("有人在嗎"),
            _msg("欸真的"),
            _msg("這個哪裡買啊"),
            _msg("我也是欸"),
            _msg("噢這樣噢"),
        ]
        fp = fingerprint_conversation(msgs)
        self.assertGreaterEqual(fp["sample_count"], 6)
        self.assertIsInstance(fp["median_length"], int)
        # Should detect 啊/欸 as common particles
        self.assertTrue(
            any(p in fp["top_ending_particles"] for p in ("啊", "欸", "噢")),
            f"expected 啊/欸/噢 in {fp['top_ending_particles']}",
        )
        self.assertIn("中位字數", fp["summary_zh"])

    def test_emoji_rate_detected(self):
        msgs = [
            _msg("這個超讚🤣"),
            _msg("我也覺得🤣🤣"),
            _msg("有人想一起嗎"),
            _msg("可以啊"),
        ]
        fp = fingerprint_conversation(msgs)
        self.assertGreater(fp["emoji_rate"], 0)

    def test_no_emoji_reported_as_zero(self):
        msgs = [_msg("欸真的"), _msg("對啊"), _msg("有人知道嗎"), _msg("我也是")]
        fp = fingerprint_conversation(msgs)
        self.assertEqual(fp["emoji_rate"], 0.0)
        self.assertIn("幾乎不用 emoji", fp["summary_zh"])


class HarvestStyleSamplesIntegrationTests(unittest.TestCase):
    """Drive harvest_style_samples end-to-end with mocked navigate + read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.profile_path = self.tmp_path / "voice_profile.md"
        self.profile_path.write_text(
            "# Voice profile — test\n\nOperator section preserved.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_harvest_writes_block_and_preserves_operator_content(self):
        community_stub = type(
            "C",
            (),
            {"customer_id": "customer_a", "community_id": "openchat_test", "device_id": "emulator-5554"},
        )()
        fake_messages = [
            _msg("欸這個我也想知道", position=i)
            for i in range(5)
        ] + [
            _msg("公告：本群禁止廣告", position=10),  # filtered
            _msg("https://line.me/x", position=11),  # filtered
            _msg("對啊可以再看看哈", position=12),
            _msg("有人想一起嗎", position=13),
            _msg("這個哪裡買啊", position=14),
        ]

        with patch.object(style_harvest, "load_community_config", return_value=community_stub), \
             patch.object(style_harvest, "navigate_to_openchat", return_value={"status": "ok"}), \
             patch.object(style_harvest, "read_recent_chat", return_value=fake_messages), \
             patch.object(style_harvest, "voice_profile_path", return_value=self.profile_path), \
             patch.object(style_harvest, "default_raw_xml_path", return_value=Path("/tmp/dummy.xml")), \
             patch.object(style_harvest, "AdbClient", lambda **_: None), \
             patch.object(style_harvest, "append_audit_event", lambda *a, **k: None):
            result = harvest_style_samples("customer_a", "openchat_test")

        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["samples_written"], 0)
        text = self.profile_path.read_text(encoding="utf-8")
        self.assertIn("Operator section preserved.", text)
        self.assertIn("BEGIN auto-harvested", text)
        self.assertNotIn("公告", text)  # broadcast filtered
        self.assertNotIn("https://", text)  # link filtered

    def test_missing_profile_returns_error(self):
        community_stub = type(
            "C",
            (),
            {"customer_id": "customer_a", "community_id": "openchat_x", "device_id": "emulator-5554"},
        )()
        with patch.object(style_harvest, "load_community_config", return_value=community_stub), \
             patch.object(style_harvest, "navigate_to_openchat", return_value={"status": "ok"}), \
             patch.object(style_harvest, "read_recent_chat", return_value=[_msg("欸對啊真的")] * 5), \
             patch.object(style_harvest, "voice_profile_path", return_value=self.tmp_path / "missing.md"), \
             patch.object(style_harvest, "default_raw_xml_path", return_value=Path("/tmp/dummy.xml")), \
             patch.object(style_harvest, "AdbClient", lambda **_: None):
            result = harvest_style_samples("customer_a", "openchat_x")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "voice_profile_missing")


if __name__ == "__main__":
    unittest.main()
