import unittest

from app.lark.events import extract_command_text, extract_reply_target


class LarkEventTests(unittest.TestCase):
    def test_extract_command_text(self) -> None:
        payload = {
            "type": "event_callback",
            "event": {
                "message": {
                    "content": '{"text":"請回報系統狀態"}',
                    "chat_id": "oc_123",
                }
            },
        }
        self.assertEqual(extract_command_text(payload), "請回報系統狀態")

    def test_extract_reply_target_chat_id(self) -> None:
        payload = {
            "event": {
                "message": {"chat_id": "oc_123"},
            }
        }
        self.assertEqual(extract_reply_target(payload), {"receive_id": "oc_123", "receive_id_type": "chat_id"})


if __name__ == "__main__":
    unittest.main()
