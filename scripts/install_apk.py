from __future__ import annotations

import argparse
from pathlib import Path

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apk_path")
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--no-replace", action="store_true")
    args = parser.parse_args()

    apk_path = Path(args.apk_path).expanduser().resolve()
    if not apk_path.exists():
        print(f"APK not found: {apk_path}")
        return 1

    client = AdbClient(device_id=args.device_id, timeout=120)
    try:
        result = client.install(str(apk_path), replace=not args.no_replace)
    except AdbError as exc:
        print(f"Failed to install APK: {exc}")
        return 2

    print(result.stdout.strip() or "INSTALL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

