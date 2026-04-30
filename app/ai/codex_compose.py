"""Composer backed by Codex (ChatGPT Pro subscription) — the production
LLM compose path for autonomous watch ticks.

Why Codex (not Anthropic API):
  CLAUDE.md §8 — subscription-backed, 0 token cost. The Anthropic API
  path (`app.ai.llm_client`) is kept dormant for budget reasons; the
  AUP classifier on `claude -p` also flags the LINE-automation tool
  surface (see CLAUDE.md §8 for the full story). Codex has no
  equivalent client-side classifier on user MCP, so HIL-gated drafts
  flow end-to-end.

Inputs (rich, not just raw messages):
  - VoiceProfile (parsed frontmatter + sections — nickname, value
    proposition, route_mix, stage, off_limits, etc.)
  - TargetCandidate from reply_target_selector (who to reply to,
    why, with what score)
  - MemberFingerprint of that target (length / emoji / particles)
  - Recent thread excerpt (last N messages)
  - Operator's recent self-posts (so we mirror their voice, not
    invent a new one)

Output: ComposerOutput with should_engage / draft / rationale /
confidence / off_limits_hit. Caller stages this as a ReviewRecord —
HIL gate unchanged.

Failure modes (all → ComposerUnavailable, caller should skip):
  - codex CLI not installed / not logged in
  - codex run timed out
  - JSON parse failure (LLM ignored output schema)
  - voice_profile incomplete (refuse rather than draft from empty
    placeholders — surfaces the missing slot to the operator)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.ai.voice_profile_v2 import VoiceProfile


PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "composer_v1.md"
BRAND_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "composer_brand_v1.md"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ComposerUnavailable(RuntimeError):
    """Raised when Codex compose cannot produce a draft. Caller should skip."""


@dataclass(frozen=True)
class ComposerOutput:
    should_engage: bool
    rationale: str
    draft: str
    confidence: float
    off_limits_hit: str | None
    raw_text: str  # full codex stdout for audit


def is_enabled() -> bool:
    """Composer is on only when env switch + per-community gate align.

    Per-community gate is checked by the caller (it has the
    CommunityConfig). This function only checks the global env.
    """

    return os.getenv("ECHO_COMPOSE_BACKEND", "rule").strip().lower() == "codex"


def compose_via_codex(
    *,
    voice_profile: VoiceProfile,
    community_name: str,
    target_sender: str,
    target_message: str,
    target_score: float,
    target_threshold: float,
    target_reasons: Sequence[str],
    target_fingerprint: dict | None,
    thread_excerpt: Sequence[dict],
    recent_self_posts: Sequence[str],
    target_ts_epoch: float | None = None,
    now_epoch: float | None = None,
    timeout_seconds: int = 90,
) -> ComposerOutput:
    """Compose a draft via codex exec, return structured output.

    Refuses (raises ComposerUnavailable) if voice_profile.is_complete
    is False — this is the §0.5.6 gate. Operator must populate the
    profile before LLM drafts go live.

    target_ts_epoch / now_epoch: if both provided, the prompt will
    display target's age (minutes_ago) AND a staleness gate rule —
    'if target > 180min old AND thread has moved on, should_engage=
    false with rationale 話題已過時'. Without timestamps, the LLM
    falls back to content-only judgment (legacy behavior).
    """

    if not voice_profile.is_complete:
        raise ComposerUnavailable(
            f"voice_profile_incomplete:{','.join(voice_profile.missing_fields)}"
        )

    prompt = _build_prompt(
        voice_profile=voice_profile,
        community_name=community_name,
        target_sender=target_sender,
        target_message=target_message,
        target_score=target_score,
        target_threshold=target_threshold,
        target_reasons=target_reasons,
        target_fingerprint=target_fingerprint or {},
        thread_excerpt=thread_excerpt,
        recent_self_posts=recent_self_posts,
        target_ts_epoch=target_ts_epoch,
        now_epoch=now_epoch,
    )

    raw_text = _run_codex(prompt, timeout_seconds=timeout_seconds)
    return _parse_output(raw_text)


def _build_prompt(
    *,
    voice_profile: VoiceProfile,
    community_name: str,
    target_sender: str,
    target_message: str,
    target_score: float,
    target_threshold: float,
    target_reasons: Sequence[str],
    target_fingerprint: dict,
    thread_excerpt: Sequence[dict],
    recent_self_posts: Sequence[str],
    target_ts_epoch: float | None = None,
    now_epoch: float | None = None,
) -> str:
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    # Format thread excerpt: "[sender · X 分鐘前] text"
    thread_lines = []
    for msg in thread_excerpt:
        sender = str(msg.get("sender") or "?").strip()
        text = str(msg.get("text") or "").strip()
        if not text:
            continue
        age = _format_age(msg.get("ts_epoch"), now_epoch)
        prefix = f"[{sender}{(' · ' + age) if age else ''}]"
        thread_lines.append(f"  - {prefix} {text}")
    thread_text = "\n".join(thread_lines) or "  - （無訊息）"

    target_age_text = _format_age(target_ts_epoch, now_epoch) or "（時間不詳——當作可能已過時）"
    last_activity_age_text = _last_activity_age(thread_excerpt, now_epoch) or "（時間不詳）"

    self_posts_text = "\n".join(f"  - {s.strip()}" for s in recent_self_posts if s.strip()) or "  - （未匯入操作員歷史發言）"

    # Fingerprint detail
    avg_len = target_fingerprint.get("avg_length") if target_fingerprint else None
    emoji_rate = target_fingerprint.get("emoji_rate") if target_fingerprint else None
    tail_particles = target_fingerprint.get("top_ending_particles") if target_fingerprint else None
    target_recent = target_fingerprint.get("recent_lines") if target_fingerprint else None
    target_recent_text = "\n".join(f"    - {s}" for s in (target_recent or [])[:5]) or "    - （無歷史語料）"

    reasons_text = ", ".join(target_reasons) or "（無）"

    # Use simple replace (not str.format) — the template has JSON braces
    # that .format() misinterprets even with {{ }} escaping if any new
    # placeholders are added later. Replace is safer for prompt files.
    replacements = {
        "{operator_nickname}": voice_profile.nickname or "（未設定）",
        "{community_name}": community_name,
        "{value_proposition}": voice_profile.value_proposition or "（未設定）",
        "{route_ip}": f"{voice_profile.route_mix.ip:.0%}",
        "{route_interest}": f"{voice_profile.route_mix.interest:.0%}",
        "{route_info}": f"{voice_profile.route_mix.info:.0%}",
        "{route_dominant}": voice_profile.route_mix.dominant(),
        "{stage}": voice_profile.stage or "（未設定）",
        "{stage_objective}": voice_profile.stage_objective,
        "{engagement_appetite}": voice_profile.engagement_appetite,
        "{personality}": voice_profile.personality or "（未設定）",
        "{style_anchors}": voice_profile.style_anchors or "（未設定）",
        "{recent_self_posts}": self_posts_text,
        "{off_limits}": voice_profile.off_limits or "（未設定，預設不接任何具爭議話題）",
        "{thread_size}": str(len(thread_excerpt)),
        "{thread_excerpt}": thread_text,
        "{target_sender}": target_sender or "（未知）",
        "{target_message}": target_message or "（空訊息）",
        "{target_score}": f"{target_score:.2f}",
        "{target_threshold}": f"{target_threshold:.2f}",
        "{target_reasons}": reasons_text,
        "{target_avg_length}": f"{avg_len:.1f}" if isinstance(avg_len, (int, float)) else "（無）",
        "{target_emoji_rate}": f"{emoji_rate:.2%}" if isinstance(emoji_rate, (int, float)) else "（無）",
        "{target_tail_particles}": ", ".join(tail_particles) if isinstance(tail_particles, list) and tail_particles else "（無）",
        "{target_recent_lines}": target_recent_text,
        "{target_age}": target_age_text,
        "{last_activity_age}": last_activity_age_text,
    }
    out = template
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


def _format_age(ts_epoch: float | None, now_epoch: float | None) -> str:
    """Human-friendly age string ('3 分鐘前' / '4 小時前' / '昨天'). Returns
    empty string when either timestamp is missing — caller decides what
    fallback wording to use.
    """

    if ts_epoch is None or now_epoch is None:
        return ""
    delta = max(0.0, now_epoch - ts_epoch)
    minutes = delta / 60
    if minutes < 1:
        return "剛剛"
    if minutes < 60:
        return f"{int(minutes)} 分鐘前"
    hours = minutes / 60
    if hours < 24:
        return f"{hours:.1f} 小時前".replace(".0", "")
    days = hours / 24
    if days < 2:
        return "昨天"
    return f"{int(days)} 天前"


def _last_activity_age(thread_excerpt: Sequence[dict], now_epoch: float | None) -> str:
    """Age of the most recent NON-SELF message in the thread.

    Used to express community 'temperature' to the LLM. Operator's own
    bubbles don't count as group activity — the operator typing alone
    while no member responds is exactly the kind of "ghost-town" feel
    we should NOT bot-reinforce by chatting into the void.
    """

    if now_epoch is None or not thread_excerpt:
        return ""
    latest = None
    for msg in thread_excerpt:
        if msg.get("is_self"):
            continue
        ts = msg.get("ts_epoch")
        if isinstance(ts, (int, float)):
            if latest is None or ts > latest:
                latest = ts
    if latest is None:
        return ""
    return _format_age(latest, now_epoch)


def compose_brand_post_via_codex(
    *,
    voice_profile: VoiceProfile,
    community_name: str,
    brief: str,
    thread_excerpt: Sequence[dict],
    recent_self_posts: Sequence[str],
    now_epoch: float | None = None,
    timeout_seconds: int = 90,
) -> ComposerOutput:
    """Compose a brand-mode (proactive, non-reply) post via codex.

    Same gating as `compose_via_codex`: refuses with ComposerUnavailable
    when voice_profile is incomplete. Output schema is identical so the
    caller flow (review_store + lint + audit) is unchanged.

    `thread_excerpt` + `now_epoch` together feed a temperature signal
    into the prompt so the LLM can refuse a brand post that would land
    on a hot unrelated thread, or judge whether a quiet group is even
    worth seeding right now.
    """

    if not voice_profile.is_complete:
        raise ComposerUnavailable(
            f"voice_profile_incomplete:{','.join(voice_profile.missing_fields)}"
        )
    if not (brief or "").strip():
        raise ComposerUnavailable("brand_compose_requires_brief")

    prompt = _build_brand_prompt(
        voice_profile=voice_profile,
        community_name=community_name,
        brief=brief,
        thread_excerpt=thread_excerpt,
        recent_self_posts=recent_self_posts,
        now_epoch=now_epoch,
    )

    raw_text = _run_codex(prompt, timeout_seconds=timeout_seconds)
    return _parse_output(raw_text)


def _build_brand_prompt(
    *,
    voice_profile: VoiceProfile,
    community_name: str,
    brief: str,
    thread_excerpt: Sequence[dict],
    recent_self_posts: Sequence[str],
    now_epoch: float | None = None,
) -> str:
    template = BRAND_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

    thread_lines = []
    for msg in thread_excerpt:
        sender = str(msg.get("sender") or "?").strip()
        text = str(msg.get("text") or "").strip()
        if not text:
            continue
        age = _format_age(msg.get("ts_epoch"), now_epoch)
        prefix = f"[{sender}{(' · ' + age) if age else ''}]"
        thread_lines.append(f"  - {prefix} {text}")
    thread_text = "\n".join(thread_lines) or "  - （無近期對話可參考）"

    self_posts_text = "\n".join(
        f"  - {s.strip()}" for s in recent_self_posts if s.strip()
    ) or "  - （未匯入操作員歷史發言）"

    last_activity_age = _last_activity_age(thread_excerpt, now_epoch) or "（時間不詳）"
    temperature = _community_temperature(thread_excerpt, now_epoch)

    replacements = {
        "{operator_nickname}": voice_profile.nickname or "（未設定）",
        "{community_name}": community_name,
        "{value_proposition}": voice_profile.value_proposition or "（未設定）",
        "{route_ip}": f"{voice_profile.route_mix.ip:.0%}",
        "{route_interest}": f"{voice_profile.route_mix.interest:.0%}",
        "{route_info}": f"{voice_profile.route_mix.info:.0%}",
        "{route_dominant}": voice_profile.route_mix.dominant(),
        "{stage}": voice_profile.stage or "（未設定）",
        "{stage_objective}": voice_profile.stage_objective,
        "{engagement_appetite}": voice_profile.engagement_appetite,
        "{personality}": voice_profile.personality or "（未設定）",
        "{style_anchors}": voice_profile.style_anchors or "（未設定）",
        "{recent_self_posts}": self_posts_text,
        "{off_limits}": voice_profile.off_limits or "（未設定，預設不接任何具爭議話題）",
        "{thread_size}": str(len(thread_excerpt)),
        "{thread_excerpt}": thread_text,
        "{brief}": brief.strip(),
        "{last_activity_age}": last_activity_age,
        "{community_temperature}": temperature,
    }
    out = template
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


def _community_temperature(thread_excerpt: Sequence[dict], now_epoch: float | None) -> str:
    """Categorize current chat temperature for the brand-mode prompt.

    Returns one of: 熱絡 / 溫熱 / 漸冷 / 沉寂 / 未知. Operator self-posts
    are excluded — only OTHERS' messages count toward heat.

    熱絡: ≥3 non-self messages in last 30min
    溫熱: ≥1 non-self message in last 30min
    漸冷: latest non-self message is 30-180min old
    沉寂: latest non-self message is >180min old (or none in window)
    """

    if now_epoch is None or not thread_excerpt:
        return "未知（無時間資訊）"
    others_with_ts = [
        float(m.get("ts_epoch"))
        for m in thread_excerpt
        if not m.get("is_self") and isinstance(m.get("ts_epoch"), (int, float))
    ]
    if not others_with_ts:
        return "未知（thread 中無他人時間戳）"
    others_with_ts.sort(reverse=True)
    latest = others_with_ts[0]
    age_min = (now_epoch - latest) / 60.0
    in_30min = sum(1 for ts in others_with_ts if (now_epoch - ts) / 60.0 <= 30)
    if age_min <= 30 and in_30min >= 3:
        return "熱絡（30 分鐘內多人在說話）"
    if age_min <= 30:
        return "溫熱（30 分鐘內有人說話）"
    if age_min <= 180:
        return f"漸冷（最後活動 {int(age_min)} 分鐘前，群裡沒人在線）"
    return f"沉寂（最後活動 {int(age_min)} 分鐘前）"


def _run_codex(prompt: str, *, timeout_seconds: int) -> str:
    """Spawn `codex exec` headless, return last assistant message.

    Mirrors the bridge's _run_codex helper (start_lark_long_connection.py)
    — kept as a separate function so prompt-engineering changes don't
    couple to the bridge's input shape (the bridge sends conversational
    user text; this sends a fully-rendered template).
    """

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False, encoding="utf-8") as fh:
        last_msg_path = fh.name

    cmd = [
        "codex", "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--cd", str(PROJECT_ROOT),
        "--output-last-message", last_msg_path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ComposerUnavailable(f"codex timeout after {timeout_seconds}s") from exc
    except FileNotFoundError as exc:
        raise ComposerUnavailable("codex CLI not found on PATH") from exc

    if proc.returncode != 0:
        raise ComposerUnavailable(
            f"codex exit {proc.returncode}: {(proc.stderr or '')[:300]}"
        )

    try:
        last_msg = Path(last_msg_path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ComposerUnavailable(f"codex output read failed: {exc}") from exc
    finally:
        try:
            os.unlink(last_msg_path)
        except OSError:
            pass

    if not last_msg:
        raise ComposerUnavailable("codex returned empty last_message")
    return last_msg


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_output(raw_text: str) -> ComposerOutput:
    payload = _extract_json(raw_text)

    should_engage_raw = payload.get("should_engage")
    if isinstance(should_engage_raw, bool):
        should_engage = should_engage_raw
    elif isinstance(should_engage_raw, str):
        should_engage = should_engage_raw.strip().lower() in {"true", "yes", "1"}
    else:
        raise ComposerUnavailable(f"invalid should_engage: {should_engage_raw!r}")

    confidence_raw = payload.get("confidence", 0.5)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    off_limits_hit = payload.get("off_limits_hit")
    if isinstance(off_limits_hit, str):
        off_limits_hit = off_limits_hit.strip() or None
    elif off_limits_hit is not None:
        off_limits_hit = None

    draft = str(payload.get("draft") or "").strip()
    rationale = str(payload.get("rationale") or "").strip()

    if should_engage and not draft:
        raise ComposerUnavailable("should_engage=true but draft is empty")

    return ComposerOutput(
        should_engage=should_engage,
        rationale=rationale or "（未說明）",
        draft=draft,
        confidence=confidence,
        off_limits_hit=off_limits_hit,
        raw_text=raw_text,
    )


def _extract_json(raw_text: str) -> dict:
    text = raw_text.strip()
    fenced = _JSON_FENCE_RE.search(text)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ComposerUnavailable("composer response has no JSON object")
        candidate = text[start : end + 1]
    try:
        loaded = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ComposerUnavailable(f"composer JSON parse failed: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ComposerUnavailable("composer JSON is not an object")
    return loaded
