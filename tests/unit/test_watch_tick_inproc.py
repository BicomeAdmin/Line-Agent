"""Tests for in-process watch tick path (replacement for codex spawn)."""

import time
import unittest
from unittest.mock import MagicMock, patch

from app.workflows import watch_tick_inproc as wti


def _stub_watch(community_id="openchat_003", **overrides):
    base = {
        "watch_id": f"watch-test-{community_id}",
        "customer_id": "customer_a",
        "community_id": community_id,
        "cooldown_seconds": 300,
        "poll_interval_seconds": 60,
        "last_check_epoch": 0,
        "last_seen_signature": "",
        "last_draft_epoch": 0,
        "initiator_chat_id": None,
    }
    base.update(overrides)
    return base


class _FakeRiskControl:
    def __init__(self, in_window: bool = True):
        self.in_window = in_window
        from datetime import time as dt_time
        self.activity_start = dt_time(9, 0)
        self.activity_end = dt_time(23, 0)
    def is_activity_time(self, now=None) -> bool:
        return self.in_window


class WatchTickInprocTests(unittest.TestCase):
    def setUp(self) -> None:
        # Module-level review_store persists across tests; clear it so the
        # auto_watch dedup helper doesn't see leftovers from previous tests.
        from app.core.reviews import review_store
        with review_store._lock:
            review_store._reviews.clear()

    def test_outside_activity_window_returns_skip(self) -> None:
        with patch("app.core.risk_control.default_risk_control", _FakeRiskControl(in_window=False)):
            r = wti.tick_one_inprocess(_stub_watch())
        self.assertFalse(r["acted"])
        self.assertEqual(r["reason"], "outside_activity_hours")

    def test_navigate_failure_returns_reason(self) -> None:
        with patch("app.core.risk_control.default_risk_control", _FakeRiskControl()), \
             patch.object(wti, "navigate_to_openchat", return_value={"status": "blocked", "reason": "no_app"}):
            r = wti.tick_one_inprocess(_stub_watch())
        self.assertFalse(r["acted"])
        self.assertTrue(r["reason"].startswith("navigate_failed"))

    def test_no_new_content_returns_skip(self) -> None:
        msgs = [{"text": "hi"}, {"text": "hello"}]
        with patch("app.core.risk_control.default_risk_control", _FakeRiskControl()), \
             patch.object(wti, "navigate_to_openchat", return_value={"status": "ok"}), \
             patch.object(wti, "load_community_config", return_value=MagicMock(device_id="emulator-5554", display_name="X")), \
             patch.object(wti, "read_recent_chat", return_value=msgs):
            from app.storage.watches import messages_signature
            sig = messages_signature(msgs)
            r = wti.tick_one_inprocess(_stub_watch(last_seen_signature=sig))
        self.assertFalse(r["acted"])
        self.assertEqual(r["reason"], "no_new_content")

    def test_no_actionable_target_returns_skip(self) -> None:
        msgs = [{"text": "hi"}, {"text": "hello"}]
        decision = MagicMock()
        decision.to_dict.return_value = {"target": {"actionable": False, "score": 0.5}, "skip_reason": "no_candidate"}
        with patch("app.core.risk_control.default_risk_control", _FakeRiskControl()), \
             patch.object(wti, "navigate_to_openchat", return_value={"status": "ok"}), \
             patch.object(wti, "load_community_config", return_value=MagicMock(device_id="emulator-5554", display_name="X")), \
             patch.object(wti, "read_recent_chat", return_value=msgs), \
             patch("app.workflows.persona_context.get_persona_context", return_value={"voice_profile": {}}), \
             patch("app.workflows.member_fingerprint.load_member_fingerprints", return_value={}), \
             patch.object(wti, "select_reply_target_workflow", return_value=decision):
            r = wti.tick_one_inprocess(_stub_watch())
        self.assertFalse(r["acted"])
        self.assertEqual(r["reason"], "no_candidate")

    def test_actionable_target_creates_review(self) -> None:
        msgs = [{"text": "?", "sender": "alice"}]
        decision = MagicMock()
        decision.to_dict.return_value = {
            "target": {"actionable": True, "score": 5.0, "sender": "alice", "text": "?", "index": 0},
            "skip_reason": None,
        }
        # llm_compose_enabled=False keeps the test on the rule-based path —
        # MagicMock auto-attributes return truthy Mock objects which would
        # otherwise route into the codex branch and break the rule-based
        # path test.
        community = MagicMock(device_id="emulator-5554", display_name="X", llm_compose_enabled=False)
        customer = MagicMock(display_name="C")
        from app.ai.decision import DraftDecision
        rule_decision = DraftDecision(action="draft_reply", reason="user_question", confidence=0.84, draft="OK 我來回")
        with patch("app.core.risk_control.default_risk_control", _FakeRiskControl()), \
             patch.object(wti, "navigate_to_openchat", return_value={"status": "ok"}), \
             patch.object(wti, "load_community_config", return_value=community), \
             patch.object(wti, "load_customer_config", return_value=customer), \
             patch.object(wti, "read_recent_chat", return_value=msgs), \
             patch("app.workflows.persona_context.get_persona_context", return_value={"voice_profile": {"personality_zh": "test"}}), \
             patch("app.workflows.member_fingerprint.load_member_fingerprints", return_value={}), \
             patch.object(wti, "select_reply_target_workflow", return_value=decision), \
             patch("app.ai.decision.decide_reply", return_value=rule_decision), \
             patch("app.workflows.watch_tick_inproc.review_store") as store, \
             patch("app.workflows.watch_tick_inproc.append_audit_event") as audit:
            store.list_all.return_value = []
            r = wti.tick_one_inprocess(_stub_watch())
        self.assertTrue(r["acted"])
        store.upsert.assert_called_once()
        # Two audit events: mcp_compose_review_created + watch_tick_fired
        types = [c[0][1] for c in audit.call_args_list]
        self.assertIn("mcp_compose_review_created", types)
        self.assertIn("watch_tick_fired", types)


class ModelWarmupTests(unittest.TestCase):
    def test_warmup_returns_status_dict(self) -> None:
        from app.workflows.model_warmup import warm_up_models
        with patch("app.ai.embedding_service.get_embedding_service", return_value=MagicMock()), \
             patch("app.ai.emotion_classifier.get_emotion_classifier", return_value=MagicMock()):
            stats = warm_up_models()
        self.assertIn("embedding", stats)
        self.assertIn("emotion", stats)
        self.assertTrue(stats["embedding"]["loaded"])
        self.assertTrue(stats["emotion"]["loaded"])

    def test_warmup_handles_missing_models(self) -> None:
        from app.workflows.model_warmup import warm_up_models
        with patch("app.ai.embedding_service.get_embedding_service", return_value=None), \
             patch("app.ai.emotion_classifier.get_emotion_classifier", return_value=None):
            stats = warm_up_models()
        self.assertFalse(stats["embedding"]["loaded"])
        self.assertFalse(stats["emotion"]["loaded"])


if __name__ == "__main__":
    unittest.main()
