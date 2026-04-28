from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from app.adb.client import AdbClient, AdbError
from app.adb.devices import wait_for_boot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id", nargs="?", default=None)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    client = AdbClient(device_id=args.device_id, timeout=max(args.timeout, 20))
    try:
        ready = wait_for_boot(client, timeout_seconds=args.timeout)
    except AdbError as exc:
        print(f"Failed to wait for device: {exc}")
        return 2

    print("BOOT_COMPLETED" if ready else "BOOT_TIMEOUT")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

