"""Persona context bundle — one-stop load of "who am I, where am I, what
have I said here lately" for the LLM brain.

Conceptually: every (customer × community) intersection has a persona.
The voice profile defines the static side (nickname, personality, tone
anchors, off-limits); the audit log holds the dynamic side (what the
operator has actually posted in this community recently). Composing
without that full picture is how the bot ends up writing things the
operator never said in a stance the operator never took.

This bundle is what `get_persona_context` MCP tool returns, and the
bridge / watcher prompts make it a hard prerequisite before any
compose_and_send. The point: persistence — the rules don't have to be
re-explained every turn; the LLM loads the persona, echoes it back,
and only then drafts.

Read-only, no side effects.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.core.audit import read_recent_audit_events
from app.core.timezone import to_taipei_str
from app.storage.config_loader import load_community_config, load_customer_config
from app.storage.paths import voice_profile_path


def get_persona_context(
    customer_id: str,
    community_id: str,
    *,
    self_posts_lookback_hours: float = 24 * 7,
    self_posts_limit: int = 15,
) -> dict[str, object]:
    """Bundle account + community + persona + recent self-posts.

    The result is structured (so the LLM can quote specific fields back
    to the operator) AND includes a Chinese one-line summary the LLM
    should echo verbatim before composing — that echo is the operator's
    confirmation that the right persona was loaded.
    """

    try:
        customer = load_customer_config(customer_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"customer_lookup_failed:{exc}"}

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    vp_path = voice_profile_path(customer_id, community_id)
    voice_profile_text = ""
    voice_profile_loaded = False
    if vp_path.exists():
        try:
            voice_profile_text = vp_path.read_text(encoding="utf-8")
            voice_profile_loaded = True
        except OSError:
            pass

    # Operator's nickname in THIS community is the authoritative source.
    # Falls back to whatever was extracted from voice_profile.md only if
    # operator_nickname isn't yet configured in the community YAML.
    nickname = (
        getattr(community, "operator_nickname", None)
        or _extract_nickname(voice_profile_text)
    )
    # Additional operator identities in this community (e.g. internal
    # test account). reply_target_selector dedupes on this list to avoid
    # scoring operator's own historical messages as reply targets.
    aliases = tuple(getattr(community, "operator_aliases", ()) or ())
    personality = _extract_personality(voice_profile_text)
    off_limits = _extract_off_limits(voice_profile_text)
    style_anchors = _extract_section(voice_profile_text, "Style anchors")

    recent_self_posts = list(_recent_self_posts(
        customer_id,
        community_id,
        lookback_hours=self_posts_lookback_hours,
        limit=self_posts_limit,
    ))

    # If we know operator_nickname AND there's a member-fingerprint cache
    # (built from imported chat exports), augment with the operator's
    # historical recent_lines. send_attempt audit only captures messages
    # that went through OUR system; chat_export covers everything the
    # operator said in this community.
    if nickname:
        try:
            from app.workflows.member_fingerprint import get_member_fingerprint
            fp = get_member_fingerprint(customer_id, community_id, nickname)
            if fp and isinstance(fp.get("recent_lines"), list):
                seen_texts = {p.get("text") for p in recent_self_posts}
                for line in fp["recent_lines"]:
                    if not line or line in seen_texts:
                        continue
                    recent_self_posts.append({
                        "ts_epoch": None,
                        "ts_taipei": "(from chat export)",
                        "text": line,
                    })
                    if len(recent_self_posts) >= self_posts_limit:
                        break
        except Exception:  # noqa: BLE001 — augmentation is best-effort
            pass

    summary_zh = _build_summary(
        customer_display=customer.display_name,
        community_display=community.display_name,
        community_id=community_id,
        nickname=nickname,
        personality=personality,
        recent_self_posts=recent_self_posts,
    )

    # Top KOC candidates for this community — bot uses these to know
    # who's high-leverage when scoring reply targets / composing.
    koc_candidates: list[dict[str, object]] = []
    try:
        from app.workflows.relationship_graph import load_relationship_graph
        graph = load_relationship_graph(customer_id, community_id)
        if graph and graph.get("koc_candidates"):
            koc_candidates = graph["koc_candidates"][:5]  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        pass

    # Recent operator edits — Paul's "實時回饋優化". Bot uses these
    # as in-context learning to mimic operator's preferences.
    recent_edits: list[dict[str, object]] = []
    edit_lessons_zh = ""
    try:
        from app.workflows.edit_feedback import load_recent_edits, render_for_prompt
        recent_edits = load_recent_edits(customer_id, community_id, limit=5)
        if recent_edits:
            edit_lessons_zh = render_for_prompt(recent_edits)
    except Exception:  # noqa: BLE001
        pass

    return {
        "status": "ok",
        "summary_zh": summary_zh,
        "account": {
            "customer_id": customer_id,
            "display_name": customer.display_name,
        },
        "community": {
            "community_id": community_id,
            "display_name": community.display_name,
            "persona_name": community.persona,
        },
        "voice_profile": {
            "loaded": voice_profile_loaded,
            "path": str(vp_path),
            "nickname": nickname,
            "aliases": list(aliases),
            "personality": personality,
            "style_anchors": style_anchors,
            "off_limits": off_limits,
            "raw_markdown": voice_profile_text,
        },
        "recent_self_posts": recent_self_posts,
        "koc_candidates": koc_candidates,  # top 5 high-leverage members
        "recent_edits": recent_edits,
        "edit_lessons_zh": edit_lessons_zh,  # ready-to-paste prompt section
    }


# ──────────────────────────────────────────────────────────────────────
# Voice profile extraction
# ──────────────────────────────────────────────────────────────────────

def _extract_section(text: str, header_substring: str) -> str:
    """Return the body of the first markdown section whose `## ...`
    header contains the given substring. Body ends at the next `##`."""

    if not text:
        return ""
    lines = text.splitlines()
    in_section = False
    body: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            if header_substring in stripped:
                in_section = True
                continue
        elif in_section:
            body.append(line)
    return "\n".join(body).strip()


def _extract_nickname(text: str) -> str:
    section = _extract_section(text, "nickname")
    if not section:
        return ""
    # Take first non-empty bullet line, strip "- " prefix and parens.
    for line in section.splitlines():
        s = line.strip().lstrip("-").strip()
        if not s or s.startswith("(") or s.startswith("（") or s.startswith("（請"):
            continue
        return s.split("（")[0].strip()[:40]
    return ""


def _extract_personality(text: str) -> str:
    section = _extract_section(text, "personality") or _extract_section(text, "個性")
    if not section:
        return ""
    # First non-placeholder line is fine.
    for line in section.splitlines():
        s = line.strip().lstrip("-").strip()
        if not s:
            continue
        if "（請" in s or "（操作員" in s:
            continue
        return s[:120]
    return ""


def _extract_off_limits(text: str) -> list[str]:
    section = _extract_section(text, "Off-limits")
    if not section:
        return []
    items: list[str] = []
    for line in section.splitlines():
        s = line.strip().lstrip("-").strip()
        if not s or s.startswith("（") or "（操作員" in s:
            continue
        items.append(s[:120])
    return items


# ──────────────────────────────────────────────────────────────────────
# Recent self-posts from audit log
# ──────────────────────────────────────────────────────────────────────

def _recent_self_posts(
    customer_id: str,
    community_id: str,
    *,
    lookback_hours: float,
    limit: int,
) -> Iterable[dict[str, object]]:
    cutoff = time.time() - lookback_hours * 3600
    # We pull a generous window from audit; the file is per-customer not
    # per-community, so we filter as we go.
    raw = read_recent_audit_events(customer_id, limit=500) or []
    out: list[dict[str, object]] = []
    for event in raw:
        if event.get("event_type") != "send_attempt":
            continue
        payload = event.get("payload") or {}
        if payload.get("community_id") != community_id:
            continue
        if str(payload.get("status") or "") != "sent":
            continue
        ts = _event_epoch(event)
        if ts is None or ts < cutoff:
            continue
        text = (payload.get("text") or payload.get("draft_text") or "").strip()
        if not text:
            continue
        out.append({
            "ts_epoch": ts,
            "ts_taipei": to_taipei_str(event.get("timestamp")),
            "text": text,
        })
    out.sort(key=lambda x: x["ts_epoch"], reverse=True)
    return out[:limit]


def _event_epoch(event: dict[str, object]) -> float | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────────────────
# Summary line — what the LLM echoes back to the operator
# ──────────────────────────────────────────────────────────────────────

def _build_summary(
    *,
    customer_display: str,
    community_display: str,
    community_id: str,
    nickname: str,
    personality: str,
    recent_self_posts: list[dict[str, object]],
) -> str:
    """The one-line zh summary the LLM should echo before composing.

    Format:
      在「<community>」({community_id})，你是 <customer> ─ 暱稱「<nickname>」，
      個性「<personality 短摘>」。最近講過 N 句，最後一句：「<latest>」。
    """

    parts = [f"在「{community_display}」({community_id})，你是 {customer_display}"]
    if nickname:
        parts.append(f"暱稱「{nickname}」")
    if personality:
        parts.append(f"個性「{personality[:40]}」")
    head = " ─ ".join(parts)

    if recent_self_posts:
        latest = (recent_self_posts[0].get("text") or "")[:50]
        tail = f"最近 {len(recent_self_posts)} 天送過 {len(recent_self_posts)} 句，最後一句：「{latest}」"
    else:
        tail = "最近沒有送出紀錄（這是新社群或沒接過話）"
    return f"{head}。{tail}。"
