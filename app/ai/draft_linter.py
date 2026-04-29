"""Draft linter — score a draft against the Taiwan chat register cheat-sheet.

The cheat-sheet is from 19.4 萬則 chat across 16 communities. See
`memory/feedback_taiwan_chat_register.md`.

Scoring categories (each contributes to a 0-100 final score):
  + particle_density   每句末尾語助詞密度（冷句點 → 扣分）
  + hedger_count       軟化詞數量（感覺 / 可能 / 其實 / 好像 ...）
  + first_person_open  起手是否第一人稱
  - forbidden_phrases  小編腔 / 客服腔 / 推銷詞觸發 → 重扣
  - banned_openers     「大家 / 歡迎 / 您 / 親愛的 / 請」起手 → 重扣
  - too_long           > 60 字（OpenChat 自然句長偏短）
  - too_announce       排版列點 / heading

Use:
  - `score_draft(text)` returns DraftLintResult; importable.
  - CLI: `python3 scripts/lint_draft.py "candidate text"` for one-off.
  - Watch tick can call this between codex output and review_store insert,
    asking for a retry if score < threshold.

This is a heuristic, not a model. It enforces hard "you sound like a
小編" red flags, not subtle taste — that's what the LLM is for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


PARTICLES = ("了", "嗎", "喔", "哈", "啊", "吧", "唷", "呢", "啦", "耶", "呀", "哦", "餒", "嘛")
HEDGERS = ("感覺", "可能", "其實", "好像", "我覺得", "我自己", "不一定", "或許", "也許", "我看", "看狀況", "看情況", "我這邊")
FIRST_PERSON_OPENERS = ("我", "我也", "我自己", "我以前", "我覺得", "我看", "我這邊")

# Hard banned openers (broadcast / customer-service register)
BANNED_OPENERS = ("大家", "歡迎", "您", "親愛的", "請大家", "請各位", "麻煩各位")

# Hard forbidden phrases anywhere in the draft
FORBIDDEN_PHRASES = (
    "希望這對您有幫助",
    "為您服務",
    "感謝您的提問",
    "您好",
    "為您",
    "歡迎大家",
    "請大家",
    "我們一起",
    "整理一下",
    "順手補一下",
    "收個聲量",
    "立刻購買",
    "立即購買",
    "限時搶購",
    "搶購",
    "編-",
    "點我",
    "聲量",
    "讓我們",
    "敬請",
    "敬告",
)

# Soft warnings — used as opener checks (start of draft)
SOFT_WARN_OPENERS = ("收到", "已收到", "感謝您")

ANNOUNCE_PATTERNS = (
    re.compile(r"^\s*[一二三四五六七八九十\d]+[、.)）]\s"),  # numbered list
    re.compile(r"^\s*[\-•▪️·]\s"),                           # bulleted list
    re.compile(r"^\s*#{1,6}\s"),                             # markdown heading
)


@dataclass(frozen=True)
class DraftLintResult:
    score: int                       # 0-100
    verdict: str                      # "natural" | "ok" | "stiff" | "broadcast"
    breakdown: dict[str, object] = field(default_factory=dict)
    issues: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "breakdown": self.breakdown,
            "issues": list(self.issues),
            "suggestions": list(self.suggestions),
        }


def score_draft(text: str) -> DraftLintResult:
    """Score a draft 0-100 against the Taiwan chat register cheat-sheet."""

    cleaned = text.strip()
    if not cleaned:
        return DraftLintResult(
            score=0,
            verdict="empty",
            issues=("empty draft",),
            suggestions=("draft is empty — nothing to lint",),
        )

    issues: list[str] = []
    suggestions: list[str] = []
    score = 100

    # Sentence split: split by 。.！!？? and newlines; keep non-empty
    sentences = [s.strip() for s in re.split(r"[。．\.！!？\?\n]+", cleaned) if s.strip()]

    # 1. Particle density
    sentences_with_particle = 0
    for sent in sentences:
        # Particle counts if it appears in last 3 chars (chat-natural ending)
        tail = sent[-3:]
        if any(p in tail for p in PARTICLES):
            sentences_with_particle += 1
    particle_ratio = sentences_with_particle / max(1, len(sentences))
    if particle_ratio < 0.5:
        deduction = int((0.5 - particle_ratio) * 60)
        score -= deduction
        issues.append(f"句尾語助詞太少（{sentences_with_particle}/{len(sentences)} 句帶語助詞）")
        suggestions.append("加 喔/啦/呢/吧/啊/耶 收尾，避免冷句點")

    # 2. Hedger count
    # Skip the hedger rule for very short replies (≤12 chars) — those are
    # natural one-word acks like 「我也還沒填欸 哈」 where a hedger would
    # actually hurt naturalness. Hedger is for >= medium-length opinions.
    hedger_hits = sum(1 for h in HEDGERS if h in cleaned)
    if len(cleaned) > 12:
        if hedger_hits == 0:
            score -= 20
            issues.append("完全沒有 hedger（感覺/可能/其實/好像/我覺得 等）")
            suggestions.append("台灣 chat 不愛斷言，加一個軟化詞")
        elif len(sentences) >= 2 and hedger_hits < 1:
            score -= 10

    # 3. First-person opener
    first_token = _first_meaningful_token(cleaned)
    if first_token in FIRST_PERSON_OPENERS or any(cleaned.startswith(o) for o in FIRST_PERSON_OPENERS):
        # bonus reflected by no deduction; nothing to do
        pass
    elif any(cleaned.startswith(o) for o in BANNED_OPENERS):
        score -= 35
        issues.append(f"起手「{first_token}」是廣播 / 客服腔（禁忌字）")
        suggestions.append("改用第一人稱起手：「我」「我也」「我自己」「我以前」「我覺得」")
    elif any(cleaned.startswith(o) for o in SOFT_WARN_OPENERS):
        score -= 20
        issues.append(f"起手「{first_token}」偏制式營運用語")
        suggestions.append("ack 偏好順序：謝謝 > 了解 > 好的；避免「收到」當開頭")

    # 4. Forbidden phrases anywhere
    forbidden_hits = [p for p in FORBIDDEN_PHRASES if p in cleaned]
    if forbidden_hits:
        score -= 25 * len(forbidden_hits)
        issues.append(f"觸發小編腔 / 客服腔 / 推銷詞：{forbidden_hits}")
        suggestions.append("整段重寫，這些詞讓草稿洩底是 bot/小編")

    # 5. Length
    if len(cleaned) > 80:
        score -= 15
        issues.append(f"草稿過長（{len(cleaned)} 字，建議 < 60）")
        suggestions.append("OpenChat 自然句長偏短，砍到 1-2 句")
    elif len(cleaned) > 60:
        score -= 5

    # 6. Announce / list patterns
    has_announce = any(any(p.match(line) for p in ANNOUNCE_PATTERNS) for line in cleaned.splitlines())
    if has_announce:
        score -= 30
        issues.append("出現列點 / heading — chat 不會列點")
        suggestions.append("用流暢句子取代列點")

    # 7. emoji + emoji-only check
    # Strip emoji + variation selectors (FE00-FE0F) + zero-width joiner +
    # whitespace, then see if any text remains. ❤️ etc. carry VS16 (FE0F)
    # that we must remove or the "pure emoji" check misfires.
    emoji_re = r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]"
    emoji_count = len(re.findall(emoji_re, cleaned))
    text_only = re.sub(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF︀-️‍\s]", "", cleaned)
    if not text_only:
        score = max(0, score - 50)
        issues.append("純 emoji 無內容")

    score = max(0, min(100, score))

    if score >= 80:
        verdict = "natural"
    elif score >= 60:
        verdict = "ok"
    elif score >= 35:
        verdict = "stiff"
    else:
        verdict = "broadcast"

    breakdown = {
        "length": len(cleaned),
        "sentence_count": len(sentences),
        "particle_ratio": round(particle_ratio, 2),
        "hedger_count": hedger_hits,
        "starts_first_person": first_token in FIRST_PERSON_OPENERS or any(cleaned.startswith(o) for o in FIRST_PERSON_OPENERS),
        "starts_banned": any(cleaned.startswith(o) for o in BANNED_OPENERS),
        "forbidden_phrase_hits": forbidden_hits,
        "has_list_or_heading": has_announce,
        "emoji_count": emoji_count,
    }

    return DraftLintResult(
        score=score,
        verdict=verdict,
        breakdown=breakdown,
        issues=tuple(issues),
        suggestions=tuple(suggestions),
    )


def _first_meaningful_token(text: str) -> str:
    """Strip leading whitespace / emoji / punctuation, return first 1-3 chars."""
    cleaned = re.sub(r"^[\s\W\d]+", "", text)
    if not cleaned:
        return ""
    # Try 3-char then 2-char then 1-char
    for n in (3, 2, 1):
        if len(cleaned) >= n:
            return cleaned[:n]
    return cleaned
