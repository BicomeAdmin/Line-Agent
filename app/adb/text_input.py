"""Unified text input over ADB.

Native `adb shell input text` only handles ASCII. For non-ASCII (Chinese, JP,
etc.) we delegate to ADBKeyboard's broadcast IME, which must be installed AND
selected as the current IME.

`send_text` is the single entry point everything else should use.
"""

from __future__ import annotations

import shlex

from app.adb.client import AdbClient, AdbError

ADBKEYBOARD_PACKAGE = "com.android.adbkeyboard"
ADBKEYBOARD_IME_ID = "com.android.adbkeyboard/.AdbIME"
BROADCAST_ACTION = "ADB_INPUT_TEXT"


class TextInputError(RuntimeError):
    pass


def _is_ascii(value: str) -> bool:
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True


def is_adbkeyboard_active(client: AdbClient) -> bool:
    try:
        result = client.shell("settings", "get", "secure", "default_input_method", check=False)
    except AdbError:
        return False
    return ADBKEYBOARD_IME_ID in (result.stdout or "")


def is_adbkeyboard_installed(client: AdbClient) -> bool:
    try:
        result = client.shell("pm", "list", "packages", ADBKEYBOARD_PACKAGE, check=False)
    except AdbError:
        return False
    return ADBKEYBOARD_PACKAGE in (result.stdout or "")


def send_text(client: AdbClient, text: str) -> dict[str, object]:
    """Type `text` into whatever EditText currently has focus.

    Returns a dict describing which mechanism was used so callers can audit /
    surface to the operator on failure.
    """

    if not text:
        return {"status": "noop", "reason": "empty_text"}

    if _is_ascii(text):
        # Android `input text` treats space as separator unless escaped; standard idiom
        # is to substitute %s.
        escaped = text.replace(" ", "%s")
        try:
            client.shell("input", "text", escaped)
        except AdbError as exc:
            return {"status": "error", "method": "input_text", "detail": str(exc)}
        return {"status": "ok", "method": "input_text", "char_count": len(text)}

    # Non-ASCII: must go through ADBKeyboard.
    if not is_adbkeyboard_installed(client):
        raise TextInputError(
            "Non-ASCII text requires ADBKeyboard, but com.android.adbkeyboard is not installed."
        )
    if not is_adbkeyboard_active(client):
        raise TextInputError(
            "Non-ASCII text requires ADBKeyboard to be the active IME. "
            "Run: `adb shell ime set com.android.adbkeyboard/.AdbIME`."
        )

    # Single-quote the message so device-side shell sees it as one token even
    # though it contains spaces or special chars. shlex.quote escapes any
    # embedded single-quotes safely.
    quoted = shlex.quote(text)
    cmd = f"am broadcast -a {BROADCAST_ACTION} --es msg {quoted}"
    try:
        result = client.shell(cmd)
    except AdbError as exc:
        return {"status": "error", "method": "broadcast", "detail": str(exc)}
    if "Broadcast completed: result=" not in (result.stdout or ""):
        return {"status": "error", "method": "broadcast", "detail": result.stdout}
    return {"status": "ok", "method": "broadcast", "char_count": len(text)}


def clear_text(client: AdbClient) -> dict[str, object]:
    """Clear the focused EditText via the ADBKeyboard CLEAR broadcast."""

    if not is_adbkeyboard_active(client):
        return {"status": "skipped", "reason": "adbkeyboard_not_active"}
    try:
        client.shell("am", "broadcast", "-a", "ADB_CLEAR_TEXT")
    except AdbError as exc:
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok"}
