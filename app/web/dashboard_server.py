"""Local web dashboard for Project Echo — stdlib-only HTTP.

Browser-friendly monitoring surface: open http://localhost:8080 and
leave a tab open. Page auto-polls every 5s for the structured
snapshot (same data as scripts/dashboard.py CLI) plus a tail of the
most recent audit events so you can see things happening as they
happen.

Read-only by design. The action buttons on review cards still flow
through Lark / CLI — keeping the operator approval surface in one
place per CLAUDE.md §3.1 (HIL invariant). A future v2 can add
approve/ignore endpoints once we're confident about local-network
trust assumptions.

Endpoints:
  GET /                     → single-page HTML (vanilla JS polls below)
  GET /api/snapshot         → structured dashboard data
  GET /api/events?limit=50  → recent audit events (most-recent first)
  GET /api/health           → liveness ping
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.core.audit import read_recent_audit_events
from app.core.timezone import to_taipei_str
from app.workflows.dashboard import collect_dashboard_data


HTML_PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>Project Echo — 監控儀表板</title>
<style>
  :root {
    --bg: #0f1419;
    --panel: #1a1f29;
    --border: #2a3441;
    --text: #e6e6e6;
    --muted: #8a96a8;
    --accent: #4fc3f7;
    --good: #66bb6a;
    --warn: #ffa726;
    --bad: #ef5350;
    --pending: #ffd54f;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", "PingFang TC", sans-serif;
    background: var(--bg);
    color: var(--text);
  }
  header {
    background: var(--panel);
    padding: 12px 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 16px;
  }
  header h1 { margin: 0; font-size: 16px; font-weight: 600; }
  #ts { color: var(--muted); font-size: 12px; }
  #status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--good); }
  #status-dot.stale { background: var(--warn); }
  #status-dot.dead { background: var(--bad); }
  main { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 16px; max-width: 1600px; margin: 0 auto; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 14px 16px; }
  .panel h2 { margin: 0 0 10px; font-size: 13px; font-weight: 600; color: var(--accent); text-transform: uppercase; letter-spacing: 0.5px; }
  .full { grid-column: 1 / -1; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid var(--border); font-size: 12px; }
  th { color: var(--muted); font-weight: 500; text-transform: uppercase; font-size: 11px; }
  tr:last-child td { border-bottom: none; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 500; }
  .badge.good { background: rgba(102, 187, 106, 0.2); color: var(--good); }
  .badge.warn { background: rgba(255, 167, 38, 0.2); color: var(--warn); }
  .badge.bad  { background: rgba(239, 83, 80, 0.2); color: var(--bad); }
  .badge.muted { background: rgba(138, 150, 168, 0.15); color: var(--muted); }
  .metric { display: flex; gap: 24px; margin-top: 4px; }
  .metric > div { flex: 1; }
  .metric .num { font-size: 22px; font-weight: 600; }
  .metric .label { color: var(--muted); font-size: 11px; text-transform: uppercase; }
  pre { margin: 0; font: 12px/1.5 ui-monospace, "SF Mono", Menlo, monospace; color: var(--text); white-space: pre-wrap; word-break: break-word; }
  .events { max-height: 480px; overflow-y: auto; font: 12px/1.4 ui-monospace, "SF Mono", Menlo, monospace; }
  .events .row { padding: 4px 0; border-bottom: 1px dashed var(--border); }
  .events .row:last-child { border-bottom: none; }
  .events .ts { color: var(--muted); }
  .events .et { color: var(--accent); margin: 0 6px; }
  .events .et.send { color: var(--good); }
  .events .et.error, .events .et.failed { color: var(--bad); }
  .events .et.fired, .events .et.compose { color: var(--pending); }
  .empty { color: var(--muted); font-style: italic; }
  footer { text-align: center; padding: 16px; color: var(--muted); font-size: 11px; }
</style>
</head>
<body>
<header>
  <span id="status-dot"></span>
  <h1>📊 Project Echo</h1>
  <span id="ts"></span>
  <span style="margin-left:auto; color: var(--muted); font-size: 12px;">每 5 秒刷新</span>
</header>
<main>
  <section class="panel"><h2>🩺 系統健康</h2><div id="health"></div></section>
  <section class="panel"><h2>📨 24h 送發統計</h2><div id="metrics"></div></section>
  <section class="panel full"><h2>🌐 社群</h2><div id="communities"></div></section>
  <section class="panel"><h2>📥 待審 inbox</h2><div id="inbox"></div></section>
  <section class="panel"><h2>⏰ Active watches</h2><div id="watches"></div></section>
  <section class="panel full"><h2>🛎 最近 auto-fires</h2><div id="auto-fires"></div></section>
  <section class="panel full"><h2>📋 即時事件流（audit log tail）</h2><div id="events" class="events"></div></section>
</main>
<footer>Project Echo dashboard · localhost only · 純讀取，不改狀態</footer>

<script>
const $ = (id) => document.getElementById(id);
const escape = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

let lastSnapshotTs = 0;

async function refresh() {
  try {
    const [snapshot, events] = await Promise.all([
      fetch("/api/snapshot").then(r => r.json()),
      fetch("/api/events?limit=80").then(r => r.json()),
    ]);
    renderSnapshot(snapshot);
    renderEvents(events.events || []);
    $("status-dot").className = "";
    lastSnapshotTs = Date.now();
  } catch (err) {
    $("status-dot").className = "dead";
    console.error("refresh failed", err);
  }
}

function renderSnapshot(d) {
  $("ts").textContent = d.generated_at_taipei || "";

  // Health
  const h = d.health || {};
  const healthRows = Object.entries(h).map(([name, info]) => {
    if (info.running) {
      return `<tr><td>${escape(name)}</td><td><span class="badge good">running</span></td><td>PID ${escape(info.pid)}</td><td>${escape(info.etime || "?")}</td></tr>`;
    }
    return `<tr><td>${escape(name)}</td><td><span class="badge bad">down</span></td><td colspan=2></td></tr>`;
  }).join("");
  $("health").innerHTML = `<table>${healthRows}</table>`;

  // Metrics
  const t = (d.send_metrics_24h || {}).totals || {};
  const sources = t.by_source || {};
  const sourceLine = Object.entries(sources).map(([k,v]) => `${escape(k)}=${v}`).join("&nbsp;&nbsp;");
  $("metrics").innerHTML = `
    <div class="metric">
      <div><div class="num">${t.drafts_created ?? 0}</div><div class="label">drafts</div></div>
      <div><div class="num">${t.sent ?? 0}</div><div class="label">sent</div></div>
      <div><div class="num">${t.ignored ?? 0}</div><div class="label">ignored</div></div>
      <div><div class="num">${t.review_pending ?? 0}</div><div class="label">pending</div></div>
    </div>
    ${sourceLine ? `<div style="margin-top:10px; color:var(--muted); font-size:11px;">by source: ${sourceLine}</div>` : ""}
  `;

  // Communities
  const cs = d.communities || [];
  $("communities").innerHTML = cs.length ? `<table>
    <tr><th>community</th><th>name</th><th>voice profile</th><th>watch</th><th>pending</th></tr>
    ${cs.map(c => {
      const vp = c.voice_profile_harvested
        ? `<span class="badge good">harvested</span> <span class="muted">${c.voice_profile_lines} 行</span>`
        : `<span class="badge muted">stub</span> <span class="muted">${c.voice_profile_lines} 行</span>`;
      const w = c.active_watch ? `<span class="badge warn">⏰ ${c.active_watch.remaining_minutes}m</span>` : "";
      const p = c.pending_reviews ? `<span class="badge warn">${c.pending_reviews}</span>` : "";
      return `<tr><td>${escape(c.community_id)}</td><td>${escape(c.display_name)}</td><td>${vp}</td><td>${w}</td><td>${p}</td></tr>`;
    }).join("")}
  </table>` : `<div class="empty">無社群配置</div>`;

  // Inbox
  const inbox = d.pending_reviews || [];
  $("inbox").innerHTML = inbox.length ? `<table>
    <tr><th>review_id</th><th>社群</th><th>草稿</th><th>等待</th></tr>
    ${inbox.map(p => {
      const age = p.age_hours >= 1 ? `${Math.floor(p.age_hours)}h` : `${Math.floor(p.age_seconds/60)}m`;
      const ageBadge = p.age_hours >= 4 ? "bad" : p.age_hours >= 2 ? "warn" : "muted";
      return `<tr><td><code>${escape(p.review_id)}</code></td><td>${escape(p.community_name)}</td><td>${escape(p.draft_text).slice(0, 50)}</td><td><span class="badge ${ageBadge}">${age}</span></td></tr>`;
    }).join("")}
  </table>` : `<div class="empty">無待審 ✅</div>`;

  // Watches
  const ws = d.active_watches || [];
  $("watches").innerHTML = ws.length ? `<table>
    <tr><th>community</th><th>剩餘</th><th>上次 check</th></tr>
    ${ws.map(w => `<tr><td>${escape(w.community_id)}</td><td>${escape(w.remaining_minutes)}m</td><td>${escape(w.last_check_minutes_ago)}m 前</td></tr>`).join("")}
  </table>` : `<div class="empty">無 active watch</div>`;

  // Auto-fires
  const fs = d.recent_auto_fires || [];
  $("auto-fires").innerHTML = fs.length ? `<table>
    <tr><th>時間</th><th>社群</th><th>codex 摘要</th></tr>
    ${fs.map(f => `<tr><td>${escape(f.fired_at_taipei)}</td><td>${escape(f.community_name)}</td><td>${escape((f.codex_summary || "").slice(0, 100))}</td></tr>`).join("")}
  </table>` : `<div class="empty">最近無 auto-fire</div>`;
}

function renderEvents(events) {
  if (!events.length) {
    $("events").innerHTML = `<div class="empty">無事件</div>`;
    return;
  }
  const html = events.map(e => {
    const cls = (e.event_type || "").toLowerCase().includes("error") ? "error"
      : (e.event_type || "").includes("send") ? "send"
      : (e.event_type || "").includes("fired") || (e.event_type || "").includes("compose") ? "fired"
      : "";
    return `<div class="row"><span class="ts">${escape(e.ts_taipei)}</span><span class="et ${cls}">${escape(e.event_type)}</span><span>${escape(e.summary)}</span></div>`;
  }).join("");
  $("events").innerHTML = html;
}

// Stale-detection: if no successful refresh in 30s, mark dot stale.
setInterval(() => {
  if (Date.now() - lastSnapshotTs > 30000) {
    $("status-dot").className = "stale";
  }
}, 5000);

refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


# ──────────────────────────────────────────────────────────────────────
# HTTP handler
# ──────────────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    """One handler per request — ThreadingHTTPServer spawns threads."""

    customer_id: str = "customer_a"  # set by run_server()

    def log_message(self, format, *args):  # noqa: A002 — suppress noisy default
        pass

    def do_GET(self) -> None:
        try:
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                self._respond(200, "text/html; charset=utf-8", HTML_PAGE.encode("utf-8"))
            elif url.path == "/api/snapshot":
                data = collect_dashboard_data(self.customer_id)
                self._respond_json(data)
            elif url.path == "/api/events":
                qs = parse_qs(url.query)
                try:
                    limit = max(1, min(500, int((qs.get("limit") or ["50"])[0])))
                except ValueError:
                    limit = 50
                events = []
                for raw in (read_recent_audit_events(self.customer_id, limit=limit) or [])[::-1]:
                    events.append({
                        "ts_taipei": to_taipei_str(raw.get("timestamp")),
                        "event_type": raw.get("event_type"),
                        "summary": _summarize(raw),
                    })
                self._respond_json({"events": events})
            elif url.path == "/api/health":
                self._respond_json({"status": "ok", "ts": time.time()})
            else:
                self._respond(404, "text/plain", b"not found")
        except Exception as exc:  # noqa: BLE001 — never crash the server
            try:
                self._respond_json({"error": str(exc)}, status=500)
            except Exception:  # noqa: BLE001
                pass

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _respond_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self._respond(status, "application/json; charset=utf-8", body)


def _summarize(event: dict) -> str:
    payload = event.get("payload") or {}
    et = event.get("event_type") or ""
    cid = payload.get("community_id") or ""
    if et == "send_attempt":
        return f"{cid} status={payload.get('status')} delay={payload.get('delay_seconds')}"
    if et == "mcp_compose_review_created":
        return f"{cid} review={payload.get('review_id')} «{(payload.get('text_preview') or '')[:40]}»"
    if et == "watch_tick_fired":
        return f"{cid} {(payload.get('codex_summary') or '')[:60]}"
    if et == "watch_tick_error":
        return f"{cid} ERROR {(payload.get('error') or '')[:80]}"
    if et == "review_status_changed":
        return f"review={payload.get('review_id')} → {payload.get('status')}"
    if et == "operator_review_card_pushed":
        return f"{cid} review={payload.get('review_id')} → Lark"
    if et == "style_samples_harvested":
        return f"{cid} kept={payload.get('candidates_kept')} wrote={payload.get('samples_written')}"
    if et == "community_title_refreshed":
        return f"{cid} {payload.get('old_display_name')} → {payload.get('new_display_name')}"
    if et == "lark_message_received":
        return "Lark inbound message"
    if et == "lark_reply_sent":
        return "Lark reply sent"
    return cid or "—"


def run_server(host: str = "127.0.0.1", port: int = 8080, customer_id: str = "customer_a") -> None:
    DashboardHandler.customer_id = customer_id
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"[web-dashboard] listening on http://{host}:{port}  (customer={customer_id})", flush=True)
    print(f"[web-dashboard] open in your browser; Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[web-dashboard] stopping", flush=True)
    finally:
        server.server_close()
