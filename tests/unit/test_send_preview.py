import unittest
from unittest.mock import patch

from app.workflows.send_preview import preview_send


class SendPreviewTests(unittest.TestCase):
    @patch("app.workflows.send_preview.load_community_config")
    def test_preview_send_returns_blocked_when_coordinates_missing(self, mock_load_community_config) -> None:
        mock_load_community_config.return_value = type(
            "Community",
            (),
            {
                "display_name": "測試群",
                "device_id": "emulator-5554",
                "coordinate_source": "missing",
                "input_x": None,
                "input_y": None,
                "send_x": None,
                "send_y": None,
            },
        )()
        result = preview_send("customer_a", "openchat_001", "hello")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "missing_send_coordinates")

    @patch("app.workflows.send_preview.load_community_config")
    def test_preview_send_returns_plan_when_coordinates_ready(self, mock_load_community_config) -> None:
        mock_load_community_config.return_value = type(
            "Community",
            (),
            {
                "display_name": "測試群",
                "device_id": "emulator-5554",
                "coordinate_source": "runtime_cli",
                "input_x": 11,
                "input_y": 22,
                "send_x": 33,
                "send_y": 44,
            },
        )()
        result = preview_send("customer_a", "openchat_001", "hello world")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["coordinate_source"], "runtime_cli")
        self.assertEqual(result["plan"]["send_tap"], {"x": 33, "y": 44})


if __name__ == "__main__":
    unittest.main()
