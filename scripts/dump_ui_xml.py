from __future__ import annotations

import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError
from app.adb.uiautomator import dump_ui_xml


def main() -> int:
    device_id = sys.argv[1] if len(sys.argv) > 1 else None
    output = Path("customers/customer_a/data/raw_xml/latest.xml")
    try:
        path = dump_ui_xml(AdbClient(device_id=device_id), output)
    except AdbError as exc:
        print(f"Failed to dump UI XML: {exc}")
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
