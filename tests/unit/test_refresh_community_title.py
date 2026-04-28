"""Tests for refresh_community_title — verify pure-logic paths.

The auto-detect path (deep link → UI dump) is hardware-dependent and lives
in `_detect_display_name`; we cover it via monkeypatched stubs here. The
explicit-override path is fully unit-testable.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows import community_onboarding


def _make_community_stub(
    *,
    customer_id="customer_a",
    community_id="openchat_004",
    display_name="未命名社群 (-VDtB9z6…)",
    group_id="-VDtB9z6f5vJPUwsjbZDRLcblPLrildOO53hsA",
    device_id="emulator-5554",
):
    return type(
        "Community",
        (),
        {
            "customer_id": customer_id,
            "community_id": community_id,
            "display_name": display_name,
            "group_id": group_id,
            "device_id": device_id,
        },
    )()


YAML_TEMPLATE = (
    'community_id: openchat_004\n'
    'display_name: "未命名社群 (-VDtB9z6…)"\n'
    'invite_url: "https://line.me/ti/g2/-VDtB9z6f5vJPUwsjbZDRLcblPLrildOO53hsA"\n'
    'group_id: "-VDtB9z6f5vJPUwsjbZDRLcblPLrildOO53hsA"\n'
    'persona: default\n'
    'device_id: emulator-5554\n'
    'patrol_interval_minutes: 720\n'
    'enabled: true\n'
    'input_x: null\n'
    'input_y: null\n'
    'send_x: null\n'
    'send_y: null\n'
)


class RefreshCommunityTitleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.yaml_path = self.tmp_path / "communities" / "openchat_004.yaml"
        self.yaml_path.parent.mkdir(parents=True)
        self.yaml_path.write_text(YAML_TEMPLATE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _patches(self):
        # Patch the YAML path resolver + audit + community loader so we operate
        # entirely on the temp dir without touching real customer state.
        return [
            patch.object(
                community_onboarding,
                "_community_yaml_path",
                lambda customer_id, community_id: self.yaml_path,
            ),
            patch.object(community_onboarding, "append_audit_event", lambda *a, **k: None),
        ]

    def _enter(self, patches):
        return [p.start() for p in patches]

    def _stop(self, patches):
        for p in patches:
            p.stop()

    def test_explicit_override_rewrites_display_name(self):
        patches = self._patches()
        with patch(
            "app.storage.config_loader.load_community_config",
            return_value=_make_community_stub(),
        ):
            self._enter(patches)
            try:
                result = community_onboarding.refresh_community_title(
                    "customer_a",
                    "openchat_004",
                    display_name="開發者實驗群",
                )
            finally:
                self._stop(patches)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["changed"])
        self.assertEqual(result["display_name"], "開發者實驗群")
        # YAML file actually rewritten:
        text = self.yaml_path.read_text(encoding="utf-8")
        self.assertIn('display_name: "開發者實驗群"', text)
        self.assertNotIn("未命名社群", text)
        # Other fields preserved:
        self.assertIn("group_id: \"-VDtB9z6", text)
        self.assertIn("patrol_interval_minutes: 720", text)

    def test_explicit_override_empty_string_rejected(self):
        with patch(
            "app.storage.config_loader.load_community_config",
            return_value=_make_community_stub(),
        ):
            result = community_onboarding.refresh_community_title(
                "customer_a",
                "openchat_004",
                display_name="   ",
            )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "display_name_empty")

    def test_no_change_when_name_matches(self):
        patches = self._patches()
        with patch(
            "app.storage.config_loader.load_community_config",
            return_value=_make_community_stub(display_name="同名"),
        ):
            self._enter(patches)
            try:
                result = community_onboarding.refresh_community_title(
                    "customer_a",
                    "openchat_004",
                    display_name="同名",
                )
            finally:
                self._stop(patches)

        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["changed"])

    def test_auto_detect_uses_deep_link_extraction(self):
        patches = self._patches()
        with patch(
            "app.storage.config_loader.load_community_config",
            return_value=_make_community_stub(),
        ), patch.object(
            community_onboarding,
            "_detect_display_name",
            return_value=("山納百景", ["deep_link_dispatched", "title_found:山納百景"]),
        ):
            self._enter(patches)
            try:
                result = community_onboarding.refresh_community_title(
                    "customer_a",
                    "openchat_004",
                )
            finally:
                self._stop(patches)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["changed"])
        self.assertEqual(result["display_name"], "山納百景")
        self.assertIn("title_found:山納百景", result["trace"])
        text = self.yaml_path.read_text(encoding="utf-8")
        self.assertIn('display_name: "山納百景"', text)

    def test_auto_detect_failure_reports_trace(self):
        with patch(
            "app.storage.config_loader.load_community_config",
            return_value=_make_community_stub(),
        ), patch.object(
            community_onboarding,
            "_detect_display_name",
            return_value=(None, ["deep_link_dispatched", "title_not_found"]),
        ):
            result = community_onboarding.refresh_community_title(
                "customer_a",
                "openchat_004",
            )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "title_not_detected")
        self.assertIn("title_not_found", result["trace"])
        # Caller still gets the old name back so the LLM can mention it:
        self.assertEqual(result["old_display_name"], "未命名社群 (-VDtB9z6…)")

    def test_missing_group_id_fails_fast(self):
        with patch(
            "app.storage.config_loader.load_community_config",
            return_value=_make_community_stub(group_id=""),
        ):
            result = community_onboarding.refresh_community_title(
                "customer_a",
                "openchat_004",
            )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "no_group_id_on_community")


if __name__ == "__main__":
    unittest.main()
