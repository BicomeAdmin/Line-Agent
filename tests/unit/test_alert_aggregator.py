"""Tests for the dashboard alert aggregator."""

import time
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.workflows.alert_aggregator import (
    Alert,
    alerts_summary,
    collect_alerts,
)


def _ev(event_type: str, *, ts: float, community_id: str | None = "openchat_004", **payload) -> dict:
    full = {"community_id": community_id} if community_id else {}
    full.update(payload)
    return {
        "event_type": event_type,
        "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
        "payload": full,
    }


class HilDisabledAlertTests(unittest.TestCase):
    def test_hil_disabled_emits_blocking_alert(self) -> None:
        with patch("app.workflows.alert_aggregator.settings") as ms, \
             patch("app.workflows.alert_aggregator.review_store") as rs:
            ms.require_human_approval = False
            rs.list_all.return_value = []
            alerts = collect_alerts(audit_events=[])
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, "blocking")
        self.assertEqual(alerts[0].audit_event_type, "hil_disabled")

    def test_hil_enabled_no_alert(self) -> None:
        with patch("app.workflows.alert_aggregator.settings") as ms, \
             patch("app.workflows.alert_aggregator.review_store") as rs:
            ms.require_human_approval = True
            rs.list_all.return_value = []
            alerts = collect_alerts(audit_events=[])
        self.assertEqual(len(alerts), 0)


class ReviewAgingAlertTests(unittest.TestCase):
    def test_review_4h_old_blocking(self) -> None:
        old_review = MagicMock(
            customer_id="customer_a", community_id="openchat_004",
            community_name="X", status="pending",
            review_id="r1", draft_text="test draft",
            created_at=time.time() - 5 * 3600,  # 5h old
        )
        with patch("app.workflows.alert_aggregator.settings") as ms, \
             patch("app.workflows.alert_aggregator.review_store") as rs:
            ms.require_human_approval = True
            rs.list_all.return_value = [old_review]
            alerts = collect_alerts(audit_events=[])
        review_alerts = [a for a in alerts if a.audit_event_type == "review_aging_blocking"]
        self.assertEqual(len(review_alerts), 1)
        self.assertEqual(review_alerts[0].severity, "blocking")

    def test_review_2h_old_important(self) -> None:
        review = MagicMock(
            customer_id="customer_a", community_id="g",
            community_name="X", status="pending",
            review_id="r1", draft_text="test",
            created_at=time.time() - 2 * 3600,
        )
        with patch("app.workflows.alert_aggregator.settings") as ms, \
             patch("app.workflows.alert_aggregator.review_store") as rs:
            ms.require_human_approval = True
            rs.list_all.return_value = [review]
            alerts = collect_alerts(audit_events=[])
        important = [a for a in alerts if a.audit_event_type == "review_aging_important"]
        self.assertEqual(len(important), 1)

    def test_fresh_review_no_alert(self) -> None:
        review = MagicMock(
            customer_id="customer_a", community_id="g",
            community_name="X", status="pending",
            review_id="r1", draft_text="test",
            created_at=time.time() - 60,
        )
        with patch("app.workflows.alert_aggregator.settings") as ms, \
             patch("app.workflows.alert_aggregator.review_store") as rs:
            ms.require_human_approval = True
            rs.list_all.return_value = [review]
            alerts = collect_alerts(audit_events=[])
        self.assertEqual(len([a for a in alerts if a.category == "review"]), 0)


class AuditEventRollupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = time.time()
        self.settings_patch = patch("app.workflows.alert_aggregator.settings")
        self.store_patch = patch("app.workflows.alert_aggregator.review_store")
        ms = self.settings_patch.start()
        rs = self.store_patch.start()
        ms.require_human_approval = True
        rs.list_all.return_value = []
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.store_patch.stop)

    def test_send_verification_failed_blocking(self) -> None:
        events = [_ev("send_verification_failed", ts=self.now - 60,
                      verdict={"reason": "no_self_bubble_after_send"})]
        alerts = collect_alerts(audit_events=events, now=self.now)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, "blocking")
        self.assertIn("no_self_bubble", alerts[0].detail)

    def test_send_safety_blocked_includes_codes(self) -> None:
        events = [_ev("send_safety_blocked", ts=self.now - 30,
                      verdict={"issues": [{"code": "url_in_draft"}, {"code": "phone_in_draft"}]})]
        alerts = collect_alerts(audit_events=events, now=self.now)
        self.assertIn("url_in_draft", alerts[0].detail)
        self.assertIn("phone_in_draft", alerts[0].detail)

    def test_groups_same_event_type_per_community(self) -> None:
        # 3 events same type same community → 1 alert with count=3
        events = [
            _ev("composer_temporal_override", ts=self.now - 60, stale_minutes=200),
            _ev("composer_temporal_override", ts=self.now - 120, stale_minutes=190),
            _ev("composer_temporal_override", ts=self.now - 180, stale_minutes=240),
        ]
        alerts = collect_alerts(audit_events=events, now=self.now)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].audit_event_count, 3)
        self.assertEqual(alerts[0].severity, "info")

    def test_different_communities_separate_alerts(self) -> None:
        events = [
            _ev("composer_temporal_override", ts=self.now - 60,
                community_id="A", stale_minutes=200),
            _ev("composer_temporal_override", ts=self.now - 60,
                community_id="B", stale_minutes=190),
        ]
        alerts = collect_alerts(audit_events=events, now=self.now)
        community_ids = sorted(a.community_id for a in alerts)
        self.assertEqual(community_ids, ["A", "B"])

    def test_old_events_excluded(self) -> None:
        # Event 30h ago — outside default 24h window
        events = [_ev("send_verification_failed", ts=self.now - 30 * 3600)]
        alerts = collect_alerts(audit_events=events, now=self.now)
        self.assertEqual(len(alerts), 0)

    def test_unknown_event_types_ignored(self) -> None:
        events = [_ev("totally_made_up_event", ts=self.now - 60)]
        alerts = collect_alerts(audit_events=events, now=self.now)
        self.assertEqual(len(alerts), 0)

    def test_chat_title_mismatch_includes_expected_vs_actual(self) -> None:
        events = [_ev("approve_send_chat_title_mismatch", ts=self.now - 60,
                      expected="水月觀音道場", current_title="愛美星")]
        alerts = collect_alerts(audit_events=events, now=self.now)
        self.assertIn("水月觀音道場", alerts[0].detail)
        self.assertIn("愛美星", alerts[0].detail)


class SortAndSummaryTests(unittest.TestCase):
    def test_sort_by_severity_then_count(self) -> None:
        with patch("app.workflows.alert_aggregator.settings") as ms, \
             patch("app.workflows.alert_aggregator.review_store") as rs:
            ms.require_human_approval = False  # blocking
            rs.list_all.return_value = []
            now = time.time()
            events = [
                # 1× important
                _ev("composer_codex_unavailable", ts=now - 60),
                # 5× info
                *[_ev("composer_temporal_override", ts=now - 60 - i, stale_minutes=200) for i in range(5)],
            ]
            alerts = collect_alerts(audit_events=events, now=now)
        self.assertEqual(alerts[0].severity, "blocking")
        self.assertEqual(alerts[1].severity, "important")
        self.assertEqual(alerts[2].severity, "info")
        self.assertEqual(alerts[2].audit_event_count, 5)

    def test_alerts_summary_counts(self) -> None:
        items = [
            Alert(severity="blocking", category="x", title="t", detail="d",
                  community_id=None, action_hint=None, audit_event_type="t",
                  audit_ts_taipei=None, audit_event_count=1),
            Alert(severity="important", category="x", title="t", detail="d",
                  community_id=None, action_hint=None, audit_event_type="t",
                  audit_ts_taipei=None, audit_event_count=1),
            Alert(severity="info", category="x", title="t", detail="d",
                  community_id=None, action_hint=None, audit_event_type="t",
                  audit_ts_taipei=None, audit_event_count=1),
            Alert(severity="info", category="x", title="t", detail="d",
                  community_id=None, action_hint=None, audit_event_type="t",
                  audit_ts_taipei=None, audit_event_count=1),
        ]
        summary = alerts_summary(items)
        self.assertEqual(summary["blocking"], 1)
        self.assertEqual(summary["important"], 1)
        self.assertEqual(summary["info"], 2)
        self.assertEqual(summary["total"], 4)


if __name__ == "__main__":
    unittest.main()
