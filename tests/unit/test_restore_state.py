"""Tests for state restore workflow."""

import io
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.workflows import backup_state as bs
from app.workflows import restore_state as rs


class RestoreStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Build a minimal live state to back up
        (self.root / ".project_echo").mkdir()
        (self.root / ".project_echo" / "jobs.jsonl").write_text("v1\n", encoding="utf-8")
        (self.root / "configs").mkdir()
        (self.root / "configs" / "risk_control.yaml").write_text(
            "require_human_approval: true\n", encoding="utf-8"
        )
        cust = self.root / "customers" / "customer_a" / "data"
        cust.mkdir(parents=True)
        (cust / "audit.jsonl").write_text("{\"v\":1}\n", encoding="utf-8")

        self.audit_calls: list[tuple] = []

        def fake_audit(customer_id, event_type, payload):
            self.audit_calls.append((customer_id, event_type, payload))

        # Patch audit in BOTH modules (backup writes one too during safety backup)
        self.audit_patches = [
            patch.object(bs, "append_audit_event", side_effect=fake_audit),
            patch.object(rs, "append_audit_event", side_effect=fake_audit),
        ]
        for p in self.audit_patches:
            p.start()

        # Make a v1 backup
        self.archive_v1 = bs.run_backup(
            project_root=self.root,
            backup_dir=self.root / "backups",
            keep=14,
            now=datetime(2026, 4, 29, 0, 0, 0, tzinfo=timezone.utc),
        ).archive_path

        # Mutate live state to v2
        (self.root / ".project_echo" / "jobs.jsonl").write_text("v2\n", encoding="utf-8")
        (cust / "audit.jsonl").write_text("{\"v\":2}\n", encoding="utf-8")

        self.audit_calls.clear()

    def tearDown(self) -> None:
        for p in self.audit_patches:
            p.stop()
        self.tmp.cleanup()

    def test_restore_overwrites_files_with_archive_contents(self) -> None:
        result = rs.run_restore(
            self.archive_v1,
            project_root=self.root,
            customer_id="customer_a",
        )
        self.assertFalse(result.dry_run)
        self.assertEqual(
            (self.root / ".project_echo" / "jobs.jsonl").read_text(encoding="utf-8"),
            "v1\n",
        )

    def test_restore_takes_safety_backup_by_default(self) -> None:
        result = rs.run_restore(
            self.archive_v1,
            project_root=self.root,
            customer_id="customer_a",
        )
        self.assertIsNotNone(result.safety_backup)
        assert result.safety_backup is not None
        # Safety backup must contain v2 (live state at restore time)
        with tarfile.open(result.safety_backup, "r:gz") as tar:
            member = tar.getmember(".project_echo/jobs.jsonl")
            extracted = tar.extractfile(member)
            assert extracted is not None
            self.assertEqual(extracted.read().decode("utf-8"), "v2\n")

    def test_restore_skips_safety_backup_when_disabled(self) -> None:
        result = rs.run_restore(
            self.archive_v1,
            project_root=self.root,
            customer_id="customer_a",
            safety_backup=False,
        )
        self.assertIsNone(result.safety_backup)

    def test_dry_run_does_not_modify_live_state_or_audit(self) -> None:
        result = rs.run_restore(
            self.archive_v1,
            project_root=self.root,
            customer_id="customer_a",
            dry_run=True,
        )
        self.assertTrue(result.dry_run)
        self.assertEqual(
            (self.root / ".project_echo" / "jobs.jsonl").read_text(encoding="utf-8"),
            "v2\n",
        )
        self.assertEqual(self.audit_calls, [])
        self.assertGreater(result.file_count, 0)

    def test_audit_events_emitted_on_success(self) -> None:
        rs.run_restore(
            self.archive_v1,
            project_root=self.root,
            customer_id="customer_a",
            safety_backup=False,
        )
        types = [evt for _, evt, _ in self.audit_calls]
        self.assertIn("state_restore_started", types)
        self.assertIn("state_restore_completed", types)
        self.assertNotIn("state_restore_failed", types)

    def test_missing_archive_raises(self) -> None:
        with self.assertRaises(rs.RestoreError):
            rs.run_restore(self.root / "nope.tar.gz", project_root=self.root)

    def test_rejects_path_traversal_member(self) -> None:
        bad_archive = self.root / "bad.tar.gz"
        with tarfile.open(bad_archive, "w:gz") as tar:
            data = b"pwn"
            info = tarfile.TarInfo(name="../escape.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with self.assertRaises(rs.RestoreError):
            rs.run_restore(bad_archive, project_root=self.root, safety_backup=False)

    def test_rejects_member_outside_allowed_roots(self) -> None:
        bad_archive = self.root / "bad2.tar.gz"
        with tarfile.open(bad_archive, "w:gz") as tar:
            data = b"x"
            info = tarfile.TarInfo(name="etc/passwd")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with self.assertRaises(rs.RestoreError):
            rs.run_restore(bad_archive, project_root=self.root, safety_backup=False)


if __name__ == "__main__":
    unittest.main()
