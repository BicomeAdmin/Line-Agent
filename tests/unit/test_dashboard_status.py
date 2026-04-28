import unittest
from unittest.mock import patch

from app.core.jobs import JobRecord
from app.core.reviews import ReviewRecord
from app.workflows.dashboard_status import _is_operational_job, get_dashboard_status


class DashboardStatusTests(unittest.TestCase):
    def test_operational_job_filter(self) -> None:
        self.assertTrue(_is_operational_job("lark_command", {"command": {}}))
        self.assertFalse(_is_operational_job("demo", {}))
        self.assertFalse(_is_operational_job("scheduled_patrol", {"device_id": "emulator-5554"}))

    @patch("app.workflows.dashboard_status._collect_recent_audits")
    @patch("app.workflows.dashboard_status.review_store")
    @patch("app.workflows.dashboard_status.job_registry")
    @patch("app.workflows.dashboard_status.load_all_communities")
    @patch("app.workflows.dashboard_status.get_readiness_status")
    @patch("app.workflows.dashboard_status.get_system_status")
    def test_dashboard_groups_review_queue_and_history(
        self,
        mock_system_status,
        mock_get_readiness_status,
        mock_load_all_communities,
        mock_job_registry,
        mock_review_store,
        mock_collect_recent_audits,
    ) -> None:
        mock_system_status.return_value = {
            "status": "ok",
            "devices": [
                {"device_id": "emulator-5554", "enabled": True, "line_installed": True, "line_active": True},
                {"device_id": "emulator-5556", "enabled": True, "line_installed": True, "line_active": False},
            ],
        }
        mock_get_readiness_status.return_value = {
            "summary": {"ready": False, "blocker_count": 1, "warning_count": 1},
            "next_actions": ["安裝 LINE APK"],
        }
        mock_job_registry.list_jobs.return_value = [
            JobRecord(job_id="job-1", job_type="lark_command", payload={"command": {"action": "system_status"}}, status="completed")
        ]
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "投資群",
                    "device_id": "emulator-5554",
                    "patrol_interval_minutes": 120,
                    "enabled": True,
                    "input_x": None,
                    "input_y": None,
                    "send_x": None,
                    "send_y": None,
                    "coordinate_source": "missing",
                },
            )(),
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_002",
                    "display_name": "媽媽群",
                    "device_id": "emulator-5556",
                    "patrol_interval_minutes": 60,
                    "enabled": True,
                    "input_x": None,
                    "input_y": None,
                    "send_x": None,
                    "send_y": None,
                    "coordinate_source": "missing",
                },
            )(),
        ]
        mock_review_store.list_all.return_value = [
            ReviewRecord(
                review_id="review-1",
                source_job_id="job-1",
                customer_id="customer_a",
                customer_name="客戶 A",
                community_id="openchat_001",
                community_name="投資群",
                device_id="emulator-5554",
                draft_text="先暖場一下",
                status="pending_reapproval",
            ),
            ReviewRecord(
                review_id="review-2",
                source_job_id="job-2",
                customer_id="customer_a",
                customer_name="客戶 A",
                community_id="openchat_002",
                community_name="媽媽群",
                device_id="emulator-5556",
                draft_text="請人工修改",
                status="edit_required",
            ),
        ]
        mock_review_store.list_pending.return_value = mock_review_store.list_all.return_value
        mock_collect_recent_audits.return_value = [
            {"customer_id": "customer_a", "event_type": "send_attempt", "payload": {"community_id": "openchat_001", "status": "sent"}},
            {"customer_id": "customer_a", "event_type": "scheduled_patrol_processed", "payload": {"community_id": "openchat_001", "status": "line_inactive"}},
            {"customer_id": "customer_a", "event_type": "action_received", "payload": {"action": "edit"}},
        ]

        result = get_dashboard_status()

        self.assertEqual(result["operations"]["enabled_device_count"], 2)
        self.assertEqual(result["operations"]["line_ready_device_count"], 1)
        self.assertEqual(result["readiness"]["summary"]["blocker_count"], 1)
        self.assertEqual(result["readiness"]["next_actions"][0], "安裝 LINE APK")
        self.assertEqual(result["operations"]["reviews"]["open_count"], 2)
        self.assertEqual(result["review_queue"]["waiting_reapproval_count"], 1)
        self.assertEqual(result["review_queue"]["needs_edit_count"], 1)
        self.assertEqual(result["review_queue"]["items"][0]["status_label"], "待二次審核")
        self.assertEqual(result["operations"]["communities"]["total"], 2)
        self.assertEqual(result["operations"]["communities"]["calibrated_count"], 0)
        self.assertEqual(result["community_operations"][0]["last_patrol_status"], "line_inactive")
        self.assertEqual(result["community_operations"][0]["last_send_status"], "sent")
        self.assertEqual(len(result["history"]["send_attempts"]), 1)
        self.assertEqual(len(result["history"]["patrol_events"]), 1)
        self.assertEqual(len(result["history"]["action_events"]), 1)


if __name__ == "__main__":
    unittest.main()
