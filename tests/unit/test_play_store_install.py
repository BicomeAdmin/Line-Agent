import unittest
from unittest.mock import patch

from app.workflows.play_store_install import (
    LINE_PACKAGE,
    PLAY_STORE_PACKAGE,
    PLAY_STORE_URL,
    has_play_store,
    open_line_in_play_store,
    wait_for_line_installed,
)


def _shell_response(stdout: str = "") -> object:
    return type("R", (), {"stdout": stdout})()


class PlayStoreInstallTests(unittest.TestCase):
    @patch("app.workflows.play_store_install.append_audit_event")
    @patch("app.workflows.play_store_install.AdbClient")
    @patch("app.workflows.play_store_install.ensure_device_ready")
    @patch("app.workflows.play_store_install.get_device_config")
    def test_open_line_in_play_store_dispatches_intent(
        self,
        mock_get_device_config,
        mock_ensure_device_ready,
        mock_adb_client,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
        mock_ensure_device_ready.return_value = {"status": "ready"}
        mock_adb_client.return_value.shell.side_effect = [
            _shell_response(f"{PLAY_STORE_PACKAGE}/com.google.android.finsky.activities.MainActivity\n"),
            _shell_response(""),
            _shell_response(""),
        ]

        result = open_line_in_play_store("emulator-5554")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["play_store_url"], PLAY_STORE_URL)
        called_args = [call.args for call in mock_adb_client.return_value.shell.call_args_list]
        self.assertIn(("am", "start", "-a", "android.intent.action.VIEW", "-d", PLAY_STORE_URL), called_args)
        mock_append_audit_event.assert_called()

    @patch("app.workflows.play_store_install.append_audit_event")
    @patch("app.workflows.play_store_install.AdbClient")
    @patch("app.workflows.play_store_install.ensure_device_ready")
    @patch("app.workflows.play_store_install.get_device_config")
    def test_open_line_blocks_when_play_store_missing(
        self,
        mock_get_device_config,
        mock_ensure_device_ready,
        mock_adb_client,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
        mock_ensure_device_ready.return_value = {"status": "ready"}
        mock_adb_client.return_value.shell.return_value = _shell_response("No activity found")

        result = open_line_in_play_store("emulator-5554")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "play_store_missing")

    @patch("app.workflows.play_store_install.time.sleep", lambda *_: None)
    @patch("app.workflows.play_store_install.append_audit_event")
    @patch("app.workflows.play_store_install.get_device_status")
    @patch("app.workflows.play_store_install.AdbClient")
    @patch("app.workflows.play_store_install.get_device_config")
    def test_wait_for_line_installed_returns_ok_when_package_appears(
        self,
        mock_get_device_config,
        mock_adb_client,
        mock_get_device_status,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
        mock_adb_client.return_value.shell.side_effect = [
            _shell_response(""),
            _shell_response(f"package:{LINE_PACKAGE}\n"),
        ]
        mock_get_device_status.return_value = {"line_installed": True, "foreground_package": None}

        result = wait_for_line_installed("emulator-5554", timeout_seconds=30, poll_seconds=1)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["polls"], 2)
        self.assertEqual(result["source"], "play_store")
        events = [call.args[1] for call in mock_append_audit_event.call_args_list]
        self.assertIn("line_install_completed", events)

    @patch("app.workflows.play_store_install.time.monotonic")
    @patch("app.workflows.play_store_install.time.sleep", lambda *_: None)
    @patch("app.workflows.play_store_install.append_audit_event")
    @patch("app.workflows.play_store_install.AdbClient")
    @patch("app.workflows.play_store_install.get_device_config")
    def test_wait_for_line_installed_times_out(
        self,
        mock_get_device_config,
        mock_adb_client,
        mock_append_audit_event,
        mock_monotonic,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
        mock_adb_client.return_value.shell.return_value = _shell_response("")
        # Start at 0; poll once, then deadline exceeded.
        mock_monotonic.side_effect = [0.0, 0.5, 100.0]

        result = wait_for_line_installed("emulator-5554", timeout_seconds=10, poll_seconds=1)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "timeout")
        events = [call.args[1] for call in mock_append_audit_event.call_args_list]
        self.assertIn("line_install_blocked", events)


    @patch("app.workflows.play_store_install.AdbClient")
    def test_has_play_store_false_on_license_checker_stub(self, mock_adb_client) -> None:
        # On Google APIs (non-Play) emulator images, com.android.vending exists as a
        # LicenseChecker stub with no launchable activity. Intent resolution returns
        # "No activity found" and we must report no Play Store.
        mock_adb_client.return_value.shell.return_value = _shell_response("No activity found")
        self.assertFalse(has_play_store("emulator-5554"))

    @patch("app.workflows.play_store_install.AdbClient")
    def test_has_play_store_true_when_intent_resolves_to_phonesky(self, mock_adb_client) -> None:
        mock_adb_client.return_value.shell.return_value = _shell_response(
            f"{PLAY_STORE_PACKAGE}/com.google.android.finsky.activities.MainActivity"
        )
        self.assertTrue(has_play_store("emulator-5554"))


if __name__ == "__main__":
    unittest.main()
