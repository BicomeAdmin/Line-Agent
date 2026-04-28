import unittest

from app.core.risk_control import RiskControl
from app.core.send_gate import SendGate


class SendGateTests(unittest.TestCase):
    def test_first_send_has_no_wait(self) -> None:
        gate = SendGate()
        meta = gate.wait_turn("account-1", "community-1", RiskControl(account_cooldown_seconds=0, community_cooldown_seconds=0))
        self.assertEqual(meta["waited_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
