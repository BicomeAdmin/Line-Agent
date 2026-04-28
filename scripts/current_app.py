from __future__ import annotations

import sys

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError
from app.adb.devices import foreground_package


def main() -> int:
    device_id = sys.argv[1] if len(sys.argv) > 1 else None
    client = AdbClient(device_id=device_id)
    try:
        package = foreground_package(client)
    except AdbError as exc:
        print(f"Failed to inspect foreground app: {exc}")
        return 2

    print(package or "UNKNOWN")
    return 0 if package else 1


if __name__ == "__main__":
    raise SystemExit(main())

