"""Tests for the chat-list back-out helpers added to fix navigate
failures when LINE is left inside a prior ChatHistoryActivity."""

import unittest
from unittest.mock import MagicMock

from app.adb import line_app


# Real `dumpsys window windows` excerpts captured from the emulator.
DUMPSYS_INSIDE_CHAT = """
  mFocusedApp=ActivityRecord{abc u0 jp.naver.line.android/.activity.chathistory.ChatHistoryActivity t28}
  mLastFocusedRootTask=Task{def #28 type=standard A=10207:jp.naver.line.android}
  mFocusedWindow=Window{xyz u0 jp.naver.line.android/jp.naver.line.android.activity.chathistory.ChatHistoryActivity}
"""

DUMPSYS_AT_CHAT_LIST = """
  mFocusedApp=ActivityRecord{abc u0 jp.naver.line.android/jp.naver.line.android.activity.MainActivity t28}
  mFocusedWindow=Window{xyz u0 jp.naver.line.android/jp.naver.line.android.activity.MainActivity}
"""

DUMPSYS_NO_FOCUS = """
  mFocusedApp=null
"""


def _client_with_dump(dump_text: str) -> MagicMock:
    """Build a fake AdbClient whose .shell('dumpsys',...) returns
    dump_text. Other shell calls (like input keyevent) get tracked."""

    client = MagicMock()
    shell_calls: list[tuple] = []

    def fake_shell(*args, **kwargs):
        shell_calls.append(args)
        if args[:2] == ("dumpsys", "window"):
            result = MagicMock()
            result.stdout = dump_text
            result.stderr = ""
            return result
        return MagicMock(stdout="", stderr="")

    client.shell = MagicMock(side_effect=fake_shell)
    client._shell_calls = shell_calls
    return client


class CurrentActivityTests(unittest.TestCase):
    def test_detects_chat_history_activity(self):
        client = _client_with_dump(DUMPSYS_INSIDE_CHAT)
        activity = line_app.current_activity(client)
        self.assertIsNotNone(activity)
        self.assertIn("ChatHistoryActivity", activity)

    def test_detects_main_activity(self):
        client = _client_with_dump(DUMPSYS_AT_CHAT_LIST)
        activity = line_app.current_activity(client)
        self.assertIsNotNone(activity)
        self.assertIn("MainActivity", activity)

    def test_returns_none_when_no_focus(self):
        client = _client_with_dump(DUMPSYS_NO_FOCUS)
        self.assertIsNone(line_app.current_activity(client))


class IsInsideChatHistoryTests(unittest.TestCase):
    def test_true_when_chat_history_focused(self):
        client = _client_with_dump(DUMPSYS_INSIDE_CHAT)
        self.assertTrue(line_app.is_inside_chat_history(client))

    def test_false_when_at_chat_list(self):
        client = _client_with_dump(DUMPSYS_AT_CHAT_LIST)
        self.assertFalse(line_app.is_inside_chat_history(client))


class BackToChatListTests(unittest.TestCase):
    def test_no_op_when_already_outside(self):
        client = _client_with_dump(DUMPSYS_AT_CHAT_LIST)
        result = line_app.back_to_chat_list(client, max_attempts=3, settle_seconds=0)
        self.assertTrue(result["success"])
        self.assertEqual(result["attempts"], 0)
        # No BACK keyevent should have been issued.
        keyevents = [c for c in client._shell_calls if c[:2] == ("input", "keyevent")]
        self.assertEqual(keyevents, [])

    def test_presses_back_until_outside(self):
        # Simulate: first 2 dumps say inside chat, then at chat list.
        # back_to_chat_list calls dumpsys multiple times: per loop check
        # (3) + final is_inside_chat_history (1) + final current_activity
        # (1) = 5. Make the iterator forgiving.
        client = MagicMock()
        responses = iter([
            DUMPSYS_INSIDE_CHAT,
            DUMPSYS_INSIDE_CHAT,
            DUMPSYS_AT_CHAT_LIST,
            DUMPSYS_AT_CHAT_LIST,
            DUMPSYS_AT_CHAT_LIST,
            DUMPSYS_AT_CHAT_LIST,  # spare
        ])
        shell_calls: list[tuple] = []

        def fake_shell(*args, **kwargs):
            shell_calls.append(args)
            if args[:2] == ("dumpsys", "window"):
                r = MagicMock()
                r.stdout = next(responses)
                r.stderr = ""
                return r
            return MagicMock(stdout="", stderr="")

        client.shell = MagicMock(side_effect=fake_shell)
        result = line_app.back_to_chat_list(client, max_attempts=4, settle_seconds=0)
        self.assertTrue(result["success"])
        self.assertEqual(result["attempts"], 2)
        keyevents = [c for c in shell_calls if c[:2] == ("input", "keyevent")]
        self.assertEqual(len(keyevents), 2)

    def test_gives_up_after_max_attempts(self):
        client = _client_with_dump(DUMPSYS_INSIDE_CHAT)
        result = line_app.back_to_chat_list(client, max_attempts=2, settle_seconds=0)
        self.assertFalse(result["success"])
        self.assertEqual(result["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
