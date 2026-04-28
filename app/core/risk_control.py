from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, time as day_time


@dataclass(frozen=True)
class RiskControl:
    fixed_ip_mode: bool = True
    activity_start: day_time = day_time(9, 0)
    activity_end: day_time = day_time(23, 0)
    min_send_delay_seconds: float = 5.0
    max_send_delay_seconds: float = 30.0
    account_cooldown_seconds: int = 900
    community_cooldown_seconds: int = 1800
    require_human_approval: bool = True

    def is_activity_time(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now()).time()
        return self.activity_start <= current <= self.activity_end

    def random_send_delay(self) -> float:
        return random.uniform(self.min_send_delay_seconds, self.max_send_delay_seconds)

    def wait_before_send(self) -> float:
        delay = self.random_send_delay()
        time.sleep(delay)
        return delay


default_risk_control = RiskControl()
