"""Tests for chat-title verification — the cross-community contamination guard."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.workflows.openchat_verify import (
    TitleVerification,
    _extract_header_title,
    verify_chat_title,
)


def _xml_with_title(title: str, *, rid: str = "header_title") -> str:
    return f"""<?xml version='1.0'?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]" resource-id="">
    <node text="{title}" bounds="[200,100][800,180]"
          resource-id="jp.naver.line.android:id/{rid}" />
  </node>
</hierarchy>"""


class ExtractTitleTests(unittest.TestCase):
    def test_extracts_header_title(self) -> None:
        self.assertEqual(_extract_header_title(_xml_with_title("水月觀音道場")), "水月觀音道場")

    def test_chat_ui_header_title_takes_priority(self) -> None:
        xml = """<?xml version='1.0'?>
<hierarchy>
  <node bounds="[0,0][1080,2400]">
    <node text="A" bounds="[0,0][100,100]"
          resource-id="jp.naver.line.android:id/header_title" />
    <node text="B" bounds="[0,0][100,100]"
          resource-id="jp.naver.line.android:id/chat_ui_header_title" />
  </node>
</hierarchy>"""
        self.assertEqual(_extract_header_title(xml), "B")

    def test_empty_when_no_title_node(self) -> None:
        xml = """<?xml version='1.0'?><hierarchy><node bounds="[0,0][100,100]" /></hierarchy>"""
        self.assertEqual(_extract_header_title(xml), "")

    def test_malformed_xml_returns_empty(self) -> None:
        self.assertEqual(_extract_header_title("not xml at all <<<"), "")


class VerifyChatTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
        self._tmp.close()
        self._path = Path(self._tmp.name)
        self.addCleanup(lambda: self._path.unlink(missing_ok=True))

    def _verify(self, xml: str, expected: str) -> TitleVerification:
        client = MagicMock()
        with patch("app.workflows.openchat_verify.dump_ui_xml") as dump:
            self._path.write_text(xml, encoding="utf-8")
            dump.return_value = self._path
            return verify_chat_title(client, self._path, expected)

    def test_exact_match_passes(self) -> None:
        v = self._verify(_xml_with_title("水月觀音道場"), "水月觀音道場")
        self.assertTrue(v.ok)
        self.assertEqual(v.reason, "match")

    def test_member_count_suffix_tolerated(self) -> None:
        # LINE shows "水月觀音道場(123)" — should still match expected.
        v = self._verify(_xml_with_title("水月觀音道場(123)"), "水月觀音道場")
        self.assertTrue(v.ok)

    def test_substring_match_works_both_ways(self) -> None:
        # Expected is shorter than current
        v = self._verify(_xml_with_title("水月觀音道場分院"), "水月觀音道場")
        self.assertTrue(v.ok)

    def test_mismatch_caught(self) -> None:
        v = self._verify(_xml_with_title("愛美星 Cfans 俱樂部"), "水月觀音道場")
        self.assertFalse(v.ok)
        self.assertEqual(v.reason, "title_mismatch")
        self.assertEqual(v.current_title, "愛美星 Cfans 俱樂部")

    def test_no_title_node_fails(self) -> None:
        xml = """<?xml version='1.0'?><hierarchy><node bounds="[0,0][1,1]" /></hierarchy>"""
        v = self._verify(xml, "水月觀音道場")
        self.assertFalse(v.ok)
        self.assertEqual(v.reason, "no_title_node")

    def test_empty_expected_fails_safe(self) -> None:
        v = self._verify(_xml_with_title("X"), "")
        self.assertFalse(v.ok)
        self.assertEqual(v.reason, "empty_expected_name")

    def test_dump_failure_fails_safe(self) -> None:
        client = MagicMock()
        with patch("app.workflows.openchat_verify.dump_ui_xml", side_effect=RuntimeError("boom")):
            v = verify_chat_title(client, self._path, "x")
        self.assertFalse(v.ok)
        self.assertTrue(v.reason.startswith("xml_dump_failed"))


if __name__ == "__main__":
    unittest.main()
