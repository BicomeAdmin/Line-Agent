import unittest
from unittest.mock import patch

from app.workflows.onboarding_timeline import get_onboarding_timeline


class OnboardingTimelineTests(unittest.TestCase):
    @patch("app.workflows.onboarding_timeline.read_all_audit_events")
    @patch("app.workflows.onboarding_timeline.load_customer_config")
    @patch("app.workflows.onboarding_timeline.load_all_communities")
    def test_builds_timeline_and_milestones(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_read_all_audit_events,
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
        mock_read_all_audit_events.return_value = [
            {
                "timestamp": "2026-04-27T07:18:17+00:00",
                "event_type": "job_completed",
                "payload": {
                    "action": "acceptance_status",
                    "result": {
                        "items": [
                            {
                                "community_id": "openchat_001",
                                "stage": "device_not_ready",
                            }
                        ]
                    },
                },
            },
            {
                "timestamp": "2026-04-27T07:19:54+00:00",
                "event_type": "line_session_prepare_blocked",
                "payload": {
                    "community_id": "openchat_001",
                    "device_id": "emulator-5554",
                    "reason": "boot_not_completed",
                },
            },
            {
                "timestamp": "2026-04-27T07:25:00+00:00",
                "event_type": "community_calibration_saved",
                "payload": {
                    "community_id": "openchat_001",
                    "device_id": "emulator-5554",
                },
            },
            {
                "timestamp": "2026-04-27T07:26:00+00:00",
                "event_type": "openchat_validation_checked",
                "payload": {
                    "community_id": "openchat_001",
                    "device_id": "emulator-5554",
                    "status": "ok",
                    "matched_title": "測試群",
                },
            },
        ]

        result = get_onboarding_timeline(community_id="openchat_001")

        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual(item["timeline_count"], 4)
        self.assertTrue(item["milestones"]["acceptance_checked"])
        self.assertTrue(item["milestones"]["line_session_attempted"])
        self.assertTrue(item["milestones"]["openchat_verified"])
        self.assertTrue(item["milestones"]["calibration_saved"])
        self.assertEqual(item["latest_stage"], "openchat_verified")
        self.assertFalse(item["milestones"]["first_send_completed"])
        for entry in item["timeline"]:
            self.assertNotIn("extra_milestones", entry)

    @patch("app.workflows.onboarding_timeline.read_all_audit_events")
    @patch("app.workflows.onboarding_timeline.load_customer_config")
    @patch("app.workflows.onboarding_timeline.load_all_communities")
    def test_first_send_completed_milestone_when_send_attempt_ok(
        self,
        mock_load_all_communities,
        mock_load_customer_config,
        mock_read_all_audit_events,
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
        mock_read_all_audit_events.return_value = [
            {
                "timestamp": "2026-04-27T08:00:00+00:00",
                "event_type": "send_attempt",
                "payload": {
                    "community_id": "openchat_001",
                    "device_id": "emulator-5554",
                    "status": "blocked",
                },
            },
            {
                "timestamp": "2026-04-27T08:05:00+00:00",
                "event_type": "send_attempt",
                "payload": {
                    "community_id": "openchat_001",
                    "device_id": "emulator-5554",
                    "status": "ok",
                },
            },
        ]

        result = get_onboarding_timeline(community_id="openchat_001")
        item = result["items"][0]
        self.assertTrue(item["milestones"]["send_attempted"])
        self.assertTrue(item["milestones"]["first_send_completed"])
        self.assertEqual(item["latest_stage"], "send_attempt")
        for entry in item["timeline"]:
            self.assertNotIn("extra_milestones", entry)


if __name__ == "__main__":
    unittest.main()
