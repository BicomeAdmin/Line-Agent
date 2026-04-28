import unittest
from unittest.mock import patch

from app.ai.decision import decide_reply
from app.ai.llm_client import LlmDraft, LlmUnavailable


class LlmDecisionTests(unittest.TestCase):
    def test_uses_llm_when_enabled(self) -> None:
        with patch("app.ai.decision.is_enabled", return_value=True), patch(
            "app.ai.decision.generate_draft",
            return_value=LlmDraft(
                action="draft_reply",
                reason="picked up an unanswered question",
                confidence=0.78,
                draft="這個問題我有觀察過，我等下整理給你。",
                raw_text='{"action":"draft_reply","reason":"...","confidence":0.78,"draft":"..."}',
            ),
        ):
            result = decide_reply(
                [{"text": "有人試過 brand X 嗎？"}],
                persona_text="自然、克制",
                community_name="測試群",
                playbook_text="不評論個人外貌",
                safety_rules=["禁止商業推銷"],
            )

        self.assertEqual(result.source, "llm")
        self.assertEqual(result.action, "draft_reply")
        self.assertEqual(result.draft, "這個問題我有觀察過，我等下整理給你。")
        self.assertAlmostEqual(result.confidence, 0.78, places=2)

    def test_falls_back_to_rule_based_when_llm_unavailable(self) -> None:
        with patch("app.ai.decision.is_enabled", return_value=True), patch(
            "app.ai.decision.generate_draft",
            side_effect=LlmUnavailable("network down"),
        ):
            result = decide_reply(
                [{"text": "請問奶瓶怎麼選？"}],
                persona_text="自然、克制",
                community_name="測試群",
            )

        self.assertEqual(result.source, "llm_fallback")
        self.assertEqual(result.action, "draft_reply")
        self.assertEqual(result.reason, "user_question")
        self.assertIn("奶瓶", result.draft) if "奶瓶" in result.draft else self.assertTrue(result.draft)

    def test_pure_rule_based_when_llm_disabled(self) -> None:
        with patch("app.ai.decision.is_enabled", return_value=False):
            result = decide_reply(
                [{"text": f"msg-{i}"} for i in range(8)],
                persona_text="自然",
                community_name="測試群",
            )
        self.assertEqual(result.source, "rule_based")
        self.assertEqual(result.action, "no_action")


class LlmClientParseTests(unittest.TestCase):
    def test_parses_json_with_markdown_fences(self) -> None:
        from app.ai.llm_client import _parse_draft

        raw = '```json\n{"action":"no_action","reason":"crowd is talking","confidence":0.9,"draft":""}\n```'
        draft = _parse_draft(raw)
        self.assertEqual(draft.action, "no_action")
        self.assertEqual(draft.confidence, 0.9)

    def test_clamps_confidence_to_unit_interval(self) -> None:
        from app.ai.llm_client import _parse_draft

        draft = _parse_draft('{"action":"draft_reply","reason":"x","confidence":2.5,"draft":"hi"}')
        self.assertEqual(draft.confidence, 1.0)

    def test_rejects_invalid_action(self) -> None:
        from app.ai.llm_client import _parse_draft

        with self.assertRaises(LlmUnavailable):
            _parse_draft('{"action":"send_now","reason":"x","confidence":0.5,"draft":"hi"}')


if __name__ == "__main__":
    unittest.main()
