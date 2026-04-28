import unittest
from unittest.mock import patch

from app.workflows.prepare_line_session import prepare_line_session


class PrepareLineSessionTests(unittest.TestCase):
    @patch("app.workflows.prepare_line_session.append_audit_event")
    @patch("app.workflows.prepare_line_session.get_device_config")
    @patch("app.workflows.prepare_line_session.wait_for_boot")
    def test_prepare_line_session_blocks_when_boot_not_completed(
        self,
        mock_wait_for_boot,
        mock_get_device_config,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
        mock_wait_for_boot.return_value = False

        result = prepare_line_session("emulator-5554", boot_timeout_seconds=1)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "boot_not_completed")
        mock_append_audit_event.assert_called()

    @patch("app.workflows.prepare_line_session.append_audit_event")
    @patch("app.workflows.prepare_line_session.get_device_status")
    @patch("app.workflows.prepare_line_session.open_line")
    @patch("app.workflows.prepare_line_session.package_installed")
    @patch("app.workflows.prepare_line_session.wake_and_unlock")
    @patch("app.workflows.prepare_line_session.wait_for_boot")
    @patch("app.workflows.prepare_line_session.get_device_config")
    def test_prepare_line_session_returns_partial_when_line_not_foreground(
        self,
        mock_get_device_config,
        mock_wait_for_boot,
        mock_wake_and_unlock,
        mock_package_installed,
        mock_open_line,
        mock_get_device_status,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
        mock_wait_for_boot.return_value = True
        mock_package_installed.return_value = True
        mock_get_device_status.return_value = {
            "boot_completed": True,
            "line_installed": True,
            "line_active": False,
            "foreground_package": "jp.naver.line.android",
        }

        result = prepare_line_session("emulator-5554", boot_timeout_seconds=1)

        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["line_active"])
        mock_wake_and_unlock.assert_called()
        mock_open_line.assert_called()
        mock_append_audit_event.assert_called()


if __name__ == "__main__":
    unittest.main()
