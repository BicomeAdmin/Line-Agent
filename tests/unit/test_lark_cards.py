import unittest

from app.lark.cards import build_review_card
from app.lark.status_cards import build_readiness_status_card


class LarkCardTests(unittest.TestCase):
    def test_review_card_includes_reason_and_confidence(self) -> None:
        card = build_review_card(
            "客戶 A",
            "測試群",
            "這是一段草稿",
            "job-123",
            customer_id="customer_a",
            community_id="openchat_001",
            device_id="emulator-5554",
            reason="user_question",
            confidence=0.88,
        )
        elements = card["elements"]
        self.assertTrue(isinstance(elements, list))
        body = elements[0]["content"]
        self.assertIn("user_question", body)
        self.assertIn("0.88", body)
        actions = elements[1]["actions"]
        self.assertEqual(actions[0]["value"]["customer_id"], "customer_a")
        self.assertEqual(actions[0]["value"]["community_id"], "openchat_001")
        self.assertEqual(actions[0]["value"]["device_id"], "emulator-5554")
        self.assertEqual(actions[0]["value"]["draft_text"], "這是一段草稿")

    def test_readiness_card_includes_blockers_and_next_actions(self) -> None:
        card = build_readiness_status_card(
            {
                "summary": {"ready": False, "blocker_count": 2, "warning_count": 1, "device_count": 1, "community_count": 1},
                "global_checks": [
                    {"severity": "warning", "message": "尚未配置 Lark verification token。"},
                ],
                "devices": [
                    {
                        "device_id": "emulator-5554",
                        "checks": [
                            {"severity": "blocker", "message": "LINE 尚未安裝到模擬器。"},
                        ],
                    }
                ],
                "communities": [
                    {
                        "community_name": "測試群",
                        "checks": [
                            {"severity": "blocker", "message": "尚未配置 input/send 座標。"},
                        ],
                    }
                ],
                "next_actions": ["安裝 LINE APK", "校準送出座標"],
            }
        )
        elements = card["elements"]
        self.assertIn("blockers", elements[0]["content"])
        self.assertIn("LINE 尚未安裝", elements[1]["content"])
        self.assertIn("verification token", elements[2]["content"])
        self.assertIn("安裝 LINE APK", elements[3]["content"])


if __name__ == "__main__":
    unittest.main()
