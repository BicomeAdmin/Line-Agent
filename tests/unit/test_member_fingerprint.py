"""Tests for MemberFingerprint stylometric extension (Tier 2.4).

Covers the pure feature extractors (function words, punctuation, typo
signature, type-token ratio, multi-msg burst), the compute_fingerprints
aggregator, and the summary_zh rendering. Live calibration cases from
the change-log are encoded as fixtures so future tweaks to the
extractors regress visibly.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows import member_fingerprint as mf
from app.workflows.chat_export_import import ChatMessage


def _msg(sender: str, text: str, date: str = "2026-04-28", time: str = "10:00") -> ChatMessage:
    return ChatMessage(date=date, time=time, sender=sender, text=text)


class FunctionWordFreqTests(unittest.TestCase):
    def test_empty_input_returns_empty_dict(self):
        self.assertEqual(mf._function_word_freq([]), {})

    def test_freq_per_100_chars(self):
        # 「我我我我我」 = 5 chars, 5 「我」 → 100/100 = 100.0 per 100
        self.assertEqual(mf._function_word_freq(["我我我我我"]).get("我"), 100.0)

    def test_uncounted_words_absent(self):
        out = mf._function_word_freq(["完全不含任何虛詞的句子"])
        self.assertNotIn("啊", out)

    def test_caps_top_12(self):
        # Use a text with many distinct function words
        text = "我你他啊喔吧嗎呢啦耶嗯哈" * 10
        out = mf._function_word_freq([text])
        self.assertLessEqual(len(out), 12)


class PunctuationSignatureTests(unittest.TestCase):
    def test_counts_each_mark(self):
        sig = mf._punctuation_signature(["你好！！", "謝謝~~", "嗯..."])
        self.assertEqual(sig.get("！"), 2)
        self.assertEqual(sig.get("~"), 2)
        self.assertEqual(sig.get("..."), 1)

    def test_omits_zero_counts(self):
        sig = mf._punctuation_signature(["純文字沒符號"])
        self.assertEqual(sig, {})


class TypoSignatureTests(unittest.TestCase):
    """Encodes live calibration cases — these are explicit regression
    guards from change-log entries."""

    def test_zhuyin_d_for_de(self):
        # Common 注音文 substitution: ㄉ for 的
        sig = mf._typo_signature(["好ㄉ", "我ㄉ書"])
        self.assertEqual(sig.get("注音文_ㄉ"), 2)

    def test_jiang_for_zheyang_caught(self):
        # Live calibration: 山長王志鈞 「降_for_這樣」×2
        sig = mf._typo_signature(["降說好", "降好嗎"])
        self.assertEqual(sig.get("降_for_這樣"), 2)

    def test_jiang_excludes_legit_compounds(self):
        # 降水 / 降低 / 降溫 must NOT be miscounted as 「降」for 這樣
        sig = mf._typo_signature(["最近降水多", "降溫了", "成本要降低"])
        self.assertNotIn("降_for_這樣", sig)

    def test_fen_excludes_pink_etc(self):
        # 粉紅 / 粉碎 / 粉末 / 粉筆 must NOT be miscounted as 「粉」for 很
        sig = mf._typo_signature(["粉紅色", "粉碎機", "粉筆"])
        self.assertNotIn("粉_for_很", sig)

    def test_fen_for_hen_caught(self):
        sig = mf._typo_signature(["這個粉好用", "粉開心"])
        self.assertEqual(sig.get("粉_for_很"), 2)

    def test_xiami_for_shenme(self):
        sig = mf._typo_signature(["蝦米意思", "你說蝦米"])
        self.assertEqual(sig.get("蝦米_for_什麼"), 2)


class TypeTokenRatioTests(unittest.TestCase):
    def test_repetitive_text_low_ttr(self):
        # 同一個 bigram 重複 → 低多樣性
        ttr_low = mf._type_token_ratio(["你好你好你好你好"])
        # 多樣 bigram → 高 ttr
        ttr_high = mf._type_token_ratio(["今天天氣很好適合出門走走"])
        self.assertLess(ttr_low, ttr_high)

    def test_no_han_returns_zero(self):
        self.assertEqual(mf._type_token_ratio(["abc def 123"]), 0.0)


class MultiMsgBurstRateTests(unittest.TestCase):
    def test_single_message_zero(self):
        self.assertEqual(mf._multi_msg_burst_rate([_msg("a", "x")]), 0.0)

    def test_all_same_sender_full_burst(self):
        items = [_msg("a", "x"), _msg("a", "y"), _msg("a", "z")]
        # 2 of 2 transitions stay on same sender → 1.0
        self.assertEqual(mf._multi_msg_burst_rate(items), 1.0)

    def test_alternating_zero_burst(self):
        items = [_msg("a", "x"), _msg("b", "y"), _msg("a", "z"), _msg("b", "w")]
        self.assertEqual(mf._multi_msg_burst_rate(items), 0.0)


class ComputeFingerprintsTests(unittest.TestCase):
    def test_aggregates_per_sender(self):
        msgs = [
            _msg("alice", "嗨大家好"),
            _msg("alice", "今天天氣很好"),
            _msg("alice", "我去走走"),
            _msg("bob", "ok"),
        ]
        fps = mf.compute_fingerprints(msgs)
        senders = {f.sender for f in fps}
        self.assertEqual(senders, {"alice", "bob"})

    def test_sorted_by_message_count_desc(self):
        msgs = [_msg("low", "a"), _msg("high", "x"), _msg("high", "y"), _msg("high", "z")]
        fps = mf.compute_fingerprints(msgs)
        self.assertEqual(fps[0].sender, "high")
        self.assertEqual(fps[0].message_count, 3)

    def test_skips_empty_text(self):
        msgs = [_msg("a", ""), _msg("a", "  "), _msg("a", "real")]
        fps = mf.compute_fingerprints(msgs)
        # Only "real" counts → message_count=1
        self.assertEqual(fps[0].message_count, 1)

    def test_last_seen_date_picks_max(self):
        msgs = [
            _msg("a", "old", date="2026-04-01"),
            _msg("a", "new", date="2026-04-28"),
            _msg("a", "mid", date="2026-04-15"),
        ]
        fps = mf.compute_fingerprints(msgs)
        self.assertEqual(fps[0].last_seen_date, "2026-04-28")


class SummaryZhTests(unittest.TestCase):
    def test_includes_message_count_and_median(self):
        f = mf.MemberFingerprint(sender="alice", message_count=42, median_length=15.0)
        s = f.summary_zh()
        self.assertIn("alice", s)
        self.assertIn("42", s)
        self.assertIn("15", s)

    def test_emoji_rate_thresholds(self):
        high = mf.MemberFingerprint(sender="x", emoji_rate=0.10).summary_zh()
        self.assertIn("emoji 多", high)
        low = mf.MemberFingerprint(sender="x", emoji_rate=0.01).summary_zh()
        self.assertIn("少用 emoji", low)
        zero = mf.MemberFingerprint(sender="x", emoji_rate=0).summary_zh()
        self.assertIn("不用 emoji", zero)

    def test_repeated_punct_flag(self):
        s = mf.MemberFingerprint(sender="x", repeated_punct_rate=0.20).summary_zh()
        self.assertIn("!!/??", s)
        s_calm = mf.MemberFingerprint(sender="x", repeated_punct_rate=0.05).summary_zh()
        self.assertNotIn("!!/??", s_calm)

    def test_line_break_flag(self):
        s = mf.MemberFingerprint(sender="x", line_break_rate=0.7).summary_zh()
        self.assertIn("愛換行", s)


class CacheIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fingerprints_path_layout(self):
        p = mf.fingerprints_path("customer_a", "openchat_007")
        self.assertEqual(p.name, "openchat_007.json")
        self.assertEqual(p.parent.name, "member_fingerprints")

    def test_load_returns_none_when_missing(self):
        with patch.object(mf, "customer_data_root", lambda *_: self.data_root):
            self.assertIsNone(mf.load_member_fingerprints("customer_a", "openchat_x"))

    def test_get_member_fingerprint_lookup(self):
        path = self.data_root / "member_fingerprints" / "openchat_x.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "fingerprints": [
                {"sender": "alice", "message_count": 5},
                {"sender": "bob", "message_count": 2},
            ]
        }))
        with patch.object(mf, "customer_data_root", lambda *_: self.data_root):
            fp = mf.get_member_fingerprint("customer_a", "openchat_x", "alice")
            self.assertEqual(fp["message_count"], 5)
            self.assertIsNone(mf.get_member_fingerprint("customer_a", "openchat_x", "ghost"))


if __name__ == "__main__":
    unittest.main()
