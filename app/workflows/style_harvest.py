"""Style learning workflows — make composed drafts match how the community
actually talks, not how the bootstrap voice profile says they should.

Two public surfaces:

  - `harvest_style_samples(customer_id, community_id, ...)`
      Reads recent chat, filters out announcement / system / link / very-short
      noise, scores remaining lines by "naturalness" (favors mid-length casual
      conversational lines), and appends top N to the community's
      voice_profile.md under a managed `## Observed community lines` block.
      Operator runs this when the voice profile is too sparse — typically
      once per onboarding, again every few weeks as the community drifts.

  - `fingerprint_conversation(messages)`
      Pure function over a list of `{sender, text, position}` dicts. Returns
      a small dict of style stats: median length, emoji rate, common opening
      words, common closing particles. Watch_tick injects this into the codex
      prompt so the auto-watch draft matches *current vibe*, not just the
      voice profile's static description.

Both deliberately stay rule-based + textual. We don't ship messages to an
external embedding service — the inputs are operator-curated chat that
shouldn't leave the local machine.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Iterable

from app.adb.client import AdbClient
from app.core.audit import append_audit_event
from app.storage.config_loader import load_community_config
from app.storage.paths import default_raw_xml_path, voice_profile_path
from app.workflows.openchat_navigate import navigate_to_openchat
from app.workflows.read_chat import read_recent_chat


# Phrases that smell like announcement / system / mod-broadcast — we want to
# learn from members, not from official posts. Conservative list; better to
# accept a noisy line than to over-filter and lose real samples.
_BROADCAST_HINTS = (
    "公告", "歡迎加入", "請各位", "提醒大家", "請大家", "本群規定",
    "管理員", "小編", "群主", "違反規定", "請遵守",
)

# UI labels / button text that bleeds into the dump. Conservative — only
# strings that almost never appear inside real chat messages.
_UI_NOISE_EXACT = frozenset({
    "瞭解詳情", "查看更多", "顯示更多", "顯示全部", "傳送", "送出", "加入",
    "貼圖", "相機", "相片", "影片", "聯絡資訊", "連結", "已加入聊天",
    "本尊", "我", "你", "他",  # bare pronouns from member badges
})
# Member-count badges like "(74)" / "（120）".
_MEMBER_COUNT_RE = re.compile(r"^[\(（]\s*\d{1,5}\s*[\)）]$")
# Lines that look like a member display-name suffix marker — e.g.
# "阿樂 本尊" / "Alice 本尊". Real members say "本尊" rarely; when it
# appears as a trailing token after a name-like token, it's the badge.
_MEMBER_BADGE_RE = re.compile(r"^\S{1,12}\s+本尊\s*$")
_LINK_RE = re.compile(r"https?://|line\.me|youtu\.be|youtube\.com|t\.me/", re.IGNORECASE)
_AT_MENTION_NOISE_RE = re.compile(r"^@\S+\s*$")  # bare @mention with nothing after

# UI-rendered timestamps / date headers / read-marker noise — uiautomator
# scrapes these alongside real chat lines, and they pollute samples.
_TIMESTAMP_NOISE_RE = re.compile(
    r"^("
    r"(?:上午|下午|AM|PM)\s*\d{1,2}[:：]\d{2}"  # 下午7:47 / AM 11:59
    r"|\d{1,2}[:：]\d{2}"                        # 19:47
    r"|\d{1,2}\s*月\s*\d{1,2}\s*日(?:\s*週[一二三四五六日天])?"  # 4月2日 / 4月2日 週四
    r"|\d{4}\s*[/\-]\s*\d{1,2}\s*[/\-]\s*\d{1,2}"  # 2026-04-28
    r"|週[一二三四五六日天]"                      # 週四
    r"|星期[一二三四五六日天]"
    r"|昨天|今天|前天|明天|稍早"
    r"|傳送中|已傳送"
    r"|已讀(?:\s*\d+)?|未讀(?:\s*\d+)?"           # 已讀 / 已讀 3
    r"|\d+\s*人(?:已讀|看過)?"                    # "5 人已讀"
    r")\s*$"
)

# Emoji detection: Unicode ranges covering common emoji + symbols + dingbats.
# Not exhaustive (skin-tone modifiers etc.) but good enough for ratio stats.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"  # symbols & pictographs, supplemental
    "\U0001FA00-\U0001FAFF"  # extended-A
    "☀-➿"          # misc symbols + dingbats
    "]"
)

# Common Mandarin chat-ending particles we care about — short list, kept
# tight so the stats are interpretable rather than a long-tail spread.
_ENDING_PARTICLES = ("啊", "哈", "欸", "噢", "喔", "耶", "吧", "嗎", "呢", "啦", "齁", "哦", "餒", "誒")

# Markers in voice_profile.md that delimit the auto-managed samples block.
_HARVEST_BEGIN = "<!-- BEGIN auto-harvested community lines -->"
_HARVEST_END = "<!-- END auto-harvested community lines -->"


# ──────────────────────────────────────────────────────────────────────
# Skill A: harvest natural-sounding lines into voice profile
# ──────────────────────────────────────────────────────────────────────

def harvest_style_samples(
    customer_id: str,
    community_id: str,
    *,
    limit: int = 200,
    top_n: int = 30,
    skip_navigate: bool = False,
) -> dict[str, object]:
    """Read recent chat → filter noise → score → top N appended to voice profile.

    Returns a structured dict the LLM brain can summarize back to operator.
    """

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    if not skip_navigate:
        nav = navigate_to_openchat(customer_id, community_id, overall_timeout_seconds=20.0)
        if nav.get("status") != "ok":
            return {"status": "error", "reason": f"navigate_failed:{nav.get('reason')}"}

    try:
        messages = read_recent_chat(
            AdbClient(device_id=community.device_id),
            default_raw_xml_path(customer_id),
            limit=limit,
        )
    except RuntimeError as exc:
        return {"status": "error", "reason": f"read_failed:{exc}"}

    candidates = _filter_natural_lines(messages)
    scored = sorted(candidates, key=_score_line, reverse=True)
    # Dedupe while preserving score order.
    seen: set[str] = set()
    selected: list[str] = []
    for line in scored:
        if line in seen:
            continue
        seen.add(line)
        selected.append(line)
        if len(selected) >= top_n:
            break

    profile_path = voice_profile_path(customer_id, community_id)
    if not profile_path.exists():
        return {"status": "error", "reason": "voice_profile_missing", "path": str(profile_path)}

    before = profile_path.read_text(encoding="utf-8")
    after = _splice_harvest_block(before, selected)
    profile_path.write_text(after, encoding="utf-8")

    append_audit_event(
        customer_id,
        "style_samples_harvested",
        {
            "community_id": community_id,
            "messages_read": len(messages),
            "candidates_kept": len(candidates),
            "samples_written": len(selected),
        },
    )

    return {
        "status": "ok",
        "community_id": community_id,
        "messages_read": len(messages),
        "candidates_kept": len(candidates),
        "samples_written": len(selected),
        "preview": selected[:5],
        "voice_profile_path": str(profile_path),
    }


def _filter_natural_lines(messages: Iterable[dict[str, object]]) -> list[str]:
    out: list[str] = []
    for msg in messages:
        text = str(msg.get("text") or "").strip()
        if not _is_natural_line(text):
            continue
        out.append(text)
    return out


def _is_natural_line(text: str) -> bool:
    if not text or len(text) < 4:
        return False
    if len(text) > 80:  # very long lines are usually announcements / pasted content
        return False
    if _LINK_RE.search(text):
        return False
    if _AT_MENTION_NOISE_RE.match(text):
        return False
    if _TIMESTAMP_NOISE_RE.match(text):
        return False
    if text in _UI_NOISE_EXACT:
        return False
    if _MEMBER_COUNT_RE.match(text):
        return False
    if _MEMBER_BADGE_RE.match(text):
        return False
    if any(hint in text for hint in _BROADCAST_HINTS):
        return False
    # Drop pure-emoji / pure-punctuation noise — we want lines with substance.
    if not re.search(r"[一-鿿A-Za-z0-9]", text):
        return False
    return True


def _score_line(text: str) -> float:
    """Higher = more natural-sounding. Prefer mid-length conversational lines."""

    length = len(text)
    # Sweet spot 6–25 chars; penalize outside.
    if 6 <= length <= 25:
        length_score = 1.0
    elif length < 6:
        length_score = 0.4
    else:
        length_score = max(0.2, 1.0 - (length - 25) * 0.04)

    # Bonus for casual particles & question marks (shows people engaging).
    casual_bonus = 0.0
    for particle in _ENDING_PARTICLES:
        if text.endswith(particle) or text.endswith(particle + "～") or text.endswith(particle + "?") or text.endswith(particle + "？"):
            casual_bonus += 0.3
            break
    if "?" in text or "？" in text:
        casual_bonus += 0.1

    # Penalty for ALL CAPS / repeated punctuation (often spam-y).
    if re.search(r"[!！]{3,}|[?？]{3,}", text):
        casual_bonus -= 0.2

    return length_score + casual_bonus


def _splice_harvest_block(existing: str, samples: list[str]) -> str:
    """Replace any existing auto-managed block, or append a fresh one."""

    block = _build_harvest_block(samples)
    if _HARVEST_BEGIN in existing and _HARVEST_END in existing:
        pattern = re.compile(
            re.escape(_HARVEST_BEGIN) + r".*?" + re.escape(_HARVEST_END),
            re.DOTALL,
        )
        return pattern.sub(block, existing)
    sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + sep + block + "\n"


def _build_harvest_block(samples: list[str]) -> str:
    if not samples:
        body = "_(尚無足夠樣本，請再多累積對話後重跑 harvest_style_samples)_"
    else:
        body = "\n".join(f"- {s}" for s in samples)
    return (
        f"{_HARVEST_BEGIN}\n"
        "## Observed community lines（自動抓取的真實成員語句，覆寫安全，請勿手改）\n\n"
        f"{body}\n"
        f"{_HARVEST_END}"
    )


# ──────────────────────────────────────────────────────────────────────
# Skill B: fingerprint current conversation vibe
# ──────────────────────────────────────────────────────────────────────

def fingerprint_conversation(messages: list[dict[str, object]]) -> dict[str, object]:
    """Compute a small style fingerprint over the supplied messages.

    Pure function — no IO. Used by watch_tick to inject "current vibe" into
    the codex prompt so drafts match length / formality / particle usage of
    what's happening right now in chat.

    Returns: median_length, emoji_rate, top_opening_words, top_ending_particles,
    sample_count. None values when sample is too small.
    """

    texts = [str(m.get("text") or "").strip() for m in messages]
    texts = [t for t in texts if t and len(t) >= 2]
    if len(texts) < 3:
        return {
            "sample_count": len(texts),
            "median_length": None,
            "emoji_rate": None,
            "top_opening_words": [],
            "top_ending_particles": [],
            "summary_zh": "（樣本不足，無法判斷風格）",
        }

    lengths = [len(t) for t in texts]
    median_length = int(statistics.median(lengths))

    emoji_chars = sum(len(_EMOJI_RE.findall(t)) for t in texts)
    total_chars = sum(lengths)
    emoji_rate = round(emoji_chars / total_chars, 3) if total_chars else 0.0

    # Top opening "words" — first 1-2 chars (Mandarin chat opens are typically
    # 1-char particles like 對/欸/我 or 2-char like 我覺/有人).
    opening_counter: Counter[str] = Counter()
    for t in texts:
        head = t[:2] if len(t) >= 2 else t[:1]
        # Skip pure punctuation / numeric heads.
        if re.match(r"^[一-鿿A-Za-z]", head):
            opening_counter[head] += 1
    top_openings = [w for w, _ in opening_counter.most_common(5)]

    ending_counter: Counter[str] = Counter()
    for t in texts:
        for particle in _ENDING_PARTICLES:
            if t.endswith(particle) or t.endswith(particle + "～") or t.endswith(particle + "！") or t.endswith(particle + "?") or t.endswith(particle + "？"):
                ending_counter[particle] += 1
                break
    top_endings = [p for p, _ in ending_counter.most_common(5)]

    summary_parts = [f"中位字數 {median_length}"]
    if emoji_rate > 0:
        summary_parts.append(f"emoji 密度 {emoji_rate:.2f}/字")
    else:
        summary_parts.append("幾乎不用 emoji")
    if top_openings:
        summary_parts.append(f"常見開頭 {'/'.join(top_openings[:3])}")
    if top_endings:
        summary_parts.append(f"常見句尾語助詞 {'/'.join(top_endings[:3])}")

    return {
        "sample_count": len(texts),
        "median_length": median_length,
        "emoji_rate": emoji_rate,
        "top_opening_words": top_openings,
        "top_ending_particles": top_endings,
        "summary_zh": "；".join(summary_parts),
    }
