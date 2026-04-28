"""Tests for reply_target_selector — pure-logic scoring of which
chat message the bot should reply to in autonomous mode."""

import unittest

from app.workflows.reply_target_selector import select_reply_target


def _msg(sender, text, position=0):
    return {"sender": sender, "text": text, "position": position}


def _persona(nickname, recent_texts=None):
    return {
        "status": "ok",
        "voice_profile": {"nickname": nickname, "style_anchors": ""},
        "recent_self_posts": [{"text": t} for t in (recent_texts or [])],
    }


class SelectReplyTargetTests(unittest.TestCase):
    def test_no_messages_skips(self):
        d = select_reply_target([], operator_persona=_persona("阿樂"))
        self.assertIsNone(d.target)
        self.assertEqual(d.skip_reason, "no_messages")

    def test_mention_to_operator_wins(self):
        msgs = [
            _msg("Alice", "今天天氣不錯"),
            _msg("Bob", "@阿樂 你看一下這個"),
            _msg("Carol", "貼圖"),
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        self.assertIsNotNone(d.target)
        self.assertEqual(d.target.sender, "Bob")
        self.assertTrue(any("mentions_operator" in r for r in d.target.reasons))

    def test_skips_self_messages(self):
        msgs = [
            _msg("阿樂", "我覺得這樣不錯"),
            _msg("Alice", "貼圖"),
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        # No good candidate — operator's own msg should not be selected.
        if d.target is not None:
            self.assertNotEqual(d.target.sender, "阿樂")

    def test_unanswered_question_in_op_thread(self):
        msgs = [
            _msg("阿樂", "JN3 我也還沒填欸"),
            _msg("Alice", "JN3 是要填到什麼時候啊"),  # question, no answer, op was in thread
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        self.assertIsNotNone(d.target)
        self.assertEqual(d.target.sender, "Alice")
        self.assertTrue(any("unanswered_q" in r for r in d.target.reasons))

    def test_after_operator_speech_boosts(self):
        msgs = [
            _msg("Carol", "聊聊週末做什麼"),
            _msg("阿樂", "我會去爬山"),
            _msg("Alice", "爬山好啊我也想去"),  # immediately after operator
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        self.assertIsNotNone(d.target)
        self.assertEqual(d.target.sender, "Alice")
        self.assertTrue(any("after_operator_speech" in r for r in d.target.reasons))

    def test_auto_reply_penalty(self):
        msgs = [
            _msg("Auto-reply", "🦦晚安各位隊友"),
            _msg("Alice", "@阿樂 晚安"),
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        self.assertEqual(d.target.sender, "Alice")  # mention beats auto-reply

    def test_threshold_skips_weak_picks(self):
        # All messages low-quality: no mentions, no questions, no operator presence.
        msgs = [
            _msg("Alice", "貼圖"),
            _msg("Bob", "圖片"),
            _msg("Carol", "🤣🤣"),
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        self.assertIsNone(d.target)
        self.assertIn("no_candidate_above_threshold", d.skip_reason or "")

    def test_threshold_override(self):
        # Same low-quality msgs but with threshold=0.0 (accept anything non-negative).
        msgs = [
            _msg("Alice", "今天天氣很好啊"),  # gets recency only
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"), threshold=0.0)
        # Anything non-negative passes — should select something.
        self.assertIsNotNone(d.target)

    def test_recency_decay(self):
        # 20 messages; older mentions should score less than newer ones.
        msgs = [_msg(f"User{i}", f"hello {i}", i) for i in range(18)]
        msgs[1] = _msg("Bob", "@阿樂 看一下", 1)        # very old mention
        msgs[17] = _msg("Carol", "@阿樂 看一下", 17)   # very recent mention
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        self.assertEqual(d.target.sender, "Carol")  # recency wins


class TopicOverlapTests(unittest.TestCase):
    def test_topic_overlap_with_operator_recent_posts(self):
        msgs = [
            _msg("Alice", "今天股票行情怎麼樣"),
            _msg("Bob", "我家狗狗很可愛"),
        ]
        # Operator recently talked about 股票
        d = select_reply_target(
            msgs,
            operator_persona=_persona("阿樂", recent_texts=["昨天股票漲了不少"]),
            threshold=1.0,  # topic overlap alone is intentionally a weak signal
        )
        # Alice's message has topic overlap (股票) → should be picked
        self.assertEqual(d.target.sender, "Alice")
        self.assertTrue(any("topic_overlap" in r for r in d.target.reasons))

    def test_topic_overlap_alone_below_default_threshold(self):
        """Topic overlap alone (no mention, no question, no thread participation)
        should NOT trigger autonomous compose — too weak a signal."""
        msgs = [_msg("Alice", "今天股票行情怎麼樣")]
        d = select_reply_target(
            msgs,
            operator_persona=_persona("阿樂", recent_texts=["昨天股票漲了不少"]),
        )
        self.assertIsNone(d.target)


class PaulPrinciplesTests(unittest.TestCase):
    """Verify Paul《私域流量》-derived weights: pain bonus + broadcast penalty."""

    def test_pain_message_gets_bonus(self):
        msgs = [
            _msg("Alice", "好難喔不知道怎麼辦"),  # pain signal
            _msg("Bob", "貼圖"),
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        # Alice should win on pain bonus alone.
        self.assertIsNotNone(d.target)
        self.assertEqual(d.target.sender, "Alice")
        self.assertTrue(any("pain_or_need" in r for r in d.target.reasons), msg=str(d.target.reasons))

    def test_pain_message_with_question_combines(self):
        msgs = [
            _msg("阿樂", "今天聊聊整理"),
            _msg("Alice", "求救！我家衣櫃完全卡住怎麼辦"),
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        self.assertEqual(d.target.sender, "Alice")
        # Pain (+2.0) + after_operator_speech (+2.5) — easily clears threshold
        self.assertGreater(d.target.score, 4.0)

    def test_broadcast_penalty(self):
        msgs = [
            _msg("Alice", "@All 福利公告：限時優惠！快搶 https://example.com"),
            _msg("Bob", "我覺得這個還不錯"),
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        # Bob's chatty message should win over Alice's broadcast,
        # even though Alice has a mention. Broadcast penalty -1.5
        # mostly negates the @-mention.
        if d.target:
            self.assertNotEqual(d.target.sender, "Alice")

    def test_broadcast_alone_doesnt_fire(self):
        msgs = [
            _msg("Alice", "@All 抽獎活動報名連結 https://example.com 名額有限"),
        ]
        d = select_reply_target(msgs, operator_persona=_persona("阿樂"))
        # Pure broadcast → should be skipped entirely.
        self.assertIsNone(d.target)


if __name__ == "__main__":
    unittest.main()
