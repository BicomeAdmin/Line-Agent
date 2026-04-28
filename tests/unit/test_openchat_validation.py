import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.workflows.openchat_validation import validate_openchat_session


class OpenChatValidationTests(unittest.TestCase):
    @patch("app.workflows.openchat_validation.append_audit_event")
    @patch("app.workflows.openchat_validation.check_current_app")
    @patch("app.workflows.openchat_validation.get_device_status")
    @patch("app.workflows.openchat_validation.load_customer_config")
    @patch("app.workflows.openchat_validation.load_all_communities")
    def test_blocks_when_line_not_foreground(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_get_device_status,
        mock_check_current_app,
        mock_append_audit_event,
    ) -> None:
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "客戶 A - 測試群",
                    "device_id": "emulator-5554",
                },
            )()
        ]
        mock_load_customer_config.return_value = type("Customer", (), {"display_name": "客戶 A"})()
        mock_get_device_status.return_value = {
            "line_active": False,
            "foreground_package": "com.android.launcher",
        }
        mock_check_current_app.return_value = False

        result = validate_openchat_session(community_id="openchat_001")

        item = result["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertEqual(item["reason"], "line_not_foreground")

    @patch("app.workflows.openchat_validation.append_audit_event")
    @patch("app.workflows.openchat_validation.dump_ui_xml")
    @patch("app.workflows.openchat_validation.check_current_app")
    @patch("app.workflows.openchat_validation.get_device_status")
    @patch("app.workflows.openchat_validation.load_customer_config")
    @patch("app.workflows.openchat_validation.load_all_communities")
    def test_matches_target_openchat_title(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_get_device_status,
        mock_check_current_app,
        mock_dump_ui_xml,
        mock_append_audit_event,
    ) -> None:
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "客戶 A - 測試群",
                    "device_id": "emulator-5554",
                },
            )()
        ]
        mock_load_customer_config.return_value = type("Customer", (), {"display_name": "客戶 A"})()
        mock_get_device_status.return_value = {
            "line_active": True,
            "foreground_package": "jp.naver.line.android",
        }
        mock_check_current_app.return_value = True

        with TemporaryDirectory() as tmpdir:
            xml_path = Path(tmpdir) / "validation.xml"
            xml_path.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
                <hierarchy>
                  <node text="測試群" />
                  <node text="大家好" />
                </hierarchy>
                """,
                encoding="utf-8",
            )
            mock_dump_ui_xml.return_value = xml_path

            result = validate_openchat_session(community_id="openchat_001")

        item = result["items"][0]
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["matched_title"], "測試群")

    @patch("app.workflows.openchat_validation.append_audit_event")
    @patch("app.workflows.openchat_validation.dump_ui_xml")
    @patch("app.workflows.openchat_validation.check_current_app")
    @patch("app.workflows.openchat_validation.get_device_status")
    @patch("app.workflows.openchat_validation.load_customer_config")
    @patch("app.workflows.openchat_validation.load_all_communities")
    def test_blocks_when_target_openchat_not_visible(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_get_device_status,
        mock_check_current_app,
        mock_dump_ui_xml,
        mock_append_audit_event,
    ) -> None:
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "客戶 A - 測試群",
                    "device_id": "emulator-5554",
                },
            )()
        ]
        mock_load_customer_config.return_value = type("Customer", (), {"display_name": "客戶 A"})()
        mock_get_device_status.return_value = {
            "line_active": True,
            "foreground_package": "jp.naver.line.android",
        }
        mock_check_current_app.return_value = True

        with TemporaryDirectory() as tmpdir:
            xml_path = Path(tmpdir) / "validation.xml"
            xml_path.write_text(
                """<?xml version="1.0" encoding="UTF-8"?>
                <hierarchy>
                  <node text="LINE 社群" />
                  <node text="還沒進到測試群" />
                </hierarchy>
                """,
                encoding="utf-8",
            )
            mock_dump_ui_xml.return_value = xml_path

            result = validate_openchat_session(community_id="openchat_001")

        item = result["items"][0]
        self.assertEqual(item["status"], "blocked")
        self.assertEqual(item["reason"], "target_openchat_not_visible")
