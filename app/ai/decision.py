from __future__ import annotations

from dataclasses import asdict, dataclass

from app.ai.llm_client import LlmUnavailable, generate_draft, is_enabled


QUESTION_MARKERS = ("?", "？", "請問", "想問", "有人知道")


@dataclass(frozen=True)
class DraftDecision:
    action: str
    reason: str
    confidence: float
    draft: str
    should_send: bool = False
    source: str = "rule_based"  # "llm" | "rule_based" | "llm_fallback"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def decide_reply(
    messages: list[dict[str, object]],
    persona_text: str,
    community_name: str,
    *,
    playbook_text: str = "",
    safety_rules: list[str] | None = None,
) -> DraftDecision:
    if is_enabled():
        try:
            llm_draft = generate_draft(
                community_name=community_name,
                persona_text=persona_text,
                playbook_text=playbook_text,
                safety_rules=safety_rules or [],
                recent_messages=[str(msg.get("text", "")).strip() for msg in messages if msg.get("text")],
            )
            return DraftDecision(
                action=llm_draft.action,
                reason=llm_draft.reason,
                confidence=llm_draft.confidence,
                draft=llm_draft.draft or "（LLM 判斷無需發言）",
                source="llm",
            )
        except LlmUnavailable:
            decision = _rule_based_decide(messages, persona_text, community_name)
            return DraftDecision(
                action=decision.action,
                reason=decision.reason,
                confidence=decision.confidence,
                draft=decision.draft,
                should_send=decision.should_send,
                source="llm_fallback",
            )

    return _rule_based_decide(messages, persona_text, community_name)


def _rule_based_decide(messages: list[dict[str, object]], persona_text: str, community_name: str) -> DraftDecision:
    if not messages:
        return DraftDecision(
            action="draft_reply",
            reason="cold_room",
            confidence=0.72,
            draft=f"{community_name} 最近大家最想交流的是哪一塊？我先拋個問題暖場，最近有沒有讓你最在意的選擇或觀察？",
        )

    last_text = str(messages[-1].get("text", "")).strip()
    if any(marker in last_text for marker in QUESTION_MARKERS):
        return DraftDecision(
            action="draft_reply",
            reason="user_question",
            confidence=0.84,
            draft=_answer_style(last_text, persona_text),
        )

    if len(messages) >= 6:
        return DraftDecision(
            action="no_action",
            reason="active_conversation",
            confidence=0.9,
            draft="目前對話自然進行中，暫不介入。",
        )

    return DraftDecision(
        action="draft_reply",
        reason="light_prompt",
        confidence=0.68,
        draft="我補一個小角度給大家參考，如果從實際使用情境來看，你們最在意的是價格、方便性，還是效果本身？",
    )


def _answer_style(question_text: str, persona_text: str) -> str:
    calm_prefix = "我會先抓住你最在意的條件，再往下縮小範圍。"
    if "奶瓶" in question_text:
        body = "如果是新手起步，通常先看材質、容量和清潔便利性，先不要一次買太多款，實際接受度差異很大。"
    elif "投資" in question_text or "標的" in question_text:
        body = "這種題目我會先拆成風險承受度、時間週期和資金配置，不會只看單一標的當下熱度。"
    else:
        body = "先把需求、預算和使用情境列出來，通常就能先排掉一半不適合的選項。"

    if "克制" in persona_text or "不誇大" in persona_text:
        suffix = "如果你願意，我也可以幫你一起把條件整理得更清楚。"
    else:
        suffix = "你可以先從這幾個條件往下看。"
    return f"{calm_prefix}{body}{suffix}"
