import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows.readiness_status import get_readiness_status


class ReadinessStatusTests(unittest.TestCase):
    @patch("app.workflows.readiness_status.customer_root")
    @patch("app.workflows.readiness_status.inspect_line_apk_sources")
    @patch("app.workflows.readiness_status.get_device_status")
    @patch("app.workflows.readiness_status.load_customer_config")
    @patch("app.workflows.readiness_status.load_all_communities")
    @patch("app.workflows.readiness_status.load_devices_config")
    @patch("app.workflows.readiness_status.settings")
    @patch("app.workflows.readiness_status.AdbClient")
    def test_readiness_reports_blockers_and_next_actions(
        self,
        mock_adb_client,
        mock_settings,
        mock_load_devices_config,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_get_device_status,
        mock_inspect_line_apk_sources,
        mock_customer_root,
    ) -> None:
        mock_adb_client.return_value.is_available.return_value = True
        mock_settings.lark_app_id = "cli_xxx"
        mock_settings.lark_app_secret = "secret_xxx"
        mock_settings.lark_verification_token = None
        mock_settings.require_human_approval = True
        mock_inspect_line_apk_sources.return_value = {"available": False}
        mock_load_devices_config.return_value = [
            type("Device", (), {"device_id": "emulator-5554", "customer_id": "customer_a"})()
        ]
        mock_load_customer_config.return_value = type("Customer", (), {"display_name": "客戶 A"})()
        mock_get_device_status.return_value = {
            "boot_completed": True,
            "line_installed": False,
            "line_active": False,
        }
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "測試群",
                    "persona": "default",
                    "device_id": "emulator-5554",
                    "patrol_interval_minutes": 120,
                    "input_x": None,
                    "input_y": None,
                    "send_x": None,
                    "send_y": None,
                },
            )()
        ]
        mock_customer_root.return_value = Path("/virtual/customer_a")

        result = get_readiness_status()

        self.assertFalse(result["summary"]["ready"])
        self.assertGreaterEqual(result["summary"]["blocker_count"], 2)
        self.assertGreaterEqual(result["summary"]["warning_count"], 2)
        self.assertTrue(any(item["key"] == "line_apk_available" and not item["ok"] for item in result["global_checks"]))
        self.assertTrue(any("LINE APK 安裝" in step or "LINE APK" in step for step in result["next_actions"]))
        self.assertTrue(any(item["key"] == "send_coordinates_ready" and not item["ok"] for item in result["communities"][0]["checks"]))


if __name__ == "__main__":
    unittest.main()
