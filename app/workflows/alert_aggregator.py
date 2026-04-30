"""Aggregate audit-log signals into operator-facing "today's actions" alerts.

The dashboard's previous data model showed every audit event in a
flat stream — useful for debugging, useless for "what should I do
right now?". After 8 rounds of red-team hardening we have ~20
distinct audit event types each carrying a different actionable
implication. This module rolls them up into three severity buckets:

  - **blocking**: operator must act NOW. Examples: HIL disabled,
    send didn't actually land in chat, review aging past threshold.
  - **important**: operator should investigate. Examples: send
    blocked by safety lint, chat title mismatch, bot-pattern block.
  - **info**: auto-handled or low-stakes signal. Examples: drift
    warnings, bot-pattern warn, post-send verification errors.

Each alert carries a `community_id` (when applicable), a 1-line
`detail`, and an `action_hint` so the dashboard renders not just
what happened but what to do next.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Iterable

from app.config import settings
from app.core.audit import read_recent_audit_events
from app.core.reviews import review_store
from app.core.timezone import to_taipei_str


_DEFAULT_LOOKBACK_HOURS = 24
_REVIEW_AGING_BLOCKING_HOURS = 4.0
_REVIEW_AGING_IMPORTANT_HOURS = 1.0


@dataclass(frozen=True)
class Alert:
    severity: str        # "blocking" | "important" | "info"
    category: str        # "send" | "compose" | "review" | "system" | "community"
    title: str
    detail: str
    community_id: str | None
    action_hint: str | None
    audit_event_type: str
    audit_ts_taipei: str | None
    audit_event_count: int   # rolled-up count if multiple recent

    def to_dict(self) -> dict:
        return asdict(self)


# Severity ordering for sort.
_SEVERITY_RANK = {"blocking": 0, "important": 1, "info": 2}

# Audit event_type → (severity, category, title, action_hint).
# `None` action_hint means "no action — informational signal only".
_EVENT_RULES: dict[str, tuple[str, str, str, str | None]] = {
    # Send-side blockers (operator probably needs to know NOW)
    "send_verification_failed": (
        "blocking", "send", "訊息可能沒進群",
        "查 audit 確認原文是否真送出；可能要手動重送",
    ),
    "send_safety_blocked": (
        "blocking", "send", "草稿被擋（含 URL/電話/email/金流）",
        "看 verdict 哪條觸發，編輯後重審",
    ),
    "approve_send_chat_title_mismatch": (
        "blocking", "send", "送出時 LINE 在錯的群",
        "確認 LINE 已切回正確社群；avoid send 至錯誤群",
    ),
    "approve_send_navigate_blocked": (
        "important", "send", "送出前 navigate 失敗",
        "檢查 device + LINE app 狀態",
    ),
    # Watcher / compose-time blockers
    "watch_tick_chat_title_mismatch": (
        "important", "compose", "watcher 讀到錯的群",
        "確認 device 上 LINE 是否被切走",
    ),
    "watch_tick_blocked_bot_pattern": (
        "important", "compose", "Bot 累計指紋過高（>=10/day）",
        "今天暫停該社群自動擬稿；想發改手動",
    ),
    "scheduled_post_compose_dropped_after_cancel": (
        "info", "compose", "排程稿被取消（compose 期間）",
        "預期行為，無需處理",
    ),
    # Belt-and-suspenders catches (system did the right thing)
    "composer_temporal_override": (
        "info", "compose", "Server 擋下 LLM 失神的稿（>3h 過時）",
        "看 audit 知道 LLM 在哪個 case 上漏看時態",
    ),
    "approve_send_aborted_temporal_drift": (
        "info", "send", "Drift guard 擋下：review 老 + 群已變",
        "操作員可手動 ignore 或重新編輯後再批",
    ),
    "approve_send_off_limits_drift": (
        "info", "send", "voice_profile.off_limits 自 compose 後改過",
        "建議檢查草稿是否仍符合新規則",
    ),
    "send_safety_warned": (
        "info", "send", "草稿含可疑 pattern（已放行）",
        "看 verdict 是否誤判；連續多次該調 lint 規則",
    ),
    "watch_tick_bot_pattern_warning": (
        "info", "compose", "Bot 累計指紋接近上限（>=5/day）",
        "今天該社群草稿已偏多，注意節奏",
    ),
    # System-level
    "send_verification_error": (
        "info", "system", "送出後驗證步驟異常",
        "檢查 ADB / 裝置狀態",
    ),
    "approve_send_drift_read_failed": (
        "info", "system", "Drift guard 讀群失敗",
        "通常是裝置狀態問題；不影響送出",
    ),
    "scheduled_post_temperature_read_failed": (
        "info", "system", "排程稿讀群冷度失敗",
        "通常是裝置狀態問題；prompt fallback 到「未知」",
    ),
    "composer_codex_unavailable": (
        "important", "compose", "Codex CLI 失效",
        "檢查 codex 訂閱 + PATH",
    ),
    "composer_lint_rejected": (
        "info", "compose", "草稿 lint 分數過低被擋",
        "通常是 LLM 異常；連續多次該調 prompt",
    ),
    # Long-term state
    "orphan_recovery_post_reset_to_scheduled": (
        "info", "system", "啟動時回收孤兒 post",
        "預期行為：daemon 上次 crash 後留下的中間狀態",
    ),
    "orphan_recovery_post_reviewing_marked_skipped": (
        "info", "system", "啟動時清理 reviewing 孤兒",
        "預期行為：之前 review 沒寫成功",
    ),
    "orphan_recovery_stale_pending_review": (
        "important", "review", "Pending review 超過 24h 未處理",
        "確認 Lark 推送是否失敗 / 操作員是否漏看",
    ),
    # Operator-initiated config changes — informational so the
    # operator sees their edit was picked up by the daemon.
    "voice_profile_changed": (
        "info", "system", "voice_profile.md 已更新",
        "新草稿會以更新後的規則 compose；舊 pending review 會在 approve 時觸發 drift 警告",
    ),
    # Self-detection health (post-2026-04-30 翊→妍 incident defense layer)
    "operator_self_detection_low": (
        "important", "community", "操作員自我訊號偏低（疑似 nickname 不一致）",
        "到 LINE UI 個人檔案頁確認 operator_nickname 與顯示名稱一致；fan/broadcast 群正常會偏低",
    ),
}


def collect_alerts(
    customer_id: str = "customer_a",
    *,
    lookback_hours: float = _DEFAULT_LOOKBACK_HOURS,
    now: float | None = None,
    audit_events: list[dict] | None = None,
) -> list[Alert]:
    """Gather actionable alerts from audit log + system invariants.

    Returns a list sorted by severity then recency. Empty list when
    everything is healthy.
    """

    current = now if now is not None else time.time()
    cutoff = current - lookback_hours * 3600
    alerts: list[Alert] = []

    # ── 1. System-level invariants (don't depend on audit log) ──────
    if not settings.require_human_approval:
        alerts.append(Alert(
            severity="blocking",
            category="system",
            title="⚠️ HIL 已關閉",
            detail="ECHO_REQUIRE_HUMAN_APPROVAL=false：pre-approved 排程稿會自動送出",
            community_id=None,
            action_hint="若非故意，立即 unset env / 設 true 後重啟 daemon",
            audit_event_type="hil_disabled",
            audit_ts_taipei=None,
            audit_event_count=1,
        ))

    # ── 2. Pending review aging ─────────────────────────────────────
    pending = [
        r for r in review_store.list_all()
        if r.customer_id == customer_id and r.status == "pending"
    ]
    for r in pending:
        age_hours = (current - r.created_at) / 3600
        if age_hours >= _REVIEW_AGING_BLOCKING_HOURS:
            alerts.append(Alert(
                severity="blocking",
                category="review",
                title=f"待審稿 {age_hours:.1f}h 沒處理",
                detail=f"{r.community_name}：{(r.draft_text or '')[:40]}…",
                community_id=r.community_id,
                action_hint="去 Lark 或 CLI approve / edit / ignore",
                audit_event_type="review_aging_blocking",
                audit_ts_taipei=to_taipei_str(
                    datetime.fromtimestamp(r.created_at).astimezone()
                ),
                audit_event_count=1,
            ))
        elif age_hours >= _REVIEW_AGING_IMPORTANT_HOURS:
            alerts.append(Alert(
                severity="important",
                category="review",
                title=f"待審稿 {age_hours:.1f}h",
                detail=f"{r.community_name}：{(r.draft_text or '')[:40]}…",
                community_id=r.community_id,
                action_hint="抽空審一下",
                audit_event_type="review_aging_important",
                audit_ts_taipei=to_taipei_str(
                    datetime.fromtimestamp(r.created_at).astimezone()
                ),
                audit_event_count=1,
            ))

    # ── 3. Audit log roll-up ────────────────────────────────────────
    events = audit_events
    if events is None:
        events = read_recent_audit_events(customer_id, limit=500) or []

    # Group same event_type + community_id within window. We only emit
    # one alert per (type, community) pair, with `count` rolled up.
    grouped: dict[tuple[str, str | None], dict] = {}
    for ev in events:
        et = ev.get("event_type")
        if et not in _EVENT_RULES:
            continue
        ts = _event_epoch(ev)
        if ts is None or ts < cutoff:
            continue
        payload = ev.get("payload") or {}
        community_id = payload.get("community_id")
        if community_id is not None:
            community_id = str(community_id)
        key = (et, community_id)
        bucket = grouped.setdefault(key, {
            "first_ts": ts, "last_ts": ts, "count": 0, "last_payload": payload,
        })
        bucket["count"] += 1
        if ts > bucket["last_ts"]:
            bucket["last_ts"] = ts
            bucket["last_payload"] = payload
        if ts < bucket["first_ts"]:
            bucket["first_ts"] = ts

    for (et, community_id), bucket in grouped.items():
        severity, category, title, action_hint = _EVENT_RULES[et]
        detail = _build_detail(et, bucket["last_payload"], bucket["count"])
        alerts.append(Alert(
            severity=severity,
            category=category,
            title=title,
            detail=detail,
            community_id=community_id,
            action_hint=action_hint,
            audit_event_type=et,
            audit_ts_taipei=to_taipei_str(
                datetime.fromtimestamp(bucket["last_ts"]).astimezone()
            ),
            audit_event_count=bucket["count"],
        ))

    alerts.sort(key=lambda a: (
        _SEVERITY_RANK.get(a.severity, 99),
        -(a.audit_event_count or 0),
    ))
    return alerts


def _build_detail(event_type: str, payload: dict, count: int) -> str:
    """Compose a 1-line detail string from the most recent payload of a group."""

    bits: list[str] = []
    if count > 1:
        bits.append(f"24h 內 {count} 次")
    if event_type == "send_verification_failed":
        v = payload.get("verdict") or {}
        reason = v.get("reason") if isinstance(v, dict) else None
        if reason:
            bits.append(f"reason={reason}")
    elif event_type == "send_safety_blocked":
        v = payload.get("verdict") or {}
        issues = v.get("issues") if isinstance(v, dict) else None
        if isinstance(issues, list) and issues:
            codes = ",".join(str(i.get("code")) for i in issues if isinstance(i, dict))
            bits.append(f"類型={codes}")
    elif event_type in ("watch_tick_chat_title_mismatch", "approve_send_chat_title_mismatch"):
        cur = payload.get("current_title")
        exp = payload.get("expected")
        if cur and exp:
            bits.append(f"預期={exp} / 實際={cur}")
    elif event_type == "composer_temporal_override":
        m = payload.get("stale_minutes")
        if m:
            bits.append(f"target {m} 分鐘前")
    elif event_type in ("watch_tick_blocked_bot_pattern", "watch_tick_bot_pattern_warning"):
        v = payload.get("verdict") or {}
        cnt = v.get("daily_draft_count") if isinstance(v, dict) else None
        if cnt is not None:
            bits.append(f"24h 已 {cnt} 篇 AI 稿")
    elif event_type == "approve_send_aborted_temporal_drift":
        m = payload.get("review_age_minutes")
        r = payload.get("reason")
        if m and r:
            bits.append(f"review {m} 分鐘老 / {r}")
    elif event_type == "approve_send_off_limits_drift":
        bits.append("voice_profile.off_limits 已改變")
    elif event_type == "orphan_recovery_stale_pending_review":
        h = payload.get("age_hours")
        if h:
            bits.append(f"{h} 小時未處理")
    elif event_type == "voice_profile_changed":
        if payload.get("off_limits_hash_changed"):
            bits.append("off-limits 規則有變動")
        else:
            bits.append("voice_profile 內容已更新")
    if not bits:
        # Fall back to "1 次" so the dashboard cell isn't blank.
        bits.append(f"{count} 次" if count > 1 else "1 次")
    return "；".join(bits)


def _event_epoch(event: dict) -> float | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def alerts_summary(alerts: Iterable[Alert]) -> dict:
    """Aggregate counters for the dashboard header bar."""

    counts = Counter(a.severity for a in alerts)
    return {
        "blocking": counts.get("blocking", 0),
        "important": counts.get("important", 0),
        "info": counts.get("info", 0),
        "total": sum(counts.values()),
    }
