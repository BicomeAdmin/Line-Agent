from __future__ import annotations

from app.adb.client import AdbClient
from app.adb.devices import foreground_package


LINE_PACKAGE = "jp.naver.line.android"


def check_current_app(client: AdbClient) -> bool:
    package = foreground_package(client)
    if package is not None:
        return package == LINE_PACKAGE
    return LINE_PACKAGE in current_window_dump(client)


def current_window_dump(client: AdbClient) -> str:
    result = client.shell("dumpsys", "window", "windows", check=False)
    return f"{result.stdout}\n{result.stderr}"


def current_activity(client: AdbClient) -> str | None:
    """Return the focused activity component, e.g.
    'jp.naver.line.android/.activity.chathistory.ChatHistoryActivity'.

    Used to detect when LINE is sitting inside a specific chat
    (ChatHistoryActivity) — in that state, chat-list scans / searches
    operate on the wrong view and never find the target.

    Uses `dumpsys activity activities` (the same source foreground_package
    relies on); falls back to `dumpsys window` if that yields nothing.
    """

    import re

    activity_re = re.compile(
        r"(?:topResumedActivity|ResumedActivity|mFocusedApp)\s*[:=]\s*ActivityRecord\{[^}]*?(\S+/\S+)"
    )

    primary = client.shell("dumpsys", "activity", "activities", check=False)
    primary_text = f"{primary.stdout}\n{primary.stderr}"
    match = activity_re.search(primary_text)
    if match:
        return match.group(1)

    fallback_text = current_window_dump(client)
    match = activity_re.search(fallback_text)
    if match:
        return match.group(1)
    match = re.search(r"mCurrentFocus=Window\{[^}]*?(\S+/\S+)", fallback_text)
    if match:
        return match.group(1)
    return None


def is_inside_chat_history(client: AdbClient) -> bool:
    activity = current_activity(client)
    return activity is not None and "ChatHistoryActivity" in activity


def back_to_chat_list(client: AdbClient, max_attempts: int = 3, settle_seconds: float = 0.7) -> dict[str, object]:
    """Press BACK until we exit ChatHistoryActivity. Returns
    {success, attempts, final_activity}. No-op when already outside chat
    history. Critical before any chat-list-based navigation in
    openchat_navigate, otherwise we end up searching inside the wrong
    chat's message stream and silently fail to find the target.
    """

    import time

    attempts = 0
    while attempts < max_attempts and is_inside_chat_history(client):
        client.shell("input", "keyevent", "KEYCODE_BACK", check=False)
        time.sleep(settle_seconds)
        attempts += 1

    final = current_activity(client)
    return {
        "success": not is_inside_chat_history(client),
        "attempts": attempts,
        "final_activity": final,
    }


def open_line(client: AdbClient) -> None:
    # Prefer `am start` with MAIN/LAUNCHER intent so we don't depend on a fixed
    # activity class name — LINE renames its launcher component between versions.
    # Monkey returned non-zero on this emulator (no physical keys), so we avoid it.
    client.shell(
        "am", "start",
        "-a", "android.intent.action.MAIN",
        "-c", "android.intent.category.LAUNCHER",
        "-n", f"{LINE_PACKAGE}/.activity.SplashActivity",
        check=False,
    )
