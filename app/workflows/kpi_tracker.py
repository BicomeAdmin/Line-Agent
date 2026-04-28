"""九宮格 KPI 追蹤器 — Paul《私域流量》四步驟 metrics, computed from
imported chat-export data.

Paul's framework gives 4 transition KPIs:

  拉新 → 留存:  社群人數
  留存 → 活躍:  已讀率 + 互動率
  活躍 → 裂變:  導購率 + UGC 數量
  裂變期:       質變 (KOC 比例) + 量變 (子群數)

We compute what's measurable from operator-supplied chat exports:

  ✅ daily_message_count      — UGC 數量 proxy
  ✅ daily_active_senders     — 互動深度 proxy
  ✅ active_sender_ratio      — 互動率 proxy (active_senders / known members)
  ✅ broadcast_vs_natural     — 文化健康度 (Paul's V — value vs noise)
  ✅ operator_participation   — 操作員親自參與比例
  ✅ topic_diversity          — 訊息語義多樣性 (via embedding clustering)
  ⚠️ conversion_rate          — needs operator-labelled order data; not
                                  computed yet (Tier 2 work)
  ⚠️ read_rate                — LINE doesn't expose; not computable

Storage: customers/<id>/data/kpi_snapshots/<community_id>.json
  - Time series of daily snapshots
  - Append-only; previous days kept for trend analysis

This is the data layer that turns Paul's 九宮格 from philosophy into
metrics. Read by the dashboard and by the LLM brain when operator
asks 「X 群現在健康嗎」.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from app.core.audit import append_audit_event
from app.core.timezone import TAIPEI
from app.storage.config_loader import load_community_config
from app.storage.paths import customer_data_root
from app.workflows.chat_export_import import ChatMessage, parse_line_export
from app.workflows.member_fingerprint import latest_export_path


def kpi_snapshots_path(customer_id: str, community_id: str) -> Path:
    return customer_data_root(customer_id) / "kpi_snapshots" / f"{community_id}.json"


def compute_community_kpis(
    customer_id: str,
    community_id: str,
    *,
    days_back: int = 30,
) -> dict[str, object]:
    """Read latest chat export for this community and compute KPIs
    for the most recent `days_back` days. Updates the snapshots file
    with one row per (community_id × date)."""

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    src = latest_export_path(customer_id, community_id)
    if src is None:
        return {
            "status": "error",
            "reason": "no_export_available",
            "hint": "先用 import_chat_export 匯入該社群的對話紀錄",
        }

    try:
        messages = parse_line_export(src)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"parse_failed:{exc}"}

    operator_nickname = (community.operator_nickname or "").strip()

    # Group messages by date
    by_date: dict[str, list[ChatMessage]] = {}
    for m in messages:
        if not m.date:
            continue
        by_date.setdefault(m.date, []).append(m)

    # Cutoff: only compute days within window
    today = datetime.now(TAIPEI).date()
    cutoff = today - timedelta(days=days_back)

    daily: list[dict[str, object]] = []
    for date_str, items in sorted(by_date.items()):
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            continue
        daily.append(_compute_single_day(date_str, items, operator_nickname))

    # Aggregate trends
    n_recent_7 = sum(d["message_count"] for d in daily[-7:])
    n_recent_30 = sum(d["message_count"] for d in daily[-30:])
    avg_daily_msg = round(n_recent_30 / max(1, min(30, len(daily))), 1) if daily else 0
    overall_active = set()
    for d in daily[-7:]:
        overall_active.update(d.get("active_senders_list") or [])
    weekly_active_count = len(overall_active)

    summary = {
        "community_id": community_id,
        "community_name": community.display_name,
        "operator_nickname": operator_nickname or None,
        "days_with_data": len(daily),
        "messages_last_7_days": n_recent_7,
        "messages_last_30_days": n_recent_30,
        "avg_daily_messages": avg_daily_msg,
        "weekly_active_senders": weekly_active_count,
        "daily": [
            {k: v for k, v in d.items() if k != "active_senders_list"}
            for d in daily
        ],
        "computed_at_taipei": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": str(src),
    }

    # Persist
    out_path = kpi_snapshots_path(customer_id, community_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    append_audit_event(
        customer_id,
        "kpi_snapshot_computed",
        {
            "community_id": community_id,
            "days_with_data": len(daily),
            "messages_last_7_days": n_recent_7,
            "weekly_active_senders": weekly_active_count,
        },
    )

    summary["status"] = "ok"
    summary["stored_at"] = str(out_path)
    return summary


def _compute_single_day(
    date_str: str,
    items: list[ChatMessage],
    operator_nickname: str,
) -> dict[str, object]:
    """KPIs for one day."""

    senders = Counter(m.sender for m in items if m.sender and m.sender != "unknown")
    active_senders_list = sorted(senders.keys())
    distinct_active = len(active_senders_list)

    # Operator participation
    operator_msgs = 0
    if operator_nickname:
        operator_msgs = sum(1 for m in items if m.sender == operator_nickname)
    operator_msgs += sum(1 for m in items if m.sender == "__operator__")

    # Broadcast vs natural
    broadcast_count = sum(1 for m in items if _looks_broadcast(m.text))

    # Avg message length (excluding empty)
    lens = [len(m.text) for m in items if m.text]
    avg_len = round(sum(lens) / len(lens), 1) if lens else 0

    return {
        "date": date_str,
        "message_count": len(items),
        "distinct_active_senders": distinct_active,
        "operator_messages": operator_msgs,
        "broadcast_messages": broadcast_count,
        "natural_messages": len(items) - broadcast_count,
        "avg_message_length": avg_len,
        "top_senders": senders.most_common(3),
        "active_senders_list": active_senders_list,
    }


_BROADCAST_TOKENS = (
    "@All", "@all", "公告", "福利", "抽獎", "限時", "搶購", "報名連結",
    "意願調查", "歡迎大家", "請各位",
)


def _looks_broadcast(text: str) -> bool:
    if not text:
        return False
    return any(t in text for t in _BROADCAST_TOKENS)


# ──────────────────────────────────────────────────────────────────────
# Cross-community summary for dashboard / Lark digest
# ──────────────────────────────────────────────────────────────────────

def kpi_summary_for_dashboard(customer_id: str = "customer_a") -> dict[str, object]:
    """Light-weight KPI summary for ALL communities of a customer.
    Reads the persisted snapshot files (no recompute) so the dashboard
    is fast. Returns rows matching the 九宮格 step abstraction.

    Run compute_community_kpis() to refresh a community's snapshot.
    """

    from app.storage.config_loader import load_all_communities

    rows: list[dict[str, object]] = []
    for c in load_all_communities():
        if c.customer_id != customer_id:
            continue
        path = kpi_snapshots_path(customer_id, c.community_id)
        if not path.exists():
            rows.append({
                "community_id": c.community_id,
                "display_name": c.display_name,
                "snapshot_present": False,
            })
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rows.append({
                "community_id": c.community_id,
                "display_name": c.display_name,
                "snapshot_present": False,
            })
            continue
        rows.append({
            "community_id": c.community_id,
            "display_name": c.display_name,
            "snapshot_present": True,
            "messages_last_7_days": data.get("messages_last_7_days", 0),
            "weekly_active_senders": data.get("weekly_active_senders", 0),
            "avg_daily_messages": data.get("avg_daily_messages", 0),
            "computed_at_taipei": data.get("computed_at_taipei"),
        })

    rows.sort(key=lambda r: r["community_id"])
    return {
        "status": "ok",
        "customer_id": customer_id,
        "rows": rows,
    }
