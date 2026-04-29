from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from app.workflows.restore_state import RestoreError, run_restore
from zoneinfo import ZoneInfo


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Restore Project Echo state from a tar.gz archive produced by "
            "scripts/backup_state.py. Takes an automatic safety backup of "
            "current state before overwriting (use --no-safety-backup to skip)."
        ),
    )
    parser.add_argument("archive", type=Path, help="Path to echo-state-*.tar.gz archive")
    parser.add_argument("--customer-id", default="customer_a")
    parser.add_argument("--dry-run", action="store_true", help="Validate + list members without extracting")
    parser.add_argument(
        "--no-safety-backup",
        action="store_true",
        help="Skip the automatic pre-restore backup of current live state (NOT recommended)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation (required for non-dry-run when stdin is a TTY)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    args = parser.parse_args()

    if not args.dry_run and not args.yes and sys.stdin.isatty():
        print(f"About to restore state from: {args.archive}")
        print("This will OVERWRITE current .project_echo/, customers/, configs/.")
        print("(A safety backup of current state will be taken first.)")
        reply = input("Type 'restore' to confirm: ").strip()
        if reply != "restore":
            print("aborted.")
            return 1

    try:
        result = run_restore(
            args.archive,
            customer_id=args.customer_id,
            dry_run=args.dry_run,
            safety_backup=not args.no_safety_backup,
        )
    except RestoreError as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "archive": str(result.archive_path),
            "file_count": result.file_count,
            "bytes": result.bytes_restored,
            "safety_backup": str(result.safety_backup) if result.safety_backup else None,
            "dry_run": result.dry_run,
            "members": result.members if result.dry_run else None,
        }, ensure_ascii=False, indent=2))
        return 0

    tpe = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S TPE")
    size_mb = result.bytes_restored / 1024 / 1024
    label = "dry-run" if result.dry_run else "restored"
    print(f"[{tpe}] {label}: {result.archive_path.name}")
    print(f"  files: {result.file_count}")
    print(f"  size:  {size_mb:.2f} MiB")
    if result.safety_backup:
        print(f"  safety backup: {result.safety_backup.name}")
    if result.dry_run:
        print(f"  members (first 10):")
        for name in result.members[:10]:
            print(f"    {name}")
        if len(result.members) > 10:
            print(f"    ... +{len(result.members) - 10} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
