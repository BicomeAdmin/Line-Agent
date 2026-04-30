"""Recurrence helpers for scheduled posts.

Schema (stored on `ScheduledPost.recurrence` as a dict — JSON-friendly):

    {
        "kind": "once" | "daily" | "weekly" | "monthly",
        "time_tpe": "20:00",                         # 24h HH:MM, Asia/Taipei
        "weekday": "mon",                             # weekly only: mon..sun
        "day_of_month": 1,                            # monthly only: 1-31
        "until_iso": "2026-12-31T23:59:00+08:00",    # optional terminator
        "max_occurrences": 12,                        # optional ceiling
        "occurrences_fired": 0,                       # bumped on each spawn
    }

`once` is a no-op (returned as None next-occurrence). `daily` / `weekly` /
`monthly` advance from the last `send_at_epoch` in TPE local time, then
convert back to UTC epoch. We deliberately keep the schema small — full
RRULE / iCal is overkill for the operator workflow this serves.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

TPE = ZoneInfo("Asia/Taipei")

VALID_KINDS = {"once", "daily", "weekly", "monthly"}
WEEKDAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# Safety caps when neither until_iso nor max_occurrences is set —
# prevents an unbounded recurrence accidentally booking years of posts.
# Tuned to land at "approximately 1-2 years out" for each cadence so
# the operator has plenty of runway but doesn't accumulate forever.
_DEFAULT_MAX_OCCURRENCES = {
    "daily":   90,    # ~3 months
    "weekly":  52,    # ~1 year
    "monthly": 24,    # ~2 years
}


class RecurrenceError(ValueError):
    """Raised when recurrence dict is malformed."""


def normalize_recurrence(raw: Any) -> dict[str, Any] | None:
    """Validate + normalize an operator-supplied recurrence dict.

    Returns None for `kind="once"` (treated as no recurrence). Raises
    RecurrenceError on malformed input. The result is JSON-safe.
    """

    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RecurrenceError(f"recurrence must be an object, got {type(raw).__name__}")

    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in VALID_KINDS:
        raise RecurrenceError(f"recurrence.kind must be one of {sorted(VALID_KINDS)}, got {kind!r}")
    if kind == "once":
        return None

    time_tpe = str(raw.get("time_tpe") or "").strip()
    try:
        hour, minute = time_tpe.split(":")
        hour_i, minute_i = int(hour), int(minute)
    except (ValueError, AttributeError) as exc:
        raise RecurrenceError(f"recurrence.time_tpe must be 'HH:MM', got {time_tpe!r}") from exc
    if not (0 <= hour_i < 24 and 0 <= minute_i < 60):
        raise RecurrenceError(f"recurrence.time_tpe out of range: {time_tpe!r}")

    norm: dict[str, Any] = {
        "kind": kind,
        "time_tpe": f"{hour_i:02d}:{minute_i:02d}",
    }

    if kind == "weekly":
        wd = str(raw.get("weekday") or "").strip().lower()
        if wd not in WEEKDAY_MAP:
            raise RecurrenceError(f"recurrence.weekday must be mon..sun, got {wd!r}")
        norm["weekday"] = wd
    elif kind == "monthly":
        try:
            dom = int(raw.get("day_of_month"))
        except (TypeError, ValueError) as exc:
            raise RecurrenceError("recurrence.day_of_month must be int 1-28") from exc
        if not (1 <= dom <= 28):
            # 28 ceiling avoids per-month edge cases — operator who wants
            # "last day of month" can use a future enhancement.
            raise RecurrenceError(f"recurrence.day_of_month must be 1-28, got {dom}")
        norm["day_of_month"] = dom

    until_iso = raw.get("until_iso")
    if until_iso is not None:
        if not isinstance(until_iso, str) or not until_iso.strip():
            raise RecurrenceError("recurrence.until_iso must be ISO 8601 string with timezone")
        try:
            dt = datetime.fromisoformat(until_iso.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RecurrenceError(f"recurrence.until_iso parse failed: {until_iso!r}") from exc
        if dt.tzinfo is None:
            raise RecurrenceError("recurrence.until_iso must include timezone offset")
        norm["until_iso"] = dt.isoformat()

    max_occ = raw.get("max_occurrences")
    if max_occ is not None:
        try:
            max_occ_i = int(max_occ)
        except (TypeError, ValueError) as exc:
            raise RecurrenceError("recurrence.max_occurrences must be int") from exc
        if max_occ_i < 1:
            raise RecurrenceError("recurrence.max_occurrences must be >= 1")
        norm["max_occurrences"] = max_occ_i
    elif "until_iso" not in norm:
        # Neither bound provided — apply the safety cap. Operator can
        # explicitly set max_occurrences=999 to opt out, but this stops
        # a typo from booking infinite occurrences.
        capped = _DEFAULT_MAX_OCCURRENCES.get(kind)
        if capped is not None:
            norm["max_occurrences"] = capped
            norm["max_occurrences_was_defaulted"] = True

    fired = raw.get("occurrences_fired")
    if fired is None:
        norm["occurrences_fired"] = 0
    else:
        try:
            norm["occurrences_fired"] = max(0, int(fired))
        except (TypeError, ValueError):
            norm["occurrences_fired"] = 0

    return norm


def parse_recurrence_string(spec: str) -> dict[str, Any] | None:
    """Convenience parser for CLI: 'weekly:mon@20:00' / 'daily@08:00' / 'monthly:1@10:00' / 'once'.

    Returns the normalized dict (or None for 'once'). Raises RecurrenceError
    on malformed input.
    """

    text = (spec or "").strip().lower()
    if not text or text == "once":
        return None

    if "@" not in text:
        raise RecurrenceError(f"recurrence spec must contain '@HH:MM', got {spec!r}")
    head, time_part = text.rsplit("@", 1)

    if head == "daily":
        return normalize_recurrence({"kind": "daily", "time_tpe": time_part})
    if head.startswith("weekly:"):
        weekday = head.split(":", 1)[1]
        return normalize_recurrence({"kind": "weekly", "time_tpe": time_part, "weekday": weekday})
    if head.startswith("monthly:"):
        try:
            dom = int(head.split(":", 1)[1])
        except ValueError as exc:
            raise RecurrenceError(f"monthly spec needs day_of_month: {spec!r}") from exc
        return normalize_recurrence({"kind": "monthly", "time_tpe": time_part, "day_of_month": dom})

    raise RecurrenceError(f"unrecognized recurrence spec: {spec!r}")


def next_occurrence(
    recurrence: dict[str, Any] | None,
    *,
    after_epoch: float,
) -> tuple[float, str] | None:
    """Compute the next send-at after `after_epoch` (UTC seconds).

    Returns (epoch, iso_with_tz) or None if recurrence is exhausted /
    None / 'once'. The iso string is in TPE for operator readability;
    callers store both fields like the rest of the post lifecycle.
    """

    rec = recurrence
    if not rec:
        return None
    kind = rec.get("kind")
    if kind not in {"daily", "weekly", "monthly"}:
        return None

    fired = int(rec.get("occurrences_fired") or 0)
    max_occ = rec.get("max_occurrences")
    if isinstance(max_occ, int) and fired >= max_occ:
        return None

    time_tpe = str(rec.get("time_tpe") or "00:00")
    hour_s, minute_s = time_tpe.split(":")
    hour, minute = int(hour_s), int(minute_s)

    after_dt_tpe = datetime.fromtimestamp(after_epoch, TPE)

    if kind == "daily":
        candidate = after_dt_tpe.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= after_dt_tpe:
            candidate += timedelta(days=1)
    elif kind == "weekly":
        target_wd = WEEKDAY_MAP[str(rec.get("weekday"))]
        candidate = after_dt_tpe.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_ahead = (target_wd - candidate.weekday()) % 7
        if days_ahead == 0 and candidate <= after_dt_tpe:
            days_ahead = 7
        candidate += timedelta(days=days_ahead)
    elif kind == "monthly":
        dom = int(rec.get("day_of_month") or 1)
        candidate = after_dt_tpe.replace(day=dom, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= after_dt_tpe:
            # Roll into next month
            month = candidate.month + 1
            year = candidate.year + (1 if month > 12 else 0)
            month = ((month - 1) % 12) + 1
            candidate = candidate.replace(year=year, month=month)
    else:  # pragma: no cover — guarded above
        return None

    until_iso = rec.get("until_iso")
    if isinstance(until_iso, str) and until_iso:
        until_dt = datetime.fromisoformat(until_iso.replace("Z", "+00:00"))
        if candidate > until_dt:
            return None

    return candidate.timestamp(), candidate.isoformat()


def bump_fired(recurrence: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a copy of `recurrence` with `occurrences_fired` incremented by 1."""

    if not recurrence:
        return None
    out = dict(recurrence)
    out["occurrences_fired"] = int(out.get("occurrences_fired") or 0) + 1
    return out
