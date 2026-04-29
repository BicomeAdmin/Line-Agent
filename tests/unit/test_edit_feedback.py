"""Tests for review-outcome feedback loop (edit_feedback.py).

Covers:
  - record_review_outcome for each of approve / edit / ignore
  - record_edit backward-compat wrapper still writes the same row
  - skip paths (empty original, empty edited, no-change edit)
  - load_recent_edits filters to action=edit + treats legacy
    (no-action) records as edits for backward compat
  - load_recent_outcomes returns all actions, optionally filtered
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.workflows import edit_feedback as ef


class RecordReviewOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.patch_root = patch.object(ef, "customer_data_root", lambda *_: self.data_root)
        self.patch_root.start()
        self.patch_audit = patch.object(ef, "append_audit_event", lambda *a, **k: None)
        self.patch_audit.start()

    def tearDown(self):
        self.patch_root.stop()
        self.patch_audit.stop()
        self.tmp.cleanup()

    def _read_jsonl(self, community_id="openchat_test") -> list[dict]:
        p = ef.edit_feedback_path("customer_a", community_id)
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_unknown_action_skipped(self):
        out = ef.record_review_outcome(
            "customer_a", "openchat_test", "rev-1",
            action="discard", original_draft="hello",
        )
        self.assertEqual(out["status"], "skipped")
        self.assertIn("unknown_action", out["reason"])

    def test_empty_original_skipped(self):
        out = ef.record_review_outcome(
            "customer_a", "openchat_test", "rev-1",
            action="approve", original_draft="   ",
        )
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(out["reason"], "empty_original")

    def test_approve_writes_row_with_action_field(self):
        out = ef.record_review_outcome(
            "customer_a", "openchat_test", "rev-approve",
            action="approve", original_draft="這個我也想知道",
        )
        self.assertEqual(out["status"], "ok")
        rows = self._read_jsonl()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "approve")
        self.assertEqual(rows[0]["original_draft"], "這個我也想知道")
        self.assertIsNone(rows[0]["edited_draft"])
        self.assertNotIn("diff_summary", rows[0])

    def test_ignore_writes_row(self):
        ef.record_review_outcome(
            "customer_a", "openchat_test", "rev-ignore",
            action="ignore", original_draft="廣告腔的草稿",
        )
        rows = self._read_jsonl()
        self.assertEqual(rows[0]["action"], "ignore")
        self.assertIsNone(rows[0]["edited_draft"])

    def test_edit_writes_row_with_diff_summary(self):
        ef.record_review_outcome(
            "customer_a", "openchat_test", "rev-edit",
            action="edit",
            original_draft="我補一個小角度給大家參考，價格還是效果重要？",
            edited_draft="欸這個我也在想",
        )
        rows = self._read_jsonl()
        self.assertEqual(rows[0]["action"], "edit")
        self.assertEqual(rows[0]["edited_draft"], "欸這個我也在想")
        self.assertIn("diff_summary", rows[0])
        self.assertIn("became_shorter", rows[0]["diff_summary"])

    def test_edit_skipped_when_no_change(self):
        out = ef.record_review_outcome(
            "customer_a", "openchat_test", "rev-edit",
            action="edit",
            original_draft="一模一樣",
            edited_draft="一模一樣",
        )
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(out["reason"], "no_change")
        self.assertEqual(self._read_jsonl(), [])

    def test_edit_skipped_when_edited_empty(self):
        out = ef.record_review_outcome(
            "customer_a", "openchat_test", "rev-edit",
            action="edit",
            original_draft="原稿",
            edited_draft="",
        )
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(out["reason"], "empty_edited")


class RecordEditWrapperTests(unittest.TestCase):
    """The legacy record_edit() must remain identical in behavior."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.patch_root = patch.object(ef, "customer_data_root", lambda *_: self.data_root)
        self.patch_root.start()
        self.patch_audit = patch.object(ef, "append_audit_event", lambda *a, **k: None)
        self.patch_audit.start()

    def tearDown(self):
        self.patch_root.stop()
        self.patch_audit.stop()
        self.tmp.cleanup()

    def test_record_edit_writes_action_edit_row(self):
        out = ef.record_edit(
            "customer_a", "openchat_test", "rev-edit",
            "原稿很長文字", "短稿",
        )
        self.assertEqual(out["status"], "ok")
        path = ef.edit_feedback_path("customer_a", "openchat_test")
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(rows[0]["action"], "edit")
        self.assertEqual(rows[0]["original_draft"], "原稿很長文字")
        self.assertEqual(rows[0]["edited_draft"], "短稿")

    def test_record_edit_skipped_when_no_change(self):
        out = ef.record_edit("customer_a", "openchat_test", "rev-x", "一樣", "一樣")
        self.assertEqual(out["status"], "skipped")


class LoadRecentEditsFiltersTests(unittest.TestCase):
    """Must filter to action=edit (or legacy no-action records) so the
    compose prompt's 「過去修改紀錄」 section doesn't leak ignore/approve."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.tmp.name)
        self.patch_root = patch.object(ef, "customer_data_root", lambda *_: self.data_root)
        self.patch_root.start()
        self.patch_audit = patch.object(ef, "append_audit_event", lambda *a, **k: None)
        self.patch_audit.start()

    def tearDown(self):
        self.patch_root.stop()
        self.patch_audit.stop()
        self.tmp.cleanup()

    def _write_raw(self, community_id, records):
        p = ef.edit_feedback_path("customer_a", community_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")

    def test_legacy_records_without_action_treated_as_edit(self):
        # Pre-2026-04-29 schema — no "action" field, all are edits.
        self._write_raw("openchat_test", [
            {"review_id": "old-1", "original_draft": "old original", "edited_draft": "old edit"},
            {"review_id": "old-2", "original_draft": "x", "edited_draft": "y"},
        ])
        edits = ef.load_recent_edits("customer_a", "openchat_test")
        self.assertEqual(len(edits), 2)

    def test_filter_excludes_approve_and_ignore(self):
        ef.record_review_outcome("customer_a", "openchat_test", "r1", action="approve", original_draft="a1")
        ef.record_review_outcome("customer_a", "openchat_test", "r2", action="ignore", original_draft="a2")
        ef.record_review_outcome("customer_a", "openchat_test", "r3", action="edit", original_draft="orig", edited_draft="edited")
        ef.record_review_outcome("customer_a", "openchat_test", "r4", action="ignore", original_draft="a4")

        edits = ef.load_recent_edits("customer_a", "openchat_test")
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["review_id"], "r3")

    def test_load_recent_outcomes_returns_all_by_default(self):
        ef.record_review_outcome("customer_a", "openchat_test", "r1", action="approve", original_draft="a1")
        ef.record_review_outcome("customer_a", "openchat_test", "r2", action="ignore", original_draft="a2")
        ef.record_review_outcome("customer_a", "openchat_test", "r3", action="edit", original_draft="orig", edited_draft="edited")

        outcomes = ef.load_recent_outcomes("customer_a", "openchat_test")
        self.assertEqual(len(outcomes), 3)
        actions = {o["action"] for o in outcomes}
        self.assertEqual(actions, {"approve", "ignore", "edit"})

    def test_load_recent_outcomes_action_filter(self):
        ef.record_review_outcome("customer_a", "openchat_test", "r1", action="approve", original_draft="a1")
        ef.record_review_outcome("customer_a", "openchat_test", "r2", action="ignore", original_draft="a2")
        ef.record_review_outcome("customer_a", "openchat_test", "r3", action="ignore", original_draft="a3")

        ignores = ef.load_recent_outcomes("customer_a", "openchat_test", actions=("ignore",))
        self.assertEqual(len(ignores), 2)
        self.assertTrue(all(o["action"] == "ignore" for o in ignores))


if __name__ == "__main__":
    unittest.main()
