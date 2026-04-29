from __future__ import annotations

import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.audit import append_audit_event


PROJECT_ROOT = Path(__file__).resolve().parents[2]

INCLUDE_PATHS = (
    ".project_echo",
    "customers",
    "configs",
)

EXCLUDE_DIR_NAMES = {
    "raw_xml",
    "__pycache__",
    "cleaned_messages",
    "llm_outputs",
    "prompts",
}

EXCLUDE_FILE_NAMES = {
    ".DS_Store",
}


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    bytes_written: int
    file_count: int
    rotated: list[Path]


def _filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
    base = Path(tarinfo.name).name
    if base in EXCLUDE_FILE_NAMES:
        return None
    parts = set(Path(tarinfo.name).parts)
    if parts & EXCLUDE_DIR_NAMES:
        return None
    return tarinfo


def _rotate(backup_dir: Path, keep: int) -> list[Path]:
    archives = sorted(backup_dir.glob("echo-state-*.tar.gz"))
    if len(archives) <= keep:
        return []
    to_remove = archives[: len(archives) - keep]
    for path in to_remove:
        path.unlink()
    return to_remove


def run_backup(
    *,
    project_root: Path = PROJECT_ROOT,
    backup_dir: Path | None = None,
    keep: int = 14,
    customer_id: str = "customer_a",
    now: datetime | None = None,
) -> BackupResult:
    backup_dir = backup_dir or (project_root / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    archive_path = backup_dir / f"echo-state-{timestamp}.tar.gz"

    file_count = 0
    with tarfile.open(archive_path, "w:gz") as tar:
        for rel in INCLUDE_PATHS:
            source = project_root / rel
            if not source.exists():
                continue
            for path in sorted(source.rglob("*")):
                if not path.is_file():
                    continue
                if path.name in EXCLUDE_FILE_NAMES:
                    continue
                if EXCLUDE_DIR_NAMES & set(path.relative_to(project_root).parts):
                    continue
                tar.add(path, arcname=str(path.relative_to(project_root)))
                file_count += 1

    bytes_written = archive_path.stat().st_size
    rotated = _rotate(backup_dir, keep)

    append_audit_event(
        customer_id,
        "state_backup_created",
        {
            "archive": str(archive_path.relative_to(project_root)),
            "bytes": bytes_written,
            "file_count": file_count,
            "rotated": [str(p.relative_to(project_root)) for p in rotated],
            "keep": keep,
        },
    )

    return BackupResult(
        archive_path=archive_path,
        bytes_written=bytes_written,
        file_count=file_count,
        rotated=rotated,
    )
