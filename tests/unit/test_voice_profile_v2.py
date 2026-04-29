import tempfile
import textwrap
import unittest
from pathlib import Path

from app.ai.voice_profile_v2 import parse_voice_profile


def _write(text: str) -> Path:
    fh = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
    fh.write(textwrap.dedent(text))
    fh.close()
    return Path(fh.name)


class VoiceProfileV2Tests(unittest.TestCase):
    def test_complete_profile_parses(self) -> None:
        path = _write(
            """
            ---
            value_proposition: 提供靜坐引導
            route_mix:
              ip: 0.5
              interest: 0.3
              info: 0.2
            stage: 留存
            engagement_appetite: medium
            ---

            ## My nickname

            - 妍

            ## My personality

            沉穩有耐心。

            ## Style anchors

            - 短句
            - 不打斷

            ## Off-limits

            - 不戰宗派
            - 不收金流
            """
        )
        vp = parse_voice_profile("c", "x", path)
        self.assertTrue(vp.is_complete)
        self.assertEqual(vp.missing_fields, ())
        self.assertEqual(vp.nickname, "妍")
        self.assertEqual(vp.stage, "留存")
        self.assertAlmostEqual(vp.route_mix.ip, 0.5)
        self.assertEqual(vp.route_mix.dominant(), "IP 主導")
        self.assertEqual(vp.engagement_appetite, "medium")

    def test_placeholder_personality_marks_incomplete(self) -> None:
        path = _write(
            """
            ---
            value_proposition: x
            route_mix:
              ip: 1
              interest: 0
              info: 0
            stage: 留存
            ---
            ## My nickname
            - 妍
            ## My personality
            （請操作員寫：你的個性）
            ## Off-limits
            - 不戰
            """
        )
        vp = parse_voice_profile("c", "x", path)
        self.assertFalse(vp.is_complete)
        self.assertIn("personality", vp.missing_fields)

    def test_invalid_stage_marks_incomplete(self) -> None:
        path = _write(
            """
            ---
            value_proposition: x
            route_mix:
              ip: 1
              interest: 0
              info: 0
            stage: 隨便寫
            ---
            ## My nickname
            - 妍
            ## My personality
            老師
            ## Off-limits
            - 不戰
            """
        )
        vp = parse_voice_profile("c", "x", path)
        self.assertFalse(vp.is_complete)
        self.assertIn("stage", vp.missing_fields)

    def test_missing_file_returns_incomplete(self) -> None:
        vp = parse_voice_profile("c", "x", Path("/no/such/path.md"))
        self.assertFalse(vp.is_complete)
        self.assertEqual(vp.missing_fields, ("file_missing",))

    def test_no_frontmatter_marks_incomplete(self) -> None:
        path = _write("# just a markdown file")
        vp = parse_voice_profile("c", "x", path)
        self.assertFalse(vp.is_complete)
        self.assertIn("value_proposition", vp.missing_fields)
        self.assertIn("route_mix", vp.missing_fields)
        self.assertIn("stage", vp.missing_fields)

    def test_route_mix_normalizes(self) -> None:
        path = _write(
            """
            ---
            value_proposition: x
            route_mix:
              ip: 5
              interest: 3
              info: 2
            stage: 留存
            ---
            ## My nickname
            - 妍
            ## My personality
            老師
            ## Off-limits
            - 不戰
            """
        )
        vp = parse_voice_profile("c", "x", path)
        # Each weight clamped to [0,1] then normalized → ip=1/(1+1+1)=.33
        total = vp.route_mix.ip + vp.route_mix.interest + vp.route_mix.info
        self.assertAlmostEqual(total, 1.0, places=3)


class StageObjectiveTests(unittest.TestCase):
    def test_stage_objective_for_each_valid_stage(self) -> None:
        for stage in ("拉新", "留存", "活躍", "裂變"):
            path = _write(
                f"""
                ---
                value_proposition: x
                route_mix: {{ip: 1, interest: 0, info: 0}}
                stage: {stage}
                ---
                ## My nickname
                - 妍
                ## My personality
                老師
                ## Off-limits
                - 不戰
                """
            )
            vp = parse_voice_profile("c", "x", path)
            self.assertNotEqual(vp.stage_objective, "")
            self.assertNotIn("未設定", vp.stage_objective)


if __name__ == "__main__":
    unittest.main()
