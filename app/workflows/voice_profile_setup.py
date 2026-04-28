"""Voice profile completion workflow — diagnose what's missing in a
community's voice_profile.md and provide a surgical section-updater
so the operator can fill it conversationally instead of editing the
markdown by hand.

Two public surfaces, both registered as MCP tools:

  - check_voice_profile(customer_id, community_id) →
      {status, completeness_pct, missing: [...], next_actions: [...]}
    Read-only diagnostic. Tells the operator (and the LLM brain) which
    sections are still placeholder vs filled, with concrete suggested
    commands for each gap.

  - update_voice_profile_section(customer_id, community_id, section, content)
    Surgical mutator: replaces the body of a single named section in
    voice_profile.md, preserving everything else (auto-harvested
    block, other sections, header). Used when operator says
    「我在 X 群暱稱叫 Y」 → update_voice_profile_section(..., "nickname", "Y").

Why a separate file: keeps style_harvest.py focused on the auto path
and keeps these manual-completion helpers cleanly separated from the
read-only persona_context bundle.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.audit import append_audit_event
from app.storage.config_loader import load_community_config
from app.storage.paths import voice_profile_path


# ──────────────────────────────────────────────────────────────────────
# Section taxonomy — what counts as "filled" for each section
# ──────────────────────────────────────────────────────────────────────

# section key -> (header substring used in markdown, has placeholder marker)
_SECTION_SPEC = [
    ("nickname",      "nickname",          True),   # 必填，bootstrap 預設是 placeholder
    ("personality",   "personality",       True),   # 必填
    ("style_anchors", "Style anchors",     False),  # default 內容夠用
    ("samples",       "Samples",           True),   # 操作員自己想累積的句子
    ("off_limits",    "Off-limits",        False),  # default 內容夠用
    ("observed",      "Observed community", False), # auto-managed by harvest
]


def check_voice_profile(customer_id: str, community_id: str) -> dict[str, object]:
    """Diagnose which voice profile sections still need operator input."""

    try:
        load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    path = voice_profile_path(customer_id, community_id)
    if not path.exists():
        return {
            "status": "error",
            "reason": "voice_profile_missing",
            "path": str(path),
            "next_actions": [
                f"先 add_community 或手動建立 {path}，再來檢查",
            ],
        }

    text = path.read_text(encoding="utf-8")
    sections = _parse_sections(text)

    missing: list[dict[str, str]] = []
    filled_required = 0
    total_required = 0

    for key, header_hint, is_required in _SECTION_SPEC:
        body = _get_body_for(sections, header_hint)
        is_placeholder = _is_placeholder_only(body)
        if is_required:
            total_required += 1
            if is_placeholder or not body.strip():
                missing.append({
                    "section": key,
                    "header_hint": header_hint,
                    "why": "placeholder_only" if body.strip() else "empty",
                    "suggestion": _suggestion_for(key, community_id),
                })
            else:
                filled_required += 1

    has_harvested = "BEGIN auto-harvested" in text
    completeness_pct = (
        round(100 * filled_required / total_required) if total_required else 100
    )

    next_actions: list[str] = []
    if not has_harvested:
        next_actions.append(
            f"執行 harvest_style_samples({community_id}) 自動補入真實成員語句"
        )
    for m in missing:
        next_actions.append(m["suggestion"])

    return {
        "status": "ok",
        "community_id": community_id,
        "path": str(path),
        "completeness_pct": completeness_pct,
        "filled_required": filled_required,
        "total_required": total_required,
        "missing": missing,
        "has_harvested_block": has_harvested,
        "next_actions": next_actions,
        "summary_zh": _build_summary(community_id, completeness_pct, missing, has_harvested),
    }


def update_voice_profile_section(
    customer_id: str,
    community_id: str,
    section: str,
    content: str,
) -> dict[str, object]:
    """Surgically replace one section's body. Section is matched by key
    (e.g. 'nickname') or header hint substring. Auto-managed harvest
    block is never touched here — use harvest_style_samples for that."""

    try:
        load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    new_body = (content or "").strip()
    if not new_body:
        return {"status": "error", "reason": "empty_content"}

    # Resolve section name → header substring used in the markdown.
    header_hint = _resolve_header_hint(section)
    if header_hint is None:
        return {
            "status": "error",
            "reason": "unknown_section",
            "valid_sections": [k for k, _, _ in _SECTION_SPEC],
        }

    path = voice_profile_path(customer_id, community_id)
    if not path.exists():
        return {"status": "error", "reason": "voice_profile_missing", "path": str(path)}

    original = path.read_text(encoding="utf-8")
    updated, replaced = _replace_section_body(original, header_hint, new_body)
    if not replaced:
        return {
            "status": "error",
            "reason": "section_header_not_found",
            "header_hint": header_hint,
            "hint": "voice profile 沒有對應的 ## 標題；請手動補一個 `## ...` 區塊再試。",
        }

    path.write_text(updated, encoding="utf-8")

    append_audit_event(
        customer_id,
        "voice_profile_section_updated",
        {
            "community_id": community_id,
            "section": section,
            "header_hint": header_hint,
            "preview": new_body[:80],
        },
    )

    return {
        "status": "ok",
        "community_id": community_id,
        "section": section,
        "header_hint": header_hint,
        "preview": new_body[:80],
        "path": str(path),
    }


# ──────────────────────────────────────────────────────────────────────
# Markdown parsing helpers
# ──────────────────────────────────────────────────────────────────────

def _parse_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (header_line, body_text) pairs. Plain regex
    keeps this dependency-free and predictable for our short profiles."""

    out: list[tuple[str, str]] = []
    current_header: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("## "):
            if current_header is not None:
                out.append((current_header, "\n".join(buf).strip()))
            current_header = line.strip()
            buf = []
        else:
            buf.append(line)
    if current_header is not None:
        out.append((current_header, "\n".join(buf).strip()))
    return out


def _get_body_for(sections: list[tuple[str, str]], header_hint: str) -> str:
    for header, body in sections:
        if header_hint.lower() in header.lower():
            return body
    return ""


_PLACEHOLDER_PATTERNS = [
    re.compile(r"（請操作員填"),
    re.compile(r"（請操作員寫"),
    re.compile(r"（請操作員之後"),
    re.compile(r"請操作員"),
]


def _is_placeholder_only(body: str) -> bool:
    """A section is a placeholder if every non-empty bullet is the
    bootstrap stub. Once the operator types one real line, this flips
    to filled."""

    if not body.strip():
        return True
    real_lines = 0
    for raw in body.splitlines():
        s = raw.strip().lstrip("-").strip()
        if not s:
            continue
        if any(p.search(s) for p in _PLACEHOLDER_PATTERNS):
            continue
        real_lines += 1
    return real_lines == 0


def _replace_section_body(text: str, header_hint: str, new_body: str) -> tuple[str, bool]:
    """Find the first `## <header_hint>` and replace its body (lines
    until the next `##` or EOF). Returns (new_text, was_replaced)."""

    lines = text.splitlines()
    section_start: int | None = None
    section_end: int | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and header_hint.lower() in stripped.lower() and section_start is None:
            section_start = i + 1
            continue
        if section_start is not None and stripped.startswith("## "):
            section_end = i
            break

    if section_start is None:
        return text, False
    if section_end is None:
        section_end = len(lines)

    new_lines = lines[:section_start] + ["", new_body.rstrip(), ""] + lines[section_end:]
    return "\n".join(new_lines), True


def _resolve_header_hint(section: str) -> str | None:
    s = (section or "").strip().lower()
    if not s:
        return None
    # Exact key match (nickname / personality / style_anchors / samples / off_limits / observed)
    for key, hint, _ in _SECTION_SPEC:
        if s == key:
            return hint
    # Already a header hint?
    for _, hint, _ in _SECTION_SPEC:
        if s == hint.lower() or s in hint.lower():
            return hint
    # Common Chinese aliases.
    alias_map = {
        "暱稱": "nickname", "稱呼": "nickname",
        "個性": "personality", "性格": "personality",
        "風格": "Style anchors", "口氣": "Style anchors",
        "樣本": "Samples", "範例": "Samples",
        "底線": "Off-limits", "禁忌": "Off-limits",
    }
    return alias_map.get(s)


# ──────────────────────────────────────────────────────────────────────
# Suggestions (Chinese strings tuned for operator UX)
# ──────────────────────────────────────────────────────────────────────

def _suggestion_for(section: str, community_id: str) -> str:
    if section == "nickname":
        return f"在 Lark 對 bot 講「我在 {community_id} 暱稱叫 XXX」"
    if section == "personality":
        return f"講「我在 {community_id} 的個性是 XXX（一兩句敘述）」"
    if section == "samples":
        return f"講「我在 {community_id} 想讓 bot 學這幾句：『...』」累積 1-3 句即可"
    return f"section={section} 需要操作員補填"


def _build_summary(community_id: str, pct: int, missing: list[dict], has_harvested: bool) -> str:
    parts = [f"{community_id} voice profile 完成度 {pct}%"]
    if not has_harvested:
        parts.append("還沒抓過真實語料（建議先 harvest_style_samples）")
    if missing:
        keys = "、".join(m["section"] for m in missing)
        parts.append(f"缺：{keys}")
    else:
        parts.append("必填欄位都填好了 ✅")
    return "；".join(parts)
