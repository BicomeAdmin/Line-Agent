"""Last-mile chat-room verification — call after navigate, before
any read/send action.

Why a separate verification module instead of trusting the navigate
result: navigate succeeds at one moment in time. Between navigate and
the next ADB action (read_recent_chat, type, send), LINE can
foreground a different room — a notification taps, a deep-link from
another app, OS lifecycle, anything. A scheduled draft for community
A landing in community B would be a brand-destroying incident, so we
verify the room is still A right before reading or sending.

Read-only: dumps UI XML and inspects the `header_title` element. No
clicks, no navigation. Cheap.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from app.adb.client import AdbClient
from app.adb.uiautomator import dump_ui_xml


@dataclass(frozen=True)
class TitleVerification:
    ok: bool
    expected: str
    current_title: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "expected": self.expected,
            "current_title": self.current_title,
            "reason": self.reason,
        }


def verify_chat_title(
    client: AdbClient,
    output_path: str | Path,
    expected_name: str,
) -> TitleVerification:
    """Check that the foreground LINE chat header matches `expected_name`.

    Match logic:
    - Strip trailing parenthesised suffixes (member counts: "X(123)").
    - Case-insensitive contains check both directions (handles minor
      whitespace differences).
    - Empty / missing header_title → ok=False with reason='no_title_node'.
    """

    expected_clean = _normalize(expected_name)
    if not expected_clean:
        return TitleVerification(
            ok=False, expected=expected_name, current_title="",
            reason="empty_expected_name",
        )

    try:
        xml_path = dump_ui_xml(client, output_path)
        xml_text = xml_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return TitleVerification(
            ok=False, expected=expected_name, current_title="",
            reason=f"xml_dump_failed:{str(exc)[:120]}",
        )

    title = _extract_header_title(xml_text)
    if not title:
        return TitleVerification(
            ok=False, expected=expected_name, current_title="",
            reason="no_title_node",
        )

    title_clean = _normalize(title)
    if expected_clean in title_clean or title_clean in expected_clean:
        return TitleVerification(
            ok=True, expected=expected_name, current_title=title, reason="match",
        )
    return TitleVerification(
        ok=False, expected=expected_name, current_title=title,
        reason="title_mismatch",
    )


_MEMBER_COUNT_SUFFIX_RE = re.compile(r"\s*[(（][^)）]*[)）]\s*$")


def _normalize(text: str) -> str:
    if not text:
        return ""
    stripped = _MEMBER_COUNT_SUFFIX_RE.sub("", text).strip()
    return stripped.lower()


def _extract_header_title(xml_text: str) -> str:
    """Find the LINE chat-header text.

    Prefer `chat_ui_header_title` if present; fall back to the more
    generic `header_title`. Returns empty string if neither found or
    XML is malformed.
    """

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ""
    candidates: list[tuple[int, str]] = []  # (priority, text), lower = better
    for node in root.iter("node"):
        rid = (node.attrib.get("resource-id") or "").rsplit("/", 1)[-1]
        text = (node.attrib.get("text") or "").strip()
        if not text:
            continue
        if rid == "chat_ui_header_title":
            candidates.append((0, text))
        elif rid == "header_title":
            candidates.append((1, text))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]
