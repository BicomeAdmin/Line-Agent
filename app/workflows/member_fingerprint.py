"""Per-member style fingerprint — built from imported chat exports.

For each sender in a community, compute:
  - message_count: how many lines we've seen them post
  - avg_length / median_length: char-count distribution
  - emoji_rate: emoji-chars / total-chars
  - top_opening_words: how they typically start lines
  - top_ending_particles: how they typically end lines (啊/哈/欸/...)
  - recent_lines: last N (default 10) message texts, most-recent first
  - last_seen_date: "YYYY-MM-DD"

Output is cached as JSON at:
  customers/<id>/data/member_fingerprints/<community_id>.json

Source data: the most recent chat_exports/<community_id>__*.txt the
operator has imported. We pick the freshest by mtime.

This is the data layer the reply-target-selector uses: when bot
decides to reply to person X, it loads X's fingerprint and crafts a
draft that mirrors their length / formality / particles instead of
producing the same generic voice for everyone.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.core.audit import append_audit_event
from app.core.timezone import TAIPEI
from app.storage.config_loader import load_community_config
from app.storage.paths import customer_data_root
from app.workflows.chat_export_import import ChatMessage, parse_line_export


# Reuse harvest's emoji + particle definitions for consistency.
from app.workflows.style_harvest import _EMOJI_RE, _ENDING_PARTICLES


@dataclass
class MemberFingerprint:
    sender: str
    message_count: int = 0
    avg_length: float = 0.0
    median_length: float = 0.0
    length_stddev: float = 0.0  # variance signature
    emoji_rate: float = 0.0
    top_opening_words: list[str] = field(default_factory=list)
    top_ending_particles: list[str] = field(default_factory=list)
    recent_lines: list[str] = field(default_factory=list)
    last_seen_date: str | None = None
    # Stylometric extensions (Tier 2 #4 — function words, punctuation,
    # line-break habits, typo signatures). These are richer authorship
    # signals than the basic three; the reply-style mirror works much
    # better with them.
    function_word_freq: dict[str, float] = field(default_factory=dict)
    punctuation_signature: dict[str, int] = field(default_factory=dict)
    line_break_rate: float = 0.0  # newlines per message
    multi_msg_burst_rate: float = 0.0  # ratio of consecutive same-sender messages
    type_token_ratio: float = 0.0  # vocabulary diversity (MTLD-style)
    typo_signature: dict[str, int] = field(default_factory=dict)
    avg_punct_per_msg: float = 0.0
    repeated_punct_rate: float = 0.0  # ratio of msgs with !! / ?? / ...

    def summary_zh(self) -> str:
        parts = [f"{self.sender} ({self.message_count} 則)"]
        parts.append(f"中位字數 {int(self.median_length)}")
        if self.emoji_rate > 0.05:
            parts.append(f"emoji 多 ({self.emoji_rate:.2f}/字)")
        elif self.emoji_rate > 0:
            parts.append(f"少用 emoji")
        else:
            parts.append("不用 emoji")
        if self.top_ending_particles:
            parts.append(f"句尾常用「{'/'.join(self.top_ending_particles[:2])}」")
        if self.line_break_rate > 0.5:
            parts.append("愛換行")
        if self.repeated_punct_rate > 0.15:
            parts.append("常用!!/??")
        return "；".join(parts)


# ──────────────────────────────────────────────────────────────────────
# Compute
# ──────────────────────────────────────────────────────────────────────

def compute_fingerprints(messages: list[ChatMessage], *, recent_n: int = 10) -> list[MemberFingerprint]:
    by_sender: dict[str, list[ChatMessage]] = {}
    for m in messages:
        by_sender.setdefault(m.sender, []).append(m)

    out: list[MemberFingerprint] = []
    for sender, items in by_sender.items():
        out.append(_fingerprint_one(sender, items, recent_n=recent_n))
    out.sort(key=lambda f: f.message_count, reverse=True)
    return out


def _fingerprint_one(sender: str, items: list[ChatMessage], *, recent_n: int) -> MemberFingerprint:
    texts = [(m.text or "").strip() for m in items]
    texts = [t for t in texts if t]
    if not texts:
        return MemberFingerprint(sender=sender)

    lengths = [len(t) for t in texts]
    median = float(statistics.median(lengths))
    avg = float(sum(lengths) / len(lengths))

    emoji_chars = sum(len(_EMOJI_RE.findall(t)) for t in texts)
    total_chars = sum(lengths)
    emoji_rate = round(emoji_chars / total_chars, 3) if total_chars else 0.0

    opening_counter: Counter[str] = Counter()
    for t in texts:
        head = t[:2] if len(t) >= 2 else t[:1]
        if re.match(r"^[一-鿿A-Za-z]", head):
            opening_counter[head] += 1
    top_opens = [w for w, _ in opening_counter.most_common(5)]

    ending_counter: Counter[str] = Counter()
    for t in texts:
        for particle in _ENDING_PARTICLES:
            if t.endswith(particle) or t.endswith(particle + "～") or t.endswith(particle + "！") or t.endswith(particle + "?") or t.endswith(particle + "？"):
                ending_counter[particle] += 1
                break
    top_ends = [p for p, _ in ending_counter.most_common(5)]

    # Recent lines: most-recent first, deduped, trimmed to readable length.
    recent_seen: set[str] = set()
    recent_lines: list[str] = []
    for m in reversed(items):
        line = (m.text or "").strip()
        if not line or line in recent_seen:
            continue
        recent_seen.add(line)
        recent_lines.append(line[:200])
        if len(recent_lines) >= recent_n:
            break

    last_seen = max((m.date for m in items if m.date), default=None)

    # Stylometric extensions (Tier 2 #4) — function words, punctuation
    # signature, line-break habit, typo patterns, vocabulary diversity.
    length_stddev = float(statistics.pstdev(lengths)) if len(lengths) > 1 else 0.0
    func_freq = _function_word_freq(texts)
    punct_sig = _punctuation_signature(texts)
    line_breaks = sum(t.count("\n") for t in texts)
    line_break_rate = round(line_breaks / max(1, len(texts)), 3)
    multi_burst = _multi_msg_burst_rate(items)
    ttr = _type_token_ratio(texts)
    typos = _typo_signature(texts)
    avg_punct = round(sum(_count_all_punct(t) for t in texts) / max(1, len(texts)), 2)
    repeated_punct = round(
        sum(1 for t in texts if re.search(r"[!！?？]{2,}|\.{3,}|～{2,}", t)) / max(1, len(texts)),
        3,
    )

    return MemberFingerprint(
        sender=sender,
        message_count=len(texts),
        avg_length=round(avg, 1),
        median_length=median,
        length_stddev=round(length_stddev, 1),
        emoji_rate=emoji_rate,
        top_opening_words=top_opens,
        top_ending_particles=top_ends,
        recent_lines=recent_lines,
        last_seen_date=last_seen,
        function_word_freq=func_freq,
        punctuation_signature=punct_sig,
        line_break_rate=line_break_rate,
        multi_msg_burst_rate=multi_burst,
        type_token_ratio=ttr,
        typo_signature=typos,
        avg_punct_per_msg=avg_punct,
        repeated_punct_rate=repeated_punct,
    )


# ──────────────────────────────────────────────────────────────────────
# Stylometric feature extractors (Tier 2 #4)
# ──────────────────────────────────────────────────────────────────────

# Function words / particles that carry strong authorship signal in
# zh-TW chat. These are the "tell" of how someone talks regardless of
# topic — virtually impossible to consciously fake when composing in
# someone else's voice.
_FUNCTION_WORDS = (
    "啊", "喔", "欸", "哈", "吧", "嗎", "呢", "啦", "耶", "齁", "誒",
    "捏", "齁", "唉", "嗯", "哎", "嘿", "诶",
    "我", "你", "他", "她", "我們", "你們", "他們",
    "對", "好", "嗯", "可以", "不過", "其實", "然後",
    "真的", "這樣", "那種", "還好", "就是", "不是",
)

# Common punctuation marks tracked separately because their frequencies
# diverge wildly across speakers (some use !!! everywhere, others never).
_PUNCT_MARKS = ("！", "!", "？", "?", "～", "~", "。", "，", ",", "...", "…", "、")


def _function_word_freq(texts: list[str]) -> dict[str, float]:
    """Frequency per 100 chars for each tracked function word."""
    if not texts:
        return {}
    total_chars = sum(len(t) for t in texts) or 1
    out: dict[str, float] = {}
    for w in _FUNCTION_WORDS:
        count = sum(t.count(w) for t in texts)
        if count > 0:
            out[w] = round(count * 100 / total_chars, 3)
    return dict(sorted(out.items(), key=lambda kv: kv[1], reverse=True)[:12])


def _punctuation_signature(texts: list[str]) -> dict[str, int]:
    """Total counts per punctuation mark across this sender's messages."""
    out: dict[str, int] = {}
    for mark in _PUNCT_MARKS:
        c = sum(t.count(mark) for t in texts)
        if c > 0:
            out[mark] = c
    return out


def _count_all_punct(text: str) -> int:
    return sum(text.count(p) for p in _PUNCT_MARKS)


def _multi_msg_burst_rate(items: list) -> float:
    """Ratio of messages that immediately follow another from the same
    sender (a "burst" pattern). Some people send 1 message at a time,
    others spam-fire 3-4 short msgs in a row — strong style signal."""
    if len(items) < 2:
        return 0.0
    bursts = 0
    for i in range(1, len(items)):
        if items[i].sender == items[i - 1].sender:
            bursts += 1
    return round(bursts / (len(items) - 1), 3)


def _type_token_ratio(texts: list[str]) -> float:
    """Vocabulary diversity: distinct 2-char Han bigrams / total bigrams.
    Higher = more varied vocabulary. Approximation of MTLD without
    needing word segmentation."""
    bigrams: list[str] = []
    for t in texts:
        han_runs = re.findall(r"[一-鿿]+", t)
        for run in han_runs:
            for i in range(len(run) - 1):
                bigrams.append(run[i : i + 2])
    if not bigrams:
        return 0.0
    return round(len(set(bigrams)) / len(bigrams), 3)


# Common 注音文 / typo patterns specific to Mandarin chat.
# When someone uses 「ㄉ」for「的」or 「醬」for「這樣」, that's an
# extremely strong style fingerprint.
_TYPO_PATTERNS = {
    "注音文_ㄉ": r"ㄉ(?![a-zA-Z])",
    "注音文_ㄅ": r"ㄅ(?![a-zA-Z])",
    "注音文_ㄋ": r"ㄋ(?![a-zA-Z])",
    "醬_for_這樣": r"醬",
    "降_for_這樣": r"降(?!水|低|溫)",  # exclude 降水/降低/降溫
    "蝦米_for_什麼": r"蝦米",
    "粉_for_很": r"粉(?!紅|碎|末|筆)",  # exclude common compounds
    "省略主詞": r"^[一-鿿]{1,2}",  # placeholder; not really a typo, but signal
}


def _typo_signature(texts: list[str]) -> dict[str, int]:
    """Count occurrences of common 注音文 / chat-typo patterns."""
    out: dict[str, int] = {}
    for label, pattern in _TYPO_PATTERNS.items():
        if label == "省略主詞":
            continue  # skip — too common to be a real signal
        compiled = re.compile(pattern)
        c = sum(len(compiled.findall(t)) for t in texts)
        if c > 0:
            out[label] = c
    return out


# ──────────────────────────────────────────────────────────────────────
# Cache (JSON on disk)
# ──────────────────────────────────────────────────────────────────────

def fingerprints_path(customer_id: str, community_id: str) -> Path:
    return customer_data_root(customer_id) / "member_fingerprints" / f"{community_id}.json"


def latest_export_path(customer_id: str, community_id: str) -> Path | None:
    """Find the most recent chat_exports/<community>__*.txt by mtime."""

    exports_dir = customer_data_root(customer_id) / "chat_exports"
    if not exports_dir.exists():
        return None
    candidates = sorted(
        exports_dir.glob(f"{community_id}__*.txt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def refresh_member_fingerprints(
    customer_id: str,
    community_id: str,
    *,
    export_path: str | Path | None = None,
) -> dict[str, object]:
    """Compute fingerprints from the latest (or given) chat export and
    persist as JSON. Operator triggers via MCP tool when they want
    fresh per-member style data after a new import."""

    try:
        load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    src = Path(export_path) if export_path else latest_export_path(customer_id, community_id)
    if src is None or not src.exists():
        return {
            "status": "error",
            "reason": "no_export_available",
            "hint": "先用 import_chat_export 匯入該社群的對話紀錄",
        }

    try:
        messages = parse_line_export(src)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"parse_failed:{exc}"}

    fingerprints = compute_fingerprints(messages)

    out_path = fingerprints_path(customer_id, community_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "community_id": community_id,
        "source_file": str(src),
        "computed_at_taipei": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
        "total_messages": len(messages),
        "distinct_senders": len(fingerprints),
        "fingerprints": [asdict(f) for f in fingerprints],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    append_audit_event(
        customer_id,
        "member_fingerprints_refreshed",
        {
            "community_id": community_id,
            "source_file": str(src),
            "total_messages": len(messages),
            "distinct_senders": len(fingerprints),
        },
    )

    return {
        "status": "ok",
        "community_id": community_id,
        "source_file": str(src),
        "total_messages": len(messages),
        "distinct_senders": len(fingerprints),
        "stored_at": str(out_path),
        "top_5": [f.summary_zh() for f in fingerprints[:5]],
    }


def load_member_fingerprints(customer_id: str, community_id: str) -> dict[str, object] | None:
    """Load the cached fingerprint bundle for a community, or None
    when it hasn't been computed yet."""

    path = fingerprints_path(customer_id, community_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def get_member_fingerprint(
    customer_id: str,
    community_id: str,
    sender: str,
) -> dict[str, object] | None:
    """Look up a single member's fingerprint by sender name (exact
    match). Returns None if cache is missing or sender unknown."""

    bundle = load_member_fingerprints(customer_id, community_id)
    if not bundle:
        return None
    for fp in bundle.get("fingerprints") or []:
        if isinstance(fp, dict) and fp.get("sender") == sender:
            return fp
    return None
