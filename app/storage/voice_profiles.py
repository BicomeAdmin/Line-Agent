"""Per-community operator voice profiles.

Each community has a markdown file describing the operator's voice in that
chat: nickname, tone notes, sample messages. The LLM brain reads this
profile via the `get_voice_profile` MCP tool before composing a draft for
that community, so the resulting message sounds like the operator and not
like a corporate assistant.

Profiles live in customers/<customer_id>/voice_profiles/<community_id>.md
and are operator-controlled. The `set_voice_profile` and `append_voice_sample`
helpers let the operator refine the profile via Lark (through the bridge LLM)
without editing files directly.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core.audit import append_audit_event
from app.storage.paths import voice_profile_path

_lock = threading.Lock()


def get_voice_profile(customer_id: str, community_id: str) -> dict[str, object]:
    """Return the markdown profile + a summary `loaded` flag.

    A missing file returns `loaded=False` and an empty `content`; the LLM is
    expected to fall back to a generic 繁中口語 default in that case.
    """

    path = voice_profile_path(customer_id, community_id)
    if not path.exists():
        return {
            "loaded": False,
            "customer_id": customer_id,
            "community_id": community_id,
            "path": str(path),
            "content": "",
            "hint": "no profile yet — fall back to generic 繁中口語短句、不客套；也可請使用者提供範例後 set_voice_profile / append_voice_sample。",
        }
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "loaded": False,
            "customer_id": customer_id,
            "community_id": community_id,
            "path": str(path),
            "error": str(exc),
        }
    return {
        "loaded": True,
        "customer_id": customer_id,
        "community_id": community_id,
        "path": str(path),
        "content": content,
        "byte_size": len(content.encode("utf-8")),
    }


def set_voice_profile(
    customer_id: str,
    community_id: str,
    content: str,
    *,
    note: str | None = None,
) -> dict[str, object]:
    """Replace the entire markdown profile."""

    path = voice_profile_path(customer_id, community_id)
    text = (content or "").strip()
    if not text:
        return {"status": "error", "reason": "empty_content"}
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(path)
    append_audit_event(
        customer_id,
        "voice_profile_updated",
        {
            "community_id": community_id,
            "path": str(path),
            "byte_size": len(text.encode("utf-8")),
            "note": note,
        },
    )
    return {
        "status": "ok",
        "customer_id": customer_id,
        "community_id": community_id,
        "path": str(path),
        "byte_size": len(text.encode("utf-8")),
    }


def append_voice_sample(
    customer_id: str,
    community_id: str,
    sample_text: str,
    *,
    note: str | None = None,
) -> dict[str, object]:
    """Append a single sample message to the profile's `## Samples` section.

    Used when the operator says (in Lark) "幫我記下這句語氣 ..." — the LLM
    routes that to this tool. If no profile exists, a fresh one is bootstrapped.
    """

    sample_text = (sample_text or "").strip()
    if not sample_text:
        return {"status": "error", "reason": "empty_sample"}

    path = voice_profile_path(customer_id, community_id)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
        else:
            existing = (
                f"# Voice profile — {community_id}\n\n"
                "## Tone notes\n\n"
                "（操作員之後可手動編輯這裡。預設：繁中口語短句、不客套、不過度禮貌。）\n\n"
                "## Samples\n\n"
            )
        if "## Samples" not in existing:
            existing = existing.rstrip() + "\n\n## Samples\n\n"
        suffix = f"- [{timestamp}] {sample_text}"
        if note:
            suffix += f"  — {note}"
        new_content = existing.rstrip() + "\n" + suffix + "\n"
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_content, encoding="utf-8")
        tmp.replace(path)

    append_audit_event(
        customer_id,
        "voice_profile_sample_appended",
        {
            "community_id": community_id,
            "path": str(path),
            "sample_preview": sample_text[:80],
            "note": note,
        },
    )
    return {
        "status": "ok",
        "customer_id": customer_id,
        "community_id": community_id,
        "path": str(path),
        "appended": sample_text,
    }


def list_voice_profiles(customer_id: str) -> list[dict[str, object]]:
    """List all known profiles for a customer with byte size + last-modified."""

    from app.storage.paths import voice_profiles_dir

    items: list[dict[str, object]] = []
    base = voice_profiles_dir(customer_id)
    for child in sorted(base.glob("*.md")):
        try:
            stat = child.stat()
            items.append(
                {
                    "community_id": child.stem,
                    "path": str(child),
                    "byte_size": stat.st_size,
                    "modified_at_epoch": stat.st_mtime,
                }
            )
        except OSError:
            continue
    return items
