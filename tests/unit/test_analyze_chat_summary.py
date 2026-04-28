"""Tests for analyze_chat's new 4-bucket summarizer (Tier 1 #3).

Heuristic, no LLM — verify it correctly identifies decisions, action
items, key engagement points, and unresolved questions in zh-TW
community chat patterns.
"""

import unittest

from app.workflows.analyze_chat import (
    _summarize_4_buckets,
    _unresolved_questions,
)


def _msg(sender, text, position=0):
    return {"sender": sender, "text": text, "position": position}


class SummarizeFourBucketsTests(unittest.TestCase):
    def test_empty_messages_returns_neutral_summary(self):
        out = _summarize_4_buckets([])
        self.assertEqual(out["key_points"], [])
        self.assertEqual(out["decisions"], [])
        self.assertIn("沒有訊息", out["summary_zh"])

    def test_decision_keyword_detection(self):
        msgs = [
            _msg("A", "我們改成週六辦活動好了"),
            _msg("B", "OK 那就決定週六"),
        ]
        out = _summarize_4_buckets(msgs)
        self.assertGreater(len(out["decisions"]), 0)
        self.assertTrue(any("決定" in d.get("matched", "") or "我們改" in d.get("matched", "")
                            for d in out["decisions"]))

    def test_action_item_detection(self):
        msgs = [
            _msg("A", "明天記得提交報名表"),
            _msg("B", "好的，我下週填寫"),
        ]
        out = _summarize_4_buckets(msgs)
        self.assertGreaterEqual(len(out["action_items"]), 2)

    def test_high_engagement_becomes_key_point(self):
        # Message 0 is followed by 3 different senders → key_point
        msgs = [
            _msg("Alice", "今天試了新的整理法很有效"),
            _msg("Bob", "怎麼做"),
            _msg("Carol", "我也想知道"),
            _msg("Dave", "+1"),
        ]
        out = _summarize_4_buckets(msgs)
        self.assertGreater(len(out["key_points"]), 0)
        # Alice's message should have follower_count >= 2
        self.assertTrue(any(kp.get("follower_count", 0) >= 2 for kp in out["key_points"]))

    def test_unresolved_question_detection(self):
        msgs = [
            _msg("A", "請問 JN3 是要填到什麼時候啊"),
            _msg("B", "貼圖"),  # not a real answer; B is different sender so... hmm
        ]
        # Actually "B" responding (any other sender) marks it as answered.
        # So we need an isolated question.
        msgs2 = [
            _msg("A", "請問 JN3 是要填到什麼時候啊"),
            # No follow-up at all
        ]
        out = _summarize_4_buckets(msgs2)
        self.assertGreater(len(out["unresolved_questions"]), 0)

    def test_summary_zh_format(self):
        msgs = [
            _msg("A", "我們決定明天開始"),  # decision + action
            _msg("B", "OK 確定了"),         # decision
        ]
        out = _summarize_4_buckets(msgs)
        # Should mention 決定 in summary
        self.assertIn("決定", out["summary_zh"])

    def test_short_messages_filtered_out(self):
        msgs = [_msg("A", "OK"), _msg("B", "好")]
        out = _summarize_4_buckets(msgs)
        # Both too short (< 4 chars) — should produce empty buckets
        self.assertEqual(len(out["decisions"]), 0)
        self.assertEqual(len(out["action_items"]), 0)


class UnresolvedQuestionsTests(unittest.TestCase):
    def test_question_with_immediate_answer_not_unresolved(self):
        msgs = [
            _msg("A", "JN3 是要填到什麼時候啊"),
            _msg("B", "五月底前喔"),
        ]
        # B (different sender) replied right after → answered
        unres = _unresolved_questions(msgs)
        self.assertEqual(len(unres), 0)

    def test_question_with_only_self_followups_unresolved(self):
        msgs = [
            _msg("A", "請問有人知道嗎"),
            _msg("A", "請問還在嗎"),  # same sender → doesn't mark first as answered
        ]
        unres = _unresolved_questions(msgs)
        # Both contain question keywords AND have only self follow-ups (or none)
        self.assertEqual(len(unres), 2)


if __name__ == "__main__":
    unittest.main()
