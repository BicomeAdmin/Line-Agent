"""Display-layer timezone helpers. Storage stays UTC ISO 8601.

CLAUDE.md §1.1 mandates Asia/Taipei for any operator-facing timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Python < 3.9 fallback (we target 3.11)
    ZoneInfo = None  # type: ignore[assignment]


TAIPEI = ZoneInfo("Asia/Taipei") if ZoneInfo is not None else timezone.utc


def taipei_now() -> datetime:
    return datetime.now(TAIPEI)


def taipei_now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return taipei_now().strftime(fmt)


def to_taipei(value: datetime | str | None) -> datetime | None:
    """Convert a UTC datetime or ISO 8601 string to Asia/Taipei tz-aware datetime."""

    if value is None:
        return None
    if isinstance(value, str):
        text = value.replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(TAIPEI)


def to_taipei_str(value: datetime | str | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str | None:
    converted = to_taipei(value)
    return None if converted is None else converted.strftime(fmt)
