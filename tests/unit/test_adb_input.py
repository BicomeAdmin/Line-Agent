import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.adb.input import build_send_plan, check_input_box_cleared, tap_type_send


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


class CheckInputBoxClearedTests(unittest.TestCase):
    def _client_with_xml(self, xml: str) -> MagicMock:
        client = MagicMock()
        # First shell call is the dump (no return value used); second is `cat`.
        client.shell.side_effect = [
            MagicMock(stdout=""),
            SimpleNamespace(stdout=xml),
        ]
        return client

    def test_input_empty_returns_cleared(self) -> None:
        xml = (
            '<hierarchy><node resource-id="jp.naver.line.android:id/chat_ui_message_edit" '
            'text="" bounds="[0,0][100,50]" /></hierarchy>'
        )
        result = check_input_box_cleared(self._client_with_xml(xml))
        self.assertEqual(result["status"], "cleared")

    def test_input_with_residual_text_emits_preview(self) -> None:
        residual = "這是還沒送出去的草稿"
        xml = (
            f'<hierarchy><node resource-id="jp.naver.line.android:id/chat_ui_message_edit" '
            f'text="{residual}" bounds="[0,0][100,50]" /></hierarchy>'
        )
        result = check_input_box_cleared(self._client_with_xml(xml))
        self.assertEqual(result["status"], "not_cleared")
        self.assertEqual(result["residual_text"], residual)
        self.assertEqual(result["residual_length"], len(residual))
        self.assertIn(residual[:10], result["preview"])

    def test_input_node_missing_returns_unknown(self) -> None:
        xml = '<hierarchy><node resource-id="other" text="foo" /></hierarchy>'
        result = check_input_box_cleared(self._client_with_xml(xml))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "input_node_not_found")


if __name__ == "__main__":
    unittest.main()
