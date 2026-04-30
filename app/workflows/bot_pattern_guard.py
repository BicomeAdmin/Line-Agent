"""Bot-fingerprint detection — refuse to compose when the cumulative
pattern would tip community members off that "this is automated".

Two visible patterns members might notice:

  1. **Frequency**: too many AI-assisted drafts per community per day.
     Even if every draft passes individual quality + temporal gates,
     volume creates the "this account is suspiciously responsive"
     fingerprint.

  2. **Repetition**: same opening phrase / sentence structure across
     consecutive drafts. LLMs have characteristic tics; a member who
     reads several of the operator's recent posts will spot them.

This module returns a risk verdict that the watcher / brand-mode
compose path consults BEFORE spawning codex. Blocked compose attempts
audit the reason so the operator can override (manual compose) or
adjust thresholds.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass

from app.core.audit import read_recent_audit_events


# Per-community, per-rolling-24h count of AI-assisted drafts created.
# Soft warning fires above WARN_DAILY_COUNT; hard block above
# BLOCK_DAILY_COUNT. Tuned for "feels human" — most engaged human
# operators of a single community post organically 5-15 times/day,
# so anything >10 driven by LLM starts looking suspicious.
WARN_DAILY_COUNT = 5
BLOCK_DAILY_COUNT = 10
ROLLING_WINDOW_HOURS = 24

# Opening-phrase repetition: collect first N chars of last K drafts
# and warn if any opening recurs >= REPEAT_THRESHOLD times.
OPENING_PROBE_CHARS = 2
LAST_DRAFTS_TO_INSPECT = 5
REPEAT_THRESHOLD = 3

# Audit event types that count as an AI-assisted draft surfacing.
# Both watcher (auto_watch) and scheduled_post compose_mode emit one
# of these when a draft lands in review_store.
_DRAFT_AUDIT_EVENT_TYPES = {
    "mcp_compose_review_created",
    "scheduled_post_compose_succeeded",
}


@dataclass(frozen=True)
class BotPatternVerdict:
    risk: str                          # "ok" | "warn" | "block"
    daily_draft_count: int
    repeated_openings: tuple[tuple[str, int], ...]   # [(phrase, count), ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "risk": self.risk,
            "daily_draft_count": self.daily_draft_count,
            "repeated_openings": [list(p) for p in self.repeated_openings],
            "reasons": list(self.reasons),
        }


def assess_bot_pattern_risk(
    customer_id: str,
    community_id: str,
    *,
    now: float | None = None,
    audit_events: list[dict] | None = None,
) -> BotPatternVerdict:
    """Return risk verdict for one community.

    `audit_events` is injectable for tests; in prod we pull from the
    customer's audit log directly.
    """

    events = audit_events
    if events is None:
        events = read_recent_audit_events(customer_id, limit=500) or []

    current = now if now is not None else time.time()
    cutoff = current - ROLLING_WINDOW_HOURS * 3600

    drafts: list[dict] = []
    for ev in events:
        if ev.get("event_type") not in _DRAFT_AUDIT_EVENT_TYPES:
            continue
        payload = ev.get("payload") or {}
        if payload.get("community_id") != community_id:
            continue
        ts = _event_epoch(ev)
        if ts is None or ts < cutoff:
            continue
        drafts.append({
            "ts": ts,
            "text_preview": str(
                payload.get("text_preview")
                or payload.get("draft_preview")
                or ""
            ).strip(),
        })

    drafts.sort(key=lambda d: d["ts"], reverse=True)
    daily_count = len(drafts)

    # Opening-phrase repetition over last N drafts.
    openings: list[str] = []
    for d in drafts[:LAST_DRAFTS_TO_INSPECT]:
        opening = _opening_phrase(d["text_preview"])
        if opening:
            openings.append(opening)
    counter = Counter(openings)
    repeated = tuple(
        (phrase, count) for phrase, count in counter.most_common()
        if count >= REPEAT_THRESHOLD
    )

    reasons: list[str] = []
    risk = "ok"
    if daily_count >= BLOCK_DAILY_COUNT:
        risk = "block"
        reasons.append(f"daily_count_{daily_count}>={BLOCK_DAILY_COUNT}")
    elif daily_count >= WARN_DAILY_COUNT:
        risk = "warn"
        reasons.append(f"daily_count_{daily_count}>={WARN_DAILY_COUNT}")
    if repeated:
        # Repetition is always at least a warn; if combined with high
        # count it remains block (we don't downgrade).
        if risk == "ok":
            risk = "warn"
        for phrase, count in repeated:
            reasons.append(f"repeated_opening:{phrase}×{count}")

    return BotPatternVerdict(
        risk=risk,
        daily_draft_count=daily_count,
        repeated_openings=repeated,
        reasons=tuple(reasons),
    )


_HAN_RE = re.compile(r"[一-鿿]")


def _opening_phrase(text: str) -> str:
    """Return the first OPENING_PROBE_CHARS Han characters of `text`,
    skipping leading whitespace / emoji / punctuation that wouldn't
    register as a stylistic opening to a human reader.
    """

    if not text:
        return ""
    han_chars: list[str] = []
    for ch in text:
        if _HAN_RE.match(ch):
            han_chars.append(ch)
            if len(han_chars) >= OPENING_PROBE_CHARS:
                break
    return "".join(han_chars)


def _event_epoch(event: dict) -> float | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None
