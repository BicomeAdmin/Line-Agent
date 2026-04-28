import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.core.calibrations import CalibrationRecord, CalibrationStore
from app.storage.config_loader import load_community_config


class CalibrationStoreTests(unittest.TestCase):
    def test_upsert_and_load(self) -> None:
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "calibrations.jsonl"
            store = CalibrationStore(state_path=state_path, persist=True)
            store.upsert(
                CalibrationRecord(
                    customer_id="customer_a",
                    community_id="openchat_001",
                    input_x=111,
                    input_y=222,
                    send_x=333,
                    send_y=444,
                    note="first pass",
                )
            )

            reloaded = CalibrationStore(state_path=state_path, persist=True)
            saved = reloaded.get("customer_a", "openchat_001")
            self.assertIsNotNone(saved)
            assert saved is not None
            self.assertEqual(saved.input_x, 111)
            self.assertEqual(saved.note, "first pass")

    @patch("app.storage.config_loader.calibration_store")
    def test_load_community_config_prefers_runtime_calibration(self, mock_calibration_store) -> None:
        mock_calibration_store.get.return_value = CalibrationRecord(
            customer_id="customer_a",
            community_id="openchat_001",
            input_x=101,
            input_y=202,
            send_x=303,
            send_y=404,
            source="runtime_cli",
        )
        community = load_community_config("customer_a", "openchat_001")
        self.assertEqual(community.input_x, 101)
        self.assertEqual(community.send_y, 404)
        self.assertEqual(community.coordinate_source, "runtime_cli")


if __name__ == "__main__":
    unittest.main()
