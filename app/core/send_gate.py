from __future__ import annotations

import threading
import time

from app.core.risk_control import RiskControl


class SendGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_account_send: dict[str, float] = {}
        self._last_community_send: dict[str, float] = {}

    def wait_turn(
        self,
        account_key: str,
        community_key: str,
        risk_control: RiskControl,
    ) -> dict[str, float]:
        with self._lock:
            now = time.time()
            account_wait = self._remaining_wait(self._last_account_send.get(account_key), risk_control.account_cooldown_seconds, now)
            community_wait = self._remaining_wait(self._last_community_send.get(community_key), risk_control.community_cooldown_seconds, now)
            total_wait = max(account_wait, community_wait)
            if total_wait > 0:
                time.sleep(total_wait)
                now = time.time()
            self._last_account_send[account_key] = now
            self._last_community_send[community_key] = now
            return {"waited_seconds": total_wait}

    @staticmethod
    def _remaining_wait(last_sent_at: float | None, cooldown_seconds: int, now: float) -> float:
        if last_sent_at is None:
            return 0.0
        remaining = cooldown_seconds - (now - last_sent_at)
        return max(0.0, remaining)


send_gate = SendGate()
