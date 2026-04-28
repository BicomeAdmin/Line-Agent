from __future__ import annotations

import json
import sys

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError
from app.adb.devices import boot_completed, foreground_package, list_packages
from app.adb.line_app import LINE_PACKAGE, check_current_app


def main() -> int:
    device_id = sys.argv[1] if len(sys.argv) > 1 else None
    client = AdbClient(device_id=device_id)

    try:
        packages = list_packages(client)
        payload = {
            "device_id": device_id,
            "boot_completed": boot_completed(client),
            "foreground_package": foreground_package(client),
            "line_installed": LINE_PACKAGE in packages,
            "line_active": check_current_app(client),
        }
    except AdbError as exc:
        print(json.dumps({"device_id": device_id, "status": "error", "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

