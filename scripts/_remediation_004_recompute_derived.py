"""Recompute 004 lifecycle / relationship_graph / KPI snapshots after
operator_nickname correction (妍 → 翊). All three filter by
operator_nickname; with the wrong value they treated 翊 as an ordinary
member, poisoning lifecycle stage distribution, KOC candidate ranking,
and KPI engagement counts.

Fingerprints are NOT recomputed — they intentionally include all
senders (operator filter is applied downstream by selector). The
fingerprint file itself was not poisoned, just used in a context
where the operator wasn't excluded."""
from __future__ import annotations

import _bootstrap  # noqa: F401

from app.core.audit import append_audit_event
from app.workflows.kpi_tracker import compute_community_kpis
from app.workflows.lifecycle_tagging import compute_lifecycle_tags
from app.workflows.relationship_graph import build_relationship_graph


def main() -> int:
    customer_id = "customer_a"
    community_id = "openchat_004"

    print("=== lifecycle ===")
    lc = compute_lifecycle_tags(customer_id, community_id)
    print(f"  status={lc.get('status')}  members={len(lc.get('members') or [])}")

    print("=== relationship graph ===")
    rg = build_relationship_graph(customer_id, community_id)
    print(f"  status={rg.get('status')}  nodes={rg.get('node_count')}  koc={[c.get('sender') for c in (rg.get('koc_candidates') or [])][:5]}")

    print("=== kpi ===")
    kpi = compute_community_kpis(customer_id, community_id)
    print(f"  status={kpi.get('status')}  operator_nickname={kpi.get('operator_nickname')}  days={len(kpi.get('daily') or [])}")

    append_audit_event(
        customer_id,
        "incident_remediation_recompute_complete",
        {
            "community_id": community_id,
            "lifecycle_status": lc.get("status"),
            "relationship_graph_status": rg.get("status"),
            "kpi_status": kpi.get("status"),
            "kpi_operator_nickname_now": kpi.get("operator_nickname"),
            "note": "Recomputed after operator_nickname '妍' → '翊' correction. Fingerprints intentionally not recomputed.",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
