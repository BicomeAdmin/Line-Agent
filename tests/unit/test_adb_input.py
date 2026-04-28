import unittest
from unittest.mock import MagicMock

from app.adb.input import build_send_plan, tap_type_send


class AdbInputTests(unittest.TestCase):
    def test_build_send_plan_contains_human_like_steps(self) -> None:
        plan = build_send_plan("這是一段測試訊息給 OpenChat", input_x=100, input_y=200, send_x=300, send_y=400)
        self.assertEqual(plan["input_tap"], {"x": 100, "y": 200})
        self.assertEqual(plan["send_tap"], {"x": 300, "y": 400})
        self.assertEqual(plan["workflow"], ["tap_input", "wait_1s", "type_chunks", "wait_2s", "tap_send"])
        self.assertGreaterEqual(plan["typing_chunk_count"], 1)

    def test_tap_type_send_dry_run_returns_plan(self) -> None:
        # Use an in-window mock so the test doesn't flake outside 09:00-23:00.
        risk_control = MagicMock()
        risk_control.is_activity_time.return_value = True
        result = tap_type_send(
            client=None,  # type: ignore[arg-type]
            text="dry run message",
            input_x=10,
            input_y=20,
            send_x=30,
            send_y=40,
            dry_run=True,
            risk_control=risk_control,
        )
        self.assertEqual(result["status"], "dry_run")
        plan = result["plan"]
        self.assertEqual(plan["send_tap"], {"x": 30, "y": 40})


if __name__ == "__main__":
    unittest.main()
