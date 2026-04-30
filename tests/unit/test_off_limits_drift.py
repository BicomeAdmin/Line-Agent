"""Tests for off-limits drift detection between compose and approve."""

import unittest
from unittest.mock import MagicMock, patch

from app.core.reviews import ReviewRecord, hash_off_limits


class HashOffLimitsTests(unittest.TestCase):
    def test_empty_returns_empty(self) -> None:
        self.assertEqual(hash_off_limits(""), "")
        self.assertEqual(hash_off_limits(None), "")
        self.assertEqual(hash_off_limits("   "), "")

    def test_same_text_same_hash(self) -> None:
        a = hash_off_limits("- 不解卦\n- 不評論個人選擇")
        b = hash_off_limits("- 不解卦\n- 不評論個人選擇")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_whitespace_normalized(self) -> None:
        a = hash_off_limits("- 不解卦\n- 不評論個人選擇")
        b = hash_off_limits("- 不解卦\n\n   - 不評論個人選擇")
        # Cosmetic whitespace doesn't trigger drift
        self.assertEqual(a, b)

    def test_real_content_change_changes_hash(self) -> None:
        a = hash_off_limits("- 不解卦")
        b = hash_off_limits("- 不解卦\n- 不討論政治")
        self.assertNotEqual(a, b)


class ReviewRecordOffLimitsField(unittest.TestCase):
    def test_default_empty(self) -> None:
        record = ReviewRecord(
            review_id="x", source_job_id="x",
            customer_id="c", customer_name="C",
            community_id="g", community_name="G",
            device_id="d", draft_text="t",
        )
        self.assertEqual(record.off_limits_hash, "")

    def test_explicit_set(self) -> None:
        record = ReviewRecord(
            review_id="x", source_job_id="x",
            customer_id="c", customer_name="C",
            community_id="g", community_name="G",
            device_id="d", draft_text="t",
            off_limits_hash="abc123",
        )
        self.assertEqual(record.off_limits_hash, "abc123")

    def test_to_dict_round_trip(self) -> None:
        record = ReviewRecord(
            review_id="x", source_job_id="x",
            customer_id="c", customer_name="C",
            community_id="g", community_name="G",
            device_id="d", draft_text="t",
            off_limits_hash="hash-abc",
        )
        d = record.to_dict()
        self.assertEqual(d["off_limits_hash"], "hash-abc")


class ApproveSendDriftCheckTests(unittest.TestCase):
    """Integration: when off_limits hash differs at approve vs compose,
    audit a drift warning. Approve still proceeds (drift is informational,
    not blocking — operator already reviewed the draft text)."""

    def test_drift_audited_when_hash_differs(self) -> None:
        from app.workflows import job_processor as jp

        existing = MagicMock(
            spec=ReviewRecord,
            status="pending",
            off_limits_hash="OLD_HASH_XXX",
            created_at=__import__("time").time(),  # fresh, no temporal drift
        )

        current_vp = MagicMock(off_limits="- 新加的規則")
        audit_calls: list[tuple] = []

        def _audit(customer_id, event_type, payload):
            audit_calls.append((event_type, payload))

        with patch("app.core.reviews.review_store") as store, \
             patch.object(jp, "settings") as mock_settings, \
             patch.object(jp, "send_draft", return_value={"status": "blocked"}), \
             patch.object(jp, "_check_pre_send_drift", return_value=None), \
             patch.object(jp, "_draft_text_for_action", return_value="test draft"), \
             patch.object(jp, "_resolve_action_value", side_effect=lambda k, *_: {
                 "customer_id": "c", "community_id": "g", "device_id": "d",
             }[k]), \
             patch.object(jp, "_update_review_from_action", return_value=None), \
             patch("app.workflows.openchat_navigate.navigate_to_openchat",
                   return_value={"status": "ok"}), \
             patch("app.storage.config_loader.load_community_config",
                   return_value=MagicMock(display_name="X")), \
             patch.object(jp, "append_audit_event", side_effect=_audit), \
             patch("app.ai.voice_profile_v2.parse_voice_profile",
                   return_value=current_vp), \
             patch("app.workflows.openchat_verify.verify_chat_title",
                   return_value=MagicMock(ok=True, expected="X", current_title="X",
                                          reason="match",
                                          to_dict=lambda: {})):
            mock_settings.require_human_approval = True
            store.get.return_value = existing
            jp._approve_send("job-1", None, {})

        # Drift audit fired (current_hash will be non-empty + differ from OLD_HASH_XXX)
        types = [c[0] for c in audit_calls]
        self.assertIn("approve_send_off_limits_drift", types)
        drift_payload = next(c[1] for c in audit_calls
                             if c[0] == "approve_send_off_limits_drift")
        self.assertEqual(drift_payload["stored_hash"], "OLD_HASH_XXX")
        self.assertNotEqual(drift_payload["current_hash"], "OLD_HASH_XXX")

    def test_no_drift_when_hash_matches(self) -> None:
        from app.workflows import job_processor as jp

        # Pre-compute the hash for a specific text, store it, then ensure
        # the approve-time check finds the same hash and audits NOTHING.
        text = "- 不解卦\n- 不評論個人"
        same_hash = hash_off_limits(text)

        existing = MagicMock(
            spec=ReviewRecord,
            status="pending",
            off_limits_hash=same_hash,
            created_at=__import__("time").time(),
        )

        audit_calls: list[tuple] = []

        def _audit(customer_id, event_type, payload):
            audit_calls.append((event_type, payload))

        with patch("app.core.reviews.review_store") as store, \
             patch.object(jp, "settings") as mock_settings, \
             patch.object(jp, "send_draft", return_value={"status": "blocked"}), \
             patch.object(jp, "_check_pre_send_drift", return_value=None), \
             patch.object(jp, "_draft_text_for_action", return_value="test draft"), \
             patch.object(jp, "_resolve_action_value", side_effect=lambda k, *_: {
                 "customer_id": "c", "community_id": "g", "device_id": "d",
             }[k]), \
             patch.object(jp, "_update_review_from_action", return_value=None), \
             patch("app.workflows.openchat_navigate.navigate_to_openchat",
                   return_value={"status": "ok"}), \
             patch("app.storage.config_loader.load_community_config",
                   return_value=MagicMock(display_name="X")), \
             patch.object(jp, "append_audit_event", side_effect=_audit), \
             patch("app.ai.voice_profile_v2.parse_voice_profile",
                   return_value=MagicMock(off_limits=text)), \
             patch("app.workflows.openchat_verify.verify_chat_title",
                   return_value=MagicMock(ok=True, expected="X", current_title="X",
                                          reason="match",
                                          to_dict=lambda: {})):
            mock_settings.require_human_approval = True
            store.get.return_value = existing
            jp._approve_send("job-1", None, {})

        types = [c[0] for c in audit_calls]
        self.assertNotIn("approve_send_off_limits_drift", types)

    def test_no_check_when_existing_has_no_hash(self) -> None:
        """Legacy reviews without off_limits_hash skip the check (we
        don't know what their compose-time off-limits were)."""

        from app.workflows import job_processor as jp

        existing = MagicMock(
            spec=ReviewRecord,
            status="pending",
            off_limits_hash="",   # legacy: no hash recorded
            created_at=__import__("time").time(),
        )

        audit_calls: list[tuple] = []

        def _audit(customer_id, event_type, payload):
            audit_calls.append((event_type, payload))

        with patch("app.core.reviews.review_store") as store, \
             patch.object(jp, "settings") as mock_settings, \
             patch.object(jp, "send_draft", return_value={"status": "blocked"}), \
             patch.object(jp, "_check_pre_send_drift", return_value=None), \
             patch.object(jp, "_draft_text_for_action", return_value="test"), \
             patch.object(jp, "_resolve_action_value", side_effect=lambda k, *_: {
                 "customer_id": "c", "community_id": "g", "device_id": "d",
             }[k]), \
             patch.object(jp, "_update_review_from_action", return_value=None), \
             patch("app.workflows.openchat_navigate.navigate_to_openchat",
                   return_value={"status": "ok"}), \
             patch("app.storage.config_loader.load_community_config",
                   return_value=MagicMock(display_name="X")), \
             patch.object(jp, "append_audit_event", side_effect=_audit), \
             patch("app.workflows.openchat_verify.verify_chat_title",
                   return_value=MagicMock(ok=True, expected="X", current_title="X",
                                          reason="match",
                                          to_dict=lambda: {})):
            mock_settings.require_human_approval = True
            store.get.return_value = existing
            jp._approve_send("job-1", None, {})

        types = [c[0] for c in audit_calls]
        self.assertNotIn("approve_send_off_limits_drift", types)


if __name__ == "__main__":
    unittest.main()
