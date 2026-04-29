"""Tests for onboarding readiness check."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage.config_loader import CommunityConfig
from app.workflows import onboarding_status as os_mod


def _make_community(**overrides) -> CommunityConfig:
    defaults = dict(
        customer_id="customer_a",
        community_id="openchat_test",
        display_name="test community",
        persona="default",
        device_id="emulator-5554",
        patrol_interval_minutes=120,
        operator_nickname="Alar",
        invite_url="https://line.me/ti/g2/abc",
        auto_watch_enabled=False,
    )
    defaults.update(overrides)
    return CommunityConfig(**defaults)


class OnboardingStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vp_path = self.root / "vp.md"
        self.fp_path = self.root / "fp.json"

        self.vp_patch = patch.object(
            os_mod, "voice_profile_path", return_value=self.vp_path
        )
        self.fp_patch = patch.object(
            os_mod, "fingerprints_path", return_value=self.fp_path
        )
        self.vp_patch.start()
        self.fp_patch.start()

    def tearDown(self) -> None:
        self.vp_patch.stop()
        self.fp_patch.stop()
        self.tmp.cleanup()

    def test_fully_ready_community(self) -> None:
        self.vp_path.write_text("x" * 1500, encoding="utf-8")
        self.fp_path.write_text('{"alice": {}}', encoding="utf-8")
        report = os_mod.build_onboarding_report([_make_community()])
        c = report.communities[0]
        self.assertTrue(c.ready_for_auto_watch)
        self.assertEqual(c.critical_gaps, ())
        self.assertEqual(c.soft_gaps, ())

    def test_missing_operator_nickname_is_critical(self) -> None:
        self.vp_path.write_text("x" * 1500, encoding="utf-8")
        self.fp_path.write_text('{"alice": {}}', encoding="utf-8")
        report = os_mod.build_onboarding_report([_make_community(operator_nickname=None)])
        c = report.communities[0]
        self.assertIn("operator_nickname", c.critical_gaps)
        self.assertFalse(c.ready_for_auto_watch)

    def test_missing_voice_profile_is_critical(self) -> None:
        self.fp_path.write_text('{"alice": {}}', encoding="utf-8")
        report = os_mod.build_onboarding_report([_make_community()])
        c = report.communities[0]
        self.assertIn("voice_profile", c.critical_gaps)

    def test_stub_voice_profile_is_soft_gap_not_critical(self) -> None:
        self.vp_path.write_text("# stub", encoding="utf-8")
        self.fp_path.write_text('{"alice": {}}', encoding="utf-8")
        report = os_mod.build_onboarding_report([_make_community()])
        c = report.communities[0]
        # Has a profile (even if stub) → not in critical
        self.assertNotIn("voice_profile", c.critical_gaps)
        # But under threshold → soft gap
        self.assertIn("voice_profile_stub", c.soft_gaps)

    def test_missing_fingerprints_is_soft(self) -> None:
        self.vp_path.write_text("x" * 1500, encoding="utf-8")
        report = os_mod.build_onboarding_report([_make_community()])
        c = report.communities[0]
        self.assertIn("member_fingerprints", c.soft_gaps)
        self.assertEqual(c.critical_gaps, ())

    def test_missing_invite_or_group_is_critical(self) -> None:
        self.vp_path.write_text("x" * 1500, encoding="utf-8")
        self.fp_path.write_text('{"alice": {}}', encoding="utf-8")
        report = os_mod.build_onboarding_report(
            [_make_community(invite_url=None, group_id=None)]
        )
        c = report.communities[0]
        self.assertIn("invite_url_or_group_id", c.critical_gaps)

    def test_auto_watch_with_gaps_surfaces(self) -> None:
        report = os_mod.build_onboarding_report(
            [_make_community(auto_watch_enabled=True, operator_nickname=None)]
        )
        self.assertEqual(len(report.auto_watch_with_gaps), 1)
        self.assertEqual(report.critical_count, 1)


if __name__ == "__main__":
    unittest.main()
