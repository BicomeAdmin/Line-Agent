"""Tests for audit event schema validation."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.audit import AuditValidationError, append_audit_event


class AuditValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.tmp.name) / "audit.jsonl"

        self.path_patch = patch(
            "app.core.audit.audit_log_path",
            return_value=self.audit_path,
        )
        self.dirs_patch = patch(
            "app.core.audit.ensure_customer_directories",
            return_value=None,
        )
        self.path_patch.start()
        self.dirs_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.dirs_patch.stop()
        self.tmp.cleanup()

    def test_valid_event_writes_line(self) -> None:
        append_audit_event("customer_a", "test_event", {"k": "v"})
        line = self.audit_path.read_text(encoding="utf-8").strip()
        entry = json.loads(line)
        self.assertEqual(entry["event_type"], "test_event")
        self.assertEqual(entry["payload"], {"k": "v"})
        self.assertIn("timestamp", entry)

    def test_rejects_empty_customer_id(self) -> None:
        with self.assertRaises(AuditValidationError):
            append_audit_event("", "test_event", {})
        self.assertFalse(self.audit_path.exists())

    def test_rejects_non_string_customer_id(self) -> None:
        with self.assertRaises(AuditValidationError):
            append_audit_event(None, "test_event", {})  # type: ignore[arg-type]

    def test_rejects_uppercase_event_type(self) -> None:
        with self.assertRaises(AuditValidationError):
            append_audit_event("customer_a", "TestEvent", {})

    def test_rejects_event_type_with_dash(self) -> None:
        with self.assertRaises(AuditValidationError):
            append_audit_event("customer_a", "test-event", {})

    def test_rejects_empty_event_type(self) -> None:
        with self.assertRaises(AuditValidationError):
            append_audit_event("customer_a", "", {})

    def test_rejects_non_dict_payload(self) -> None:
        with self.assertRaises(AuditValidationError):
            append_audit_event("customer_a", "test_event", None)  # type: ignore[arg-type]
        with self.assertRaises(AuditValidationError):
            append_audit_event("customer_a", "test_event", "string_payload")  # type: ignore[arg-type]
        with self.assertRaises(AuditValidationError):
            append_audit_event("customer_a", "test_event", [1, 2, 3])  # type: ignore[arg-type]

    def test_rejects_unserializable_payload(self) -> None:
        # set() is not JSON-serializable
        with self.assertRaises(AuditValidationError):
            append_audit_event("customer_a", "test_event", {"bad": {1, 2, 3}})
        # And no partial line written
        if self.audit_path.exists():
            self.assertEqual(self.audit_path.read_text(encoding="utf-8"), "")

    def test_accepts_empty_dict_payload(self) -> None:
        append_audit_event("customer_a", "test_event", {})
        line = self.audit_path.read_text(encoding="utf-8").strip()
        entry = json.loads(line)
        self.assertEqual(entry["payload"], {})


if __name__ == "__main__":
    unittest.main()
