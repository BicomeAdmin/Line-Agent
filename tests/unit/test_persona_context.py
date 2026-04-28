"""Tests for persona_context bundling — verify the section-extraction
helpers and the summary-line builder. Live audit/voice-profile reads
covered by the smoke run during development."""

import unittest
from unittest.mock import patch

from app.workflows import persona_context as pc


VOICE_PROFILE = """# Voice profile — 山納百景

**身份**：我是這個群裡的一個普通成員。

## My nickname in this group

- 小宇

## My personality (1-3 lines)

- 平常觀察居多，看到有趣的會冒一句
- 不太愛 emoji

## Style anchors (the way I chat)

- 短句、口語、不客套
- 常用「啊」「欸」「對啊」結尾

## Samples

- 欸真的，我也覺得

## Off-limits（底線，不可破）

- 不評論個人
- 不發政治立場
- 不用客服式語句
"""


class ExtractSectionTests(unittest.TestCase):
    def test_extracts_named_section_body(self):
        body = pc._extract_section(VOICE_PROFILE, "personality")
        self.assertIn("觀察居多", body)
        self.assertNotIn("Style anchors", body)  # next section excluded

    def test_returns_empty_when_section_missing(self):
        self.assertEqual(pc._extract_section(VOICE_PROFILE, "nonexistent"), "")

    def test_handles_empty_input(self):
        self.assertEqual(pc._extract_section("", "anything"), "")


class ExtractFieldsTests(unittest.TestCase):
    def test_nickname_uses_first_real_bullet(self):
        self.assertEqual(pc._extract_nickname(VOICE_PROFILE), "小宇")

    def test_nickname_skips_placeholder(self):
        text = "## My nickname in this group\n\n- （請操作員填）\n- 真名\n"
        self.assertEqual(pc._extract_nickname(text), "真名")

    def test_personality_first_real_line(self):
        result = pc._extract_personality(VOICE_PROFILE)
        self.assertIn("觀察居多", result)

    def test_off_limits_returns_list(self):
        items = pc._extract_off_limits(VOICE_PROFILE)
        self.assertEqual(len(items), 3)
        self.assertIn("不評論個人", items[0])
        self.assertIn("不發政治立場", items[1])

    def test_extract_handles_missing_voice_profile(self):
        self.assertEqual(pc._extract_nickname(""), "")
        self.assertEqual(pc._extract_personality(""), "")
        self.assertEqual(pc._extract_off_limits(""), [])


class BuildSummaryTests(unittest.TestCase):
    def test_summary_with_recent_posts(self):
        s = pc._build_summary(
            customer_display="客戶 A",
            community_display="山納百景",
            community_id="openchat_003",
            nickname="小宇",
            personality="平常觀察居多",
            recent_self_posts=[{"text": "JN3 我也還沒填欸", "ts_taipei": "2026-04-28"}],
        )
        self.assertIn("山納百景", s)
        self.assertIn("openchat_003", s)
        self.assertIn("客戶 A", s)
        self.assertIn("小宇", s)
        self.assertIn("觀察居多", s)
        self.assertIn("JN3 我也還沒填欸", s)

    def test_summary_with_no_recent_posts(self):
        s = pc._build_summary(
            customer_display="客戶 A",
            community_display="水月觀音道場",
            community_id="openchat_004",
            nickname="",
            personality="",
            recent_self_posts=[],
        )
        self.assertIn("水月觀音道場", s)
        self.assertIn("沒有送出紀錄", s)


class GetPersonaContextIntegrationTests(unittest.TestCase):
    """Drive get_persona_context with everything mocked except section parsing."""

    def test_returns_full_bundle(self):
        customer_stub = type("C", (), {"display_name": "客戶 A"})()
        community_stub = type("Co", (), {
            "display_name": "山納百景",
            "persona": "default",
        })()
        with patch.object(pc, "load_customer_config", return_value=customer_stub), \
             patch.object(pc, "load_community_config", return_value=community_stub), \
             patch.object(pc, "voice_profile_path", lambda customer_id, cid: type("P", (), {
                 "exists": lambda self: True,
                 "read_text": lambda self, encoding="utf-8": VOICE_PROFILE,
             })()), \
             patch.object(pc, "_recent_self_posts", return_value=[]):
            result = pc.get_persona_context("customer_a", "openchat_003")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["account"]["customer_id"], "customer_a")
        self.assertEqual(result["community"]["display_name"], "山納百景")
        self.assertEqual(result["voice_profile"]["nickname"], "小宇")
        self.assertTrue(result["voice_profile"]["loaded"])
        self.assertGreater(len(result["voice_profile"]["off_limits"]), 0)
        # Summary should be a usable echo line.
        self.assertIn("山納百景", result["summary_zh"])
        self.assertIn("小宇", result["summary_zh"])

    def test_handles_missing_community(self):
        customer_stub = type("C", (), {"display_name": "客戶 A"})()
        with patch.object(pc, "load_customer_config", return_value=customer_stub), \
             patch.object(pc, "load_community_config", side_effect=ValueError("not found")):
            result = pc.get_persona_context("customer_a", "openchat_does_not_exist")
        self.assertEqual(result["status"], "error")
        self.assertIn("community_lookup_failed", result["reason"])


if __name__ == "__main__":
    unittest.main()
