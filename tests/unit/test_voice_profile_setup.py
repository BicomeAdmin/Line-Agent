"""Tests for voice profile completeness check + surgical section update."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows import voice_profile_setup as vps


STUB_PROFILE = """# Voice profile — test community

**身份**：我是這個群裡的一個普通成員。

## My nickname in this group

- （請操作員填：你在這個群顯示的暱稱）

## My personality (1-3 lines)

- （請操作員寫：你想呈現的個性）

## Style anchors (the way I chat)

- 像 chat 不像公告：1 句通常夠
- 不講「大家」「歡迎」「請」這種廣播詞

## Samples (real lines I've said or would say)

- （請操作員之後用 Lark 對 bot 說「幫我記下這個語氣 ...」累積真實樣本）

## Off-limits（底線，不可破）

- 不評論個人
- 不發政治立場
"""

FILLED_PROFILE = """# Voice profile — test

## My nickname in this group

- 小宇

## My personality (1-3 lines)

- 平常觀察居多，看到有趣的會冒一句

## Style anchors (the way I chat)

- 短句、口語

## Samples (real lines I've said or would say)

- 欸真的 我也覺得

## Off-limits（底線，不可破）

- 不評論個人
"""


class CheckVoiceProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "vp.md"

    def tearDown(self):
        self.tmp.cleanup()

    def _patches(self):
        return [
            patch.object(vps, "load_community_config", lambda *a, **k: object()),
            patch.object(vps, "voice_profile_path", lambda *a, **k: self.path),
        ]

    def _enter(self, ps):
        for p in ps:
            p.start()

    def _stop(self, ps):
        for p in ps:
            p.stop()

    def test_stub_profile_reports_low_completeness(self):
        self.path.write_text(STUB_PROFILE, encoding="utf-8")
        ps = self._patches()
        self._enter(ps)
        try:
            result = vps.check_voice_profile("customer_a", "openchat_test")
        finally:
            self._stop(ps)

        self.assertEqual(result["status"], "ok")
        self.assertLess(result["completeness_pct"], 50)
        missing_keys = {m["section"] for m in result["missing"]}
        self.assertIn("nickname", missing_keys)
        self.assertIn("personality", missing_keys)
        self.assertFalse(result["has_harvested_block"])
        # Next-actions includes harvest suggestion + per-section commands.
        self.assertTrue(any("harvest_style_samples" in a for a in result["next_actions"]))
        self.assertIn("沒抓過真實語料", result["summary_zh"])

    def test_filled_profile_reports_high_completeness(self):
        self.path.write_text(FILLED_PROFILE, encoding="utf-8")
        ps = self._patches()
        self._enter(ps)
        try:
            result = vps.check_voice_profile("customer_a", "openchat_test")
        finally:
            self._stop(ps)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["completeness_pct"], 100)
        self.assertEqual(result["missing"], [])
        self.assertIn("✅", result["summary_zh"])

    def test_missing_profile_returns_error(self):
        ps = self._patches()
        self._enter(ps)
        try:
            result = vps.check_voice_profile("customer_a", "openchat_test")
        finally:
            self._stop(ps)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "voice_profile_missing")


class UpdateVoiceProfileSectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "vp.md"
        self.path.write_text(STUB_PROFILE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _patches(self):
        return [
            patch.object(vps, "load_community_config", lambda *a, **k: object()),
            patch.object(vps, "voice_profile_path", lambda *a, **k: self.path),
            patch.object(vps, "append_audit_event", lambda *a, **k: None),
        ]

    def _enter(self, ps):
        for p in ps:
            p.start()

    def _stop(self, ps):
        for p in ps:
            p.stop()

    def test_updates_nickname_preserves_other_sections(self):
        ps = self._patches()
        self._enter(ps)
        try:
            r = vps.update_voice_profile_section("customer_a", "openchat_test", "nickname", "- 小宇")
        finally:
            self._stop(ps)
        self.assertEqual(r["status"], "ok")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("- 小宇", text)
        self.assertNotIn("（請操作員填", text.split("## My personality")[0])
        # Other sections preserved:
        self.assertIn("Style anchors", text)
        self.assertIn("Off-limits", text)
        self.assertIn("不評論個人", text)

    def test_chinese_alias_resolves(self):
        ps = self._patches()
        self._enter(ps)
        try:
            r = vps.update_voice_profile_section("customer_a", "openchat_test", "暱稱", "- 阿哲")
        finally:
            self._stop(ps)
        self.assertEqual(r["status"], "ok")
        self.assertIn("- 阿哲", self.path.read_text(encoding="utf-8"))

    def test_unknown_section_rejected(self):
        ps = self._patches()
        self._enter(ps)
        try:
            r = vps.update_voice_profile_section("customer_a", "openchat_test", "nonsense", "- xxx")
        finally:
            self._stop(ps)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason"], "unknown_section")

    def test_empty_content_rejected(self):
        ps = self._patches()
        self._enter(ps)
        try:
            r = vps.update_voice_profile_section("customer_a", "openchat_test", "nickname", "  ")
        finally:
            self._stop(ps)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason"], "empty_content")


class PlaceholderDetectionTests(unittest.TestCase):
    def test_only_placeholder_lines_is_placeholder(self):
        body = "- （請操作員填：你的暱稱）\n\n"
        self.assertTrue(vps._is_placeholder_only(body))

    def test_one_real_line_flips_to_filled(self):
        body = "- （請操作員填）\n- 小宇\n"
        self.assertFalse(vps._is_placeholder_only(body))

    def test_empty_is_placeholder(self):
        self.assertTrue(vps._is_placeholder_only(""))
        self.assertTrue(vps._is_placeholder_only("   \n\n"))


if __name__ == "__main__":
    unittest.main()
