import unittest
from pathlib import Path

from app.parsing.line_chat_parser import parse_line_chat


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


if __name__ == "__main__":
    unittest.main()
