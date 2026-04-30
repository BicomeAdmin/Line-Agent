"""Set / inspect the operator's nickname per community.

Each LINE OpenChat may show the operator under a different display
name (e.g. "比利" in 愛美星, "山寶" in 山納百景). The autonomous
reply pipeline needs this mapping to:
  - filter out the operator's own messages from chat-export-derived
    history (which doesn't have the runtime is_self flag)
  - identify @-mentions to the operator
  - distinguish "after_operator_speech" boost when scoring candidates

Stored in customers/<id>/communities/<community_id>.yaml as
`operator_nickname: <name>`. Surgically-edited so other YAML fields
remain untouched.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.audit import append_audit_event
from app.storage.config_loader import load_community_config
from app.storage.paths import customer_root


# Visually-similar Han characters that previously caused a 6-month
# operator_nickname misconfiguration (翊 read as 妍). Each pair below
# is a known confusable cluster from real onboarding mistakes — when
# any of these chars appears in a new nickname, callers get a hint to
# verify against LINE UI rather than relying on screenshot reading.
_CONFUSABLE_HAN = set("翊妍玕玟玥彥彦顏徐徒徙璿瑢璇蓁榛溱")


def _community_yaml_path(customer_id: str, community_id: str) -> Path:
    return customer_root(customer_id) / "communities" / f"{community_id}.yaml"


def _count_export_hits(customer_id: str, community_id: str, nickname: str) -> dict[str, object]:
    """Count how many times `nickname` appears in the latest chat_export.
    Cheap proxy for "is this nickname plausible?" — but a 0-hit result
    is NOT proof the nickname is wrong (broadcast / fan groups have
    operators who barely post). Caller treats this as a soft signal."""

    from app.workflows.member_fingerprint import latest_export_path

    src = latest_export_path(customer_id, community_id)
    if src is None or not src.exists():
        return {"export_available": False, "hits": 0, "export_path": None}
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return {"export_available": False, "hits": 0, "export_path": str(src)}
    return {
        "export_available": True,
        "hits": text.count(nickname),
        "export_path": str(src),
    }


def set_operator_nickname(
    customer_id: str,
    community_id: str,
    nickname: str,
) -> dict[str, object]:
    """Persist the operator's nickname for this community into its YAML."""

    nickname = (nickname or "").strip()
    if not nickname:
        return {"status": "error", "reason": "empty_nickname"}

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    yaml_path = _community_yaml_path(customer_id, community_id)
    if not yaml_path.exists():
        return {"status": "error", "reason": "yaml_missing", "path": str(yaml_path)}

    text = yaml_path.read_text(encoding="utf-8")
    new_line = f'operator_nickname: "{nickname}"'

    # Replace existing key if present, else append.
    pattern = re.compile(r"^operator_nickname:\s*.*$", re.MULTILINE)
    if pattern.search(text):
        new_text = pattern.sub(new_line, text, count=1)
    else:
        # Append at end with a clean newline boundary.
        sep = "" if text.endswith("\n") else "\n"
        new_text = text + sep + new_line + "\n"

    yaml_path.write_text(new_text, encoding="utf-8")

    # Soft sanity check: count nickname hits in latest chat_export.
    # 0 hits is legitimate for fan / broadcast groups where operator
    # barely posts (verified empirically on 001 + 005 — yaml was correct
    # despite 0 export hits). So we never block, only signal.
    export_info = _count_export_hits(customer_id, community_id, nickname)
    confusable_hit = sorted(set(nickname) & _CONFUSABLE_HAN)
    warnings: list[str] = []
    if confusable_hit:
        warnings.append(
            f"形似漢字警示：暱稱含 {''.join(confusable_hit)}，請對照 LINE 個人檔案頁字形再確認"
        )
    if export_info.get("export_available") and export_info.get("hits") == 0:
        warnings.append(
            "暱稱在最近 chat_export 0 命中。fan / broadcast 群操作員少發言屬正常；"
            "但若是高互動 IP 主導群，建議到 LINE UI 個人檔案頁親眼確認。"
        )
    verification_hint = (
        "Ground truth = LINE UI 個人檔案頁。emulator navigate 進群 → 點群標題 → "
        "群封面頁底部會顯示「<頭像>「<我的暱稱>」」。或 ≡ menu → 設定 → 個人檔案。"
    )

    append_audit_event(
        customer_id,
        "operator_nickname_set",
        {
            "community_id": community_id,
            "old_nickname": community.operator_nickname,
            "new_nickname": nickname,
            "export_hits": export_info.get("hits"),
            "export_available": export_info.get("export_available"),
            "confusable_chars": confusable_hit,
            "warnings": warnings,
        },
    )
    return {
        "status": "ok",
        "community_id": community_id,
        "operator_nickname": nickname,
        "previous": community.operator_nickname,
        "yaml_path": str(yaml_path),
        "export_hits": export_info.get("hits"),
        "export_available": export_info.get("export_available"),
        "confusable_chars": confusable_hit,
        "warnings": warnings,
        "verification_hint": verification_hint,
    }


def audit_all_communities(customer_id: str = "customer_a") -> dict[str, object]:
    """Daemon-startup invariant: build a per-community summary of
    operator_nickname × chat_export hits. Used to surface mis-typed
    nicknames before they corrupt 6 months of fingerprint data.

    Returns a dict with `rows` and a list of `warnings`. Caller (daemon)
    decides how to render — typically prints a table to stdout and
    appends one audit event."""

    from app.storage.config_loader import load_all_communities

    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    for c in load_all_communities():
        if c.customer_id != customer_id:
            continue
        nickname = (c.operator_nickname or "").strip()
        if not nickname:
            rows.append({
                "community_id": c.community_id,
                "display_name": c.display_name,
                "operator_nickname": None,
                "export_hits": None,
                "export_available": False,
                "confusable_chars": [],
                "status": "missing",
            })
            warnings.append(f"{c.community_id}: operator_nickname not set")
            continue
        info = _count_export_hits(customer_id, c.community_id, nickname)
        confusable = sorted(set(nickname) & _CONFUSABLE_HAN)
        hits = info.get("hits")
        if not info.get("export_available"):
            row_status = "no_export"
        elif hits == 0:
            row_status = "low_activity"  # legitimate for fan/broadcast
        else:
            row_status = "ok"
        rows.append({
            "community_id": c.community_id,
            "display_name": c.display_name,
            "operator_nickname": nickname,
            "export_hits": hits,
            "export_available": info.get("export_available"),
            "confusable_chars": confusable,
            "status": row_status,
        })
        if confusable:
            warnings.append(
                f"{c.community_id}: nickname '{nickname}' contains visually-confusable char(s) {''.join(confusable)}"
            )
    rows.sort(key=lambda r: r["community_id"])
    return {
        "status": "ok",
        "rows": rows,
        "warnings": warnings,
        "warning_count": len(warnings),
    }


def list_operator_identity(customer_id: str = "customer_a") -> dict[str, object]:
    """Diagnostic: show which communities have operator_nickname set
    and which still need it. Used by the dashboard / by Lark when
    operator asks 「我的暱稱在每個群分別是什麼」."""

    from app.storage.config_loader import load_all_communities

    rows: list[dict[str, object]] = []
    for c in load_all_communities():
        if c.customer_id != customer_id:
            continue
        rows.append({
            "community_id": c.community_id,
            "display_name": c.display_name,
            "operator_nickname": c.operator_nickname,
            "configured": bool(c.operator_nickname),
        })
    rows.sort(key=lambda r: r["community_id"])
    missing = [r for r in rows if not r["configured"]]
    return {
        "status": "ok",
        "rows": rows,
        "missing_count": len(missing),
        "missing": [r["community_id"] for r in missing],
    }
