"""Review-outcome feedback loop — capture every operator decision
(approve / edit / ignore) as a training signal for future compose.

Paul's《私域流量》 Step 4「實時回饋優化」 made concrete. Originally
this only captured edits; that left the system blind to the most
common negative signal — operator silently ignoring bad drafts
(e.g. 2026-04-29 selector mis-fire). Now every action writes a row
so future tuning sees the full picture:

  approve : draft was fit-to-send as-is — positive signal
  edit    : draft needed rewrite — operator's edited version is the
            target; diff vs original surfaces patterns
  ignore  : draft wasn't worth sending — selector / composer bug
            signal, often the most actionable for tuning

Storage: customers/<id>/data/edit_feedback/<community_id>.jsonl
  Append-only JSON Lines, one record per outcome.
  Schema:
    {
      "ts_taipei": "...",
      "review_id": "...",
      "community_id": "...",
      "action": "approve" | "edit" | "ignore",
      "original_draft": "...",         # what the bot drafted
      "edited_draft": "..." | null,    # only set for action=edit
      "diff_summary": {...}            # only for action=edit
    }

Records written before this schema landed lack the `action` field;
load_recent_edits() treats those as edits for backward compat.

Why JSONL: append-only, easy to stream-tail, robust to crashes
mid-write, easy to grep / cat for debugging.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.core.audit import append_audit_event
from app.core.timezone import TAIPEI
from app.storage.paths import customer_data_root


def edit_feedback_path(customer_id: str, community_id: str) -> Path:
    return customer_data_root(customer_id) / "edit_feedback" / f"{community_id}.jsonl"


_VALID_ACTIONS = ("approve", "edit", "ignore")


def record_review_outcome(
    customer_id: str,
    community_id: str,
    review_id: str,
    action: str,
    *,
    original_draft: str,
    edited_draft: str | None = None,
) -> dict[str, object]:
    """Append a review outcome to the feedback log. Action is one of
    approve / edit / ignore. For action=edit, edited_draft must differ
    from original_draft; otherwise the record is skipped (no signal)."""

    if action not in _VALID_ACTIONS:
        return {"status": "skipped", "reason": f"unknown_action:{action}"}
    if not original_draft or not original_draft.strip():
        return {"status": "skipped", "reason": "empty_original"}

    original = original_draft.strip()
    edited: str | None = None
    diff: dict[str, object] = {}

    if action == "edit":
        if not edited_draft or not edited_draft.strip():
            return {"status": "skipped", "reason": "empty_edited"}
        edited = edited_draft.strip()
        if original == edited:
            return {"status": "skipped", "reason": "no_change"}
        diff = _summarize_diff(original, edited)

    record: dict[str, object] = {
        "ts_taipei": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
        "review_id": review_id,
        "community_id": community_id,
        "action": action,
        "original_draft": original,
        "edited_draft": edited,
    }
    if diff:
        record["diff_summary"] = diff

    path = edit_feedback_path(customer_id, community_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    append_audit_event(
        customer_id,
        "review_outcome_recorded",
        {
            "community_id": community_id,
            "review_id": review_id,
            "action": action,
            "diff_summary": diff or None,
        },
    )
    return {"status": "ok", "stored_at": str(path), "record": record}


def record_edit(
    customer_id: str,
    community_id: str,
    review_id: str,
    original_draft: str,
    edited_draft: str,
) -> dict[str, object]:
    """Backward-compatible wrapper for the original edit-only API.
    New code should call record_review_outcome directly."""

    return record_review_outcome(
        customer_id,
        community_id,
        review_id,
        action="edit",
        original_draft=original_draft,
        edited_draft=edited_draft,
    )


def load_recent_edits(
    customer_id: str,
    community_id: str,
    *,
    limit: int = 5,
) -> list[dict[str, object]]:
    """Return the most-recent N edit pairs for this community.
    Used to inject into compose prompt as in-context learning.
    Filters to action=edit (or legacy records without action field)
    so approve/ignore rows don't pollute the prompt's edit examples."""

    path = edit_feedback_path(customer_id, community_id)
    if not path.exists():
        return []
    # Read more than `limit` so the action filter still has runway.
    lines = path.read_text(encoding="utf-8").splitlines()
    edits: list[dict[str, object]] = []
    for raw in lines[-limit * 10:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        action = rec.get("action")
        # Legacy records (pre-2026-04-29 schema) have no action field
        # but ARE all edits — keep them for backward compat.
        if action is None or action == "edit":
            edits.append(rec)
    # Most-recent first, capped at `limit`
    return edits[-limit:][::-1]


def load_recent_outcomes(
    customer_id: str,
    community_id: str,
    *,
    limit: int = 20,
    actions: tuple[str, ...] | None = None,
) -> list[dict[str, object]]:
    """Return the most-recent N outcomes (any action). Useful for
    diagnostics and selector-tuning analysis."""

    path = edit_feedback_path(customer_id, community_id)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, object]] = []
    for raw in lines[-limit * 5:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if actions is not None:
            action = rec.get("action") or "edit"  # legacy records
            if action not in actions:
                continue
        out.append(rec)
    return out[-limit:][::-1]


def render_for_prompt(edits: list[dict[str, object]]) -> str:
    """Render edit pairs as zh-TW prompt-friendly markdown that the
    LLM brain can absorb as in-context learning."""

    if not edits:
        return ""
    lines: list[str] = ["## 操作員過去修改紀錄（學習用）"]
    lines.append("以下是你過去擬的稿，操作員實際送出的修改版。**模仿操作員的偏好**："
                 "他會怎麼改、改掉什麼、加進什麼。")
    for i, rec in enumerate(edits, 1):
        orig = (rec.get("original_draft") or "")[:120]
        edited = (rec.get("edited_draft") or "")[:120]
        ds = rec.get("diff_summary") or {}
        delta = ""
        if isinstance(ds, dict):
            shorter = ds.get("became_shorter")
            longer = ds.get("became_longer")
            note = ds.get("note") or ""
            if shorter:
                delta = f"（縮短 {shorter} 字）"
            elif longer:
                delta = f"（加長 {longer} 字）"
            if note:
                delta += f"  {note}"
        lines.append(f"\n[{i}] 我寫: 「{orig}」")
        lines.append(f"    操作員改成: 「{edited}」 {delta}")
    lines.append("\n→ 下次擬稿時，**先想一下操作員會怎麼改**，再寫。")
    return "\n".join(lines)


def _summarize_diff(original: str, edited: str) -> dict[str, object]:
    """Cheap heuristic delta — useful for surfacing patterns without LLM."""

    olen = len(original)
    elen = len(edited)
    diff: dict[str, object] = {}
    if elen < olen:
        diff["became_shorter"] = olen - elen
    elif elen > olen:
        diff["became_longer"] = elen - olen

    # Punctuation deltas
    for marker in ("！", "!", "？", "?", "～", "～～", "🤣", "...", "。"):
        d = edited.count(marker) - original.count(marker)
        if d != 0:
            diff[f"punct_{marker}_delta"] = d

    # Particle deltas (zh-TW chat-y endings)
    for particle in ("啊", "喔", "欸", "哈", "吧", "嗎", "呢", "啦", "耶"):
        d = edited.count(particle) - original.count(particle)
        if d != 0:
            diff[f"particle_{particle}_delta"] = d

    # Quick characterization
    notes = []
    if diff.get("became_shorter") and diff.get("became_shorter") > 5:
        notes.append("操作員偏好較短")
    if diff.get("became_longer") and diff.get("became_longer") > 10:
        notes.append("操作員加了內容")
    if any(k.startswith("punct_") for k in diff):
        notes.append("標點習慣不同")
    if any(k.startswith("particle_") for k in diff):
        notes.append("語助詞調整")
    if notes:
        diff["note"] = "、".join(notes)

    return diff
