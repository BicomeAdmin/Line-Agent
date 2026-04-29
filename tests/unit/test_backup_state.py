"""Tests for state backup workflow."""

import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.workflows import backup_state as bs


class BackupStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # Mimic project layout
        (self.root / ".project_echo").mkdir()
        (self.root / ".project_echo" / "jobs.jsonl").write_text("job1\n", encoding="utf-8")
        (self.root / "configs").mkdir()
        (self.root / "configs" / "risk_control.yaml").write_text("require_human_approval: true\n", encoding="utf-8")
        cust = self.root / "customers" / "customer_a" / "data"
        cust.mkdir(parents=True)
        (cust / "audit.jsonl").write_text("{\"e\":1}\n", encoding="utf-8")
        (cust / "raw_xml").mkdir()
        (cust / "raw_xml" / "skip_me.xml").write_text("<x/>", encoding="utf-8")
        (cust / "chat_exports").mkdir()
        (cust / "chat_exports" / "keep_me.txt").write_text("hello", encoding="utf-8")
        ds_store = cust / ".DS_Store"
        ds_store.write_bytes(b"\x00")
        # Ensure audit can write
        self.audit_calls: list[tuple] = []

        def fake_audit(customer_id, event_type, payload):
            self.audit_calls.append((customer_id, event_type, payload))

        self.audit_patch = patch.object(bs, "append_audit_event", side_effect=fake_audit)
        self.audit_patch.start()

    def tearDown(self) -> None:
        self.audit_patch.stop()
        self.tmp.cleanup()

    def _members(self, archive: Path) -> set[str]:
        with tarfile.open(archive, "r:gz") as tar:
            return {m.name for m in tar.getmembers() if m.isfile()}

    def test_archive_includes_state_excludes_raw_xml_and_dsstore(self) -> None:
        result = bs.run_backup(
            project_root=self.root,
            backup_dir=self.root / "backups",
            keep=14,
            now=datetime(2026, 4, 29, 0, 0, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(result.archive_path.exists())
        self.assertEqual(result.archive_path.name, "echo-state-20260429T000000Z.tar.gz")
        members = self._members(result.archive_path)

        self.assertIn(".project_echo/jobs.jsonl", members)
        self.assertIn("configs/risk_control.yaml", members)
        self.assertIn("customers/customer_a/data/audit.jsonl", members)
        self.assertIn("customers/customer_a/data/chat_exports/keep_me.txt", members)
        # excluded
        self.assertNotIn("customers/customer_a/data/raw_xml/skip_me.xml", members)
        self.assertNotIn("customers/customer_a/data/.DS_Store", members)

    def test_audit_event_written(self) -> None:
        bs.run_backup(project_root=self.root, backup_dir=self.root / "backups", keep=14)
        self.assertEqual(len(self.audit_calls), 1)
        cust, event_type, payload = self.audit_calls[0]
        self.assertEqual(event_type, "state_backup_created")
        self.assertGreater(payload["file_count"], 0)
        self.assertGreater(payload["bytes"], 0)

    def test_rotation_keeps_only_n(self) -> None:
        backup_dir = self.root / "backups"
        for hour in range(20):
            bs.run_backup(
                project_root=self.root,
                backup_dir=backup_dir,
                keep=5,
                now=datetime(2026, 4, 29, hour, 0, 0, tzinfo=timezone.utc),
            )
        archives = sorted(backup_dir.glob("echo-state-*.tar.gz"))
        self.assertEqual(len(archives), 5)
        # Most recent should be hour=19
        self.assertIn("T190000Z", archives[-1].name)


if __name__ == "__main__":
    unittest.main()
