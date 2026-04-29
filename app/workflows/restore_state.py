"""Restore Project Echo state from a backup archive.

Counterpart to `backup_state.py`. Validates the tarball, takes an automatic
safety backup of the current live state (so the restore itself is reversible),
then extracts the archive over the project root. Orphan files (present in
live state but not in the archive) are preserved — restore overwrites, it
does not wipe.

HIL note: restore touches `.project_echo/`, `customers/`, and `configs/`.
It does NOT touch `.env`, code, or `risk_control.yaml`'s require_human_approval
setting (which is in configs/, so it WILL be replaced — operator should verify
post-restore that the flag is still true).
"""

from __future__ import annotations

import tarfile
from dataclasses import dataclass
from pathlib import Path

from app.core.audit import append_audit_event
from app.workflows.backup_state import (
    INCLUDE_PATHS,
    PROJECT_ROOT,
    run_backup,
)


class RestoreError(RuntimeError):
    """Raised when an archive fails validation or extraction."""


@dataclass(frozen=True)
class RestoreResult:
    archive_path: Path
    file_count: int
    bytes_restored: int
    safety_backup: Path | None
    dry_run: bool
    members: list[str]


def _validate_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Reject path traversal, absolute paths, and members outside INCLUDE_PATHS."""
    safe: list[tarfile.TarInfo] = []
    allowed_roots = set(INCLUDE_PATHS)
    for member in tar.getmembers():
        name = member.name
        if not name or name.startswith("/") or ".." in Path(name).parts:
            raise RestoreError(f"unsafe archive member: {name!r}")
        top = Path(name).parts[0]
        if top not in allowed_roots:
            raise RestoreError(
                f"archive member outside allowed roots {sorted(allowed_roots)}: {name!r}"
            )
        if member.issym() or member.islnk():
            raise RestoreError(f"refusing to extract link member: {name!r}")
        safe.append(member)
    return safe


def run_restore(
    archive_path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    customer_id: str = "customer_a",
    dry_run: bool = False,
    safety_backup: bool = True,
) -> RestoreResult:
    archive_path = Path(archive_path)
    if not archive_path.exists():
        raise RestoreError(f"archive not found: {archive_path}")
    if not archive_path.is_file():
        raise RestoreError(f"archive is not a file: {archive_path}")

    # Phase 1: open + validate (no side effects yet)
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            members = _validate_members(tar)
    except tarfile.TarError as exc:
        raise RestoreError(f"archive unreadable: {exc}") from exc

    member_names = [m.name for m in members]
    file_count = sum(1 for m in members if m.isfile())
    bytes_total = sum(m.size for m in members if m.isfile())

    if dry_run:
        return RestoreResult(
            archive_path=archive_path,
            file_count=file_count,
            bytes_restored=bytes_total,
            safety_backup=None,
            dry_run=True,
            members=member_names,
        )

    # Phase 2: audit start (so we have a record even if extract fails)
    append_audit_event(
        customer_id,
        "state_restore_started",
        {
            "archive": str(archive_path),
            "file_count": file_count,
            "bytes": bytes_total,
            "safety_backup_requested": safety_backup,
        },
    )

    # Phase 3: safety backup of current live state
    safety_path: Path | None = None
    if safety_backup:
        safety_result = run_backup(
            project_root=project_root,
            customer_id=customer_id,
        )
        safety_path = safety_result.archive_path

    # Phase 4: extract
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            # Re-validate inside the same open context (defence in depth: archive
            # could have been swapped between phases on a hostile filesystem).
            for member in _validate_members(tar):
                tar.extract(member, path=project_root)
    except (tarfile.TarError, OSError) as exc:
        append_audit_event(
            customer_id,
            "state_restore_failed",
            {
                "archive": str(archive_path),
                "error": str(exc),
                "safety_backup": str(safety_path) if safety_path else None,
            },
        )
        raise RestoreError(f"extract failed: {exc}") from exc

    append_audit_event(
        customer_id,
        "state_restore_completed",
        {
            "archive": str(archive_path),
            "file_count": file_count,
            "bytes": bytes_total,
            "safety_backup": str(safety_path) if safety_path else None,
        },
    )

    return RestoreResult(
        archive_path=archive_path,
        file_count=file_count,
        bytes_restored=bytes_total,
        safety_backup=safety_path,
        dry_run=False,
        members=member_names,
    )
