from __future__ import annotations

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError


def main() -> int:
    client = AdbClient()
    try:
        devices = client.devices()
    except AdbError as exc:
        print(f"ADB unavailable: {exc}")
        return 2

    if not devices:
        print("ADB is installed, but no connected emulator/device was found.")
        return 1

    print("Connected devices:")
    for device in devices:
        print(f"- {device}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
