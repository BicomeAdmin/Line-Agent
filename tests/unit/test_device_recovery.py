import unittest
from unittest.mock import patch

from app.adb.client import AdbError
from app.workflows.device_recovery import ensure_device_ready


class DeviceRecoveryTests(unittest.TestCase):
    @patch("app.workflows.device_recovery.append_audit_event")
    @patch("app.workflows.device_recovery.get_device_config")
    @patch("app.workflows.device_recovery.AdbClient")
    def test_blocks_when_device_not_visible_and_no_avd(
        self,
        mock_adb_client,
        mock_get_device_config,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a", "avd_name": None})()
        controller = mock_adb_client.return_value
        controller.is_available.return_value = True
        controller.devices.return_value = []

        result = ensure_device_ready("emulator-5554", wait_timeout_seconds=1)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "device_not_visible")
        mock_append_audit_event.assert_called()

    @patch("app.workflows.device_recovery.append_audit_event")
    @patch("app.workflows.device_recovery.get_device_status")
    @patch("app.workflows.device_recovery.wait_for_boot")
    @patch("app.workflows.device_recovery.boot_completed")
    @patch("app.workflows.device_recovery.get_device_config")
    @patch("app.workflows.device_recovery.AdbClient")
    def test_returns_ready_when_device_visible_and_booted(
        self,
        mock_adb_client,
        mock_get_device_config,
        mock_boot_completed,
        mock_wait_for_boot,
        mock_get_device_status,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a", "avd_name": "project-echo-api35"})()
        controller = mock_adb_client.return_value
        controller.is_available.return_value = True
        controller.devices.return_value = ["emulator-5554"]
        mock_boot_completed.return_value = True
        mock_wait_for_boot.return_value = True
        mock_get_device_status.return_value = {"boot_completed": True, "line_installed": False, "line_active": False}

        result = ensure_device_ready("emulator-5554", wait_timeout_seconds=1)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["device_status"]["boot_completed"], True)
        mock_append_audit_event.assert_called()

    @patch("app.workflows.device_recovery.append_audit_event")
    @patch("app.workflows.device_recovery._wait_for_device_presence")
    @patch("app.workflows.device_recovery._ensure_avd_started")
    @patch("app.workflows.device_recovery.get_device_config")
    @patch("app.workflows.device_recovery.AdbClient")
    def test_starts_avd_when_device_missing(
        self,
        mock_adb_client,
        mock_get_device_config,
        mock_ensure_avd_started,
        mock_wait_for_device_presence,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a", "avd_name": "project-echo-api35"})()
        controller = mock_adb_client.return_value
        controller.is_available.return_value = True
        controller.devices.return_value = []
        mock_ensure_avd_started.return_value = True
        mock_wait_for_device_presence.return_value = False

        result = ensure_device_ready("emulator-5554", wait_timeout_seconds=1)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "device_not_visible_after_start")
        mock_ensure_avd_started.assert_called_with("project-echo-api35")
        mock_append_audit_event.assert_called()

    @patch("app.workflows.device_recovery.append_audit_event")
    @patch("app.workflows.device_recovery.get_device_config")
    @patch("app.workflows.device_recovery.AdbClient")
    def test_blocks_when_adb_unavailable(
        self,
        mock_adb_client,
        mock_get_device_config,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a", "avd_name": "project-echo-api35"})()
        controller = mock_adb_client.return_value
        controller.is_available.return_value = True
        controller.devices.side_effect = AdbError("adb daemon busy")

        result = ensure_device_ready("emulator-5554", wait_timeout_seconds=1)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "adb_unavailable")
        mock_append_audit_event.assert_called()


if __name__ == "__main__":
    unittest.main()
