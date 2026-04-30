"""Tests for the 24h self-detection health check (Phase B defense 3).

The check counts operator self-messages in recent chat_export and
compares against a route_mix-based threshold. Failures emit
`operator_self_detection_low` audit events that alert_aggregator
surfaces as `important` severity.

Tests cover:
  - threshold selection by route_mix
  - skip when below minimum sample volume
  - skip when nickname missing or no export
  - healthy classification when self_ratio meets threshold
  - failed classification when self_ratio below threshold
  - audit emission on failure (mocked)
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.workflows import self_detection_health as sdh


class ThresholdSelectionTests(unittest.TestCase):
    def test_ip_dominant_selects_higher_threshold(self) -> None:
        thr, label = sdh._expected_threshold({"ip": 0.5, "interest": 0.3, "info": 0.2})
        self.assertEqual(thr, 0.05)
        self.assertEqual(label, "ip")

    def test_info_dominant_selects_highest_threshold(self) -> None:
        thr, label = sdh._expected_threshold({"info": 0.6, "ip": 0.2})
        self.assertEqual(thr, 0.10)
        self.assertEqual(label, "info")

    def test_default_when_no_route_dominates(self) -> None:
        thr, label = sdh._expected_threshold({"ip": 0.3, "interest": 0.4, "info": 0.3})
        self.assertEqual(thr, 0.02)
        self.assertEqual(label, "default")

    def test_empty_route_mix_uses_default(self) -> None:
        thr, label = sdh._expected_threshold({})
        self.assertEqual(thr, 0.02)
        self.assertEqual(label, "default")


class CheckCommunityTests(unittest.TestCase):
    """check_community() is the per-community workhorse."""

    def _make_config(self, nickname: str | None = "比利"):
        from app.storage.config_loader import CommunityConfig
        return CommunityConfig(
            customer_id="customer_a",
            community_id="openchat_test",
            display_name="測試群",
            persona="default",
            device_id="emulator-5554",
            patrol_interval_minutes=60,
            operator_nickname=nickname,
        )

    def test_skip_when_no_nickname(self) -> None:
        cfg = self._make_config(nickname=None)
        with patch("app.storage.config_loader.load_community_config", return_value=cfg):
            result = sdh.check_community("customer_a", "openchat_test")
        self.assertEqual(result["status"], "skip")
        self.assertEqual(result["reason"], "no_nickname")

    def test_skip_when_no_export(self) -> None:
        cfg = self._make_config()
        with patch("app.storage.config_loader.load_community_config", return_value=cfg), \
             patch.object(sdh, "latest_export_path", return_value=None):
            result = sdh.check_community("customer_a", "openchat_test")
        self.assertEqual(result["status"], "skip")
        self.assertEqual(result["reason"], "no_export")

    def test_skip_when_below_min_volume(self) -> None:
        cfg = self._make_config()
        with patch("app.storage.config_loader.load_community_config", return_value=cfg), \
             patch.object(sdh, "latest_export_path", return_value="/fake/path"), \
             patch.object(sdh, "_load_recent_messages", return_value=[("X", "hi")] * 10), \
             patch.object(sdh, "_load_route_mix", return_value={}):
            result = sdh.check_community("customer_a", "openchat_test")
        self.assertEqual(result["status"], "skip")
        self.assertEqual(result["reason"], "below_min_volume")

    def test_healthy_when_self_ratio_meets_default_threshold(self) -> None:
        cfg = self._make_config(nickname="X")
        # 50 msgs, 5 from operator → 10% (well above 2% default)
        msgs = [("Y", "a")] * 45 + [("X", "b")] * 5
        with patch("app.storage.config_loader.load_community_config", return_value=cfg), \
             patch.object(sdh, "latest_export_path", return_value="/fake/path"), \
             patch.object(sdh, "_load_recent_messages", return_value=msgs), \
             patch.object(sdh, "_load_route_mix", return_value={}):
            result = sdh.check_community("customer_a", "openchat_test")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["healthy"])
        self.assertEqual(result["self_messages"], 5)
        self.assertEqual(result["total_messages"], 50)

    def test_unhealthy_when_self_ratio_zero(self) -> None:
        cfg = self._make_config(nickname="X")
        # 50 msgs, 0 from operator
        msgs = [("Y", "a")] * 50
        with patch("app.storage.config_loader.load_community_config", return_value=cfg), \
             patch.object(sdh, "latest_export_path", return_value="/fake/path"), \
             patch.object(sdh, "_load_recent_messages", return_value=msgs), \
             patch.object(sdh, "_load_route_mix", return_value={}):
            result = sdh.check_community("customer_a", "openchat_test")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["healthy"])
        self.assertEqual(result["self_messages"], 0)

    def test_ip_route_uses_higher_threshold(self) -> None:
        cfg = self._make_config(nickname="X")
        # 50 msgs, 1 from operator → 2% (above default 2%, below ip 5%)
        msgs = [("Y", "a")] * 49 + [("X", "b")]
        with patch("app.storage.config_loader.load_community_config", return_value=cfg), \
             patch.object(sdh, "latest_export_path", return_value="/fake/path"), \
             patch.object(sdh, "_load_recent_messages", return_value=msgs), \
             patch.object(sdh, "_load_route_mix", return_value={"ip": 0.5}):
            result = sdh.check_community("customer_a", "openchat_test")
        # Under ip threshold — must fail
        self.assertEqual(result["route_label"], "ip")
        self.assertFalse(result["healthy"])


class RunHealthCheckAuditTests(unittest.TestCase):
    def test_failure_emits_audit_event(self) -> None:
        from app.storage.config_loader import CommunityConfig

        cfg = CommunityConfig(
            customer_id="customer_a",
            community_id="openchat_test",
            display_name="測試群",
            persona="default",
            device_id="d",
            patrol_interval_minutes=60,
            operator_nickname="X",
        )
        msgs = [("Y", "a")] * 50  # all non-operator → ratio 0 → fail

        emitted: list[tuple] = []

        def fake_audit(customer_id, event_type, payload):
            emitted.append((event_type, payload))

        with patch("app.workflows.self_detection_health.load_all_communities", return_value=[cfg]), \
             patch("app.storage.config_loader.load_community_config", return_value=cfg), \
             patch.object(sdh, "latest_export_path", return_value="/fake/path"), \
             patch.object(sdh, "_load_recent_messages", return_value=msgs), \
             patch.object(sdh, "_load_route_mix", return_value={}), \
             patch("app.workflows.self_detection_health.append_audit_event", fake_audit):
            result = sdh.run_health_check("customer_a")

        self.assertEqual(result["failed_count"], 1)
        # Two audit calls: one operator_self_detection_low, one summary
        types = [e[0] for e in emitted]
        self.assertIn("operator_self_detection_low", types)
        self.assertIn("operator_self_detection_check", types)


if __name__ == "__main__":
    unittest.main()
