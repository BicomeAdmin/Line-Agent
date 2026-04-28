"""Tests for the bridge reply-card builder.

Verifies:
  - Card body wraps long / structured replies in a code block so digest
    column alignment survives Lark's markdown collapse.
  - Short conversational replies stay as plain markdown.
  - Empty / whitespace-only replies fall back gracefully.
"""

import unittest

from app.lark.cards import build_reply_card, _looks_structured


class LooksStructuredTests(unittest.TestCase):
    def test_short_conversational_is_plain(self):
        self.assertFalse(_looks_structured("已擬一句『JN3 我也還沒填欸 哈』，請看下方卡片"))
        self.assertFalse(_looks_structured("好的，已忽略 review job-abc123"))

    def test_multiline_is_structured(self):
        self.assertTrue(_looks_structured("一行\n兩行\n三行\n四行"))

    def test_box_drawing_markers_trigger_code_block(self):
        # Even a single-line reply with separators counts as structured.
        self.assertTrue(_looks_structured("───── totals ─────"))

    def test_status_emoji_triggers_code_block(self):
        # Lines starting with status emoji indicate digest-like layout.
        text = "📊 Project Echo 狀態 — 2026-04-29 09:00:00"
        self.assertTrue(_looks_structured(text))


class BuildReplyCardTests(unittest.TestCase):
    def test_short_reply_no_code_block(self):
        card = build_reply_card("已忽略 review job-abc")
        body = card["elements"][0]["content"]
        self.assertNotIn("```", body)
        self.assertIn("已忽略", body)

    def test_long_reply_wrapped_in_code_block(self):
        digest = (
            "📊 Project Echo 狀態 — 2026-04-29\n\n"
            "🩺 系統健康\n"
            "  ✅ scheduler_daemon  PID 12345\n"
            "  ✅ lark_bridge       PID 12346\n"
        )
        card = build_reply_card(digest)
        body = card["elements"][0]["content"]
        self.assertTrue(body.startswith("```"))
        self.assertTrue(body.rstrip().endswith("```"))
        self.assertIn("scheduler_daemon", body)

    def test_card_has_header_and_markdown_element(self):
        card = build_reply_card("hi")
        self.assertEqual(card["header"]["title"]["content"], "🤖 Project Echo")
        self.assertEqual(card["header"]["template"], "blue")
        self.assertEqual(len(card["elements"]), 1)
        self.assertEqual(card["elements"][0]["tag"], "markdown")

    def test_empty_reply_falls_back(self):
        card = build_reply_card("")
        body = card["elements"][0]["content"]
        self.assertIn("沒有回應", body)

    def test_whitespace_only_falls_back(self):
        card = build_reply_card("   \n  \n  ")
        body = card["elements"][0]["content"]
        self.assertIn("沒有回應", body)

    def test_custom_header_title(self):
        card = build_reply_card("hi", header_title="🌅 早安摘要")
        self.assertEqual(card["header"]["title"]["content"], "🌅 早安摘要")


if __name__ == "__main__":
    unittest.main()
