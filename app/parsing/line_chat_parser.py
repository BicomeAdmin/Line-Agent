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
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


# Sentinel for the operator's own messages (identified structurally
# via right-alignment). The reply-target selector treats this as
# "self" without needing a per-community nickname match.
SELF_SENDER = "__operator__"

_TPE = ZoneInfo("Asia/Taipei")
_LINE_TIME_RE = re.compile(r"^(上午|下午)?\s*(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class ChatMessage:
    sender: str
    text: str
    position: int
    source: str = "uiautomator"
    is_self: bool = False
    # UTC seconds. None when the LINE UI didn't expose a timestamp for
    # this bubble (e.g. consecutive messages within the same minute
    # collapse to a single timestamp; we attribute it to the LAST
    # bubble in that run, the older ones get None).
    ts_epoch: float | None = None
    # Original LINE label, e.g. "下午4:19" — kept for debugging /
    # operator-facing display. Empty when no timestamp was parsed.
    ts_label: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _parse_line_time_label(label: str, *, now_tpe: datetime) -> datetime | None:
    """Convert a LINE chat row timestamp like '下午4:19' / '上午10:28' / '16:19'
    into a TPE datetime.

    LINE only shows H:MM (no date) for today's messages. If the resulting
    time is more than ~3h IN THE FUTURE relative to now_tpe, we assume it
    belongs to YESTERDAY (LINE shows yesterday's H:MM after midnight roll
    until the user scrolls past a date separator). Returning None means
    the label couldn't be parsed.

    Date separators ('昨天' / '5月3日' / specific dates) are NOT yet
    supported — caller falls back to ts_epoch=None, which the temporal
    layer treats as "unknown age, very stale".
    """

    text = (label or "").strip()
    if not text:
        return None
    m = _LINE_TIME_RE.match(text)
    if not m:
        return None
    period, hour_s, minute_s = m.group(1), m.group(2), m.group(3)
    hour, minute = int(hour_s), int(minute_s)
    if period == "下午" and hour < 12:
        hour += 12
    elif period == "上午" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    candidate = now_tpe.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # If the parsed time is meaningfully in the future, it's yesterday's.
    if candidate > now_tpe + timedelta(hours=3):
        candidate -= timedelta(days=1)
    return candidate


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


def parse_line_chat(
    xml_text: str,
    limit: int = 10,
    *,
    now_epoch: float | None = None,
) -> list[ChatMessage]:
    """Structured parse with sender attribution. See module docstring
    for resource-id semantics.

    Pipeline: walk nodes in doc order, when we see chat_ui_message_text
    stash it as `pending`; when we see chat_ui_row_sender attribute
    pending to that name (unless x-bounds say it was operator's own,
    in which case attribute to SELF_SENDER and ignore the row_sender,
    which would be a stale label for someone else). Flush trailing
    pending at end.

    Timestamps: chat_ui_row_timestamp nodes are collected with their
    y_top during the walk and paired to messages AFTER all bubbles are
    flushed — pair by y-proximity (timestamp lives within ~200px below
    its bubble row). Messages without a nearby timestamp get
    ts_epoch=None, which downstream temporal logic treats as "unknown
    age, suspect stale".
    """

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    now_tpe = datetime.fromtimestamp(now_epoch if now_epoch is not None else time.time(), _TPE)

    # Detect screen width from the root bounds for a sane right-half threshold.
    root_bounds = _parse_bounds(root.attrib.get("bounds")) if hasattr(root, "attrib") else None
    if root_bounds is not None:
        screen_width = root_bounds[2]
    else:
        screen_width = 1080
    self_threshold_x = int(screen_width * 0.40)  # x_left ≥ 40% → self

    messages: list[ChatMessage] = []
    message_y_tops: list[int] = []
    pending: dict | None = None     # {"text", "x_left", "y_top"}
    last_seen_sender: str | None = None  # for consecutive same-speaker runs
    timestamps: list[tuple[int, str, datetime | None]] = []  # (y_top, label, parsed_dt)

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
        message_y_tops.append(p["y_top"])

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

        if rid == "chat_ui_row_timestamp":
            timestamps.append((bounds[1], text, _parse_line_time_label(text, now_tpe=now_tpe)))
            continue

        # chat_ui_sender_name + chat_ui_content_text are quoted preceding
        # messages (reply context), not separate conversational entries.
        # Skip both.

        if rid == "chat_ui_announcement_fold_content_message":
            # Folded pinned post bar at top. Skip.
            continue

        # Other ids (header_title, message_edit, etc.) are not content — ignore.

    # Trailing pending message with no row_sender after it: rely on
    # x-bounds (operator if right-aligned) or last_seen_sender carry-over.
    flush(pending, sender_hint=None)

    # Pair timestamps to messages by y-proximity. A timestamp belongs
    # to the bubble whose y_top is closest within a 200px window
    # (LINE's row height is ~140-180px depending on font scaling).
    # Each timestamp can claim only one bubble — sort by y, walk in
    # order, attach to the most recently seen bubble whose y is
    # within window.
    if messages and timestamps:
        timestamps.sort(key=lambda t: t[0])
        ts_idx = 0
        ts_for_msg: list[tuple[str, datetime | None]] = [("", None)] * len(messages)
        for i, msg_y in enumerate(message_y_tops):
            best = None
            for ts in timestamps:
                ts_y, label, parsed = ts
                # Timestamp shows just below its bubble; window 200px.
                if 0 <= ts_y - msg_y <= 200:
                    best = (label, parsed)
                    break
                # Some bubbles have timestamp slightly above (stacked
                # rendering); allow a 50px upward window.
                if -50 <= ts_y - msg_y < 0 and best is None:
                    best = (label, parsed)
            if best is not None:
                ts_for_msg[i] = best
        # Re-emit messages with ts attached.
        rebuilt: list[ChatMessage] = []
        for i, m in enumerate(messages):
            label, parsed = ts_for_msg[i]
            ts_epoch = parsed.timestamp() if parsed is not None else None
            rebuilt.append(
                ChatMessage(
                    sender=m.sender,
                    text=m.text,
                    position=m.position,
                    source=m.source,
                    is_self=m.is_self,
                    ts_epoch=ts_epoch,
                    ts_label=label,
                )
            )
        messages = rebuilt

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
