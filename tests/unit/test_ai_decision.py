import unittest

from app.ai.decision import decide_reply


class AiDecisionTests(unittest.TestCase):
    def test_question_becomes_draft_reply(self) -> None:
        result = decide_reply([{"text": "請問奶瓶怎麼選？"}], "語氣自然、克制、不誇大。", "測試群")
        self.assertEqual(result.reason, "user_question")
        self.assertEqual(result.action, "draft_reply")

    def test_busy_chat_returns_no_action(self) -> None:
        messages = [{"text": f"msg-{index}"} for index in range(6)]
        result = decide_reply(messages, "語氣自然", "測試群")
        self.assertEqual(result.action, "no_action")


if __name__ == "__main__":
    unittest.main()
