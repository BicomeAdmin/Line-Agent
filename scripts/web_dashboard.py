"""Local web dashboard launcher.

Usage:
    python3 scripts/web_dashboard.py              # http://127.0.0.1:8080
    python3 scripts/web_dashboard.py --port 9090
    python3 scripts/web_dashboard.py --customer-id customer_b

Bind defaults to localhost (127.0.0.1) — never 0.0.0.0 by default since
the dashboard exposes everything (audit events, draft contents) and there's
no auth. Pass --host 0.0.0.0 explicitly if you want LAN access; you've
been warned.
"""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from app.web.dashboard_server import run_server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1; loopback only).")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--customer-id", default="customer_a")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port, customer_id=args.customer_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
