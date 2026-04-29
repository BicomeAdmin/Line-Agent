from __future__ import annotations

import argparse
import json
from datetime import datetime

import _bootstrap  # noqa: F401

from app.workflows.backup_state import run_backup
from zoneinfo import ZoneInfo


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot Project Echo state into a rotating tar.gz archive.")
    parser.add_argument("--keep", type=int, default=14, help="Number of archives to retain (default: 14)")
    parser.add_argument("--customer-id", default="customer_a")
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    args = parser.parse_args()

    result = run_backup(keep=args.keep, customer_id=args.customer_id)

    if args.json:
        print(json.dumps({
            "archive": str(result.archive_path),
            "bytes": result.bytes_written,
            "file_count": result.file_count,
            "rotated": [str(p) for p in result.rotated],
        }, ensure_ascii=False, indent=2))
        return 0

    tpe = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S TPE")
    size_mb = result.bytes_written / 1024 / 1024
    print(f"[{tpe}] backup ok: {result.archive_path.name}")
    print(f"  files: {result.file_count}")
    print(f"  size:  {size_mb:.2f} MiB")
    if result.rotated:
        print(f"  rotated out: {len(result.rotated)} old archive(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
