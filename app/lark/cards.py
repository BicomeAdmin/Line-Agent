from __future__ import annotations

import re


def build_reply_card(reply_text: str, *, header_title: str = "🤖 Project Echo") -> dict[str, object]:
    """Wrap a bot reply in an interactive card so Lark renders it with
    a header bar instead of as a flat text bubble.

    Layout heuristic: if the reply is structured (3+ newlines OR contains
    common ASCII art markers like ─ ✅ 📊), wrap the body in a fenced
    code block so column alignment from text-formatted digests survives
    Lark's markdown renderer. Short conversational replies render as
    plain markdown so emphasis / inline code / links still work.
    """

    body = (reply_text or "").rstrip()
    if not body:
        body = "（沒有回應）"
    if _looks_structured(body):
        # Triple-backtick fence preserves whitespace and prevents the
        # renderer from treating "  - " or "│" as formatting.
        body_md = f"```\n{body}\n```"
    else:
        body_md = body

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": header_title},
            "template": "blue",
        },
        "elements": [
            {"tag": "markdown", "content": body_md},
        ],
    }


_STRUCTURE_HINTS_RE = re.compile(r"[─━│┌┐└┘├┤┬┴┼]|^\s*[✅❌⚠️🩺📊📨🌐📥⏰🛎📋]", re.MULTILINE)


def _looks_structured(text: str) -> bool:
    if text.count("\n") >= 3:
        return True
    if _STRUCTURE_HINTS_RE.search(text):
        return True
    return False


def build_review_card(
    customer_name: str,
    community_name: str,
    draft: str,
    job_id: str,
    customer_id: str | None = None,
    community_id: str | None = None,
    device_id: str | None = None,
    reason: str | None = None,
    confidence: float | None = None,
    draft_title: str = "AI 擬稿",
) -> dict[str, object]:
    detail_lines = []
    if reason:
        detail_lines.append(f"- `reason`: `{reason}`")
    if confidence is not None:
        detail_lines.append(f"- `confidence`: `{confidence:.2f}`")
    detail_block = "\n".join(detail_lines)
    draft_block = f"**{draft_title}**\n{draft}"
    if detail_block:
        draft_block = f"{draft_block}\n\n{detail_block}"
    base_value = {
        "job_id": job_id,
        "draft_text": draft,
    }
    if customer_id:
        base_value["customer_id"] = customer_id
    if community_id:
        base_value["community_id"] = community_id
    if device_id:
        base_value["device_id"] = device_id
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"{customer_name} - {community_name}"}},
        "elements": [
            {"tag": "markdown", "content": draft_block},
            {
                "tag": "action",
                "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "立即發送"}, "value": {**base_value, "action": "send"}, "type": "primary"},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "修改稿件"}, "value": {**base_value, "action": "edit"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "忽略"}, "value": {**base_value, "action": "ignore"}},
                ],
            },
        ],
    }
