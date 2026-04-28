import unittest
from unittest.mock import patch

from app.workflows.action_queue import get_action_queue


class ActionQueueTests(unittest.TestCase):
    @patch("app.workflows.project_snapshot.get_project_snapshot")
    def test_builds_prioritized_queue(self, mock_snapshot) -> None:
        mock_snapshot.return_value = {
            "summary": {"overall_ready": False},
            "spotlight": {
                "community_id": "openchat_001",
                "community_name": "測試群",
                "acceptance_stage": "line_missing",
                "openchat_status": "blocked",
                "coordinates_ready": False,
            },
            "sections": {
                "line_apk": {
                    "apk_inspection": {"available": False},
                    "devices_needing_line": 1,
                },
            },
        }

        result = get_action_queue(community_id="openchat_001")

        self.assertEqual(result["queue_count"], 5)
        self.assertEqual(result["items"][0]["item_id"], "apk_stage")
        self.assertEqual(result["items"][1]["item_id"], "install_line")

    @patch("app.workflows.project_snapshot.get_project_snapshot")
    def test_apk_stage_dropped_when_line_already_installed(self, mock_snapshot) -> None:
        # After LINE is sideloaded, devices_needing_line drops to 0 even if
        # ~/Downloads no longer has an .apk file. apk_stage must not show up.
        mock_snapshot.return_value = {
            "summary": {"overall_ready": False},
            "spotlight": {
                "community_id": "openchat_001",
                "community_name": "測試群",
                "acceptance_stage": "line_not_openchat",
                "openchat_status": "blocked",
                "coordinates_ready": False,
            },
            "sections": {
                "line_apk": {
                    "apk_inspection": {"available": False},
                    "devices_needing_line": 0,
                },
            },
        }

        result = get_action_queue(community_id="openchat_001")
        item_ids = [item["item_id"] for item in result["items"]]
        self.assertNotIn("apk_stage", item_ids)
        self.assertEqual(item_ids[0], "open_target_openchat")


if __name__ == "__main__":
    unittest.main()
