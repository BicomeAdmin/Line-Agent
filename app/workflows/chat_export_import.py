"""Ingest LINE's built-in chat export (.txt) into Project Echo.

This is the operator-driven, fully-compliant data path: LINE's own
「傳送對話紀錄 → 文字檔」 feature exports an entire chat with
timestamps and sender names. Operator does that by hand (no
automation), drops the .txt into the project, and we parse it here.

Why this beats UI scraping:
  - **Volume**: full history vs ~200 messages from a single ADB session
  - **Attribution**: real sender names per line (UI dump gives all "unknown")
  - **Safety**: zero ADB / no automation footprint inside LINE
  - **ToS**: a built-in user feature, operator's own data

Format observed (LINE OpenChat export, 2026-04 era):
    YYYY.MM.DD 星期X            ← date header on its own line
    HH:MM <sender> <content>     ← message start
    <continuation line 1>        ← lines without HH:MM prefix continue
    <continuation line 2>          the previous message

Sender names can contain spaces ("阿樂 本尊") because LINE OpenChat
appends role badges like 「本尊 / 副管」 to the display name.

Two public entry points:

  - parse_line_export(path) → list[ChatMessage]
      Pure parser. No IO beyond reading the file. Used by tests.

  - import_chat_export(customer_id, community_id, file_path)
      End-to-end: copies the .txt into the project's data dir,
      parses it, derives per-sender stats, and merges natural
      conversational lines into voice_profile.md (append mode,
      same dedup as harvest_style_samples). Returns a structured
      summary for the LLM brain to report back.
"""

from __future__ import annotations

import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.core.audit import append_audit_event
from app.core.timezone import TAIPEI
from app.storage.config_loader import load_community_config
from app.storage.paths import customer_data_root, voice_profile_path


# Reuse the harvest module's filtering / scoring / splice helpers so the
# Observed lines block stays consistent regardless of source (UI scrape
# vs export ingest).
from app.workflows.style_harvest import (
    _build_harvest_block,
    _extract_existing_samples,
    _filter_natural_lines,
    _score_line,
    _splice_harvest_block,
)


# Role badges LINE OpenChat appends after the display name. When detected,
# we treat "<name> <badge>" as one sender token, not two.
_ROLE_BADGES = ("本尊", "副管", "共同管理", "管理員")

_DATE_HEADER_RE = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})\s+星期[一二三四五六日天]\s*$")
_MESSAGE_START_RE = re.compile(r"^(\d{1,2}):(\d{2})\s+(.+)$")


@dataclass
class ChatMessage:
    date: str          # "YYYY-MM-DD" (Asia/Taipei calendar from header)
    time: str          # "HH:MM"
    sender: str
    text: str          # full content, possibly multi-line (joined with \n)

    def to_dict(self) -> dict[str, object]:
        return {"date": self.date, "time": self.time, "sender": self.sender, "text": self.text}


# ──────────────────────────────────────────────────────────────────────
# Pure parser
# ──────────────────────────────────────────────────────────────────────

def parse_line_export(file_path: str | Path) -> list[ChatMessage]:
    """Parse a LINE OpenChat .txt export. Two-pass: first pass collects
    sender-name candidates (since names contain spaces, we need a known
    set to do longest-match); second pass attributes each message."""

    raw = Path(file_path).read_text(encoding="utf-8")
    lines = raw.splitlines()

    sender_set = _build_sender_set(lines)
    return list(_iter_messages(lines, sender_set))


def _build_sender_set(lines: list[str]) -> set[str]:
    """Find all plausible sender names. Two-pass:
      Pass 1: collect 1-token candidates that appear >= 2 times as
              line starters — these are the actual display names.
      Pass 2: 2-token candidates are only accepted when the second
              token is a known role badge (本尊 / 副管 / etc).
              Plain 2-token candidates are rejected because LINE
              inserts content placeholders like 「圖片」「貼圖」「影片」
              after the sender name, which would otherwise look like
              "<sender> <placeholder>" sender candidates and beat the
              real 1-token name in longest-match attribution.
    """

    one_tok: Counter[str] = Counter()
    two_tok_with_badge: Counter[str] = Counter()
    for line in lines:
        m = _MESSAGE_START_RE.match(line)
        if not m:
            continue
        tokens = m.group(3).split()
        if not tokens:
            continue
        one_tok[tokens[0]] += 1
        if len(tokens) >= 2 and tokens[1] in _ROLE_BADGES:
            two_tok_with_badge[" ".join(tokens[:2])] += 1

    sender_set: set[str] = set()
    for name, count in one_tok.items():
        if count >= 2:
            sender_set.add(name)
    for name in two_tok_with_badge:
        sender_set.add(name)
    return sender_set


def _attribute_sender(rest: str, sender_set: set[str]) -> tuple[str, str]:
    """Given the post-time chunk of a message line, return (sender, content)
    using longest-match against the known sender set. Falls back to the
    first whitespace-separated token if no match."""

    tokens = rest.split()
    # Try 2-token then 1-token longest match.
    for n in (2, 1):
        if len(tokens) >= n:
            cand = " ".join(tokens[:n])
            if cand in sender_set:
                return cand, " ".join(tokens[n:])
    # Last resort: first token is sender, remainder is content.
    return tokens[0], " ".join(tokens[1:])


def _iter_messages(lines: list[str], sender_set: set[str]) -> Iterable[ChatMessage]:
    current_date: str | None = None
    pending: ChatMessage | None = None

    for line in lines:
        date_match = _DATE_HEADER_RE.match(line)
        if date_match:
            if pending is not None:
                yield pending
                pending = None
            year, month, day = date_match.groups()
            current_date = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
            continue

        msg_match = _MESSAGE_START_RE.match(line)
        if msg_match:
            if pending is not None:
                yield pending
            hour, minute, rest = msg_match.groups()
            sender, content = _attribute_sender(rest, sender_set)
            pending = ChatMessage(
                date=current_date or "",
                time=f"{int(hour):02d}:{minute}",
                sender=sender,
                text=content,
            )
            continue

        # Continuation line — append to current message if we have one,
        # skipping pure blank lines (those are visual gaps).
        if pending is not None and line.strip():
            pending.text = (pending.text + "\n" + line).rstrip()

    if pending is not None:
        yield pending


# ──────────────────────────────────────────────────────────────────────
# Per-sender aggregates
# ──────────────────────────────────────────────────────────────────────

@dataclass
class SenderStats:
    sender: str
    message_count: int = 0
    total_chars: int = 0
    sample_lines: list[str] = field(default_factory=list)  # up to 5 distinct lines

    def to_dict(self) -> dict[str, object]:
        avg_len = round(self.total_chars / self.message_count, 1) if self.message_count else 0
        return {
            "sender": self.sender,
            "message_count": self.message_count,
            "avg_length": avg_len,
            "sample_lines": self.sample_lines[:5],
        }


def aggregate_per_sender(messages: list[ChatMessage], *, sample_size: int = 5) -> list[SenderStats]:
    """Group parsed messages by sender. Cheap and useful as a first
    fingerprint surface; deeper per-sender style analysis (emoji rate,
    particle preferences) can build on this output."""

    by_sender: dict[str, SenderStats] = {}
    for msg in messages:
        s = by_sender.setdefault(msg.sender, SenderStats(sender=msg.sender))
        s.message_count += 1
        s.total_chars += len(msg.text)
        if len(s.sample_lines) < sample_size and msg.text not in s.sample_lines:
            s.sample_lines.append(msg.text[:80])
    return sorted(by_sender.values(), key=lambda s: s.message_count, reverse=True)


# ──────────────────────────────────────────────────────────────────────
# Import: copy file + parse + merge into voice profile
# ──────────────────────────────────────────────────────────────────────

def chat_exports_dir(customer_id: str) -> Path:
    return customer_data_root(customer_id) / "chat_exports"


def import_chat_export(
    customer_id: str,
    community_id: str,
    file_path: str | Path,
    *,
    top_n_new_samples: int = 50,
    total_cap: int = 200,
    keep_local_copy: bool = True,
) -> dict[str, object]:
    """Ingest an operator-supplied LINE export into the project.

    Steps:
      1. Validate community + source file.
      2. (optional) copy the .txt into customers/<id>/data/chat_exports/
         so we can re-parse without depending on the operator's
         Downloads folder still having it.
      3. Parse, then dedup-merge top_n_new_samples natural lines into
         the voice_profile.md auto-managed block (same append logic as
         harvest_style_samples).
      4. Audit + return a structured summary including per-sender stats.
    """

    src = Path(file_path).expanduser()
    if not src.exists() or not src.is_file():
        return {"status": "error", "reason": "export_file_not_found", "path": str(src)}

    try:
        load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    profile_path = voice_profile_path(customer_id, community_id)
    if not profile_path.exists():
        return {"status": "error", "reason": "voice_profile_missing", "path": str(profile_path)}

    # Step 2: persist a local copy so re-parsing doesn't depend on
    # the operator's Downloads still holding the original.
    local_copy: Path | None = None
    if keep_local_copy:
        target_dir = chat_exports_dir(customer_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(TAIPEI).strftime("%Y%m%d_%H%M%S")
        local_copy = target_dir / f"{community_id}__{timestamp}.txt"
        try:
            shutil.copy2(src, local_copy)
        except OSError as exc:
            local_copy = None
            # not fatal — proceed with the original path
            _ = exc

    parse_path = local_copy or src
    try:
        messages = parse_line_export(parse_path)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"parse_failed:{exc}", "path": str(parse_path)}

    if not messages:
        return {
            "status": "ok",
            "warning": "no_messages_parsed",
            "messages_parsed": 0,
            "stored_at": str(local_copy) if local_copy else None,
            "hint": "檔案格式可能不是 LINE 文字匯出標準格式，請確認來源",
        }

    # Convert to the dict shape that _filter_natural_lines expects
    # (style_harvest pipeline reuses {sender, text, position}).
    msg_dicts = [
        {"sender": m.sender, "text": m.text, "position": i}
        for i, m in enumerate(messages)
    ]
    natural = _filter_natural_lines(msg_dicts)
    scored = sorted(natural, key=_score_line, reverse=True)

    before_text = profile_path.read_text(encoding="utf-8")
    existing = _extract_existing_samples(before_text)
    existing_set = set(existing)

    new_picks: list[str] = []
    seen: set[str] = set()
    for line in scored:
        if line in existing_set or line in seen:
            continue
        seen.add(line)
        new_picks.append(line)
        if len(new_picks) >= top_n_new_samples:
            break

    merged = existing + new_picks
    dropped_old = 0
    if len(merged) > total_cap:
        dropped_old = len(merged) - total_cap
        merged = merged[-total_cap:]

    after_text = _splice_harvest_block(before_text, merged)
    profile_path.write_text(after_text, encoding="utf-8")

    sender_stats = aggregate_per_sender(messages)

    append_audit_event(
        customer_id,
        "chat_export_imported",
        {
            "community_id": community_id,
            "source_file": str(src),
            "stored_at": str(local_copy) if local_copy else None,
            "messages_parsed": len(messages),
            "natural_lines_kept": len(natural),
            "new_samples_added": len(new_picks),
            "total_samples_now": len(merged),
            "dropped_oldest": dropped_old,
            "distinct_senders": len(sender_stats),
        },
    )

    return {
        "status": "ok",
        "community_id": community_id,
        "messages_parsed": len(messages),
        "distinct_senders": len(sender_stats),
        "natural_lines_kept": len(natural),
        "new_samples_added": len(new_picks),
        "total_samples_now": len(merged),
        "existing_samples_before": len(existing),
        "dropped_oldest": dropped_old,
        "stored_at": str(local_copy) if local_copy else None,
        "preview_new": new_picks[:5],
        "sender_stats": [s.to_dict() for s in sender_stats[:10]],  # top 10 by volume
        "voice_profile_path": str(profile_path),
    }
