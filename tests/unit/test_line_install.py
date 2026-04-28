import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.workflows.line_install import (
    MIN_REASONABLE_APK_BYTES,
    inspect_line_apk_sources,
    install_line_app,
    line_apk_candidate_paths,
)


def _write_fake_apk(path: Path, size_bytes: int = MIN_REASONABLE_APK_BYTES) -> Path:
    path.write_bytes(b"\0" * size_bytes)
    return path


class LineInstallTests(unittest.TestCase):
    @patch("app.workflows.line_install.settings")
    def test_inspect_line_apk_sources_reports_missing_candidates(self, mock_settings) -> None:
        mock_settings.line_apk_path = None

        result = inspect_line_apk_sources("/tmp/missing-line.apk")

        self.assertFalse(result["available"])
        self.assertGreaterEqual(result["candidate_count"], 1)

    @patch("app.workflows.line_install.append_audit_event")
    @patch("app.workflows.line_install.ensure_device_ready")
    @patch("app.workflows.line_install.get_device_config")
    def test_blocks_when_device_not_ready(
        self,
        mock_get_device_config,
        mock_ensure_device_ready,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
        mock_ensure_device_ready.return_value = {"status": "blocked", "reason": "device_not_visible"}

        result = install_line_app("emulator-5554", apk_path="/tmp/fake.apk")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "device_not_ready")
        mock_append_audit_event.assert_called()

    @patch("app.workflows.line_install.append_audit_event")
    @patch("app.workflows.line_install.ensure_device_ready")
    @patch("app.workflows.line_install.get_device_config")
    def test_blocks_when_apk_missing(
        self,
        mock_get_device_config,
        mock_ensure_device_ready,
        mock_append_audit_event,
    ) -> None:
        mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
        mock_ensure_device_ready.return_value = {"status": "ready"}

        result = install_line_app("emulator-5554", apk_path="/tmp/missing-line.apk")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "apk_not_found")
        self.assertIn("apk_inspection", result)
        mock_append_audit_event.assert_called()

    @patch("app.workflows.line_install.append_audit_event")
    @patch("app.workflows.line_install.get_device_status")
    @patch("app.workflows.line_install.ensure_device_ready")
    @patch("app.workflows.line_install.get_device_config")
    @patch("app.workflows.line_install.AdbClient")
    def test_installs_when_apk_present(
        self,
        mock_adb_client,
        mock_get_device_config,
        mock_ensure_device_ready,
        mock_get_device_status,
        mock_append_audit_event,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            apk_path = _write_fake_apk(Path(temp_dir) / "line.apk")
            mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
            mock_ensure_device_ready.return_value = {"status": "ready"}
            mock_get_device_status.return_value = {"line_installed": True, "foreground_package": None}
            mock_adb_client.return_value.install.return_value = type("Result", (), {"stdout": "Success"})()

            result = install_line_app("emulator-5554", apk_path=str(apk_path))

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["device_status"]["line_installed"], True)
            mock_adb_client.return_value.install.assert_called()
            mock_append_audit_event.assert_called()


    @patch("app.workflows.line_install.append_audit_event")
    @patch("app.workflows.line_install.ensure_device_ready")
    @patch("app.workflows.line_install.get_device_config")
    def test_blocks_when_apk_too_small(
        self,
        mock_get_device_config,
        mock_ensure_device_ready,
        mock_append_audit_event,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            apk_path = Path(temp_dir) / "line.apk"
            apk_path.write_bytes(b"junk")
            mock_get_device_config.return_value = type("Device", (), {"customer_id": "customer_a"})()
            mock_ensure_device_ready.return_value = {"status": "ready"}

            result = install_line_app("emulator-5554", apk_path=str(apk_path))

            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "apk_too_small")
            self.assertIn("apk_inspection", result)
            self.assertEqual(result["apk_inspection"]["rejected_too_small"], [str(apk_path.resolve())])
            mock_append_audit_event.assert_called()

    @patch("app.workflows.line_install.settings")
    def test_inspect_reports_size_and_reasonable_flags(self, mock_settings) -> None:
        mock_settings.line_apk_path = None
        with TemporaryDirectory() as temp_dir:
            big = _write_fake_apk(Path(temp_dir) / "LINE_v13.apk")

            result = inspect_line_apk_sources(str(big))
            big_resolved = str(big.resolve())

            self.assertTrue(result["available"])
            self.assertEqual(result["selected_path"], big_resolved)
            selected_item = next(item for item in result["items"] if item["path"] == big_resolved)
            self.assertTrue(selected_item["looks_reasonable"])
            self.assertGreaterEqual(selected_item["size_bytes"], MIN_REASONABLE_APK_BYTES)

    @patch("app.workflows.line_install.settings")
    def test_glob_discovery_in_downloads(self, mock_settings) -> None:
        mock_settings.line_apk_path = None
        with TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            downloads = home / "Downloads"
            downloads.mkdir()
            (downloads / "LINE_v13.5.0.apk").write_bytes(b"x")
            (downloads / "unrelated.txt").write_text("nope")

            with patch("app.workflows.line_install.Path.home", return_value=home):
                candidates = line_apk_candidate_paths()

            paths = [str(c) for c in candidates]
            self.assertIn(str(downloads / "LINE_v13.5.0.apk"), paths)
            self.assertNotIn(str(downloads / "unrelated.txt"), paths)


if __name__ == "__main__":
    unittest.main()
