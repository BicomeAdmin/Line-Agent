from __future__ import annotations

import json

from app.core.jobs import JobRecord, job_registry
from app.lark.commands import parse_command
from app.lark.verification import handle_url_verification


def enqueue_lark_event(payload: dict[str, object]) -> dict[str, object]:
    verification = handle_url_verification(payload)
    if verification is not None:
        return verification

    event_id = _extract_event_id(payload)
    command_text = extract_command_text(payload)
    if command_text is None:
        return {"status": "ignored", "reason": "unsupported_event"}

    parsed = parse_command(command_text)
    job = job_registry.enqueue(
        "lark_command",
        {
            "command": parsed.to_dict(),
            "reply_target": extract_reply_target(payload),
            "source_payload": payload,
        },
        event_id=event_id,
    )
    return {"status": "processing", "job_id": job.job_id, "action": parsed.action}


def enqueue_lark_action(payload: dict[str, object]) -> dict[str, object]:
    action = extract_card_action(payload)
    if action is None:
        return {"status": "ignored", "reason": "unsupported_action"}
    job = job_registry.enqueue("lark_action", action)
    return {"status": "processing", "job_id": job.job_id, "action": action.get("action")}


def extract_command_text(payload: dict[str, object]) -> str | None:
    # Accept both v1 webhook envelope (`type=event_callback`) and v2 long-connection
    # schema (`schema=2.0` with `header.event_type`). Both place the message under
    # `event.message.content` as a JSON string.
    if not _is_message_event(payload):
        return None
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    try:
        content_payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    text = content_payload.get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def _is_message_event(payload: dict[str, object]) -> bool:
    if payload.get("type") == "event_callback":
        return True
    if str(payload.get("schema") or "").startswith("2."):
        header = payload.get("header")
        if isinstance(header, dict) and header.get("event_type") == "im.message.receive_v1":
            return True
    return False


def extract_reply_target(payload: dict[str, object]) -> dict[str, str] | None:
    event = payload.get("event")
    if not isinstance(event, dict):
        return None
    message = event.get("message")
    sender = event.get("sender")
    if not isinstance(message, dict):
        return None
    chat_id = message.get("chat_id")
    if isinstance(chat_id, str) and chat_id:
        return {"receive_id": chat_id, "receive_id_type": "chat_id"}
    if isinstance(sender, dict):
        sender_id = sender.get("sender_id")
        if isinstance(sender_id, dict):
            open_id = sender_id.get("open_id")
            if isinstance(open_id, str) and open_id:
                return {"receive_id": open_id, "receive_id_type": "open_id"}
    return None


def extract_card_action(payload: dict[str, object]) -> dict[str, object] | None:
    """Extract the actionable bits from a Lark card.action.trigger payload.

    Accepts both schemas:
      - v1 webhook (`action` at top level) — historical HTTP callback shape.
      - v2 long-connection (`event.action`) — what lark-oapi WsClient delivers.
    """

    action = payload.get("action")
    if not isinstance(action, dict):
        # v2 long-connection: action lives under event.
        event = payload.get("event")
        if isinstance(event, dict):
            event_action = event.get("action")
            if isinstance(event_action, dict):
                action = event_action
    if not isinstance(action, dict):
        return None
    value = action.get("value")
    if not isinstance(value, dict):
        return None
    job_id = value.get("job_id")
    action_name = value.get("action")
    if not isinstance(job_id, str) or not isinstance(action_name, str):
        return None
    result = {"job_id": job_id, "action": action_name, "source_payload": payload}
    for key in ("customer_id", "community_id", "device_id", "draft_text", "edited_draft_text"):
        item = value.get(key)
        if isinstance(item, str) and item:
            result[key] = item
    return result


def _extract_event_id(payload: dict[str, object]) -> str | None:
    header = payload.get("header")
    if not isinstance(header, dict):
        return None
    event_id = header.get("event_id")
    return event_id if isinstance(event_id, str) else None
