"""Post-send verification — `send_draft` returning "sent" doesn't
guarantee the message actually landed in the LINE chat.

Failure modes that produce "sent" without a real send:
  - IME switch failure mid-typing (text typed but send button missed)
  - LINE app crashed between type and send
  - Send button tapped but UI was on a notification overlay
  - ADB drop after type, before send button confirmation
  - LINE rate-limited / network blip

We catch these by reading the chat AFTER send_draft completes and
verifying the operator's most-recent self-bubble matches the draft
text. If it doesn't (or no self-bubble exists / matches a stale one),
the operator gets an audit signal so they can manually re-send or
investigate.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from app.adb.client import AdbClient
from app.workflows.read_chat import read_recent_chat


@dataclass(frozen=True)
class SendVerification:
    ok: bool
    matched_text: str
    expected_preview: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "matched_text": self.matched_text,
            "expected_preview": self.expected_preview,
            "reason": self.reason,
        }


def verify_send(
    client: AdbClient,
    output_path: str | Path,
    expected_draft: str,
    *,
    max_attempts: int = 3,
    sleep_seconds: float = 1.5,
) -> SendVerification:
    """Confirm the operator's last self-bubble matches `expected_draft`.

    LINE may take a moment to render the new bubble post-send; we poll
    up to `max_attempts` reads with a small sleep between them. Returns
    on first success. If the bubble never appears or doesn't match,
    returns ok=False with a diagnostic reason.

    Best-effort: any read failure short-circuits to ok=False with the
    failure reason — caller decides whether that's bad enough to flag.
    """

    expected = _normalize(expected_draft)
    if not expected:
        return SendVerification(
            ok=False, matched_text="", expected_preview="",
            reason="empty_expected",
        )
    expected_preview = expected_draft[:60]

    last_self_bubble = ""
    for attempt in range(max_attempts):
        if attempt > 0:
            time.sleep(sleep_seconds)
        try:
            messages = read_recent_chat(client, output_path, limit=10)
        except Exception as exc:  # noqa: BLE001
            return SendVerification(
                ok=False, matched_text="", expected_preview=expected_preview,
                reason=f"read_failed:{str(exc)[:120]}",
            )

        # Find latest self-bubble (last in chronological order with is_self=True)
        for msg in reversed(messages):
            if msg.get("is_self"):
                last_self_bubble = str(msg.get("text") or "")
                break
        if not last_self_bubble:
            continue
        if _matches(last_self_bubble, expected):
            return SendVerification(
                ok=True,
                matched_text=last_self_bubble[:120],
                expected_preview=expected_preview,
                reason="match",
            )

    if not last_self_bubble:
        return SendVerification(
            ok=False, matched_text="", expected_preview=expected_preview,
            reason="no_self_bubble_after_send",
        )
    return SendVerification(
        ok=False,
        matched_text=last_self_bubble[:120],
        expected_preview=expected_preview,
        reason="latest_self_bubble_does_not_match",
    )


# Whitespace + punctuation tolerance: LINE may render with subtle
# spacing differences vs what we typed. Normalize before comparison.
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    if not text:
        return ""
    return _WS_RE.sub("", text).strip()


def _matches(landed: str, expected_normalized: str) -> bool:
    """Lenient match: normalized substring either way handles minor
    LINE rendering quirks AND truncation (LINE may collapse trailing
    whitespace, etc.)."""

    landed_norm = _normalize(landed)
    if not landed_norm:
        return False
    if expected_normalized in landed_norm:
        return True
    if landed_norm in expected_normalized:
        return True
    # Last resort: 90% prefix match — handles cases where LINE
    # post-send rendering differs slightly at the tail.
    common_prefix = _common_prefix_len(landed_norm, expected_normalized)
    if common_prefix >= max(20, int(0.9 * min(len(landed_norm), len(expected_normalized)))):
        return True
    return False


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i
