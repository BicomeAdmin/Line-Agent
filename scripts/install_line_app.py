from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from app.workflows.line_install import install_line_app


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("--apk-path", default=None)
    parser.add_argument("--no-replace", action="store_true")
    args = parser.parse_args()
    result = install_line_app(args.device_id, apk_path=args.apk_path, replace=not args.no_replace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
