from __future__ import annotations

import argparse
import _bootstrap  # noqa: F401

from app.adb.emulator import EmulatorError, list_avds, start_avd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--avd", default="project-echo-api35")
    parser.add_argument("--no-snapshot", action="store_true")
    args = parser.parse_args()

    try:
        avds = list_avds()
    except EmulatorError as exc:
        print(f"Failed to prepare emulator: {exc}")
        return 2

    if args.avd not in avds:
        print(f"AVD not found: {args.avd}")
        return 1

    start_avd(args.avd, no_snapshot=args.no_snapshot)
    print(f"STARTED {args.avd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
