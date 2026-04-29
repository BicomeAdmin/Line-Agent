import os
import unittest
from unittest import mock

from app.ai.codex_compose import (
    ComposerOutput,
    ComposerUnavailable,
    _build_prompt,
    _parse_output,
    compose_via_codex,
    is_enabled,
)
from app.ai.voice_profile_v2 import RouteMix, VoiceProfile


def _complete_vp() -> VoiceProfile:
    return VoiceProfile(
        customer_id="c",
        community_id="x",
        raw_text="...",
        value_proposition="提供靜坐引導",
        route_mix=RouteMix(ip=0.5, interest=0.3, info=0.2),
        stage="留存",
        engagement_appetite="medium",
        nickname="妍",
        personality="沉穩",
        style_anchors="短句",
        off_limits="不戰",
        is_complete=True,
        missing_fields=(),
    )


def _incomplete_vp() -> VoiceProfile:
    return VoiceProfile(
        customer_id="c",
        community_id="x",
        raw_text="",
        is_complete=False,
        missing_fields=("value_proposition", "stage"),
    )


class IsEnabledTests(unittest.TestCase):
    def test_default_off(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ECHO_COMPOSE_BACKEND", None)
            self.assertFalse(is_enabled())

    def test_codex_value(self) -> None:
        with mock.patch.dict(os.environ, {"ECHO_COMPOSE_BACKEND": "codex"}):
            self.assertTrue(is_enabled())

    def test_other_value_off(self) -> None:
        with mock.patch.dict(os.environ, {"ECHO_COMPOSE_BACKEND": "rule"}):
            self.assertFalse(is_enabled())


class IncompleteProfileRefusedTests(unittest.TestCase):
    def test_incomplete_profile_raises(self) -> None:
        with self.assertRaises(ComposerUnavailable) as ctx:
            compose_via_codex(
                voice_profile=_incomplete_vp(),
                community_name="x",
                target_sender="A",
                target_message="t",
                target_score=3.0,
                target_threshold=2.0,
                target_reasons=[],
                target_fingerprint=None,
                thread_excerpt=[],
                recent_self_posts=[],
            )
        self.assertIn("voice_profile_incomplete", str(ctx.exception))


class PromptBuildingTests(unittest.TestCase):
    def test_prompt_includes_target_and_voice(self) -> None:
        prompt = _build_prompt(
            voice_profile=_complete_vp(),
            community_name="水月觀音道場",
            target_sender="Lee",
            target_message="老師請問靜坐多久才有效？",
            target_score=3.5,
            target_threshold=2.0,
            target_reasons=["question:+3.5"],
            target_fingerprint={
                "avg_length": 12.0,
                "emoji_rate": 0.05,
                "top_ending_particles": ["啊", "呢"],
                "recent_lines": ["老師我盤腿會痛", "謝謝老師"],
            },
            thread_excerpt=[
                {"sender": "妍", "text": "今天大家有靜坐嗎"},
                {"sender": "Lee", "text": "老師請問靜坐多久才有效？"},
            ],
            recent_self_posts=["散盤就是腳自然交疊"],
        )
        self.assertIn("妍", prompt)
        self.assertIn("水月觀音道場", prompt)
        self.assertIn("Lee", prompt)
        self.assertIn("老師請問靜坐多久才有效？", prompt)
        self.assertIn("提供靜坐引導", prompt)
        self.assertIn("留存", prompt)
        self.assertIn("IP 主導", prompt)
        self.assertIn("散盤就是腳自然交疊", prompt)


class OutputParsingTests(unittest.TestCase):
    def test_parses_clean_json(self) -> None:
        raw = '{"should_engage": true, "rationale": "回答靜坐問題", "draft": "散盤就好", "confidence": 0.8, "off_limits_hit": null}'
        out = _parse_output(raw)
        self.assertTrue(out.should_engage)
        self.assertEqual(out.draft, "散盤就好")
        self.assertEqual(out.rationale, "回答靜坐問題")
        self.assertAlmostEqual(out.confidence, 0.8)
        self.assertIsNone(out.off_limits_hit)

    def test_parses_fenced_json(self) -> None:
        raw = '```json\n{"should_engage": false, "rationale": "踩到底線", "draft": "", "confidence": 0.9, "off_limits_hit": "命理"}\n```'
        out = _parse_output(raw)
        self.assertFalse(out.should_engage)
        self.assertEqual(out.off_limits_hit, "命理")

    def test_engage_without_draft_raises(self) -> None:
        raw = '{"should_engage": true, "rationale": "x", "draft": "", "confidence": 0.5}'
        with self.assertRaises(ComposerUnavailable):
            _parse_output(raw)

    def test_no_json_raises(self) -> None:
        with self.assertRaises(ComposerUnavailable):
            _parse_output("nothing here")

    def test_invalid_json_raises(self) -> None:
        with self.assertRaises(ComposerUnavailable):
            _parse_output("{ not valid json }")

    def test_confidence_clamped(self) -> None:
        raw = '{"should_engage": false, "rationale": "x", "draft": "", "confidence": 5.0}'
        out = _parse_output(raw)
        self.assertEqual(out.confidence, 1.0)

    def test_string_should_engage_accepted(self) -> None:
        raw = '{"should_engage": "true", "rationale": "x", "draft": "y", "confidence": 0.5}'
        out = _parse_output(raw)
        self.assertTrue(out.should_engage)


if __name__ == "__main__":
    unittest.main()
