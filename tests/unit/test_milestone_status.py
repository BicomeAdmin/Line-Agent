import unittest
from unittest.mock import patch

from app.workflows.milestone_status import get_milestone_status


class MilestoneStatusTests(unittest.TestCase):
    @patch("app.workflows.milestone_status.get_project_snapshot")
    def test_reports_stage_one_as_active_when_apk_missing(self, mock_snapshot) -> None:
        mock_snapshot.return_value = {
            "summary": {"overall_ready": False},
            "spotlight": {
                "openchat_status": "blocked",
                "coordinates_ready": False,
                "acceptance_stage": "line_missing",
            },
            "sections": {
                "line_apk": {
                    "apk_inspection": {"available": False},
                    "devices_needing_line": 1,
                }
            },
        }

        result = get_milestone_status(community_id="openchat_001")

        self.assertEqual(result["current_milestone"]["milestone_id"], "stage_1_line_chain")
        self.assertTrue(result["milestones"][0]["active"])

    @patch("app.workflows.milestone_status.get_project_snapshot")
    def test_stage_one_completed_after_line_installed(self, mock_snapshot) -> None:
        # After sideload, devices_needing_line drops to 0 even if no .apk file
        # is present. Stage 1 must mark completed and stage 2 becomes active.
        mock_snapshot.return_value = {
            "summary": {"overall_ready": False},
            "spotlight": {
                "openchat_status": "blocked",
                "coordinates_ready": False,
                "acceptance_stage": "line_not_openchat",
            },
            "sections": {
                "line_apk": {
                    "apk_inspection": {"available": False},
                    "devices_needing_line": 0,
                }
            },
        }

        result = get_milestone_status(community_id="openchat_001")

        self.assertEqual(result["current_milestone"]["milestone_id"], "stage_2_openchat")
        self.assertTrue(result["milestones"][0]["completed"])
        self.assertFalse(result["milestones"][0]["active"])
        self.assertTrue(result["milestones"][1]["active"])


if __name__ == "__main__":
    unittest.main()
