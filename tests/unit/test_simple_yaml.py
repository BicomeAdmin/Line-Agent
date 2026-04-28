import unittest

from app.core.simple_yaml import load_yaml


class SimpleYamlTests(unittest.TestCase):
    def test_load_mapping_and_list(self) -> None:
        payload = load_yaml(
            """
            devices:
              - device_id: emulator-5554
                enabled: true
            """
        )
        self.assertEqual(
            payload,
            {"devices": [{"device_id": "emulator-5554", "enabled": True}]},
        )

    def test_load_nested_mapping(self) -> None:
        payload = load_yaml(
            """
            activity_window:
              start: "09:00"
              end: "23:00"
            """
        )
        self.assertEqual(payload, {"activity_window": {"start": "09:00", "end": "23:00"}})


if __name__ == "__main__":
    unittest.main()
