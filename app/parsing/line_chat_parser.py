"""Parse a uiautomator XML dump of a LINE OpenChat chat-history view.

Resource-id semantics (LINE 26.6.0, captured 2026-04-28 by inspecting
real dumps + comparing to live screenshots):

  chat_ui_message_text   → an actual message bubble's text. ANY sender,
                            not just the operator. Both incoming (left-
                            aligned) and outgoing (right-aligned) use
                            this id; you tell them apart by x-bounds
                            (right side of screen = operator's own
                            outgoing bubble, no name shown).
  chat_ui_row_sender     → the small per-message speaker label, drawn
                            below or beside the bubble. Carries the
                            display name for that bubble. May be absent
                            for consecutive bubbles by the same speaker
                            (LINE collapses repeated names) — in that
                            case we carry the previous sender forward.
                            Also absent for the operator's own bubbles
                            (right-aligned).
  chat_ui_sender_name    → the name shown INSIDE a quoted reply box
  + chat_ui_content_text   (LINE's "reply to" feature). These nodes are
                            the QUOTED previous message, not the
                            current one being said. They MUST be
                            ignored — earlier versions of this parser
                            mis-attributed them as separate messages
                            and as the operator's own messages, which
                            wrecked autonomous-flow self detection.
  chat_ui_announcement_fold_content_message → folded pinned post,
                                                skip from chat tail.
  chat_ui_message_edit, chat_ui_row_timestamp, header_*, etc. → not
                                                                content.

Right-alignment threshold:
  We treat a bubble whose x-left is past 40% of the screen width as
  "operator's own outgoing message". On a 1080-wide emulator that's
  x ≥ 432. Conservative — outgoing bubbles in LINE typically have
  x_left ≈ 540+, well past the threshold.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass


# Sentinel for the operator's own messages (identified structurally
# via right-alignment). The reply-target selector treats this as
# "self" without needing a per-community nickname match.
SELF_SENDER = "__operator__"


@dataclass(frozen=True)
class ChatMessage:
    sender: str
    text: str
    position: int
    source: str = "uiautomator"
    is_self: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def _short_rid(rid: str | None) -> str:
    if not rid:
        return ""
    return rid.rsplit("/", 1)[-1]


def _parse_bounds(raw: str | None) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    m = _BOUNDS_RE.match(raw.strip())
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def parse_line_chat(xml_text: str, limit: int = 10) -> list[ChatMessage]:
    """Structured parse with sender attribution. See module docstring
    for resource-id semantics.

    Pipeline: walk nodes in doc order, when we see chat_ui_message_text
    stash it as `pending`; when we see chat_ui_row_sender attribute
    pending to that name (unless x-bounds say it was operator's own,
    in which case attribute to SELF_SENDER and ignore the row_sender,
    which would be a stale label for someone else). Flush trailing
    pending at end.
    """

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Detect screen width from the root bounds for a sane right-half threshold.
    root_bounds = _parse_bounds(root.attrib.get("bounds")) if hasattr(root, "attrib") else None
    if root_bounds is not None:
        screen_width = root_bounds[2]
    else:
        screen_width = 1080
    self_threshold_x = int(screen_width * 0.40)  # x_left ≥ 40% → self

    messages: list[ChatMessage] = []
    pending: dict | None = None     # {"text", "x_left", "y_top"}
    last_seen_sender: str | None = None  # for consecutive same-speaker runs

    def flush(p: dict | None, *, sender_hint: str | None) -> None:
        if not p:
            return
        if p["x_left"] >= self_threshold_x:
            sender = SELF_SENDER
            is_self = True
        elif sender_hint:
            sender = sender_hint
            is_self = False
        elif last_seen_sender:
            sender = last_seen_sender
            is_self = False
        else:
            sender = "unknown"
            is_self = False
        messages.append(
            ChatMessage(
                sender=sender,
                text=p["text"],
                position=len(messages),
                is_self=is_self,
            )
        )

    for node in root.iter("node"):
        rid = _short_rid(node.attrib.get("resource-id"))
        raw_text = node.attrib.get("text") or ""
        text = html.unescape(raw_text).strip()
        if not text:
            continue
        bounds = _parse_bounds(node.attrib.get("bounds"))
        if bounds is None:
            continue
        x_left = bounds[0]

        if rid == "chat_ui_message_text":
            # Whatever was pending is now stale — flush it.
            flush(pending, sender_hint=None)
            pending = {"text": text, "x_left": x_left, "y_top": bounds[1]}
            continue

        if rid == "chat_ui_row_sender":
            # Attribute pending to this label (unless x-bounds said SELF).
            if pending is not None:
                flush(pending, sender_hint=text)
                pending = None
            # Update carry-forward sender for subsequent consecutive bubbles
            # that don't show the name.
            last_seen_sender = text
            continue

        # chat_ui_sender_name + chat_ui_content_text are quoted preceding
        # messages (reply context), not separate conversational entries.
        # Skip both.

        if rid == "chat_ui_announcement_fold_content_message":
            # Folded pinned post bar at top. Skip.
            continue

        # Other ids (timestamps, header_title, message_edit, etc.) are
        # not content — ignore.

    # Trailing pending message with no row_sender after it: rely on
    # x-bounds (operator if right-aligned) or last_seen_sender carry-over.
    flush(pending, sender_hint=None)

    # Fallback to legacy text-node extraction if structural parse yielded
    # nothing (older LINE builds, broken dumps).
    if not messages:
        from app.parsing.xml_cleaner import extract_text_nodes
        texts = extract_text_nodes(xml_text)
        messages = [
            ChatMessage(sender="unknown", text=t, position=i, is_self=False)
            for i, t in enumerate(texts)
            if _looks_like_chat_message(t)
        ]

    return messages[-limit:]


def _looks_like_chat_message(text: str) -> bool:
    if len(text) <= 1:
        return False
    if text in {"搜尋", "設定", "公告", "傳送", "送出"}:
        return False
    return True
