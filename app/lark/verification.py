from __future__ import annotations

from app.config import settings


def handle_url_verification(payload: dict[str, object]) -> dict[str, object] | None:
    if payload.get("type") != "url_verification":
        return None

    token = payload.get("token")
    if settings.lark_verification_token and token != settings.lark_verification_token:
        return {"status": "error", "reason": "invalid_token"}

    challenge = payload.get("challenge")
    return {"challenge": challenge}

