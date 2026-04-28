"""Bring Lark online: start the webhook server + an ngrok tunnel, print the public URL.

Usage:
    python3 scripts/start_lark_bridge.py [--port 8787]

What it does:
    1. Verify ngrok is configured (offers help if not).
    2. Spawn `dev_webhook_server.py` on the chosen port.
    3. Spawn `ngrok http <port>` and poll its local API for the public URL.
    4. Print the URL the operator should paste into Lark Developer Console.
    5. Stay in foreground until Ctrl-C; on exit, kill both children.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import _bootstrap  # noqa: F401


NGROK_API = "http://127.0.0.1:4040/api/tunnels"
NGROK_CONFIG = Path.home() / "Library" / "Application Support" / "ngrok" / "ngrok.yml"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-tunnel", action="store_true",
                        help="Only start the webhook server (skip ngrok). Useful when tunnel is already up.")
    args = parser.parse_args()

    if not args.no_tunnel:
        if not NGROK_CONFIG.exists():
            print(_ngrok_setup_help(), file=sys.stderr, flush=True)
            return 2

    project_root = Path(__file__).resolve().parents[1]
    server_proc = subprocess.Popen(
        ["python3", str(project_root / "scripts" / "dev_webhook_server.py"),
         "--host", "127.0.0.1", "--port", str(args.port)],
        cwd=str(project_root),
    )
    children = [server_proc]
    print(f"[bridge] webhook server PID={server_proc.pid} on 127.0.0.1:{args.port}", flush=True)

    tunnel_proc = None
    public_url: str | None = None
    if not args.no_tunnel:
        # Run ngrok with --log=stdout so we don't need to manage a log file; we'll
        # query its local API for the public URL instead of parsing logs.
        tunnel_proc = subprocess.Popen(
            ["ngrok", "http", str(args.port), "--log=stdout"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        children.append(tunnel_proc)
        print(f"[bridge] ngrok PID={tunnel_proc.pid}, waiting for tunnel...", flush=True)

        public_url = _wait_for_tunnel(timeout_seconds=20)
        if public_url is None:
            print("[bridge] ngrok did not report a tunnel within 20s — is your authtoken valid?", file=sys.stderr, flush=True)
            for child in children:
                child.terminate()
            return 3

        events_url = f"{public_url}/webhooks/lark/events"
        actions_url = f"{public_url}/webhooks/lark/actions"
        print("[bridge] tunnel ready", flush=True)
        print("", flush=True)
        print("┌──────────────────────────────────────────────────────────────────────┐", flush=True)
        print("│ Paste these into Lark Developer Console:                            │", flush=True)
        print("│                                                                      │", flush=True)
        print(f"│  Events & Callbacks → Event Configuration → Request URL:            │", flush=True)
        print(f"│    {events_url:<66} │", flush=True)
        print(f"│                                                                      │", flush=True)
        print(f"│  Events & Callbacks → Card Callbacks → Request URL:                 │", flush=True)
        print(f"│    {actions_url:<66} │", flush=True)
        print("└──────────────────────────────────────────────────────────────────────┘", flush=True)
        print("", flush=True)
        print("[bridge] Lark will hit Request URL once with type=url_verification — our handler answers automatically.", flush=True)
        print("[bridge] Ctrl-C to stop both server + tunnel.", flush=True)

    def _shutdown(*_args) -> None:
        print("[bridge] shutting down children...", flush=True)
        for child in children:
            try:
                child.terminate()
            except Exception:  # noqa: BLE001
                pass
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Block until any child dies.
    while True:
        for child in children:
            if child.poll() is not None:
                print(f"[bridge] child PID={child.pid} exited with code {child.returncode}", flush=True)
                _shutdown()
        time.sleep(1)


def _wait_for_tunnel(timeout_seconds: int = 20) -> str | None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(NGROK_API, timeout=2) as response:
                data = json.loads(response.read().decode("utf-8"))
                tunnels = data.get("tunnels") or []
                for t in tunnels:
                    public_url = t.get("public_url")
                    if isinstance(public_url, str) and public_url.startswith("https://"):
                        return public_url
                # ngrok may report only http variant — accept that too as fallback
                for t in tunnels:
                    public_url = t.get("public_url")
                    if isinstance(public_url, str) and public_url:
                        return public_url
        except (URLError, json.JSONDecodeError, OSError):
            pass
        time.sleep(0.5)
    return None


def _ngrok_setup_help() -> str:
    return (
        "[bridge] ngrok needs a one-time authtoken before it can tunnel.\n"
        "[bridge] free account: https://dashboard.ngrok.com/signup\n"
        "[bridge] then run:  ngrok config add-authtoken <YOUR_TOKEN>\n"
        "[bridge] then re-run:  python3 scripts/start_lark_bridge.py\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
