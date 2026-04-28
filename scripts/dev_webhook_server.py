"""Lightweight webhook server for Lark callbacks (no FastAPI / uvicorn dep).

Endpoints:
    GET  /health                    — liveness
    POST /webhooks/lark/events      — Lark event subscriptions (url_verification + message events)
    POST /webhooks/lark/actions     — Lark interactive-card button callbacks

For each non-verification event, we dispatch through `enqueue_lark_event` /
`enqueue_lark_action` so the in-process job worker picks it up. The result
flows back to Lark via the existing `_notify_lark` path in job_processor.

Run:  python3 scripts/dev_webhook_server.py --port 8787
Then:  expose via `ngrok http 8787` (or cloudflared) and paste the public URL
       into Lark Developer Console → Events & Callbacks → Event Configuration
       → Request URL.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

import _bootstrap  # noqa: F401

from app.lark.events import enqueue_lark_action, enqueue_lark_event
from app.workflows.job_runner import ensure_job_worker


class EchoWebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return
        self._send_json({"status": "not_found"}, status=404)

    def do_POST(self) -> None:
        if self.path == "/webhooks/lark/events":
            self._handle_event()
            return
        if self.path == "/webhooks/lark/actions":
            self._handle_action()
            return
        self._send_json({"status": "not_found"}, status=404)

    def _handle_event(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        try:
            response = enqueue_lark_event(payload)
        except Exception as exc:  # noqa: BLE001 — never blow up the Lark callback
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
            self._send_json({"status": "error", "reason": "internal", "detail": str(exc)}, status=500)
            return
        self._send_json(response)

    def _handle_action(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        try:
            response = enqueue_lark_action(payload)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
            self._send_json({"status": "error", "reason": "internal", "detail": str(exc)}, status=500)
            return
        self._send_json(response)

    def _read_json(self) -> dict[str, object] | None:
        length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json({"status": "error", "reason": "invalid_json"}, status=400)
            return None
        if not isinstance(payload, dict):
            self._send_json({"status": "error", "reason": "payload_not_object"}, status=400)
            return None
        return payload

    def log_message(self, format: str, *args: object) -> None:
        # Less noisy than default — print one line per request.
        print(f"[webhook] {self.address_string()} {format % args}", flush=True)

    def _send_json(self, payload: dict[str, object], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    # Start in-process job worker so commands the webhook enqueues actually run.
    ensure_job_worker()

    server = HTTPServer((args.host, args.port), EchoWebhookHandler)
    print(
        f"[webhook] Project Echo dev webhook server listening on http://{args.host}:{args.port}",
        flush=True,
    )
    print(
        "[webhook] endpoints: GET /health  POST /webhooks/lark/events  POST /webhooks/lark/actions",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[webhook] shutting down", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
