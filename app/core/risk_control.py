from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, time as day_time


def _read_hour_env(var: str, default: int) -> int:
    """Read an HH (0-23) integer hour from env, fall back gracefully."""
    raw = os.getenv(var, "").strip()
    if not raw:
        return default
    try:
        h = int(raw)
        if 0 <= h <= 23:
            return h
    except ValueError:
        pass
    return default


# Taipei local hours operator considers "active" — outside this window
# autonomous fires (watcher / patrol) stay silent. Operator-triggered
# Lark commands are still processed any time. Override via env:
#   ACTIVITY_HOURS_START=10
#   ACTIVITY_HOURS_END=22
_DEFAULT_ACTIVITY_START_HOUR = 10
_DEFAULT_ACTIVITY_END_HOUR = 22


@dataclass(frozen=True)
class RiskControl:
    fixed_ip_mode: bool = True
    activity_start: day_time = field(
        default_factory=lambda: day_time(_read_hour_env("ACTIVITY_HOURS_START", _DEFAULT_ACTIVITY_START_HOUR), 0)
    )
    activity_end: day_time = field(
        default_factory=lambda: day_time(_read_hour_env("ACTIVITY_HOURS_END", _DEFAULT_ACTIVITY_END_HOUR), 0)
    )
    min_send_delay_seconds: float = 5.0
    max_send_delay_seconds: float = 30.0
    account_cooldown_seconds: int = 900
    community_cooldown_seconds: int = 1800
    require_human_approval: bool = True

    def is_activity_time(self, now: datetime | None = None) -> bool:
        # activity_start / activity_end are interpreted as Asia/Taipei
        # local times (operator's reference frame). Use the Taipei helper
        # explicitly so the check is correct regardless of host timezone.
        from app.core.timezone import taipei_now
        current = (now or taipei_now()).time()
        return self.activity_start <= current <= self.activity_end

    def random_send_delay(self) -> float:
        return random.uniform(self.min_send_delay_seconds, self.max_send_delay_seconds)

    def wait_before_send(self) -> float:
        delay = self.random_send_delay()
        time.sleep(delay)
        return delay


default_risk_control = RiskControl()


def community_is_in_activity_window(community, *, now: datetime | None = None) -> bool:
    """Per-community activity window with global fallback.

    A community can override the global window via `activity_window.start_hour_tpe`
    / `activity_window.end_hour_tpe` in its YAML (e.g. a high-engagement group
    that's active 08:00-23:30, or a low-noise group restricted to 14:00-18:00).
    When either field is None the community defers to `default_risk_control`.

    The override is intentionally simple — hour-only, no minutes, no DOW/holiday
    rules. If we ever need finer granularity, extend `CommunityConfig` and this
    function together rather than scattering window logic across callers.
    """

    start_hour = getattr(community, "activity_start_hour_tpe", None)
    end_hour = getattr(community, "activity_end_hour_tpe", None)
    # Strict isinstance check (not just `is None`) so test mocks or partially
    # populated configs that happen to have a non-int sentinel for these
    # fields cleanly fall through to the global default.
    if not (isinstance(start_hour, int) and isinstance(end_hour, int)):
        return default_risk_control.is_activity_time(now)

    from app.core.timezone import taipei_now
    current = (now or taipei_now()).time()
    return day_time(start_hour, 0) <= current <= day_time(end_hour, 0)
