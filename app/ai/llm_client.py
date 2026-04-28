"""LLM client for draft generation.

Thin wrapper around the Anthropic SDK. Loaded lazily so that nothing imports
the SDK unless `ECHO_LLM_ENABLED=true` and a key is configured. Returns a
structured `LlmDraft` or raises `LlmUnavailable` (which the decision module
treats as "fall back to rule-based template").
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.config import settings


class LlmUnavailable(RuntimeError):
    """Raised when the LLM cannot be reached or is disabled by config."""


@dataclass(frozen=True)
class LlmDraft:
    action: str  # "draft_reply" | "no_action"
    reason: str
    confidence: float
    draft: str
    raw_text: str  # full LLM response for audit / debugging


_SYSTEM_PROMPT_TEMPLATE = """你是「{community_name}」社群裡的長期成員，**不是客服機器人**。
你正在判斷：根據最近的對話，現在該不該開口、要說什麼。

你的角色設定（persona）：
{persona}

社群規則（playbook）：
{playbook}

安全護欄：
{safety_rules}

決策規則：
- 如果對話正在熱絡進行（多人連續發言、彼此在互動）→ 不介入。
- 如果有明確問題沒人回 → 可以給簡短、有用的回答。
- 如果是冷場（很久沒人發言）→ 可以拋一個輕量話題。
- 寧可不發，也不要發尷尬、跑題、或聽起來像 bot 的話。
- 不要使用 emoji 過量、不要客套、不要自我介紹。
- 回應一律用繁體中文，1-3 句話，不超過 80 字。

請**僅**輸出一個 JSON 物件，格式如下，不要加任何其他文字：
{{
  "action": "draft_reply" 或 "no_action",
  "reason": "用一句話說明你的判斷",
  "confidence": 0.0 到 1.0 的數字,
  "draft": "如果 action=draft_reply 則填要發的話；如果 no_action 則填空字串"
}}
"""


def is_enabled() -> bool:
    return bool(settings.llm_enabled and settings.anthropic_api_key)


def generate_draft(
    community_name: str,
    persona_text: str,
    playbook_text: str,
    safety_rules: list[str],
    recent_messages: list[str],
) -> LlmDraft:
    if not is_enabled():
        raise LlmUnavailable("LLM disabled or API key missing")

    try:
        import anthropic
    except ImportError as exc:
        raise LlmUnavailable(f"anthropic SDK not installed: {exc}") from exc

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
        community_name=community_name,
        persona=persona_text.strip() or "（未設定）",
        playbook=playbook_text.strip() or "（未設定）",
        safety_rules="\n".join(f"- {rule}" for rule in safety_rules) or "（無）",
    )

    transcript = "\n".join(f"- {msg}" for msg in recent_messages[-15:]) or "（無近期訊息）"
    user_prompt = (
        f"以下是「{community_name}」最近的對話節錄（由舊到新）：\n\n{transcript}\n\n"
        "請依規則判斷現在是否該發言、發什麼。只輸出 JSON。"
    )

    try:
        response = client.messages.create(
            model=settings.llm_model,
            max_tokens=settings.llm_max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:  # noqa: BLE001 — any SDK/network error → unavailable
        raise LlmUnavailable(f"LLM call failed: {exc}") from exc

    text_blocks = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    raw_text = "".join(text_blocks).strip()
    if not raw_text:
        raise LlmUnavailable("LLM returned empty response")

    return _parse_draft(raw_text)


def _parse_draft(raw_text: str) -> LlmDraft:
    payload = _extract_json(raw_text)
    action = str(payload.get("action") or "").strip()
    if action not in {"draft_reply", "no_action"}:
        raise LlmUnavailable(f"LLM returned invalid action: {action!r}")

    confidence_value = payload.get("confidence", 0.5)
    try:
        confidence = float(confidence_value)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    return LlmDraft(
        action=action,
        reason=str(payload.get("reason") or "llm").strip(),
        confidence=confidence,
        draft=str(payload.get("draft") or "").strip(),
        raw_text=raw_text,
    )


def _extract_json(raw_text: str) -> dict[str, object]:
    text = raw_text.strip()
    if text.startswith("```"):
        # strip markdown code fences if the model wrapped its JSON
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LlmUnavailable("LLM response did not contain a JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LlmUnavailable(f"LLM JSON parse failed: {exc}") from exc
