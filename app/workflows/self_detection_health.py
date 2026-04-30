"""24h self-detection health check.

After the 翊→妍 incident (operator_nickname mistyped on openchat_004 for
6 months, silently corrupting fingerprints / lifecycle / KPI), we need
a runtime signal that catches "the operator's nickname doesn't match
anyone actually posting in this group". The startup invariant
(operator_identity.audit_all_communities) catches the obvious cases —
this catches the sneaky ones where chat_export hits look fine but
recent activity has 0 self-messages because the nickname diverged.

Logic:
  - For each community with operator_nickname set, look at the most
    recent N messages from chat_export.
  - Count messages attributable to the operator using
    is_operator_sender (handles aliases + role-badge suffixes).
  - Compare against route_mix expectations from voice_profile:
      ip-route   ≥ 0.4  → expect self_ratio > 0.05
      info-route ≥ 0.4  → expect self_ratio > 0.10
      else                → expect self_ratio > 0.02
  - If actual self_ratio is below threshold AND there are enough
    messages to be statistically meaningful (≥ 30), emit an
    `operator_self_detection_low` audit event. alert_aggregator
    surfaces those as `important` severity.

Design notes:
  - Read-only. Does not mutate any state.
  - 0 messages → SKIP (no signal, not an alarm).
  - Low message volume (< 30) → SKIP (premature to alarm on noise).
  - Always writes an `operator_self_detection_check` summary event
    so dashboards have positive proof the check ran today.
"""

from __future__ import annotations

from pathlib import Path

from app.core.audit import append_audit_event
from app.storage.config_loader import load_all_communities
from app.workflows.member_fingerprint import latest_export_path
from app.workflows.operator_attribution import (
    is_operator_sender,
    operator_names_for_community,
)


_RECENT_WINDOW = 200          # last 200 messages from chat_export
_MIN_MESSAGES_FOR_SIGNAL = 30  # below this we don't have enough to alarm
_DEFAULT_THRESHOLD = 0.02
_IP_ROUTE_THRESHOLD = 0.05
_INFO_ROUTE_THRESHOLD = 0.10


def _load_route_mix(customer_id: str, community_id: str) -> dict[str, float]:
    """Best-effort read of voice_profile route_mix frontmatter.
    Missing / malformed → empty dict (caller falls back to default
    threshold)."""

    from app.storage.paths import customer_root

    vp_path = customer_root(customer_id) / "voice_profiles" / f"{community_id}.md"
    if not vp_path.exists():
        return {}
    try:
        text = vp_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    try:
        # frontmatter ends at second `---`
        end = text.find("---", 3)
        if end < 0:
            return {}
        block = text[3:end]
        # very small YAML subset — we only need numeric route_mix.*
        out: dict[str, float] = {}
        in_route_mix = False
        for raw in block.splitlines():
            line = raw.rstrip()
            if line.startswith("route_mix:"):
                in_route_mix = True
                continue
            if in_route_mix:
                if not line.startswith(" ") and line.strip():
                    in_route_mix = False
                    continue
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if ":" not in stripped:
                    continue
                key, _, val = stripped.partition(":")
                val = val.split("#", 1)[0].strip()
                try:
                    out[key.strip()] = float(val)
                except ValueError:
                    continue
        return out
    except Exception:  # noqa: BLE001 — degrade to empty dict
        return {}


def _expected_threshold(route_mix: dict[str, float]) -> tuple[float, str]:
    if route_mix.get("ip", 0.0) >= 0.4:
        return _IP_ROUTE_THRESHOLD, "ip"
    if route_mix.get("info", 0.0) >= 0.4:
        return _INFO_ROUTE_THRESHOLD, "info"
    return _DEFAULT_THRESHOLD, "default"


def _load_recent_messages(export_path: Path, n: int) -> list[tuple[str, str]]:
    """Parse the tail of a LINE chat_export. Returns list of (sender, text).
    Cheap line-based parsing — we only need sender attribution."""

    try:
        lines = export_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    parsed: list[tuple[str, str]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # LINE export rows look like "HH:MM <sender> <text>"
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        ts, sender, text = parts
        # crude validation: timestamp should look like H:MM or HH:MM
        if not ts[:1].isdigit() or ":" not in ts:
            continue
        if not sender or sender.startswith("20"):  # date marker, not a row
            continue
        parsed.append((sender, text))
    return parsed[-n:]


def check_community(customer_id: str, community_id: str) -> dict[str, object]:
    """Run the health check for one community. Returns a result dict;
    caller is responsible for emitting audit events."""

    from app.storage.config_loader import load_community_config

    try:
        community = load_community_config(customer_id, community_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": f"community_lookup_failed:{exc}"}

    nickname = (community.operator_nickname or "").strip()
    if not nickname:
        return {"status": "skip", "reason": "no_nickname"}

    src = latest_export_path(customer_id, community_id)
    if src is None:
        return {"status": "skip", "reason": "no_export"}

    msgs = _load_recent_messages(src, _RECENT_WINDOW)
    total = len(msgs)
    if total < _MIN_MESSAGES_FOR_SIGNAL:
        return {
            "status": "skip",
            "reason": "below_min_volume",
            "total_messages": total,
            "min_required": _MIN_MESSAGES_FOR_SIGNAL,
        }

    operator_names = operator_names_for_community(community)
    self_count = sum(1 for sender, _ in msgs if is_operator_sender(sender, operator_names))
    self_ratio = self_count / total if total else 0.0

    route_mix = _load_route_mix(customer_id, community_id)
    threshold, route_label = _expected_threshold(route_mix)

    healthy = self_ratio >= threshold
    return {
        "status": "ok",
        "community_id": community_id,
        "operator_nickname": nickname,
        "total_messages": total,
        "self_messages": self_count,
        "self_ratio": round(self_ratio, 4),
        "expected_threshold": threshold,
        "route_label": route_label,
        "healthy": healthy,
    }


def run_health_check(customer_id: str = "customer_a") -> dict[str, object]:
    """Iterate all communities for a customer; emit an audit event per
    community that fails health check, plus one summary event so we
    have positive proof the check ran."""

    rows: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    for c in load_all_communities():
        if c.customer_id != customer_id:
            continue
        result = check_community(customer_id, c.community_id)
        rows.append(result)
        if result.get("status") == "ok" and not result.get("healthy"):
            failed.append(result)
            append_audit_event(
                customer_id,
                "operator_self_detection_low",
                {
                    "community_id": c.community_id,
                    "operator_nickname": result.get("operator_nickname"),
                    "self_ratio": result.get("self_ratio"),
                    "expected_threshold": result.get("expected_threshold"),
                    "route_label": result.get("route_label"),
                    "total_messages": result.get("total_messages"),
                    "self_messages": result.get("self_messages"),
                    "hint": (
                        "操作員在這個群最近發言比例偏低，可能 operator_nickname "
                        "與 LINE 顯示名稱不一致；建議到 LINE UI 個人檔案頁確認"
                    ),
                },
            )

    append_audit_event(
        customer_id,
        "operator_self_detection_check",
        {
            "checked_count": len(rows),
            "failed_count": len(failed),
            "failed_communities": [r.get("community_id") for r in failed],
        },
    )

    return {
        "status": "ok",
        "rows": rows,
        "failed": failed,
        "checked_count": len(rows),
        "failed_count": len(failed),
    }
