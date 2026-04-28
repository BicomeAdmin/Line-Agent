"""Parse a uiautomator XML dump of a LINE OpenChat chat-history view.

Resource-id semantics (LINE 26.6.0, captured 2026-04-28):
  chat_ui_sender_name     → other person's display name (above their bubble)
  chat_ui_content_text    → other person's message body
  chat_ui_message_text    → operator's own outgoing bubble (no name needed)
  chat_ui_row_timestamp   → time label, ignore
  chat_ui_announcement_fold_content_message → pinned announcement, low signal
  chat_ui_message_edit    → input field bottom of screen, ignore

Pairing rule:
  • Walk nodes in document order.
  • When we see chat_ui_sender_name, remember the most recent sender.
  • When we see chat_ui_content_text, attribute it to the remembered
    sender (LINE only renders sender_name above the FIRST bubble of
    a new speaker; consecutive bubbles by the same speaker reuse it).
  • When we see chat_ui_message_text, attribute to SELF_SENDER and
    reset the remembered sender so the next other-speaker block starts
    fresh.

The output sender field is either:
  • a real LINE display name (e.g. "山寶｜一起玩社群任務>換星巴克卷🦦")
  • SELF_SENDER ("__operator__") for outgoing messages
  • "unknown" only if a content node appears before any sender_name
    has been seen (rare — usually the dump starts mid-conversation).
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass


# Sentinel used in the `sender` field for the operator's own messages.
# The reply-target selector treats this as "self" without needing a
# nickname match — solves the per-community nickname-config problem.
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


def parse_line_chat(xml_text: str, limit: int = 10) -> list[ChatMessage]:
    """Structured parse with sender attribution. See module docstring."""

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    pending_sender: str | None = None
    messages: list[ChatMessage] = []

    for node in root.iter("node"):
        rid = _short_rid(node.attrib.get("resource-id"))
        raw_text = node.attrib.get("text") or ""
        text = html.unescape(raw_text).strip()
        if not text:
            continue

        if rid == "chat_ui_sender_name":
            # Remember for the next chat_ui_content_text. Same sender
            # might span multiple consecutive content_text nodes — we
            # don't reset until either a new sender_name appears or a
            # self message breaks the run.
            pending_sender = text
            continue

        if rid == "chat_ui_content_text":
            sender = pending_sender or "unknown"
            messages.append(
                ChatMessage(
                    sender=sender,
                    text=text,
                    position=len(messages),
                    is_self=False,
                )
            )
            continue

        if rid == "chat_ui_message_text":
            # Operator's own bubble; reset pending_sender so the next
            # other-speaker run gets a fresh attribution.
            messages.append(
                ChatMessage(
                    sender=SELF_SENDER,
                    text=text,
                    position=len(messages),
                    is_self=True,
                )
            )
            pending_sender = None
            continue

        if rid == "chat_ui_announcement_fold_content_message":
            # Folded announcement bar at the top — operator-curated,
            # low conversational signal. Skip from chat tail.
            continue

        # Other rids (timestamps, message_edit, header_title, etc.) are
        # not chat content — ignore.

    # Fallback: if structural parsing yielded nothing (older LINE
    # builds, weird dumps), fall back to the legacy text-node extractor
    # so we degrade gracefully rather than returning [].
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
