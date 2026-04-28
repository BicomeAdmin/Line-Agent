import unittest
from unittest.mock import patch

from app.workflows.line_apk_status import get_line_apk_status


class LineApkStatusTests(unittest.TestCase):
    @patch("app.workflows.line_apk_status.inspect_line_apk_sources")
    @patch("app.workflows.line_apk_status.get_device_status")
    @patch("app.workflows.line_apk_status.load_devices_config")
    def test_reports_devices_needing_line(
        self,
        mock_load_devices_config,
        mock_get_device_status,
        mock_inspect_line_apk_sources,
    ) -> None:
        mock_inspect_line_apk_sources.return_value = {"available": False, "selected_path": None, "items": []}
        mock_load_devices_config.return_value = [
            type("Device", (), {"device_id": "emulator-5554", "customer_id": "customer_a"})()
        ]
        mock_get_device_status.return_value = {"line_installed": False, "boot_completed": True}

        result = get_line_apk_status()

        self.assertEqual(result["devices_needing_line"], 1)
        self.assertFalse(result["apk_inspection"]["available"])
        self.assertTrue(any("APK" in item for item in result["next_actions"]))


if __name__ == "__main__":
    unittest.main()
