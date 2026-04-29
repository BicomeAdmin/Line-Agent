"""Cold-spell heartbeat alert for the operator.

Problem this solves
-------------------
The watcher/compose pipeline is correctly conservative — it refuses to draft
when there's no natural conversation thread to join (per Paul《私域流量》
"留量比流量重要"). For a community in `cold_spell` / `quiet` state that's
the right call: forcing a "嗨大家" message would break trust faster than
silence does.

But silence-without-signal is its own failure mode. If a community goes
4 days cold and the operator never hears about it, the group dies anyway
— not from over-engagement but from neglect. The operator needs a nudge:
*you* (the human running this community) should decide whether to seed a
topic, not because the bot found a conversation thread.

What this does
--------------
Daily heartbeat that pushes a Lark message to the operator listing every
enabled community whose most recent `community_chat_analyzed` audit event
shows `cold_spell` or `quiet` AND we haven't already alerted in the last
`alert_cooldown_hours`. The message is informational only — no draft,
no compose, just visibility. Operator decides what to do.

Piggybacks on existing audit signal (no extra ADB I/O). Communities that
have NO recent analyze event are reported as "stale signal" so the
operator notices that path itself is dead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.core.audit import append_audit_event, read_all_audit_events
from app.storage.config_loader import CommunityConfig, load_all_communities
from app.storage.paths import customer_data_root


# Communities in these states are candidates for a nudge. "trickle" is
# excluded — trickle means SOMETHING is happening (admin reminders, a
# few replies). Genuine silence is cold_spell / quiet.
_COLD_STATES = {"cold_spell", "quiet"}

# How recently the analyze must have run for its signal to be trusted.
# Older than this and we report the signal itself as stale rather than
# claiming the community is cold.
_FRESH_ANALYZE_HOURS = 12


@dataclass(frozen=True)
class CommunityAlertCandidate:
    customer_id: str
    community_id: str
    display_name: str
    state: str  # "cold_spell" | "quiet" | "stale_signal" | "no_signal"
    last_analyzed_iso: str | None
    hours_since_analyzed: float | None
    will_alert: bool  # False if cooldown not elapsed


@dataclass(frozen=True)
class HeartbeatResult:
    candidates: tuple[CommunityAlertCandidate, ...] = field(default_factory=tuple)
    alerted: tuple[CommunityAlertCandidate, ...] = field(default_factory=tuple)
    skipped_cooldown: tuple[CommunityAlertCandidate, ...] = field(default_factory=tuple)
    pushed_lark: bool = False


def _alert_marker_path(customer_id: str, community_id: str) -> Path:
    base = customer_data_root(customer_id) / "cold_spell_alerts"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{community_id}.txt"


def _last_analyze_event(events: list[dict[str, object]], community_id: str) -> dict[str, object] | None:
    for evt in reversed(events):
        if evt.get("event_type") != "community_chat_analyzed":
            continue
        payload = evt.get("payload") or {}
        if isinstance(payload, dict) and payload.get("community_id") == community_id:
            return evt
    return None


def _hours_between_iso(then_iso: str, now: datetime) -> float | None:
    try:
        then = datetime.fromisoformat(then_iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 3600.0


def _read_marker_epoch(path: Path) -> float | None:
    if not path.exists():
        return None
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _classify_candidate(
    community: CommunityConfig,
    events: list[dict[str, object]],
    now: datetime,
) -> CommunityAlertCandidate:
    evt = _last_analyze_event(events, community.community_id)
    if evt is None:
        return CommunityAlertCandidate(
            customer_id=community.customer_id,
            community_id=community.community_id,
            display_name=community.display_name,
            state="no_signal",
            last_analyzed_iso=None,
            hours_since_analyzed=None,
            will_alert=False,
        )

    iso = str(evt.get("timestamp") or "")
    hours = _hours_between_iso(iso, now)
    if hours is None or hours > _FRESH_ANALYZE_HOURS:
        return CommunityAlertCandidate(
            customer_id=community.customer_id,
            community_id=community.community_id,
            display_name=community.display_name,
            state="stale_signal",
            last_analyzed_iso=iso or None,
            hours_since_analyzed=hours,
            will_alert=False,
        )

    payload = evt.get("payload") or {}
    state = str(payload.get("active_state") if isinstance(payload, dict) else "") or "unknown"
    if state not in _COLD_STATES:
        return CommunityAlertCandidate(
            customer_id=community.customer_id,
            community_id=community.community_id,
            display_name=community.display_name,
            state=state,
            last_analyzed_iso=iso,
            hours_since_analyzed=hours,
            will_alert=False,
        )

    return CommunityAlertCandidate(
        customer_id=community.customer_id,
        community_id=community.community_id,
        display_name=community.display_name,
        state=state,
        last_analyzed_iso=iso,
        hours_since_analyzed=hours,
        will_alert=True,
    )


def _format_lark_message(alerted: Iterable[CommunityAlertCandidate]) -> str:
    lines = [
        "🥶 冷群心跳警示",
        "",
        "以下社群已在沉默狀態，建議你考慮 seed 一個話題（人決定，不是 bot）：",
        "",
    ]
    for c in alerted:
        hrs = f"{c.hours_since_analyzed:.0f}h" if c.hours_since_analyzed is not None else "?"
        lines.append(f"• {c.community_id} — {c.display_name}（{c.state}, signal age {hrs}）")
    lines.extend([
        "",
        "—",
        "提醒：留量比流量重要。冷群的解法不是「擬一句招呼」，",
        "是你最近真的有什麼想跟這群分享的內容。",
        "決定要 seed 時，在 Lark 對 bot 說：「在 {community_id} 發 [你的內容]」",
    ])
    return "\n".join(lines)


def run_heartbeat(
    *,
    alert_cooldown_hours: float = 24.0,
    now: datetime | None = None,
    push_lark: bool = True,
    communities: Iterable[CommunityConfig] | None = None,
) -> HeartbeatResult:
    moment = now or datetime.now(timezone.utc)
    moment_epoch = moment.timestamp()
    src = list(communities) if communities is not None else load_all_communities()

    candidates: list[CommunityAlertCandidate] = []
    alerted: list[CommunityAlertCandidate] = []
    skipped: list[CommunityAlertCandidate] = []

    # Cache audit reads per customer_id so we don't read the same file once
    # per community.
    audit_cache: dict[str, list[dict[str, object]]] = {}

    for community in src:
        if community.customer_id not in audit_cache:
            try:
                audit_cache[community.customer_id] = read_all_audit_events(community.customer_id)
            except Exception:  # noqa: BLE001
                audit_cache[community.customer_id] = []

        candidate = _classify_candidate(community, audit_cache[community.customer_id], moment)
        candidates.append(candidate)
        if not candidate.will_alert:
            continue

        marker = _alert_marker_path(community.customer_id, community.community_id)
        last_alert = _read_marker_epoch(marker)
        if last_alert is not None and (moment_epoch - last_alert) < (alert_cooldown_hours * 3600):
            skipped.append(candidate)
            continue

        marker.write_text(str(moment_epoch), encoding="utf-8")
        append_audit_event(
            community.customer_id,
            "cold_spell_alert_marked",
            {
                "community_id": community.community_id,
                "state": candidate.state,
                "hours_since_analyzed": candidate.hours_since_analyzed,
            },
        )
        alerted.append(candidate)

    pushed = False
    if push_lark and alerted:
        pushed = _push_lark(alerted)

    return HeartbeatResult(
        candidates=tuple(candidates),
        alerted=tuple(alerted),
        skipped_cooldown=tuple(skipped),
        pushed_lark=pushed,
    )


def _push_lark(alerted: list[CommunityAlertCandidate]) -> bool:
    try:
        from app.lark.client import LarkClient, LarkClientError
        from app.lark.notifier import operator_chat_id
    except ImportError:
        return False

    chat_id = operator_chat_id()
    if not chat_id:
        return False

    text = _format_lark_message(alerted)
    try:
        client = LarkClient()
        client.send_message(chat_id, "text", {"text": text}, receive_id_type="chat_id")
    except LarkClientError:
        return False
    except Exception:  # noqa: BLE001
        return False
    return True
