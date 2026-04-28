"""Member relationship graph — surface KOC candidates per Paul's
用戶營運金字塔 from imported chat exports.

Algorithm (inspired by github.com/asherkin/discograph):

  1. For each consecutive pair of messages (i, i+1) within a 5-minute
     window, treat the pair as a "reply hint" — sender_b is responding
     to sender_a. This is a noisy signal but surprisingly effective:
     real conversations cluster temporally.
  2. Build a directed graph: edge A → B with weight = count of
     responses B gave to A.
  3. Compute centrality metrics:
     - degree_in: people responded to (popularity)
     - degree_out: people who respond (engagement)
     - betweenness: people who BRIDGE conversations (KOC signal —
       these are the connectors who turn 1:1 chats into community)
     - eigenvector: high-quality reciprocal links (are you connected
       to other connected people?)
  4. KOC candidate = high (in_degree + betweenness + eigenvector) AND
     not the operator themselves AND not a system/auto sender.

Paul's 用戶營運金字塔 (CLAUDE.md §0.5.3):
  品牌 KOC ← what we want to identify
  核心用戶
  付費用戶
  機會用戶
  公域社群用戶
  泛用戶 (everyone else)

This workflow surfaces the top of the pyramid so the operator knows
who to invest relationship in — Paul's "1000 鐵粉" doctrine.

Storage: customers/<id>/data/relationship_graphs/<community_id>.json
  - Time series of graph snapshots (overwrites each refresh; history
    not needed for v1)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app.core.audit import append_audit_event
from app.core.timezone import TAIPEI
from app.storage.config_loader import load_community_config
from app.storage.paths import customer_data_root
from app.workflows.chat_export_import import ChatMessage, parse_line_export
from app.workflows.member_fingerprint import latest_export_path


REPLY_WINDOW_MINUTES = 5
SYSTEM_SENDERS = {"unknown", "Auto-reply", "auto-reply"}

# LINE system-event patterns embedded in sender names. These slip
# through the chat-export parser when the time-line "X 加入聊天" is
# treated as a one-token sender. Filter at graph layer.
import re as _re
_SYSTEM_PATTERNS = _re.compile(
    r"(加入聊天|離開聊天|已收回訊息|已被刪除|被踢出|將.*踢出|改名|更改了)$"
)


def _is_system_sender(sender: str) -> bool:
    if not sender or sender in SYSTEM_SENDERS:
        return True
    if _SYSTEM_PATTERNS.search(sender):
        return True
    return False


def graph_snapshot_path(customer_id: str, community_id: str) -> Path:
    return customer_data_root(customer_id) / "relationship_graphs" / f"{community_id}.json"


def build_relationship_graph(
    customer_id: str,
    community_id: str,
) -> dict[str, object]:
    """Build the directed reply graph + compute KOC candidates."""

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    src = latest_export_path(customer_id, community_id)
    if src is None:
        return {"status": "error", "reason": "no_export_available"}

    try:
        messages = parse_line_export(src)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"parse_failed:{exc}"}

    # NetworkX already installed via sentence-transformers' deps.
    try:
        import networkx as nx
    except ImportError:
        return {"status": "error", "reason": "networkx_not_installed"}

    operator_nick = (community.operator_nickname or "").strip()

    g = nx.DiGraph()

    # Build edges from temporal proximity. Track most recent message
    # per second-resolution sequence; pair consecutive messages from
    # different senders within the reply window.
    last_msg: ChatMessage | None = None
    last_dt: datetime | None = None
    for m in messages:
        if _is_system_sender(m.sender):
            last_msg = m
            last_dt = _parse_dt(m)
            continue
        cur_dt = _parse_dt(m)
        if (
            last_msg is not None
            and last_dt is not None
            and cur_dt is not None
            and not _is_system_sender(last_msg.sender)
            and last_msg.sender != m.sender
            and (cur_dt - last_dt) <= timedelta(minutes=REPLY_WINDOW_MINUTES)
        ):
            # m.sender replied (in temporal sense) to last_msg.sender
            if g.has_edge(m.sender, last_msg.sender):
                g[m.sender][last_msg.sender]["weight"] += 1
            else:
                g.add_edge(m.sender, last_msg.sender, weight=1)
        last_msg = m
        last_dt = cur_dt

    if g.number_of_nodes() == 0:
        return {
            "status": "ok",
            "community_id": community_id,
            "node_count": 0,
            "edge_count": 0,
            "koc_candidates": [],
            "message": "no temporal-reply edges detected",
        }

    # Centrality metrics. Wrap each in try/except — eigenvector can
    # fail to converge on disconnected components; we degrade gracefully.
    try:
        in_deg = dict(g.in_degree(weight="weight"))
    except Exception:  # noqa: BLE001
        in_deg = {}
    try:
        out_deg = dict(g.out_degree(weight="weight"))
    except Exception:  # noqa: BLE001
        out_deg = {}
    try:
        betweenness = nx.betweenness_centrality(g.to_undirected(), weight="weight")
    except Exception:  # noqa: BLE001
        betweenness = {}
    try:
        eigen = nx.eigenvector_centrality_numpy(g, weight="weight")
    except Exception:  # noqa: BLE001
        try:
            eigen = nx.eigenvector_centrality(g, weight="weight", max_iter=200)
        except Exception:  # noqa: BLE001
            eigen = {}

    # Score each node — KOC ranking. Weights chosen so betweenness and
    # in-degree both matter; out-degree is a secondary engagement signal.
    nodes_scored: list[dict[str, object]] = []
    for node in g.nodes():
        if node == operator_nick or node == "__operator__":
            continue
        if _is_system_sender(node):
            continue
        score = (
            0.40 * _normalized(in_deg.get(node, 0), in_deg)
            + 0.30 * _normalized(betweenness.get(node, 0), betweenness)
            + 0.20 * _normalized(eigen.get(node, 0), eigen)
            + 0.10 * _normalized(out_deg.get(node, 0), out_deg)
        )
        nodes_scored.append({
            "sender": node,
            "score": round(score, 3),
            "in_degree": in_deg.get(node, 0),
            "out_degree": out_deg.get(node, 0),
            "betweenness": round(betweenness.get(node, 0), 3),
            "eigenvector": round(eigen.get(node, 0), 3),
        })

    nodes_scored.sort(key=lambda n: n["score"], reverse=True)
    koc_candidates = nodes_scored[:10]

    # Persist
    out_path = graph_snapshot_path(customer_id, community_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "community_id": community_id,
        "community_name": community.display_name,
        "computed_at_taipei": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": str(src),
        "node_count": g.number_of_nodes(),
        "edge_count": g.number_of_edges(),
        "koc_candidates": koc_candidates,
        "all_nodes_scored": nodes_scored,
    }
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    append_audit_event(
        customer_id,
        "relationship_graph_built",
        {
            "community_id": community_id,
            "node_count": g.number_of_nodes(),
            "edge_count": g.number_of_edges(),
            "top_koc": [c["sender"] for c in koc_candidates[:3]],
        },
    )

    snapshot["status"] = "ok"
    snapshot["stored_at"] = str(out_path)
    return snapshot


def _parse_dt(m: ChatMessage) -> datetime | None:
    """Reassemble date+time from ChatMessage. Returns None for malformed."""

    if not m.date or not m.time:
        return None
    try:
        return datetime.fromisoformat(f"{m.date}T{m.time}:00")
    except ValueError:
        return None


def _normalized(value: float, dict_or_dict: dict | None) -> float:
    """Min-max normalize value to [0, 1] using the dict's value range.
    If the range collapses (all values equal), return 0.5 to avoid
    div-by-zero artifacts."""

    if not dict_or_dict:
        return 0.0
    vals = list(dict_or_dict.values())
    if not vals:
        return 0.0
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return 0.5
    return (value - lo) / (hi - lo)


def load_relationship_graph(
    customer_id: str,
    community_id: str,
) -> dict[str, object] | None:
    """Read cached graph snapshot, or None if not yet built."""

    path = graph_snapshot_path(customer_id, community_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
