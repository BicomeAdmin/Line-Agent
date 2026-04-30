"""Tests for audit log size monitoring."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.audit import (
    AUDIT_LOG_CRITICAL_BYTES,
    AUDIT_LOG_WARN_BYTES,
    audit_log_stats,
)


class AuditLogStatsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._path = Path(self._tmp.name) / "audit.jsonl"
        self._patch = patch("app.core.audit.audit_log_path", return_value=self._path)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _write_events(self, count: int, padding: int = 0) -> None:
        """Write `count` JSONL events; `padding` chars per event for size control."""
        with self._path.open("w", encoding="utf-8") as f:
            for i in range(count):
                ev = {
                    "timestamp": f"2026-04-30T12:00:{i % 60:02d}Z",
                    "event_type": "test",
                    "payload": {"i": i, "pad": "x" * padding},
                }
                f.write(json.dumps(ev) + "\n")

    def test_no_file_returns_zero_stats(self) -> None:
        s = audit_log_stats("c")
        self.assertFalse(s["exists"])
        self.assertEqual(s["size_bytes"], 0)
        self.assertEqual(s["severity"], "ok")

    def test_small_file_ok_severity(self) -> None:
        self._write_events(10)
        s = audit_log_stats("c")
        self.assertTrue(s["exists"])
        self.assertEqual(s["severity"], "ok")
        self.assertEqual(s["line_count"], 10)
        self.assertIsNotNone(s["oldest_ts"])
        self.assertIsNotNone(s["newest_ts"])

    def test_size_human_format(self) -> None:
        self._write_events(100, padding=200)
        s = audit_log_stats("c")
        # Should format with KB suffix
        self.assertIn("KB", s["size_human"])

    def test_warn_threshold_severity(self) -> None:
        # Fake the size by writing a sparse file
        self._path.write_bytes(b"x" * (AUDIT_LOG_WARN_BYTES + 1024))
        s = audit_log_stats("c")
        self.assertEqual(s["severity"], "warn")

    def test_critical_threshold_severity(self) -> None:
        # We don't actually write 200MB; mock stat instead
        self._write_events(5)
        with patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_size = AUDIT_LOG_CRITICAL_BYTES + 1024
            s = audit_log_stats("c")
        self.assertEqual(s["severity"], "critical")

    def test_oldest_and_newest_timestamps_extracted(self) -> None:
        self._write_events(50)
        s = audit_log_stats("c")
        # oldest from first line (i=0), newest from last (i=49)
        self.assertIn("2026-04-30T12:00:00", s["oldest_ts"])
        # i=49 % 60 = 49 → seconds=49
        self.assertIn("2026-04-30T12:00:49", s["newest_ts"])


if __name__ == "__main__":
    unittest.main()
