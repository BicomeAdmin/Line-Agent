"""Edit feedback loop — capture operator's edits as (original, edited)
pairs for future compose conditioning.

Paul's《私域流量》 Step 4「實時回饋優化」 made concrete: every time
the operator hits 「修改」 on a Lark card, we save what the bot
originally drafted vs what they sent. After accumulating ~5 pairs
per community, future compose prompts include them as in-context
learning examples — bot literally watches itself get edited and
adjusts.

Storage: customers/<id>/data/edit_feedback/<community_id>.jsonl
  Append-only JSON Lines, one record per edit.
  Schema:
    {
      "ts_taipei": "...",
      "review_id": "...",
      "community_id": "...",
      "original_draft": "...",
      "edited_draft": "...",
      "diff_summary": {... small heuristic deltas ...}
    }

Why JSONL not JSON: append-only, easy to stream-tail, robust to
crashes mid-write, easy to grep / cat for debugging.

Reading: load_recent_edits(customer_id, community_id, limit=5)
returns the most-recent N pairs for prompt injection.
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


def record_edit(
    customer_id: str,
    community_id: str,
    review_id: str,
    original_draft: str,
    edited_draft: str,
) -> dict[str, object]:
    """Append an edit pair to the feedback log. Idempotent on
    review_id — re-recording the same review with new edit text
    will create a new line (we keep edit history)."""

    if not original_draft or not edited_draft:
        return {"status": "skipped", "reason": "empty_drafts"}
    if original_draft.strip() == edited_draft.strip():
        return {"status": "skipped", "reason": "no_change"}

    path = edit_feedback_path(customer_id, community_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "ts_taipei": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
        "review_id": review_id,
        "community_id": community_id,
        "original_draft": original_draft.strip(),
        "edited_draft": edited_draft.strip(),
        "diff_summary": _summarize_diff(original_draft.strip(), edited_draft.strip()),
    }

    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    append_audit_event(
        customer_id,
        "edit_feedback_recorded",
        {
            "community_id": community_id,
            "review_id": review_id,
            "diff_summary": record["diff_summary"],
        },
    )
    return {"status": "ok", "stored_at": str(path), "record": record}


def load_recent_edits(
    customer_id: str,
    community_id: str,
    *,
    limit: int = 5,
) -> list[dict[str, object]]:
    """Return the most-recent N edit pairs for this community.
    Used to inject into compose prompt as in-context learning."""

    path = edit_feedback_path(customer_id, community_id)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, object]] = []
    for raw in lines[-limit * 3:]:  # read 3x the cap, then dedupe and trim
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        out.append(rec)
    # Most-recent first, capped at `limit`
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
