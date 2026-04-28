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


def _community_yaml_path(customer_id: str, community_id: str) -> Path:
    return customer_root(customer_id) / "communities" / f"{community_id}.yaml"


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

    append_audit_event(
        customer_id,
        "operator_nickname_set",
        {
            "community_id": community_id,
            "old_nickname": community.operator_nickname,
            "new_nickname": nickname,
        },
    )
    return {
        "status": "ok",
        "community_id": community_id,
        "operator_nickname": nickname,
        "previous": community.operator_nickname,
        "yaml_path": str(yaml_path),
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
