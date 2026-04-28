from __future__ import annotations

from pathlib import Path

from app.adb.client import AdbClient, AdbError
from app.config import settings
from app.core.audit import append_audit_event
from app.storage.config_loader import get_device_config
from app.workflows.device_recovery import ensure_device_ready
from app.workflows.device_status import get_device_status


def install_line_app(device_id: str, apk_path: str | None = None, replace: bool = True) -> dict[str, object]:
    device = get_device_config(device_id)
    customer_id = device.customer_id

    recovery = ensure_device_ready(device_id, wait_timeout_seconds=60)
    if recovery.get("status") != "ready":
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "device_not_ready",
            "recovery": recovery,
        }
        append_audit_event(customer_id, "line_install_blocked", result)
        return result

    inspection = inspect_line_apk_sources(apk_path)
    resolved_selected = inspection.get("selected_path")
    resolved_apk = Path(resolved_selected) if isinstance(resolved_selected, str) and resolved_selected else None
    if resolved_apk is None:
        rejected = inspection.get("rejected_too_small") or []
        reason = "apk_too_small" if rejected else "apk_not_found"
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": reason,
            "apk_inspection": inspection,
        }
        append_audit_event(customer_id, "line_install_blocked", result)
        return result

    append_audit_event(
        customer_id,
        "line_install_started",
        {"device_id": device_id, "apk_path": str(resolved_apk), "replace": replace},
    )

    client = AdbClient(device_id=device_id, timeout=180)
    try:
        install_result = client.install(str(resolved_apk), replace=replace)
    except AdbError as exc:
        result = {
            "status": "blocked",
            "device_id": device_id,
            "reason": "install_failed",
            "detail": str(exc),
            "apk_path": str(resolved_apk),
        }
        append_audit_event(customer_id, "line_install_blocked", result)
        return result

    status = get_device_status(device_id)
    result = {
        "status": "ok" if status.get("line_installed") else "partial",
        "device_id": device_id,
        "apk_path": str(resolved_apk),
        "install_stdout": install_result.stdout.strip() or "INSTALL_OK",
        "device_status": status,
    }
    append_audit_event(
        customer_id,
        "line_install_completed",
        {
            "device_id": device_id,
            "apk_path": str(resolved_apk),
            "line_installed": status.get("line_installed"),
            "foreground_package": status.get("foreground_package"),
        },
    )
    return result


MIN_REASONABLE_APK_BYTES = 1_000_000  # 1 MB; real LINE APK is >100 MB, this only filters obvious junk.

GLOB_PATTERNS = ("*line*.apk", "*LINE*.apk", "jp.naver.line*.apk")


def line_apk_candidate_paths(apk_path: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    if apk_path:
        candidates.append(Path(apk_path).expanduser())
    if settings.line_apk_path:
        candidates.append(Path(settings.line_apk_path).expanduser())

    home = Path.home()
    downloads = home / "Downloads"
    candidates.extend(
        [
            downloads / "line.apk",
            downloads / "LINE.apk",
            downloads / "line-latest.apk",
        ]
    )

    if downloads.is_dir():
        for pattern in GLOB_PATTERNS:
            for found in sorted(downloads.glob(pattern)):
                candidates.append(found)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def inspect_line_apk_sources(apk_path: str | None = None) -> dict[str, object]:
    items: list[dict[str, object]] = []
    selected_path: str | None = None
    rejected_paths: list[str] = []
    for candidate in line_apk_candidate_paths(apk_path):
        resolved = candidate.resolve() if candidate.exists() else candidate
        exists = resolved.exists() and resolved.is_file()
        size_bytes = resolved.stat().st_size if exists else 0
        looks_reasonable = exists and size_bytes >= MIN_REASONABLE_APK_BYTES
        items.append(
            {
                "path": str(resolved),
                "exists": exists,
                "size_bytes": size_bytes,
                "looks_reasonable": looks_reasonable,
                "source": _candidate_source(candidate, apk_path),
            }
        )
        if looks_reasonable and selected_path is None:
            selected_path = str(resolved)
        elif exists and not looks_reasonable:
            rejected_paths.append(str(resolved))
    return {
        "status": "ok",
        "selected_path": selected_path,
        "available": selected_path is not None,
        "candidate_count": len(items),
        "items": items,
        "rejected_too_small": rejected_paths,
        "min_reasonable_bytes": MIN_REASONABLE_APK_BYTES,
    }


def resolve_line_apk_path(apk_path: str | None = None) -> Path | None:
    inspection = inspect_line_apk_sources(apk_path)
    selected = inspection.get("selected_path")
    if isinstance(selected, str) and selected:
        return Path(selected)
    return None


def _candidate_source(candidate: Path, explicit_apk_path: str | None) -> str:
    if explicit_apk_path and str(candidate) == str(Path(explicit_apk_path).expanduser()):
        return "explicit"
    if settings.line_apk_path and str(candidate) == str(Path(settings.line_apk_path).expanduser()):
        return "env"
    return "default_search"
