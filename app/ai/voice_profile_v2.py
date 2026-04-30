"""Voice profile parser — frontmatter + sections.

The voice_profile.md for each (customer × community) is the operator's
source of truth: who am I in this group, what value do I bring, what
stage is the group in, what's off-limits. Composer prompts are built
from this — sparse profiles produce sparse drafts, which is by design
(don't ship LLM compose without first writing a real profile).

Frontmatter (YAML-ish, between `---` lines at top):
  value_proposition: free text — Paul §0.5.1 V (the V in VCPVC)
  route_mix:                     - §0.5.4 三種營運途徑 配比
    ip: 0.0-1.0
    interest: 0.0-1.0
    info: 0.0-1.0
  stage: 拉新 | 留存 | 活躍 | 裂變    - §0.5.2 四步驟
  engagement_appetite: low | medium | high
                                 - composer's main "活潑度" knob

Below the frontmatter, the existing free-text sections are read by
section heading: "My nickname", "My personality", "Style anchors",
"Off-limits", and the auto-harvested "Observed community lines".

Missing or malformed frontmatter is non-fatal — we return a profile
with `is_complete=False` and the composer should refuse to draft
(skip with reason `voice_profile_incomplete`). This is the §0.5.6
gate: VCPVC must be filled in before drafting.

No external deps; uses simple_yaml for the frontmatter block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.simple_yaml import load_yaml


_VALID_STAGES = {"拉新", "留存", "活躍", "裂變"}
_VALID_APPETITES = {"low", "medium", "high"}

# Stage → core objective lookup (§0.5.4). Composer prompt injects this
# so the LLM understands "what does this stage actually mean for my
# behavior right now."
_STAGE_OBJECTIVES = {
    "拉新": "讓新進成員感受到這個社群的價值，不要冷臉，但不要急著推銷",
    "留存": "累積信任、陪伴感，不急著轉換；寧可少說也不要說錯",
    "活躍": "真誠互動、引發 UGC（自然提問與分享），不要主導話題",
    "裂變": "協助鐵粉放大聲量，自己退到輔助位，把舞台讓給用戶",
}


@dataclass(frozen=True)
class RouteMix:
    ip: float = 0.0
    interest: float = 0.0
    info: float = 0.0

    def normalized(self) -> "RouteMix":
        total = self.ip + self.interest + self.info
        if total <= 0:
            return self
        return RouteMix(ip=self.ip / total, interest=self.interest / total, info=self.info / total)

    def dominant(self) -> str:
        pairs = [("IP 主導", self.ip), ("興趣主導", self.interest), ("資訊主導", self.info)]
        pairs.sort(key=lambda p: p[1], reverse=True)
        return pairs[0][0] if pairs[0][1] > 0 else "未設定"


@dataclass(frozen=True)
class VoiceProfile:
    """Parsed voice_profile.md.

    `is_complete` is the gate: composer should refuse if False, with
    reason `voice_profile_incomplete:<missing_field>`. This forces the
    operator to actually fill the profile before LLM drafts go live —
    no defaulting to empty strings inside prompts.
    """

    customer_id: str
    community_id: str
    raw_text: str
    # Frontmatter
    value_proposition: str = ""
    route_mix: RouteMix = field(default_factory=RouteMix)
    stage: str = ""
    engagement_appetite: str = "medium"
    # Sections
    nickname: str = ""
    personality: str = ""
    style_anchors: str = ""
    off_limits: str = ""
    samples: str = ""
    observed_lines: str = ""
    # Validation
    is_complete: bool = False
    missing_fields: tuple[str, ...] = ()

    @property
    def stage_objective(self) -> str:
        return _STAGE_OBJECTIVES.get(self.stage, "（社群階段未設定，先以「留存」處理）")


def parse_voice_profile(
    customer_id: str,
    community_id: str,
    path: Path,
) -> VoiceProfile:
    if not path.exists():
        return VoiceProfile(
            customer_id=customer_id,
            community_id=community_id,
            raw_text="",
            is_complete=False,
            missing_fields=("file_missing",),
        )
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)

    fm = frontmatter or {}
    value_prop = str(fm.get("value_proposition") or "").strip()
    route_mix = _parse_route_mix(fm.get("route_mix"))
    stage = str(fm.get("stage") or "").strip()
    appetite = str(fm.get("engagement_appetite") or "medium").strip().lower()
    if appetite not in _VALID_APPETITES:
        appetite = "medium"

    nickname = _extract_section_first_bullet(body, "My nickname")
    personality = _extract_section(body, "My personality")
    style_anchors = _extract_section(body, "Style anchors")
    off_limits = _extract_section(body, "Off-limits")
    samples = _extract_section(body, "Samples")
    observed = _extract_section(body, "Observed community lines")

    missing: list[str] = []
    if not value_prop or _is_placeholder(value_prop):
        missing.append("value_proposition")
    if route_mix.ip + route_mix.interest + route_mix.info <= 0:
        missing.append("route_mix")
    if stage not in _VALID_STAGES:
        missing.append("stage")
    if not nickname or _is_placeholder(nickname):
        missing.append("nickname")
    if not personality or _is_placeholder(personality):
        missing.append("personality")
    if not off_limits or _is_placeholder(off_limits):
        missing.append("off_limits")
    if style_anchors and _is_placeholder(style_anchors):
        # style_anchors is optional in shape but if present and a
        # placeholder, refuse — operator clearly intended to fill it.
        missing.append("style_anchors_placeholder")

    return VoiceProfile(
        customer_id=customer_id,
        community_id=community_id,
        raw_text=text,
        value_proposition=value_prop,
        route_mix=route_mix.normalized(),
        stage=stage,
        engagement_appetite=appetite,
        nickname=nickname,
        personality=personality,
        style_anchors=style_anchors,
        off_limits=off_limits,
        samples=samples,
        observed_lines=observed,
        is_complete=not missing,
        missing_fields=tuple(missing),
    )


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[dict[str, object] | None, str]:
    # Tolerate leading blank lines / BOM — operators may save voice_profile.md
    # with editor whitespace that would defeat a strict ^--- match.
    stripped = text.lstrip("﻿").lstrip()
    match = _FRONTMATTER_RE.match(stripped)
    if not match:
        return None, text
    raw = match.group(1)
    body_after = stripped[match.end():]
    try:
        loaded = load_yaml(raw)
    except Exception:  # noqa: BLE001 — malformed frontmatter is non-fatal
        return None, body_after
    if not isinstance(loaded, dict):
        return None, body_after
    return loaded, body_after


def _parse_route_mix(value: object) -> RouteMix:
    if not isinstance(value, dict):
        return RouteMix()
    def _f(key: str) -> float:
        raw = value.get(key)
        if raw is None:
            return 0.0
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return 0.0
    return RouteMix(ip=_f("ip"), interest=_f("interest"), info=_f("info"))


def _extract_section(body: str, heading_keyword: str) -> str:
    """Return text under any `## ...{heading_keyword}...` until next `##`."""

    pattern = re.compile(
        rf"^##\s+[^\n]*{re.escape(heading_keyword)}[^\n]*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(body)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_section_first_bullet(body: str, heading_keyword: str) -> str:
    """Find first `- ...` bullet under the matching section."""

    section = _extract_section(body, heading_keyword)
    if not section:
        return ""
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("-"):
            return stripped.lstrip("-").strip()
    return section.splitlines()[0].strip() if section.splitlines() else ""


_PLACEHOLDER_MARKERS = (
    # Chinese placeholder phrases the bootstrap script writes
    "（請操作員填", "（請操作員寫", "（未設定）", "（待補", "（待填", "（補上",
    "（待寫", "（example", "（範例", "（樣本待補", "（暫填",
    # English placeholders developers / operators sometimes leave in
    "TODO", "FIXME", "todo:", "fixme:", "lorem ipsum", "placeholder",
    "<placeholder>", "[placeholder]", "{{", "xxx",
    "fill in", "fill this in", "to be filled", "tbd", "tbc",
)
# Short stub texts that operators leave when bootstrapping but forget to expand.
_STUB_LITERALS = {"test", "wip", "draft", "n/a", "na", "?", "??", "???", "..."}


def _is_placeholder(text: str) -> bool:
    """Return True when `text` is empty-equivalent — a placeholder
    marker, a stub literal, or a too-short string that clearly isn't
    real content. Decisions based on this gate composer activation;
    err on the side of refusing rather than drafting from junk.
    """

    if not text:
        return True
    lowered = text.strip().lower()
    if not lowered:
        return True
    if lowered in _STUB_LITERALS:
        return True
    for marker in _PLACEHOLDER_MARKERS:
        if marker.lower() in lowered:
            return True
    return False
