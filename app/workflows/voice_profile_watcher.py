"""Detect operator edits to voice_profile.md between scheduler ticks.

When the operator updates a voice_profile (adds an off-limits rule,
tweaks the persona, expands sample lines), the next codex compose
will pick it up automatically — but the operator has no signal that
their edit was actually noticed by the system. This module solves
two problems:

1. **Visibility**: emits an audit event `voice_profile_changed` per
   detected mtime bump. Surfaces in the dashboard alert layer so the
   operator sees "your edit landed" without checking a status page.

2. **Drift correlation**: `approve_send_off_limits_drift` already
   audits when the off-limits hash changes vs compose-time. This
   module's events let us correlate: "operator edited voice_profile
   at 14:30, drift first appeared at 15:05 on review composed 11:00".

State: per-(customer, community) last-known mtime, persisted under
`.project_echo/voice_profile_mtimes.json`. Survives daemon restarts.

Cheap to call every tick — one `stat()` per community per call.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.audit import append_audit_event
from app.core.reviews import hash_off_limits
from app.storage.config_loader import load_all_communities
from app.storage.paths import voice_profile_path


def _state_path() -> Path:
    """Per-host state file. Lives under .project_echo/ so backup_state
    picks it up alongside review_store / scheduled_posts."""

    base = Path(__file__).resolve().parents[2] / ".project_echo"
    base.mkdir(parents=True, exist_ok=True)
    return base / "voice_profile_mtimes.json"


@dataclass(frozen=True)
class ProfileChange:
    customer_id: str
    community_id: str
    previous_mtime: float | None
    current_mtime: float
    off_limits_hash_changed: bool


def _read_state() -> dict[str, dict]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(state: dict) -> None:
    path = _state_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def detect_voice_profile_changes(*, now: float | None = None) -> list[ProfileChange]:
    """Walk all configured communities, compare current voice_profile.md
    mtime to last-known. Emits audit events for each change and updates
    the persisted state. Returns the list of detected changes (for
    diagnostic reporting; daemon doesn't strictly need it).
    """

    current = now if now is not None else time.time()
    state = _read_state()
    changes: list[ProfileChange] = []
    state_dirty = False

    for community in load_all_communities():
        path = voice_profile_path(community.customer_id, community.community_id)
        if not path.exists():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        key = f"{community.customer_id}:{community.community_id}"
        prev_entry = state.get(key) or {}
        prev_mtime = prev_entry.get("mtime")
        prev_hash = prev_entry.get("off_limits_hash")

        if isinstance(prev_mtime, (int, float)) and abs(prev_mtime - mtime) < 0.5:
            # No change (sub-second tolerance).
            continue

        # Detect off-limits-specific change vs general edit.
        off_limits_hash = ""
        try:
            from app.ai.voice_profile_v2 import parse_voice_profile
            vp = parse_voice_profile(community.customer_id, community.community_id, path)
            off_limits_hash = hash_off_limits(vp.off_limits)
        except Exception:  # noqa: BLE001 — hash is best-effort
            off_limits_hash = ""
        off_limits_changed = bool(prev_hash) and prev_hash != off_limits_hash

        # First seen → just record baseline, don't audit.
        if prev_mtime is None:
            state[key] = {"mtime": mtime, "off_limits_hash": off_limits_hash}
            state_dirty = True
            continue

        # Real edit → audit + record change.
        append_audit_event(
            community.customer_id,
            "voice_profile_changed",
            {
                "community_id": community.community_id,
                "previous_mtime": prev_mtime,
                "current_mtime": mtime,
                "off_limits_hash_changed": off_limits_changed,
                "previous_off_limits_hash": prev_hash or None,
                "current_off_limits_hash": off_limits_hash or None,
            },
        )
        changes.append(ProfileChange(
            customer_id=community.customer_id,
            community_id=community.community_id,
            previous_mtime=prev_mtime,
            current_mtime=mtime,
            off_limits_hash_changed=off_limits_changed,
        ))
        state[key] = {"mtime": mtime, "off_limits_hash": off_limits_hash}
        state_dirty = True

    if state_dirty:
        _write_state(state)

    return changes
