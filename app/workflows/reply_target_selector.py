"""Auto-select which message in a chat the operator should reply to.

The piece that turns Watcher Phase 2 from "operator says compose for
me" into autonomous "bot watches, decides, drafts — operator just
approves the card." HIL invariant unchanged: actual send still
requires operator approval. This is target-selection + composition
autonomy, not send autonomy.

The scoring rubric is deliberately aligned with Paul's《私域流量》
operating principles (CLAUDE.md §0.5):
  - Paul's "創造價值" (Value): real questions / pain points / concrete
    asks score higher than chatter, because replying to them deepens
    relationship. Generic small talk gets lower priority.
  - Paul's "陪伴 + 真誠": follow-ups to the operator's own threads
    score high — that's continuity of relationship, not opportunism.
  - Paul's "留量比流量": we'd rather skip a turn (target=None, silence)
    than fire a generic "keep the group active" template. The threshold
    enforces this.

Scoring rubric (all weights tunable, all transparent in the trace):

  +5.0  message @-mentions the operator (directed engagement)
  +3.5  message is a question (?/？/疑問詞) AND nobody has answered
        AND operator participated in this thread before (Paul: "create
        value" by being the answerer)
  +2.5  operator was last to speak in the thread before this message
        (someone is following up on operator's words — chain of trust)
  +2.5  message expresses a concrete pain / struggle / need
        ("好難" / "卡住" / "不知道怎麼" / "求救" / "求推薦") — these
        are gold per Paul: "讓嫌貨人變成連續買貨的人". Weighted
        equally with after_operator_speech.
  +1.5  topic keyword overlap with operator's recent self-posts or
        voice profile sample lines (signals operator has stake)
  -2.0  message is a system/auto-reply post (Auto-reply / 公告 / 已收回)
  -2.0  message is from operator themselves (don't reply to self)
  -1.5  message is broadcast-y (官方 / All-tag / 福利公告 / 抽獎 /
        購物連結)— replying to a broadcast adds noise, not value
  -1.0  message is too short (<4 chars after stripping emoji) —
        usually sticker / picture without context

Recency decay: linear, 1.0 weight at "most recent message", 0.0 at
the 20th message back. We don't reply to ancient threads.

A target is `actionable` only if score >= REPLY_THRESHOLD (default 2.0).
If no message clears the bar, returns target=None — watcher should
silently skip rather than burn an operator review on a weak pick.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Sequence


REPLY_THRESHOLD_DEFAULT = 2.0


def _reply_threshold() -> float:
    try:
        return float(os.getenv("REPLY_TARGET_THRESHOLD", str(REPLY_THRESHOLD_DEFAULT)))
    except ValueError:
        return REPLY_THRESHOLD_DEFAULT


_QUESTION_RE = re.compile(r"[?？]|請問|想問|有人知道|有沒有人|怎麼|什麼|哪裡|為什麼|是不是")
_AUTO_PATTERNS = ("Auto-reply", "auto-reply", "已收回訊息", "加入聊天", "離開聊天")

# Paul's "create value" signal — concrete pain / need / struggle.
# Replying to these deepens trust faster than answering chitchat.
_PAIN_RE = re.compile(
    r"好難|卡住|不知道怎麼|不知道該|求救|求推薦|求助|求問|"
    r"請教|有沒有推薦|好困擾|頭痛|崩潰|崩了|想哭|崩潰|"
    r"踩雷|採雷|被雷|想知道|想了解|急需|哪位大大|請問各位"
)

# Broadcast / promo patterns — replying to these is noise, not value.
# Most groups have a "小編" persona doing announcements; we don't add
# value by replying to those.
_BROADCAST_RE = re.compile(
    r"@All|@all|公告：|公告:|福利|抽獎|限時優惠|快搶|搶購|"
    r"開團|團購倒數|名額有限|正在火速|意願調查|報名連結|"
    r"https?://[^\s]+獎|限定|搶先看|歡迎大家"
)


@dataclass
class TargetCandidate:
    index: int                     # position in original chat list (0 = oldest)
    sender: str
    text: str
    score: float
    reasons: list[str] = field(default_factory=list)
    actionable: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class TargetDecision:
    target: TargetCandidate | None
    threshold: float
    all_scored: list[TargetCandidate]
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target.to_dict() if self.target else None,
            "threshold": self.threshold,
            "skip_reason": self.skip_reason,
            "considered": [c.to_dict() for c in self.all_scored],
        }


def select_reply_target(
    messages: Sequence[dict],
    *,
    operator_persona: dict | None = None,
    member_fingerprints: dict | None = None,  # the bundle from load_member_fingerprints
    threshold: float | None = None,
    lifecycle_tags: dict | None = None,  # bundle from load_lifecycle_tags
) -> TargetDecision:
    """Pick the most reply-worthy message in `messages` (chronological,
    oldest first). messages: list of {sender, text, position}.

    operator_persona: result of get_persona_context (so we know who I am
    in this community + my recent_self_posts).
    member_fingerprints: cached bundle from load_member_fingerprints
    (used here mostly for sender-frequency context, not core scoring).
    """

    threshold = threshold if threshold is not None else _reply_threshold()
    if not messages:
        return TargetDecision(target=None, threshold=threshold, all_scored=[], skip_reason="no_messages")

    operator_nickname = ""
    operator_recent_texts: list[str] = []
    operator_keywords: set[str] = set()
    operator_anchor_texts: list[str] = []  # for embedding similarity
    if operator_persona and operator_persona.get("status") == "ok":
        vp = operator_persona.get("voice_profile") or {}
        operator_nickname = (vp.get("nickname") or "").strip()
        recent = operator_persona.get("recent_self_posts") or []
        operator_recent_texts = [str(r.get("text") or "") for r in recent]
        anchor_lines = (vp.get("style_anchors") or "").splitlines()
        operator_keywords = _extract_keywords(operator_recent_texts + [str(s) for s in anchor_lines])
        # For semantic scoring: use recent self-posts (real voice) only.
        # Anchor lines from voice_profile are too generic ("短句、口語...")
        # and would inflate similarity for everything.
        operator_anchor_texts = [t for t in operator_recent_texts if t and len(t.strip()) >= 3]

    # Optional semantic-similarity helper. Falls back to bigram keywords
    # if BGE isn't available (no sentence-transformers, no network on
    # first run, or test environment).
    from app.ai.embedding_service import get_embedding_service
    embedding_svc = get_embedding_service() if operator_anchor_texts else None

    # Optional emotion classifier. Surfaces 疑惑 (real questions) and
    # 悲傷 (vulnerability moments) as reply opportunities; flags 憤怒
    # so we don't bot-reply into a fight.
    from app.ai.emotion_classifier import get_emotion_classifier
    emotion_clf = get_emotion_classifier()

    # Lifecycle stage lookup — skip churned, boost active, surface KOC.
    sender_stage_map: dict[str, str] = {}
    sender_msg_count_map: dict[str, int] = {}
    if lifecycle_tags and lifecycle_tags.get("members"):
        for m in lifecycle_tags["members"]:
            if isinstance(m, dict) and m.get("sender"):
                sender_stage_map[m["sender"]] = m.get("stage") or "unknown"
                sender_msg_count_map[m["sender"]] = m.get("message_count") or 0

    # KOC candidate names — boost their messages slightly per Paul's
    # "1000 鐵粉" doctrine (high-leverage relationship investments).
    koc_set: set[str] = set()
    if operator_persona:
        for c in (operator_persona.get("koc_candidates") or [])[:5]:
            if isinstance(c, dict) and c.get("sender"):
                koc_set.add(c["sender"])

    # Build operator-utterance set from chat tail. Two paths:
    #   (a) is_self flag — set by line_chat_parser when the parser saw a
    #       chat_ui_message_text (operator's own bubble). Most reliable.
    #   (b) Sender name contains the operator nickname configured for
    #       this community. Fallback for messages parsed without is_self
    #       (legacy parser, chat exports, etc.).
    operator_in_chat_indices = {
        i for i, m in enumerate(messages)
        if (
            bool(m.get("is_self"))
            or str(m.get("sender") or "") == "__operator__"
            or (operator_nickname and operator_nickname in str(m.get("sender") or ""))
        )
    }

    candidates: list[TargetCandidate] = []
    n = len(messages)
    for i, msg in enumerate(messages):
        sender = str(msg.get("sender") or "").strip()
        text = str(msg.get("text") or "").strip()
        score = 0.0
        reasons: list[str] = []

        if not text:
            continue

        # Skip operator's own messages.
        if i in operator_in_chat_indices or sender == "__operator__":
            score -= 2.0
            reasons.append("self:-2.0")
            candidates.append(TargetCandidate(index=i, sender=sender, text=text, score=score, reasons=reasons))
            continue

        # System / auto-reply patterns.
        if any(p in sender or p in text for p in _AUTO_PATTERNS):
            score -= 2.0
            reasons.append("auto_or_system:-2.0")

        # Very short messages (likely stickers/pictures without context).
        stripped_for_len = re.sub(r"[\s　]+", "", text)
        if len(stripped_for_len) < 4:
            score -= 1.0
            reasons.append("too_short:-1.0")

        # Broadcast / promo content — replying adds noise.
        if _BROADCAST_RE.search(text):
            score -= 1.5
            reasons.append("broadcast_promo:-1.5")

        # Lifecycle-aware boosts/penalties.
        stage = sender_stage_map.get(sender)
        if stage == "churned":
            score -= 1.5
            reasons.append("lifecycle_churned:-1.5")
        elif stage == "new":
            score += 1.0
            reasons.append("lifecycle_new:+1.0")  # welcome opportunity
        elif stage == "active":
            score += 0.5
            reasons.append("lifecycle_active:+0.5")

        # KOC boost — high-leverage relationship investment.
        if sender in koc_set:
            score += 1.0
            reasons.append("koc_candidate:+1.0")

        # Paul's "create value" — concrete pain / need / struggle.
        # Real chance to deepen trust by being helpful. Weighted
        # equally with after_operator_speech because both represent
        # the highest-leverage relationship moments.
        if _PAIN_RE.search(text):
            score += 2.5
            reasons.append("pain_or_need:+2.5")

        # Emotion-aware boosting. The model sometimes mis-fires on short
        # text so require confidence ≥ 0.55 for any movement. We never
        # downscore ambiguous cases — the threshold + cooldown
        # gates already protect the inbox from over-firing.
        emotion = None
        if emotion_clf is not None:
            try:
                emotion = emotion_clf.classify(text)
            except Exception:  # noqa: BLE001 — never let model errors break scoring
                emotion = None
        if emotion and emotion.get("score", 0) >= 0.55:
            label = emotion.get("label")
            score_e = emotion["score"]
            if label == "puzzled":
                # Real question / confusion — high-leverage to be helpful
                score += 2.0
                reasons.append(f"emotion_puzzled:+2.0(p={score_e:.2f})")
            elif label == "sad":
                # Vulnerability — caring response builds trust long-term
                score += 1.5
                reasons.append(f"emotion_sad:+1.5(p={score_e:.2f})")
            elif label == "angry":
                # Don't bot-reply into a fight. Flag so operator sees it,
                # but don't auto-fire a response.
                score -= 2.5
                reasons.append(f"emotion_angry:-2.5(p={score_e:.2f}) ⚠️escalate")
            elif label == "disgust":
                # Strong negative — same risk as angry
                score -= 2.0
                reasons.append(f"emotion_disgust:-2.0(p={score_e:.2f})")

        # Direct @-mention to operator.
        if operator_nickname and (f"@{operator_nickname}" in text or operator_nickname in text):
            score += 5.0
            reasons.append("mentions_operator:+5.0")

        # Question with no answer yet AND operator participated in thread.
        is_question = bool(_QUESTION_RE.search(text))
        if is_question:
            answered_after = _has_answer_within(messages, i, lookahead=5, exclude_sender=sender)
            operator_in_thread = _operator_was_in_recent(messages, i, operator_nickname, lookback=8)
            if not answered_after and operator_in_thread:
                score += 3.5
                reasons.append("unanswered_q_in_op_thread:+3.5")
            elif is_question and operator_in_thread:
                score += 1.5
                reasons.append("question_in_op_thread:+1.5")

        # Operator was last to speak before this message — someone is
        # following up on what we said. Detect via is_self flag (parser-
        # supplied) or nickname-match fallback.
        if i >= 1:
            prev_msg = messages[i - 1]
            prev_sender = str(prev_msg.get("sender") or "")
            prev_is_op = (
                bool(prev_msg.get("is_self"))
                or prev_sender == "__operator__"
                or (operator_nickname and operator_nickname in prev_sender)
            )
            if prev_is_op:
                score += 2.5
                reasons.append("after_operator_speech:+2.5")

        # Topic overlap — prefer semantic similarity (BGE), fall back
        # to bigram intersection. Both end up as a single +1.5 signal,
        # so the rest of the rubric is unaffected.
        if embedding_svc is not None and operator_anchor_texts:
            sim = embedding_svc.max_similarity(text, operator_anchor_texts)
            # 0.45 is the empirically-observed boundary between "vaguely
            # same domain" and "actually about the same thing" on
            # bge-small-zh-v1.5 for short Mandarin chat messages.
            if sim >= 0.45:
                score += 1.5
                reasons.append(f"topic_overlap_sem:+1.5(sim={sim:.2f})")
            elif sim >= 0.30:
                # Weaker signal — a small nudge but not a full point.
                score += 0.5
                reasons.append(f"topic_loose_sem:+0.5(sim={sim:.2f})")
        elif operator_keywords:
            msg_kw = _extract_keywords([text])
            overlap = msg_kw & operator_keywords
            if overlap:
                score += 1.5
                reasons.append(f"topic_overlap_kw:+1.5({'/'.join(list(overlap)[:3])})")

        # Recency decay: messages further back lose weight.
        recency_factor = max(0.0, 1.0 - (n - 1 - i) / 20.0)
        score *= recency_factor
        if recency_factor < 1.0:
            reasons.append(f"recency_x{recency_factor:.2f}")

        candidates.append(TargetCandidate(index=i, sender=sender, text=text, score=round(score, 2), reasons=reasons))

    # Mark actionable + pick best.
    for c in candidates:
        c.actionable = c.score >= threshold

    if not candidates:
        return TargetDecision(target=None, threshold=threshold, all_scored=[], skip_reason="no_scorable_messages")

    best = max(candidates, key=lambda c: c.score)
    if not best.actionable:
        return TargetDecision(
            target=None,
            threshold=threshold,
            all_scored=candidates,
            skip_reason=f"no_candidate_above_threshold(top={best.score})",
        )

    return TargetDecision(target=best, threshold=threshold, all_scored=candidates)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

_HAN_RUN_RE = re.compile(r"[一-鿿]+")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _extract_keywords(texts: list[str]) -> set[str]:
    """Topic keyword set via 2-char bigrams over Han runs + ASCII words.

    Chinese has no whitespace, so a greedy `[一-鿿]+` match would
    collapse a whole sentence into one token and miss any partial
    overlap. Sliding 2-char bigrams give us reasonable topic signal
    without a real segmenter (e.g. jieba) — '股票' will appear in both
    '昨天股票漲了不少' and '今天股票行情怎麼樣'.
    """

    kw: set[str] = set()
    for t in texts:
        for run in _HAN_RUN_RE.findall(t):
            for i in range(len(run) - 1):
                kw.add(run[i : i + 2])
        for word in _ASCII_WORD_RE.findall(t):
            kw.add(word.lower())

    # Drop common bigrams that don't carry topic signal — pronouns,
    # function-word fragments, and generic time/quantity words.
    stopwords = {
        "我們", "你們", "他們", "今天", "明天", "昨天", "可以", "什麼", "怎麼", "為什",
        "這個", "那個", "這樣", "那樣", "因為", "所以", "但是", "或是", "或者",
        "覺得", "知道", "想要", "需要", "一個", "一下", "一點", "有人", "好像",
        "還是", "已經", "真的", "其實", "結果", "後來", "然後",
    }
    return kw - stopwords


def _has_answer_within(messages: Sequence[dict], idx: int, *, lookahead: int, exclude_sender: str) -> bool:
    end = min(len(messages), idx + 1 + lookahead)
    for j in range(idx + 1, end):
        s = str(messages[j].get("sender") or "")
        if s and s != exclude_sender:
            return True
    return False


def _operator_was_in_recent(messages: Sequence[dict], idx: int, operator_nickname: str, *, lookback: int) -> bool:
    """True if any message in [idx-lookback, idx) was sent by the operator.
    Uses is_self flag, __operator__ sentinel, or nickname match."""

    start = max(0, idx - lookback)
    for j in range(start, idx):
        msg = messages[j]
        if msg.get("is_self"):
            return True
        sender = str(msg.get("sender") or "")
        if sender == "__operator__":
            return True
        if operator_nickname and operator_nickname in sender:
            return True
    return False


def _previous_non_self(messages: Sequence[dict], idx: int, operator_nickname: str) -> int | None:
    for j in range(idx - 1, -1, -1):
        s = str(messages[j].get("sender") or "")
        if not (operator_nickname and operator_nickname in s):
            return j
    return None
