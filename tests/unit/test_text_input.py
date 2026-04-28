import unittest
from unittest.mock import MagicMock, patch

from app.adb.client import AdbError
from app.adb.text_input import (
    ADBKEYBOARD_IME_ID,
    BROADCAST_ACTION,
    TextInputError,
    is_adbkeyboard_active,
    is_adbkeyboard_installed,
    send_text,
)


def _shell_response(stdout: str = "", returncode: int = 0) -> object:
    return type("R", (), {"stdout": stdout, "stderr": "", "returncode": returncode})()


class TextInputTests(unittest.TestCase):
    def test_ascii_uses_input_text(self) -> None:
        client = MagicMock()
        client.shell.return_value = _shell_response()
        result = send_text(client, "hello world")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "input_text")
        client.shell.assert_called_once_with("input", "text", "hello%sworld")

    def test_non_ascii_uses_broadcast_when_ime_active(self) -> None:
        client = MagicMock()
        # Sequence: pm list (installed) → settings get (active) → broadcast
        client.shell.side_effect = [
            _shell_response("package:com.android.adbkeyboard\n"),
            _shell_response(ADBKEYBOARD_IME_ID + "\n"),
            _shell_response("Broadcasting: ...\nBroadcast completed: result=0\n"),
        ]
        result = send_text(client, "中文測試")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "broadcast")
        # The third call should be the broadcast.
        third_call_args = client.shell.call_args_list[2].args
        self.assertEqual(len(third_call_args), 1)
        self.assertIn(BROADCAST_ACTION, third_call_args[0])
        self.assertIn("中文測試", third_call_args[0])

    def test_non_ascii_raises_when_ime_missing(self) -> None:
        client = MagicMock()
        client.shell.return_value = _shell_response("")
        with self.assertRaises(TextInputError):
            send_text(client, "中文")

    def test_non_ascii_raises_when_ime_installed_but_not_active(self) -> None:
        client = MagicMock()
        client.shell.side_effect = [
            _shell_response("package:com.android.adbkeyboard\n"),
            _shell_response("com.google.android.inputmethod.latin/.LatinIME\n"),
        ]
        with self.assertRaises(TextInputError):
            send_text(client, "中文")

    def test_empty_text_is_noop(self) -> None:
        client = MagicMock()
        result = send_text(client, "")
        self.assertEqual(result["status"], "noop")
        client.shell.assert_not_called()

    def test_input_text_error_is_returned(self) -> None:
        client = MagicMock()
        client.shell.side_effect = AdbError("device offline")
        result = send_text(client, "hi")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["method"], "input_text")

    def test_is_adbkeyboard_helpers(self) -> None:
        client = MagicMock()
        client.shell.return_value = _shell_response("package:com.android.adbkeyboard\n")
        self.assertTrue(is_adbkeyboard_installed(client))

        client.shell.return_value = _shell_response(ADBKEYBOARD_IME_ID + "\n")
        self.assertTrue(is_adbkeyboard_active(client))

        client.shell.return_value = _shell_response("com.something.else/.IME\n")
        self.assertFalse(is_adbkeyboard_active(client))


if __name__ == "__main__":
    unittest.main()
