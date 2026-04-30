"""Tests for voice_profile mtime change detection."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.workflows import voice_profile_watcher as vpw


def _fake_community(community_id: str) -> MagicMock:
    c = MagicMock()
    c.customer_id = "customer_a"
    c.community_id = community_id
    return c


class DetectVoiceProfileChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._tmp_path = Path(self._tmp.name)

        # Fake state file location
        self._state_patch = patch.object(vpw, "_state_path",
                                         return_value=self._tmp_path / "state.json")
        self._state_patch.start()
        self.addCleanup(self._state_patch.stop)

        # Audit collector
        self.audit_calls: list = []
        self._audit_patch = patch.object(
            vpw, "append_audit_event",
            side_effect=lambda c, e, p: self.audit_calls.append((e, p)),
        )
        self._audit_patch.start()
        self.addCleanup(self._audit_patch.stop)

        # Fake voice_profile path resolver
        self._vp_files: dict[str, Path] = {}

        def _vp_path(customer_id, community_id):
            key = f"{customer_id}:{community_id}"
            if key not in self._vp_files:
                p = self._tmp_path / f"{community_id}.md"
                self._vp_files[key] = p
            return self._vp_files[key]

        self._vp_patch = patch.object(vpw, "voice_profile_path", side_effect=_vp_path)
        self._vp_patch.start()
        self.addCleanup(self._vp_patch.stop)

    def _create_vp(self, community_id: str, content: str = "# voice profile\n") -> Path:
        path = self._vp_files.get(f"customer_a:{community_id}")
        if path is None:
            path = self._tmp_path / f"{community_id}.md"
            self._vp_files[f"customer_a:{community_id}"] = path
        path.write_text(content, encoding="utf-8")
        return path

    def test_first_run_records_baseline_no_audit(self) -> None:
        """On first observation, just record current mtime — operator
        doesn't need an alert for voice_profile they haven't touched."""
        self._create_vp("openchat_004")
        with patch.object(vpw, "load_all_communities",
                          return_value=[_fake_community("openchat_004")]):
            changes = vpw.detect_voice_profile_changes()
        self.assertEqual(changes, [])
        self.assertEqual(self.audit_calls, [])

    def test_no_change_between_runs_no_audit(self) -> None:
        self._create_vp("openchat_004")
        with patch.object(vpw, "load_all_communities",
                          return_value=[_fake_community("openchat_004")]):
            vpw.detect_voice_profile_changes()  # baseline
            self.audit_calls.clear()
            changes = vpw.detect_voice_profile_changes()
        self.assertEqual(changes, [])
        self.assertEqual(self.audit_calls, [])

    def test_mtime_change_emits_audit(self) -> None:
        path = self._create_vp("openchat_004")
        with patch.object(vpw, "load_all_communities",
                          return_value=[_fake_community("openchat_004")]):
            vpw.detect_voice_profile_changes()  # baseline
            # Bump mtime by re-writing
            time.sleep(0.05)  # ensure mtime difference > 0.5s tolerance
            os.utime(path, (time.time() + 2, time.time() + 2))
            self.audit_calls.clear()
            changes = vpw.detect_voice_profile_changes()
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].community_id, "openchat_004")
        types = [e for e, _ in self.audit_calls]
        self.assertIn("voice_profile_changed", types)

    def test_off_limits_change_flagged(self) -> None:
        path = self._create_vp("openchat_004", content="""---
value_proposition: x
route_mix:
  ip: 1
  interest: 0
  info: 0
stage: 留存
---
## My nickname
- 妍
## My personality
沉穩
## Off-limits
- 不解卦
""")
        with patch.object(vpw, "load_all_communities",
                          return_value=[_fake_community("openchat_004")]):
            vpw.detect_voice_profile_changes()  # baseline (with hash recorded)

            time.sleep(0.05)
            # Edit off-limits section
            path.write_text("""---
value_proposition: x
route_mix:
  ip: 1
  interest: 0
  info: 0
stage: 留存
---
## My nickname
- 妍
## My personality
沉穩
## Off-limits
- 不解卦
- 不討論政治
""", encoding="utf-8")
            os.utime(path, (time.time() + 2, time.time() + 2))
            self.audit_calls.clear()
            changes = vpw.detect_voice_profile_changes()

        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0].off_limits_hash_changed)
        evt_payload = next(p for e, p in self.audit_calls if e == "voice_profile_changed")
        self.assertTrue(evt_payload["off_limits_hash_changed"])

    def test_missing_voice_profile_skipped(self) -> None:
        # Don't create the file — community has voice_profile_path that doesn't exist
        with patch.object(vpw, "load_all_communities",
                          return_value=[_fake_community("openchat_999")]):
            changes = vpw.detect_voice_profile_changes()
        self.assertEqual(changes, [])
        self.assertEqual(self.audit_calls, [])


if __name__ == "__main__":
    unittest.main()
