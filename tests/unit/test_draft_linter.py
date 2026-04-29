import unittest

from app.ai.draft_linter import score_draft


class DraftLinterTests(unittest.TestCase):
    def test_natural_full_compliance_scores_100(self) -> None:
        r = score_draft("我自己會看靜坐後有沒有比較清明喔，不一定要追求很特別的感覺啊")
        self.assertEqual(r.score, 100)
        self.assertEqual(r.verdict, "natural")

    def test_short_first_person_reply_passes_without_hedger(self) -> None:
        r = score_draft("我也還沒填欸 哈")
        self.assertGreaterEqual(r.score, 80)
        self.assertEqual(r.verdict, "natural")

    def test_banned_opener_gets_heavy_penalty(self) -> None:
        r = score_draft("大家如果有興趣，可以順手了解一下喔")
        self.assertLess(r.score, 60)
        self.assertIn(r.verdict, ("stiff", "broadcast"))
        self.assertTrue(any("廣播" in i or "客服" in i for i in r.issues))

    def test_customer_service_phrase_gets_broadcast_verdict(self) -> None:
        r = score_draft("歡迎隨時提問，我們會盡快為您解答")
        self.assertEqual(r.verdict, "broadcast")
        self.assertLess(r.score, 35)

    def test_promo_phrases_get_broadcast(self) -> None:
        r = score_draft("立刻購買 限時搶購中")
        self.assertEqual(r.verdict, "broadcast")

    def test_shou_dao_opener_warns_softly(self) -> None:
        r = score_draft("收到，謝謝老師的講解！")
        self.assertIn(r.verdict, ("stiff", "broadcast"))
        self.assertTrue(any("收到" in (s or "") for s in r.suggestions))

    def test_list_pattern_gets_announce_penalty(self) -> None:
        r = score_draft("- 第一點\n- 第二點")
        self.assertLess(r.score, 60)
        self.assertTrue(any("列點" in i for i in r.issues))

    def test_empty_returns_zero(self) -> None:
        r = score_draft("")
        self.assertEqual(r.score, 0)
        self.assertEqual(r.verdict, "empty")

    def test_emoji_only_gets_penalized(self) -> None:
        r = score_draft("🙏❤️")
        self.assertLess(r.score, 50)

    def test_stiff_long_no_particle_no_hedger(self) -> None:
        r = score_draft("這是一段沒有任何語助詞也沒有軟化詞的長句子內容")
        self.assertLess(r.score, 80)
        self.assertTrue(any("語助詞" in i for i in r.issues))

    def test_to_dict_serializes_cleanly(self) -> None:
        r = score_draft("我覺得這個應該還行喔")
        d = r.to_dict()
        self.assertIn("score", d)
        self.assertIn("verdict", d)
        self.assertIn("breakdown", d)
        self.assertIsInstance(d["issues"], list)


if __name__ == "__main__":
    unittest.main()
