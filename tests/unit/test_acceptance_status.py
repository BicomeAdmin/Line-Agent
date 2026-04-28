import unittest
from unittest.mock import patch

from app.workflows.acceptance_status import get_acceptance_status


class AcceptanceStatusTests(unittest.TestCase):
    @patch("app.workflows.acceptance_status.read_recent_chat")
    @patch("app.workflows.acceptance_status.default_raw_xml_path")
    @patch("app.workflows.acceptance_status.preview_send")
    @patch("app.workflows.acceptance_status.validate_openchat_session")
    @patch("app.workflows.acceptance_status.load_context_bundle")
    @patch("app.workflows.acceptance_status.get_device_status")
    @patch("app.workflows.acceptance_status.load_customer_config")
    @patch("app.workflows.acceptance_status.load_all_communities")
    def test_acceptance_ready_for_hil(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_get_device_status,
        mock_load_context_bundle,
        mock_validate_openchat_session,
        mock_preview_send,
        mock_default_raw_xml_path,
        mock_read_recent_chat,
    ) -> None:
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "測試群",
                    "device_id": "emulator-5554",
                },
            )()
        ]
        mock_load_customer_config.return_value = type("Customer", (), {"display_name": "客戶 A"})()
        mock_get_device_status.return_value = {
            "boot_completed": True,
            "line_installed": True,
            "line_active": True,
        }
        mock_validate_openchat_session.return_value = {
            "items": [
                {
                    "community_id": "openchat_001",
                    "status": "ok",
                    "message": "已進到目標 OpenChat。",
                }
            ]
        }
        mock_load_context_bundle.return_value = type("Bundle", (), {"persona_text": "persona", "playbook_text": "playbook"})()
        mock_preview_send.return_value = {"status": "ok", "coordinate_source": "runtime_cli", "plan": {"typing_chunk_count": 2}}
        mock_default_raw_xml_path.return_value = "/tmp/latest.xml"
        mock_read_recent_chat.return_value = [{"text": "hello"}, {"text": "world"}]

        result = get_acceptance_status(community_id="openchat_001")

        self.assertEqual(result["ready_count"], 1)
        item = result["items"][0]
        self.assertTrue(item["ready"])
        self.assertEqual(item["stage"], "ready_for_hil")
        self.assertEqual(item["chat_probe"]["message_count"], 2)

    @patch("app.workflows.acceptance_status.read_recent_chat")
    @patch("app.workflows.acceptance_status.preview_send")
    @patch("app.workflows.acceptance_status.validate_openchat_session")
    @patch("app.workflows.acceptance_status.load_context_bundle")
    @patch("app.workflows.acceptance_status.get_device_status")
    @patch("app.workflows.acceptance_status.load_customer_config")
    @patch("app.workflows.acceptance_status.load_all_communities")
    def test_acceptance_reports_line_missing(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_get_device_status,
        mock_load_context_bundle,
        mock_validate_openchat_session,
        mock_preview_send,
        mock_read_recent_chat,
    ) -> None:
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "測試群",
                    "device_id": "emulator-5554",
                },
            )()
        ]
        mock_load_customer_config.return_value = type("Customer", (), {"display_name": "客戶 A"})()
        mock_get_device_status.return_value = {
            "boot_completed": True,
            "line_installed": False,
            "line_active": False,
        }
        mock_validate_openchat_session.return_value = {
            "items": [
                {
                    "community_id": "openchat_001",
                    "status": "blocked",
                    "message": "LINE 不在前景，尚未驗證到目標 OpenChat。",
                }
            ]
        }
        mock_load_context_bundle.return_value = type("Bundle", (), {"persona_text": "persona", "playbook_text": "playbook"})()
        mock_preview_send.return_value = {"status": "blocked", "reason": "missing_send_coordinates", "coordinate_source": "missing"}
        mock_read_recent_chat.side_effect = RuntimeError("Current emulator screen is not LINE.")

        result = get_acceptance_status(community_id="openchat_001")

        item = result["items"][0]
        self.assertFalse(item["ready"])
        self.assertEqual(item["stage"], "line_missing")
        self.assertTrue(any("LINE" in text for text in item["next_actions"]))

    @patch("app.workflows.acceptance_status.read_recent_chat")
    @patch("app.workflows.acceptance_status.preview_send")
    @patch("app.workflows.acceptance_status.validate_openchat_session")
    @patch("app.workflows.acceptance_status.load_context_bundle")
    @patch("app.workflows.acceptance_status.get_device_status")
    @patch("app.workflows.acceptance_status.load_customer_config")
    @patch("app.workflows.acceptance_status.load_all_communities")
    def test_acceptance_reports_line_not_openchat(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_get_device_status,
        mock_load_context_bundle,
        mock_validate_openchat_session,
        mock_preview_send,
        mock_read_recent_chat,
    ) -> None:
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "測試群",
                    "device_id": "emulator-5554",
                },
            )()
        ]
        mock_load_customer_config.return_value = type("Customer", (), {"display_name": "客戶 A"})()
        mock_get_device_status.return_value = {
            "boot_completed": True,
            "line_installed": True,
            "line_active": True,
        }
        mock_validate_openchat_session.return_value = {
            "items": [
                {
                    "community_id": "openchat_001",
                    "status": "blocked",
                    "message": "LINE 已在前景，但目前畫面看起來不是目標 OpenChat。",
                }
            ]
        }
        mock_load_context_bundle.return_value = type("Bundle", (), {"persona_text": "persona", "playbook_text": "playbook"})()
        mock_preview_send.return_value = {"status": "blocked", "reason": "missing_send_coordinates", "coordinate_source": "missing"}
        mock_read_recent_chat.side_effect = RuntimeError("Current emulator screen is not LINE.")

        result = get_acceptance_status(community_id="openchat_001")

        item = result["items"][0]
        self.assertFalse(item["ready"])
        self.assertEqual(item["stage"], "line_not_openchat")
        self.assertIn("OpenChat", item["checklist"][2]["message"])

        sub_checklist = item["sub_checklist"]
        self.assertEqual([step["key"] for step in sub_checklist], [
            "open_line",
            "open_openchat_tab",
            "enter_target_room",
            "rerun_validation",
        ])
        self.assertTrue(any("測試群" in step["hint"] for step in sub_checklist))
        self.assertTrue(all(step["done"] is False for step in sub_checklist))

    @patch("app.workflows.acceptance_status.read_recent_chat")
    @patch("app.workflows.acceptance_status.preview_send")
    @patch("app.workflows.acceptance_status.validate_openchat_session")
    @patch("app.workflows.acceptance_status.load_context_bundle")
    @patch("app.workflows.acceptance_status.get_device_status")
    @patch("app.workflows.acceptance_status.load_customer_config")
    @patch("app.workflows.acceptance_status.load_all_communities")
    def test_sub_checklist_empty_when_not_blocked_at_openchat(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_get_device_status,
        mock_load_context_bundle,
        mock_validate_openchat_session,
        mock_preview_send,
        mock_read_recent_chat,
    ) -> None:
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "測試群",
                    "device_id": "emulator-5554",
                },
            )()
        ]
        mock_load_customer_config.return_value = type("Customer", (), {"display_name": "客戶 A"})()
        mock_get_device_status.return_value = {
            "boot_completed": True,
            "line_installed": False,
            "line_active": False,
        }
        mock_validate_openchat_session.return_value = {"items": []}
        mock_load_context_bundle.return_value = type("Bundle", (), {"persona_text": "persona", "playbook_text": "playbook"})()
        mock_preview_send.return_value = {"status": "blocked", "reason": "missing_send_coordinates"}
        mock_read_recent_chat.side_effect = RuntimeError("not LINE")

        result = get_acceptance_status(community_id="openchat_001")
        item = result["items"][0]
        self.assertEqual(item["stage"], "line_missing")
        self.assertEqual(item["sub_checklist"], [])


if __name__ == "__main__":
    unittest.main()
