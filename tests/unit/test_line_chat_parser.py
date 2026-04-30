import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.parsing.line_chat_parser import (
    _parse_line_time_label,
    parse_line_chat,
)

TPE = ZoneInfo("Asia/Taipei")


class LineChatParserTests(unittest.TestCase):
    def test_parse_line_chat_sample(self) -> None:
        xml = Path("samples/xml/line_chat_dump.sample.xml").read_text(encoding="utf-8")
        messages = parse_line_chat(xml)
        self.assertEqual(
            [message.text for message in messages],
            [
                "請問新手媽媽奶瓶怎麼選？",
                "我之前會先看材質和寶寶接受度",
                "玻璃比較好清潔，但外出比較重",
            ],
        )


class TimestampParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 30, 14, 0, tzinfo=TPE)

    def test_pm_label(self) -> None:
        dt = _parse_line_time_label("下午4:19", now_tpe=self.now)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 16)
        self.assertEqual(dt.minute, 19)

    def test_am_label(self) -> None:
        dt = _parse_line_time_label("上午10:28", now_tpe=self.now)
        self.assertEqual(dt.hour, 10)
        self.assertEqual(dt.minute, 28)

    def test_noon_pm12(self) -> None:
        # 下午12:30 stays 12:30, not 24:30
        dt = _parse_line_time_label("下午12:30", now_tpe=self.now)
        self.assertEqual(dt.hour, 12)

    def test_midnight_am12(self) -> None:
        dt = _parse_line_time_label("上午12:05", now_tpe=self.now)
        self.assertEqual(dt.hour, 0)
        self.assertEqual(dt.minute, 5)

    def test_future_time_rolls_to_yesterday(self) -> None:
        # now=14:00, label "下午8:00" parses as today 20:00 → 6h in future →
        # treated as yesterday's 20:00.
        dt = _parse_line_time_label("下午8:00", now_tpe=self.now)
        self.assertEqual(dt.day, self.now.day - 1)
        self.assertEqual(dt.hour, 20)

    def test_invalid_returns_none(self) -> None:
        self.assertIsNone(_parse_line_time_label("hello", now_tpe=self.now))
        self.assertIsNone(_parse_line_time_label("", now_tpe=self.now))
        self.assertIsNone(_parse_line_time_label("下午25:00", now_tpe=self.now))


class TimestampExtractionFromXMLTests(unittest.TestCase):
    """Verify chat_ui_row_timestamp nodes get attached to nearby messages."""

    def _xml(self, *, msg_y: int, ts_y: int, ts_label: str = "下午4:19") -> str:
        return f"""<?xml version='1.0'?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,2400]" resource-id="">
    <node text="hello world" bounds="[100,{msg_y}][800,{msg_y + 80}]"
          resource-id="jp.naver.line.android:id/chat_ui_message_text" />
    <node text="{ts_label}" bounds="[800,{ts_y}][900,{ts_y + 30}]"
          resource-id="jp.naver.line.android:id/chat_ui_row_timestamp" />
    <node text="alice" bounds="[100,{msg_y - 60}][300,{msg_y - 30}]"
          resource-id="jp.naver.line.android:id/chat_ui_row_sender" />
  </node>
</hierarchy>"""

    def test_timestamp_below_message_attaches(self) -> None:
        # Message y=782, timestamp y=923 → Δ=141, within 200px window
        now = datetime(2026, 4, 30, 22, 0, tzinfo=TPE).timestamp()
        msgs = parse_line_chat(self._xml(msg_y=782, ts_y=923), now_epoch=now)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].ts_label, "下午4:19")
        self.assertIsNotNone(msgs[0].ts_epoch)
        # Age should be roughly 5h 41min
        age_min = (now - msgs[0].ts_epoch) / 60
        self.assertAlmostEqual(age_min, 341, delta=2)

    def test_timestamp_too_far_doesnt_attach(self) -> None:
        # Message y=100, timestamp y=600 → Δ=500, outside window
        now = datetime(2026, 4, 30, 22, 0, tzinfo=TPE).timestamp()
        msgs = parse_line_chat(self._xml(msg_y=100, ts_y=600), now_epoch=now)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].ts_label, "")
        self.assertIsNone(msgs[0].ts_epoch)


if __name__ == "__main__":
    unittest.main()
