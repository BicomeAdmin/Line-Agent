import unittest
from unittest.mock import patch

from app.workflows.project_snapshot import get_project_snapshot


class ProjectSnapshotTests(unittest.TestCase):
    @patch("app.workflows.project_snapshot.get_onboarding_timeline")
    @patch("app.workflows.project_snapshot.validate_openchat_session")
    @patch("app.workflows.project_snapshot.get_acceptance_status")
    @patch("app.workflows.project_snapshot.get_community_status")
    @patch("app.workflows.project_snapshot.get_line_apk_status")
    @patch("app.workflows.project_snapshot.get_readiness_status")
    def test_builds_summary_and_spotlight(
        self,
        mock_readiness,
        mock_line_apk,
        mock_community_status,
        mock_acceptance,
        mock_openchat,
        mock_onboarding,
    ) -> None:
        mock_readiness.return_value = {
            "summary": {"ready": False, "blocker_count": 3, "warning_count": 2},
            "next_actions": ["A", "B"],
        }
        mock_line_apk.return_value = {
            "devices_needing_line": 1,
            "apk_inspection": {"available": False},
        }
        mock_community_status.return_value = {
            "items": [
                {
                    "community_id": "openchat_001",
                    "coordinates_ready": False,
                    "last_openchat_validation_at": "2026-04-27T12:00:00+00:00",
                }
            ]
        }
        mock_acceptance.return_value = {
            "ready_count": 0,
            "items": [
                {
                    "customer_id": "customer_a",
                    "community_id": "openchat_001",
                    "community_name": "測試群",
                    "stage": "line_missing",
                }
            ],
        }
        mock_openchat.return_value = {
            "ready_count": 0,
            "items": [{"status": "blocked", "reason": "line_not_foreground"}],
        }
        mock_onboarding.return_value = {"items": [{"latest_stage": "openchat_validation_blocked"}]}

        result = get_project_snapshot(community_id="openchat_001")

        self.assertEqual(result["summary"]["devices_needing_line"], 1)
        self.assertEqual(result["summary"]["active_phase"], "apk_blocked")
        self.assertEqual(result["spotlight"]["acceptance_stage"], "line_missing")
        self.assertEqual(result["spotlight"]["openchat_reason"], "line_not_foreground")
        self.assertEqual(result["next_actions"], ["A", "B"])
        self.assertEqual(result["action_queue"]["queue_count"], 5)


if __name__ == "__main__":
    unittest.main()
