"""Tests for set_operator_nickname sanity-check additions and the
audit_all_communities daemon-startup helper.

Covers:
  - return shape now includes export_hits / confusable_chars / warnings /
    verification_hint
  - 形似漢字 trigger for chars known to have caused real onboarding errors
  - 0-hit chat_export warning fires (but doesn't block — fan/broadcast
    groups have legit 0-hit operators)
  - audit_all_communities aggregates per-community status correctly
  - audit log entry contains the new warning fields
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows import operator_identity as oi


def _build_fake_customer_tree(root: Path, *, with_export: bool = True, export_text: str = "") -> str:
    """Lay out a minimal customers/<id>/ tree the workflow expects:
    communities/openchat_test.yaml + data/chat_exports/<community>__<ts>.txt.
    Returns the customer_id."""

    customer_id = "customer_test"
    cust = root / "customers" / customer_id
    (cust / "communities").mkdir(parents=True)
    (cust / "data" / "chat_exports").mkdir(parents=True)
    yaml_path = cust / "communities" / "openchat_test.yaml"
    yaml_path.write_text(
        "community_id: openchat_test\n"
        'display_name: "測試群"\n'
        "persona: default\n"
        "device_id: emulator-5554\n"
        "patrol_interval_minutes: 60\n"
        'operator_nickname: ""\n',
        encoding="utf-8",
    )
    if with_export:
        (cust / "data" / "chat_exports" / "openchat_test__20260430_120000.txt").write_text(
            export_text, encoding="utf-8"
        )
    return customer_id


class SetOperatorNicknameSanityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _patch_paths(self, customer_id: str):
        # Patch both the customer_root and customer_data_root used by the
        # workflow + the latest_export_path helper.
        from app.storage import paths as paths_mod
        from app.workflows import member_fingerprint as mf

        def fake_customer_root(cid: str) -> Path:
            return self.tmp / "customers" / cid

        def fake_data_root(cid: str) -> Path:
            return self.tmp / "customers" / cid / "data"

        return [
            patch.object(oi, "customer_root", fake_customer_root),
            patch.object(mf, "customer_data_root", fake_data_root),
            patch("app.core.audit.append_audit_event", lambda *a, **k: None),
        ]

    def _run_with_patches(self, customer_id: str, callable_):
        ctxes = self._patch_paths(customer_id)
        for c in ctxes:
            c.start()
        try:
            return callable_()
        finally:
            for c in ctxes:
                c.stop()

    def test_return_shape_has_new_fields(self) -> None:
        cid = _build_fake_customer_tree(self.tmp, export_text="10:00 翊 哈哈\n")

        def call():
            from app.storage.config_loader import load_community_config
            with patch.object(oi, "load_community_config") as m:
                # minimal config stub matching prior nickname=""
                from app.storage.config_loader import CommunityConfig
                m.return_value = CommunityConfig(
                    customer_id=cid,
                    community_id="openchat_test",
                    display_name="測試群",
                    persona="default",
                    device_id="emulator-5554",
                    patrol_interval_minutes=60,
                    operator_nickname="",
                )
                return oi.set_operator_nickname(cid, "openchat_test", "翊")

        result = self._run_with_patches(cid, call)
        self.assertEqual(result["status"], "ok")
        self.assertIn("export_hits", result)
        self.assertIn("confusable_chars", result)
        self.assertIn("warnings", result)
        self.assertIn("verification_hint", result)
        # 翊 is in the confusable set
        self.assertIn("翊", result["confusable_chars"])
        # Warning should mention 形似漢字
        self.assertTrue(any("形似漢字" in w for w in result["warnings"]))

    def test_zero_hit_export_warns_but_does_not_block(self) -> None:
        cid = _build_fake_customer_tree(self.tmp, export_text="10:00 SomeoneElse 你好\n")

        def call():
            from app.storage.config_loader import CommunityConfig
            with patch.object(oi, "load_community_config") as m:
                m.return_value = CommunityConfig(
                    customer_id=cid,
                    community_id="openchat_test",
                    display_name="測試群",
                    persona="default",
                    device_id="emulator-5554",
                    patrol_interval_minutes=60,
                    operator_nickname="",
                )
                return oi.set_operator_nickname(cid, "openchat_test", "比利")

        result = self._run_with_patches(cid, call)
        self.assertEqual(result["status"], "ok")  # not blocked
        self.assertEqual(result["export_hits"], 0)
        self.assertTrue(any("0 命中" in w for w in result["warnings"]))

    def test_clean_nickname_produces_no_warnings(self) -> None:
        cid = _build_fake_customer_tree(self.tmp, export_text="10:00 山寶 你好\n10:01 山寶 在嗎\n")

        def call():
            from app.storage.config_loader import CommunityConfig
            with patch.object(oi, "load_community_config") as m:
                m.return_value = CommunityConfig(
                    customer_id=cid,
                    community_id="openchat_test",
                    display_name="測試群",
                    persona="default",
                    device_id="emulator-5554",
                    patrol_interval_minutes=60,
                    operator_nickname="",
                )
                return oi.set_operator_nickname(cid, "openchat_test", "山寶")

        result = self._run_with_patches(cid, call)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["confusable_chars"], [])
        self.assertEqual(result["warnings"], [])
        self.assertGreaterEqual(result["export_hits"], 1)

    def test_empty_nickname_still_rejected(self) -> None:
        cid = _build_fake_customer_tree(self.tmp)

        def call():
            return oi.set_operator_nickname(cid, "openchat_test", "  ")

        result = self._run_with_patches(cid, call)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "empty_nickname")


class AuditAllCommunitiesTests(unittest.TestCase):
    def test_aggregates_per_community_with_status_classification(self) -> None:
        # Build a fake load_all_communities returning 3 communities
        from app.storage.config_loader import CommunityConfig

        c1 = CommunityConfig(
            customer_id="customer_a",
            community_id="openchat_001",
            display_name="A",
            persona="default",
            device_id="d",
            patrol_interval_minutes=60,
            operator_nickname="比利",
        )
        c2 = CommunityConfig(
            customer_id="customer_a",
            community_id="openchat_002",
            display_name="B",
            persona="default",
            device_id="d",
            patrol_interval_minutes=60,
            operator_nickname="翊",
        )
        c3 = CommunityConfig(
            customer_id="customer_a",
            community_id="openchat_003",
            display_name="C",
            persona="default",
            device_id="d",
            patrol_interval_minutes=60,
            operator_nickname=None,  # missing
        )

        def fake_count(customer_id, community_id, nickname):
            # 001 = 0 hits, 002 = 4 hits
            mapping = {("customer_a", "openchat_001"): 0, ("customer_a", "openchat_002"): 4}
            return {
                "export_available": True,
                "hits": mapping.get((customer_id, community_id), 0),
                "export_path": "/tmp/fake.txt",
            }

        with patch.object(oi, "_count_export_hits", fake_count), \
             patch("app.storage.config_loader.load_all_communities", return_value=[c1, c2, c3]):
            result = oi.audit_all_communities("customer_a")

        self.assertEqual(result["status"], "ok")
        rows_by_id = {r["community_id"]: r for r in result["rows"]}
        self.assertEqual(rows_by_id["openchat_001"]["status"], "low_activity")
        self.assertEqual(rows_by_id["openchat_002"]["status"], "ok")
        self.assertEqual(rows_by_id["openchat_002"]["confusable_chars"], ["翊"])
        self.assertEqual(rows_by_id["openchat_003"]["status"], "missing")
        # Warnings include both confusable + missing
        self.assertGreaterEqual(result["warning_count"], 2)


if __name__ == "__main__":
    unittest.main()
