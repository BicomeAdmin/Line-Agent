from __future__ import annotations

import re
import time

from app.adb.client import AdbClient, AdbError
from app.core.risk_control import RiskControl, default_risk_control


def tap_type_send(
    client: AdbClient,
    text: str,
    input_x: int | None,
    input_y: int | None,
    send_x: int | None,
    send_y: int | None,
    risk_control: RiskControl = default_risk_control,
    dry_run: bool = False,
) -> dict[str, object]:
    if not risk_control.is_activity_time():
        return {"status": "blocked", "reason": "outside_activity_window"}

    # If the caller passed None for any coordinate (e.g. dynamically-onboarded
    # communities have no static calibration), resolve from live UI before the
    # plan is built. This makes static calibration optional — `add_community`
    # no longer needs a follow-up calibration step.
    if None in (input_x, input_y) or None in (send_x, send_y):
        resolved = _resolve_input_and_send(client)
        if input_x is None or input_y is None:
            if resolved.get("input"):
                input_x, input_y = resolved["input"]
        if send_x is None or send_y is None:
            if resolved.get("send"):
                send_x, send_y = resolved["send"]
        if None in (input_x, input_y, send_x, send_y):
            return {
                "status": "blocked",
                "reason": "could_not_resolve_send_or_input_button",
                "resolved": resolved,
            }

    plan = build_send_plan(text, input_x=input_x, input_y=input_y, send_x=send_x, send_y=send_y)
    if dry_run:
        return {"status": "dry_run", "plan": plan}

    delay = risk_control.wait_before_send()
    client.shell("input", "tap", str(input_x), str(input_y))
    time.sleep(1.0)

    from app.adb.text_input import send_text  # local import: avoid cycle at module load

    for chunk in plan["typing_chunks"]:
        send_result = send_text(client, chunk)
        if send_result.get("status") not in {"ok", "noop"}:
            return {
                "status": "blocked",
                "reason": "text_input_failed",
                "send_result": send_result,
            }
        time.sleep(0.35)

    time.sleep(2.0)

    # Calibrated send coords are recorded with native IME on; ADBKeyboard /
    # sticker panel can push the input row up by 100+ px. Always re-resolve
    # the send button's actual position from the live UI before tapping.
    actual = _resolve_send_button(client)
    if actual is not None:
        actual_x, actual_y = actual
        client.shell("input", "tap", str(actual_x), str(actual_y))
        return {
            "status": "sent",
            "delay_seconds": delay,
            "plan": plan,
            "send_tap_actual": {"x": actual_x, "y": actual_y},
            "send_tap_calibrated": {"x": send_x, "y": send_y},
        }
    # Fallback to calibrated coords if dynamic lookup failed (better than nothing).
    client.shell("input", "tap", str(send_x), str(send_y))
    return {
        "status": "sent",
        "delay_seconds": delay,
        "plan": plan,
        "send_tap_actual": None,
        "send_tap_calibrated": {"x": send_x, "y": send_y},
        "warning": "dynamic_send_button_lookup_failed",
    }


def check_input_box_cleared(client: AdbClient) -> dict[str, object]:
    """Dump live UI and read the chat_ui_message_edit `text=` attribute.

    Used as a post-send verification: after `send_attempt` reports `sent`, the
    LINE input box should be empty. Residual text means LINE swallowed our send
    silently and the operator may double-send if they retry.

    Returns:
        {"status": "cleared"} when the input is empty.
        {"status": "not_cleared", "residual_text": "<full>", "preview": "<<=40>"}
            when text remains.
        {"status": "unknown", "reason": "..."} when we couldn't determine state
            (dump failed, node missing). Do NOT treat unknown as a failure —
            absence of evidence is not evidence of failure.
    """

    try:
        client.shell("uiautomator", "dump", "/sdcard/echo_inputcheck_dump.xml")
        result = client.shell("cat", "/sdcard/echo_inputcheck_dump.xml", check=False)
    except AdbError as exc:
        return {"status": "unknown", "reason": f"adb_error:{exc}"}
    xml = (result.stdout or "") if hasattr(result, "stdout") else ""
    if not xml:
        return {"status": "unknown", "reason": "empty_dump"}

    match = re.search(
        r'<node[^>]*resource-id="jp\.naver\.line\.android:id/chat_ui_message_edit"[^>]*>',
        xml,
    )
    if not match:
        return {"status": "unknown", "reason": "input_node_not_found"}

    text_match = re.search(r'\stext="([^"]*)"', match.group(0))
    text = text_match.group(1) if text_match else ""
    if not text:
        return {"status": "cleared"}
    preview = text[:40] + ("…" if len(text) > 40 else "")
    return {
        "status": "not_cleared",
        "residual_text": text,
        "preview": preview,
        "residual_length": len(text),
    }


def _resolve_input_and_send(client: AdbClient) -> dict[str, tuple[int, int] | None]:
    """One UI dump that resolves both `chat_ui_message_edit` (input) and
    `chat_ui_send_button_image` (send). Used when caller passed None coords.
    """

    out: dict[str, tuple[int, int] | None] = {"input": None, "send": None}
    try:
        client.shell("uiautomator", "dump", "/sdcard/echo_inputresolve_dump.xml")
        result = client.shell("cat", "/sdcard/echo_inputresolve_dump.xml", check=False)
    except AdbError:
        return out
    xml = (result.stdout or "") if hasattr(result, "stdout") else ""
    if not xml:
        return out

    bounds_re = re.compile(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]')

    def _center_for_node(node_xml: str) -> tuple[int, int] | None:
        bounds_match = re.search(r'bounds="([^"]+)"', node_xml)
        if not bounds_match:
            return None
        bm = bounds_re.match(bounds_match.group(1))
        if not bm:
            return None
        x0, y0, x1, y1 = map(int, bm.groups())
        return (x0 + x1) // 2, (y0 + y1) // 2

    for node in re.finditer(r'<node[^>]*resource-id="jp\.naver\.line\.android:id/chat_ui_message_edit"[^>]*>', xml):
        out["input"] = _center_for_node(node.group(0))
        break
    for node in re.finditer(r'<node[^>]*resource-id="jp\.naver\.line\.android:id/chat_ui_send_button_image"[^>]*>', xml):
        out["send"] = _center_for_node(node.group(0))
        break
    return out


def _resolve_send_button(client: AdbClient) -> tuple[int, int] | None:
    """Dump live UI and return the chat_ui_send_button_image center coords.

    Returns None when uiautomator dump fails or the button isn't on screen.
    Resilient to LINE renaming the resource-id; falls back to content-desc
    contains "傳送" / "Send".
    """

    try:
        client.shell("uiautomator", "dump", "/sdcard/echo_input_dump.xml")
        result = client.shell("cat", "/sdcard/echo_input_dump.xml", check=False)
    except AdbError:
        return None
    xml = (result.stdout or "") if hasattr(result, "stdout") else ""
    if not xml:
        return None

    bounds_re = re.compile(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]')

    def _center_for_node(node_xml: str) -> tuple[int, int] | None:
        bounds_match = re.search(r'bounds="([^"]+)"', node_xml)
        if not bounds_match:
            return None
        bm = bounds_re.match(bounds_match.group(1))
        if not bm:
            return None
        x0, y0, x1, y1 = map(int, bm.groups())
        return (x0 + x1) // 2, (y0 + y1) // 2

    # 1) Prefer the LINE-specific resource-id.
    for node in re.finditer(r'<node[^>]*resource-id="jp\.naver\.line\.android:id/chat_ui_send_button_image"[^>]*>', xml):
        center = _center_for_node(node.group(0))
        if center is not None:
            return center

    # 2) Fall back to clickable nodes whose content-desc indicates send/傳送.
    for node in re.finditer(r'<node[^>]*>', xml):
        s = node.group(0)
        if 'clickable="true"' not in s:
            continue
        desc_match = re.search(r'content-desc="([^"]*)"', s)
        if not desc_match:
            continue
        desc = desc_match.group(1)
        if any(token in desc for token in ("傳送", "送出", "Send")):
            center = _center_for_node(s)
            if center is not None:
                return center
    return None


def build_send_plan(text: str, input_x: int, input_y: int, send_x: int, send_y: int) -> dict[str, object]:
    chunks = _split_text(text)
    return {
        "input_tap": {"x": input_x, "y": input_y},
        "send_tap": {"x": send_x, "y": send_y},
        "typing_chunks": chunks,
        "typing_chunk_count": len(chunks),
        "workflow": [
            "tap_input",
            "wait_1s",
            "type_chunks",
            "wait_2s",
            "tap_send",
        ],
    }


def _split_text(text: str, chunk_size: int = 12) -> list[str]:
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)] or [""]


def _escape_adb_text(text: str) -> str:
    return text.replace(" ", "%s").replace("&", "\\&")
