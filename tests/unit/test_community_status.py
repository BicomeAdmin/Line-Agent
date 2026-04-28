import unittest
from unittest.mock import patch

from app.core.reviews import ReviewRecord
from app.workflows.community_status import get_community_status


class CommunityStatusTests(unittest.TestCase):
    @patch("app.workflows.community_status.read_recent_audit_events")
    @patch("app.workflows.community_status.review_store")
    @patch("app.workflows.community_status.load_context_bundle")
    @patch("app.workflows.community_status.get_device_config")
    @patch("app.workflows.community_status.load_customer_config")
    @patch("app.workflows.community_status.load_all_communities")
    def test_get_community_status_builds_operational_summary(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_get_device_config,
        mock_load_context_bundle,
        mock_review_store,
        mock_read_recent_audit_events,
    ) -> None:
        mock_load_all_communities.return_value = [
            type(
                "Community",
                (),
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "display_name": "投資群",
                    "device_id": "emulator-5554",
                    "enabled": True,
                    "patrol_interval_minutes": 120,
                    "input_x": 1,
                    "input_y": 2,
                    "send_x": 3,
                    "send_y": 4,
                    "coordinate_source": "runtime_cli",
                },
            )()
        ]
        mock_load_customer_config.return_value = type("Customer", (), {"display_name": "客戶 A"})()
        mock_get_device_config.return_value = type("Device", (), {"label": "line-account-01"})()
        mock_load_context_bundle.return_value = type(
            "Bundle",
            (),
            {"persona_name": "default", "persona_text": "persona", "playbook_text": "playbook"},
        )()
        mock_review_store.list_pending.return_value = [
            ReviewRecord(
                review_id="review-1",
                source_job_id="job-1",
                customer_id="customer_a",
                customer_name="客戶 A",
                community_id="openchat_001",
                community_name="投資群",
                device_id="emulator-5554",
                draft_text="draft",
                status="pending",
            )
        ]
        mock_read_recent_audit_events.return_value = [
            {"timestamp": "2026-04-27T00:00:01+00:00", "event_type": "scheduled_patrol_processed", "payload": {"community_id": "openchat_001", "status": "skipped"}},
            {"timestamp": "2026-04-27T00:00:02+00:00", "event_type": "send_attempt", "payload": {"community_id": "openchat_001", "status": "sent"}},
        ]

        result = get_community_status(community_id="openchat_001")

        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual(item["community_id"], "openchat_001")
        self.assertTrue(item["coordinates_ready"])
        self.assertEqual(item["coordinate_source"], "runtime_cli")
        self.assertEqual(item["last_send_status"], "sent")
        self.assertIsNone(item["last_openchat_validation_status"])
        self.assertEqual(item["open_review_count"], 1)


if __name__ == "__main__":
    unittest.main()
