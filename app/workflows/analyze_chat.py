"""Watcher Mode — Phase 1: read + curate a community's recent chat.

This workflow does NOT make the engagement decision itself. It returns a
structured signal that the LLM brain (in the Lark bridge) can reason over:
state classification, last unanswered question, sensitivity flags, and a
condensed view of the messages. The brain then decides whether to draft.

Why curated, not raw: dumping 20 raw chat messages into the LLM's context
wastes tokens and hides the signal. Lightweight pre-classification (cold /
active / unanswered question) lets the LLM focus on the operator-facing
decision and message draft.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from app.adb.client import AdbClient
from app.core.audit import append_audit_event
from app.storage.config_loader import load_community_config
from app.storage.paths import default_raw_xml_path
from app.storage.voice_profiles import get_voice_profile
from app.workflows.openchat_navigate import navigate_to_openchat
from app.workflows.read_chat import read_recent_chat


QUESTION_MARKERS = ("?", "？", "請問", "想問", "有人知道", "有人會", "有人有")
COLD_SPELL_HOURS = 4
ACTIVE_WINDOW_MINUTES = 5
MODERATE_WINDOW_MINUTES = 30
TRICKLE_WINDOW_MINUTES = 120
UNANSWERED_QUESTION_GRACE_MINUTES = 15
TIME_PATTERN = re.compile(r"^(上午|下午)?\s*(\d{1,2}):(\d{2})$")


@dataclass
class _ClassifiedState:
    label: str
    notes: str
    last_message_minutes_ago: float | None


def analyze_chat(
    customer_id: str,
    community_id: str,
    *,
    limit: int = 20,
    skip_navigate: bool = False,
) -> dict[str, object]:
    """Read + classify a community's chat. Designed for the LLM brain to reason over."""

    community = load_community_config(customer_id, community_id)
    trace: list[dict[str, object]] = []

    # 1) Navigate (deep link if available). Operator may pass skip_navigate=True
    # if they know LINE is already on the right room.
    if not skip_navigate:
        nav = navigate_to_openchat(customer_id, community_id, overall_timeout_seconds=20.0)
        trace.append({"step": "navigate", "status": nav.get("status"), "reason": nav.get("reason")})
        if nav.get("status") != "ok":
            append_audit_event(
                customer_id,
                "community_chat_analyzed",
                {"community_id": community_id, "status": "blocked", "reason": "navigate_failed"},
            )
            return {
                "status": "blocked",
                "reason": "navigate_failed",
                "navigate_result": nav,
                "trace": trace,
            }

    # 2) Read recent chat.
    try:
        messages = read_recent_chat(
            AdbClient(device_id=community.device_id),
            default_raw_xml_path(customer_id),
            limit=limit,
        )
    except RuntimeError as exc:
        return {
            "status": "blocked",
            "reason": "read_failed",
            "detail": str(exc),
            "trace": trace,
        }
    trace.append({"step": "read", "message_count": len(messages)})

    # 3) Classify state (cold / active / moderate / trickle).
    state = _classify_state(messages)

    # 4) Detect last unanswered question.
    unanswered = _detect_unanswered_question(messages)

    # 5) Voice profile (Off-limits + Tone notes go back to LLM for drafting decisions).
    profile = get_voice_profile(customer_id, community_id)

    # 6) Sensitivity flags from profile Off-limits keywords (best-effort).
    sensitivity = _scan_sensitivity(messages, profile)

    result = {
        "status": "ok",
        "customer_id": customer_id,
        "community_id": community_id,
        "community_name": community.display_name,
        "message_count": len(messages),
        "active_state": state.label,
        "active_state_note": state.notes,
        "last_message_minutes_ago": state.last_message_minutes_ago,
        "unanswered_question": unanswered,
        "sensitivity_hits": sensitivity,
        "voice_profile_loaded": bool(profile.get("loaded")),
        "voice_profile_excerpt": _profile_excerpt(profile),
        "recent_messages": [
            {
                "text": str(msg.get("text", ""))[:300],
                "sender": msg.get("sender") or "unknown",
            }
            for msg in messages[-12:]
        ],
        "trace": trace,
    }
    append_audit_event(
        customer_id,
        "community_chat_analyzed",
        {
            "community_id": community_id,
            "active_state": state.label,
            "message_count": len(messages),
            "unanswered_found": unanswered.get("found", False),
            "sensitivity_hits": len(sensitivity),
        },
    )
    return result


def _classify_state(messages: list[dict]) -> _ClassifiedState:
    """Best-effort state classifier.

    LINE chat dumps don't carry absolute timestamps reliably (we get strings
    like '下午 9:35'); we approximate using the timestamp of the most recent
    parseable time and message density. When timestamps aren't usable, we
    fall back to message count as a coarse signal.
    """

    if not messages:
        return _ClassifiedState(label="empty", notes="無近期訊息", last_message_minutes_ago=None)

    last_minutes = _minutes_since_last_message(messages)

    if last_minutes is None:
        # No parseable timestamps — use message density as fallback.
        if len(messages) >= 12:
            return _ClassifiedState(label="active", notes="訊息數多但無法解析時間戳", last_message_minutes_ago=None)
        if len(messages) >= 4:
            return _ClassifiedState(label="moderate", notes="無時間戳，依訊息數估計", last_message_minutes_ago=None)
        return _ClassifiedState(label="trickle", notes="無時間戳", last_message_minutes_ago=None)

    if last_minutes >= COLD_SPELL_HOURS * 60:
        return _ClassifiedState(label="cold_spell", notes=f"最後訊息 {last_minutes:.0f} 分鐘前", last_message_minutes_ago=last_minutes)
    if last_minutes <= ACTIVE_WINDOW_MINUTES:
        return _ClassifiedState(label="active", notes=f"最後訊息 {last_minutes:.0f} 分鐘前", last_message_minutes_ago=last_minutes)
    if last_minutes <= MODERATE_WINDOW_MINUTES:
        return _ClassifiedState(label="moderate", notes=f"最後訊息 {last_minutes:.0f} 分鐘前", last_message_minutes_ago=last_minutes)
    if last_minutes <= TRICKLE_WINDOW_MINUTES:
        return _ClassifiedState(label="trickle", notes=f"最後訊息 {last_minutes:.0f} 分鐘前", last_message_minutes_ago=last_minutes)
    return _ClassifiedState(label="quiet", notes=f"最後訊息 {last_minutes:.0f} 分鐘前", last_message_minutes_ago=last_minutes)


def _minutes_since_last_message(messages: list[dict]) -> float | None:
    """Find the latest parseable time string and compute minutes ago.

    LINE typically renders short timestamps like '下午 9:35' (no date for today)
    or absolute date for older. We only handle today's '上午 / 下午 HH:MM' form
    here; other formats return None.
    """

    now = time.localtime()
    now_minutes = now.tm_hour * 60 + now.tm_min
    for msg in reversed(messages):
        text = str(msg.get("text", "")).strip()
        # In our extraction the time often appears as a separate "message" item.
        m = TIME_PATTERN.match(text)
        if not m:
            continue
        ampm, hh, mm = m.groups()
        h = int(hh)
        m_int = int(mm)
        if ampm == "下午" and h < 12:
            h += 12
        if ampm == "上午" and h == 12:
            h = 0
        msg_minutes = h * 60 + m_int
        delta = now_minutes - msg_minutes
        if delta < 0:
            # Crossed midnight: treat as "yesterday" → at least 1 day
            delta += 24 * 60
        return float(delta)
    return None


def _detect_unanswered_question(messages: list[dict]) -> dict[str, object]:
    """Find the last message that looks like a question with no later reply."""

    last_question_idx = -1
    last_question_text = ""
    for idx, msg in enumerate(messages):
        text = str(msg.get("text", "")).strip()
        if not text:
            continue
        if any(marker in text for marker in QUESTION_MARKERS):
            last_question_idx = idx
            last_question_text = text
    if last_question_idx == -1:
        return {"found": False}

    # If question is the very last message, definitely unanswered.
    # Otherwise, check whether anything after looks like an answer (heuristic:
    # at least one substantive non-time message exists after it).
    follow_ups = [
        m for m in messages[last_question_idx + 1:]
        if str(m.get("text", "")).strip() and not TIME_PATTERN.match(str(m.get("text", "")).strip())
    ]
    if follow_ups:
        return {
            "found": True,
            "answered_likely": True,
            "question_text": last_question_text[:200],
            "follow_up_count": len(follow_ups),
        }
    return {
        "found": True,
        "answered_likely": False,
        "question_text": last_question_text[:200],
        "follow_up_count": 0,
    }


def _scan_sensitivity(messages: list[dict], profile: dict) -> list[dict[str, object]]:
    """Light keyword scan against profile Off-limits cues.

    Doesn't try to be smart — the LLM will do the real judgment. This is just
    a hint so the brain knows to be careful.
    """

    profile_text = str(profile.get("content") or "")
    if not profile_text:
        return []
    # Pull a few keywords out of the Off-limits section, if present.
    off_limits_section = ""
    lower = profile_text.lower()
    if "off-limits" in lower or "off limits" in lower:
        idx = lower.find("off-limits")
        if idx == -1:
            idx = lower.find("off limits")
        off_limits_section = profile_text[idx:idx + 400]
    if not off_limits_section:
        return []

    # Crude: extract bullet keywords (Chinese tokens 2-6 chars) for matching.
    bullets = re.findall(r"[一-龥]{2,6}", off_limits_section)
    bullets = list({b for b in bullets if len(b) >= 2})

    hits: list[dict[str, object]] = []
    for msg in messages:
        text = str(msg.get("text", ""))
        if not text:
            continue
        for kw in bullets:
            if kw in text:
                hits.append({"keyword": kw, "snippet": text[:80]})
                break
    return hits[:5]


def _profile_excerpt(profile: dict) -> str:
    """Compact view of the profile for inclusion in the LLM's context."""

    if not profile.get("loaded"):
        return profile.get("hint") or "（沒有 voice profile，使用預設口語短句。）"
    content = str(profile.get("content") or "")
    # Just send the first 800 chars — enough for tone + off-limits.
    return content[:800] + ("…" if len(content) > 800 else "")
