"""Onboarding readiness check for each enabled community.

Per CLAUDE.md §7-bis the new-community SOP has six required steps. This
workflow scans each enabled community and reports which steps are complete,
so:
  - the operator can see at a glance which communities are safe to start_watch
  - the scheduler daemon can warn at boot when an auto_watch-enabled community
    is missing critical setup (operator_nickname, voice profile, fingerprints)

We deliberately do NOT block startup — a misconfigured community should be
visible, not silent, but should not prevent the rest of the system from
running. Operator decides when to act on the warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app.storage.config_loader import CommunityConfig, load_all_communities
from app.storage.paths import voice_profile_path
from app.workflows.member_fingerprint import fingerprints_path


# A voice profile shorter than this is treated as a stub / bootstrap placeholder
# rather than a populated profile. The bootstrapper writes ~400 chars of headings;
# real profiles populated from chat_exports are typically 1500+ chars.
_VOICE_PROFILE_MIN_CHARS = 800


@dataclass(frozen=True)
class CommunityReadiness:
    customer_id: str
    community_id: str
    display_name: str
    auto_watch_enabled: bool
    has_operator_nickname: bool
    has_voice_profile: bool
    voice_profile_chars: int
    has_fingerprints: bool
    has_invite_url_or_group_id: bool

    @property
    def critical_gaps(self) -> tuple[str, ...]:
        """Gaps that make autonomous compose unsafe (i.e. block start_watch).

        Operator can still manually compose — `compose_and_send` doesn't go
        through the watch loop — but auto_watch should not fire without these.
        """
        gaps: list[str] = []
        if not self.has_operator_nickname:
            gaps.append("operator_nickname")
        if not self.has_voice_profile:
            gaps.append("voice_profile")
        if not self.has_invite_url_or_group_id:
            gaps.append("invite_url_or_group_id")
        return tuple(gaps)

    @property
    def soft_gaps(self) -> tuple[str, ...]:
        """Gaps that degrade quality but don't block. Watcher will still
        produce drafts; they just won't mirror per-member style."""
        gaps: list[str] = []
        if not self.has_fingerprints:
            gaps.append("member_fingerprints")
        if self.has_voice_profile and self.voice_profile_chars < _VOICE_PROFILE_MIN_CHARS:
            gaps.append("voice_profile_stub")
        return tuple(gaps)

    @property
    def ready_for_auto_watch(self) -> bool:
        return not self.critical_gaps


@dataclass(frozen=True)
class OnboardingReport:
    communities: tuple[CommunityReadiness, ...] = field(default_factory=tuple)

    @property
    def auto_watch_with_gaps(self) -> tuple[CommunityReadiness, ...]:
        return tuple(c for c in self.communities if c.auto_watch_enabled and c.critical_gaps)

    @property
    def critical_count(self) -> int:
        return sum(1 for c in self.communities if c.critical_gaps)

    @property
    def soft_count(self) -> int:
        return sum(1 for c in self.communities if c.soft_gaps)


def _check_one(community: CommunityConfig) -> CommunityReadiness:
    vp_path: Path = voice_profile_path(community.customer_id, community.community_id)
    vp_chars = 0
    has_vp = False
    if vp_path.exists():
        try:
            vp_chars = len(vp_path.read_text(encoding="utf-8"))
            has_vp = vp_chars > 0
        except OSError:
            has_vp = False

    fp_path: Path = fingerprints_path(community.customer_id, community.community_id)
    has_fp = fp_path.exists() and fp_path.stat().st_size > 2  # >"{}"

    return CommunityReadiness(
        customer_id=community.customer_id,
        community_id=community.community_id,
        display_name=community.display_name,
        auto_watch_enabled=community.auto_watch_enabled,
        has_operator_nickname=bool(community.operator_nickname),
        has_voice_profile=has_vp,
        voice_profile_chars=vp_chars,
        has_fingerprints=has_fp,
        has_invite_url_or_group_id=bool(community.invite_url or community.group_id),
    )


def build_onboarding_report(
    communities: Iterable[CommunityConfig] | None = None,
) -> OnboardingReport:
    src = list(communities) if communities is not None else load_all_communities()
    return OnboardingReport(communities=tuple(_check_one(c) for c in src))
