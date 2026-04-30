"""Coverage for `_process_scheduled_post` — direct text vs compose mode,
HIL regression, codex-disabled fallback.
"""

import unittest
from unittest.mock import MagicMock, patch

from app.workflows.job_processor import _process_scheduled_post


def _make_payload(**overrides) -> dict:
    base = {
        "customer_id": "customer_a",
        "community_id": "openchat_004",
        "device_id": "emulator-5554",
        "post_id": "post-x",
        "draft_text": "hello",
        "pre_approved": False,
        "compose_mode": False,
        "brief": None,
        "send_at_iso": "2099-05-04T20:00:00+08:00",
    }
    base.update(overrides)
    return base


class _BaseProcessor(unittest.TestCase):
    """Common patches for all processor tests — load_customer + community + audit."""

    def setUp(self) -> None:
        self._customer_patch = patch(
            "app.workflows.job_processor.load_customer_config",
            return_value=MagicMock(display_name="客戶 A"),
        )
        community = MagicMock()
        community.community_id = "openchat_004"
        community.customer_id = "customer_a"
        community.device_id = "emulator-5554"
        community.display_name = "水月觀音道場"
        community.llm_compose_enabled = True
        self._community = community
        self._community_patch = patch(
            "app.workflows.job_processor.load_communities_for_device",
            return_value=[community],
        )
        self._audit_patch = patch(
            "app.workflows.job_processor.append_audit_event",
            lambda *a, **k: None,
        )
        self._mark_review_patch = patch(
            "app.workflows.job_processor.mark_post_reviewing",
            return_value={},
        )
        self._mark_skip_patch = patch(
            "app.workflows.job_processor.mark_post_skipped",
            return_value={},
        )
        self._mark_sent_patch = patch(
            "app.workflows.job_processor.mark_post_sent",
            return_value={},
        )
        self._build_card_patch = patch(
            "app.workflows.job_processor.build_review_card",
            return_value={"card": "stub"},
        )
        for p in (
            self._customer_patch,
            self._community_patch,
            self._audit_patch,
            self._mark_review_patch,
            self._mark_skip_patch,
            self._mark_sent_patch,
            self._build_card_patch,
        ):
            p.start()
            self.addCleanup(p.stop)


class DirectTextPathTests(_BaseProcessor):
    def test_review_pending_for_direct_text_no_pre_approval(self) -> None:
        result = _process_scheduled_post(_make_payload())
        self.assertEqual(result["status"], "review_pending")
        self.assertEqual(result["decision"]["draft"], "hello")
        self.assertEqual(result["decision"]["source"], "scheduled_post")

    @patch("app.workflows.job_processor.send_draft", return_value={"status": "sent"})
    def test_auto_send_when_pre_approved_and_global_off(self, mock_send) -> None:
        with patch("app.workflows.job_processor.settings") as mock_settings:
            mock_settings.require_human_approval = False
            result = _process_scheduled_post(_make_payload(pre_approved=True))
        self.assertEqual(result["status"], "sent")
        mock_send.assert_called_once()

    @patch("app.workflows.job_processor.send_draft")
    def test_no_auto_send_when_global_human_approval_on(self, mock_send) -> None:
        with patch("app.workflows.job_processor.settings") as mock_settings:
            mock_settings.require_human_approval = True
            result = _process_scheduled_post(_make_payload(pre_approved=True))
        self.assertEqual(result["status"], "review_pending")
        mock_send.assert_not_called()


class ComposeModePathTests(_BaseProcessor):
    def _patch_compose(self, **kwargs):
        """Patch the codex_compose surface that `_compose_brand_draft` reaches into."""

        defaults = {
            "is_enabled": True,
            "voice_profile": MagicMock(is_complete=True, missing_fields=[]),
            "persona": {"recent_self_posts": []},
        }
        defaults.update(kwargs)
        return defaults

    def test_happy_path_compose_then_review(self) -> None:
        compose_output = MagicMock(
            should_engage=True,
            draft="我最近也試了五分鐘 蠻有感的耶",
            rationale="brief 跟階段任務一致",
            confidence=0.78,
            off_limits_hit=None,
        )
        with patch("app.ai.codex_compose.is_enabled", return_value=True), \
             patch("app.ai.codex_compose.compose_brand_post_via_codex", return_value=compose_output) as mock_compose, \
             patch("app.ai.voice_profile_v2.parse_voice_profile",
                   return_value=MagicMock(is_complete=True, missing_fields=[])), \
             patch("app.workflows.persona_context.get_persona_context",
                   return_value={"recent_self_posts": []}), \
             patch("app.storage.config_loader.load_community_config",
                   return_value=self._community), \
             patch("app.storage.paths.voice_profile_path", return_value=MagicMock()), \
             patch("app.workflows.job_processor._read_thread_for_brand", return_value=[]), \
             patch("app.workflows.scheduled_posts.get_post", return_value={"post_id": "post-x", "status": "due"}):
            result = _process_scheduled_post(_make_payload(
                draft_text="",
                compose_mode=True,
                brief="靜坐入門引子",
            ))

        self.assertEqual(result["status"], "review_pending")
        self.assertEqual(result["decision"]["draft"], "我最近也試了五分鐘 蠻有感的耶")
        self.assertEqual(result["decision"]["source"], "codex_brand")
        mock_compose.assert_called_once()
        # Brief should be passed through; recent_self_posts list reachable.
        kwargs = mock_compose.call_args.kwargs
        self.assertEqual(kwargs["brief"], "靜坐入門引子")

    def test_compose_mode_skipped_when_codex_disabled(self) -> None:
        with patch("app.ai.codex_compose.is_enabled", return_value=False):
            result = _process_scheduled_post(_make_payload(
                draft_text="", compose_mode=True, brief="brief",
            ))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "codex_backend_disabled")

    def test_compose_mode_skipped_when_community_gate_off(self) -> None:
        self._community.llm_compose_enabled = False
        with patch("app.ai.codex_compose.is_enabled", return_value=True), \
             patch("app.storage.config_loader.load_community_config",
                   return_value=self._community):
            result = _process_scheduled_post(_make_payload(
                draft_text="", compose_mode=True, brief="brief",
            ))
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "community_llm_compose_disabled")

    def test_compose_mode_skipped_on_should_not_engage(self) -> None:
        compose_output = MagicMock(
            should_engage=False,
            draft="",
            rationale="brief 與 off-limits 衝突",
            confidence=0.0,
            off_limits_hit="評論個人選擇",
        )
        with patch("app.ai.codex_compose.is_enabled", return_value=True), \
             patch("app.ai.codex_compose.compose_brand_post_via_codex", return_value=compose_output), \
             patch("app.ai.voice_profile_v2.parse_voice_profile",
                   return_value=MagicMock(is_complete=True, missing_fields=[])), \
             patch("app.workflows.persona_context.get_persona_context",
                   return_value={"recent_self_posts": []}), \
             patch("app.storage.config_loader.load_community_config",
                   return_value=self._community), \
             patch("app.storage.paths.voice_profile_path", return_value=MagicMock()), \
             patch("app.workflows.job_processor._read_thread_for_brand", return_value=[]), \
             patch("app.workflows.scheduled_posts.get_post", return_value={"post_id": "post-x", "status": "due"}):
            result = _process_scheduled_post(_make_payload(
                draft_text="", compose_mode=True, brief="brief",
            ))
        self.assertEqual(result["status"], "skipped")
        self.assertIn("composer_should_not_engage", result["reason"])

    def test_drops_draft_when_post_cancelled_during_compose(self) -> None:
        """Race guard: operator cancels post mid-codex (60-90s window) →
        composed draft is dropped, review_store untouched, audit captured."""

        compose_output = MagicMock(
            should_engage=True, draft="嗨大家",
            rationale="ok", confidence=0.7, off_limits_hit=None,
        )
        with patch("app.ai.codex_compose.is_enabled", return_value=True), \
             patch("app.ai.codex_compose.compose_brand_post_via_codex", return_value=compose_output), \
             patch("app.ai.voice_profile_v2.parse_voice_profile",
                   return_value=MagicMock(is_complete=True, missing_fields=[])), \
             patch("app.workflows.persona_context.get_persona_context",
                   return_value={"recent_self_posts": []}), \
             patch("app.storage.config_loader.load_community_config",
                   return_value=self._community), \
             patch("app.storage.paths.voice_profile_path", return_value=MagicMock()), \
             patch("app.workflows.job_processor._read_thread_for_brand", return_value=[]), \
             patch("app.workflows.scheduled_posts.get_post",
                   return_value={"post_id": "post-x", "status": "cancelled"}):
            result = _process_scheduled_post(_make_payload(
                draft_text="", compose_mode=True, brief="brief",
            ))

        # Should be skipped — draft never reached review_store
        self.assertEqual(result["status"], "skipped")
        self.assertIn("post_status_changed_during_compose", result["reason"])

    @patch("app.workflows.job_processor.send_draft")
    def test_compose_mode_never_auto_sends_even_with_pre_approved(self, mock_send) -> None:
        """HIL regression: LLM drafts ALWAYS go through review,
        even when pre_approved=true AND global require_human_approval=false."""

        compose_output = MagicMock(
            should_engage=True, draft="我最近五分鐘 還滿有感的耶",
            rationale="ok", confidence=0.8, off_limits_hit=None,
        )
        with patch("app.ai.codex_compose.is_enabled", return_value=True), \
             patch("app.ai.codex_compose.compose_brand_post_via_codex", return_value=compose_output), \
             patch("app.ai.voice_profile_v2.parse_voice_profile",
                   return_value=MagicMock(is_complete=True, missing_fields=[])), \
             patch("app.workflows.persona_context.get_persona_context",
                   return_value={"recent_self_posts": []}), \
             patch("app.storage.config_loader.load_community_config",
                   return_value=self._community), \
             patch("app.storage.paths.voice_profile_path", return_value=MagicMock()), \
             patch("app.workflows.job_processor._read_thread_for_brand", return_value=[]), \
             patch("app.workflows.scheduled_posts.get_post",
                   return_value={"post_id": "post-x", "status": "due"}), \
             patch("app.workflows.job_processor.settings") as mock_settings:
            mock_settings.require_human_approval = False
            result = _process_scheduled_post(_make_payload(
                draft_text="", compose_mode=True, brief="brief",
                pre_approved=True,
            ))

        self.assertEqual(result["status"], "review_pending")
        mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
