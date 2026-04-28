"""Verify an APK in ~/Downloads and install to the emulator.

Computes SHA-256 of the discovered APK, optionally compares against an expected
hash (e.g. the one APKMirror publishes on the download page), then delegates
to the existing install_line_app workflow. Records source URL + sha256 +
size in audit for supply-chain traceability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from app.core.audit import append_audit_event
from app.workflows.line_install import install_line_app, resolve_line_apk_path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("device_id")
    parser.add_argument("--apk-path", default=None, help="Override APK path; otherwise auto-discover.")
    parser.add_argument("--expected-sha256", default=None, help="Hash from publisher; install aborted on mismatch.")
    parser.add_argument("--source-url", default=None, help="URL the APK was downloaded from (recorded in audit).")
    parser.add_argument("--version-label", default=None, help="Human-readable version label, e.g. '26.6.0'.")
    parser.add_argument("--customer-id", default="customer_a")
    args = parser.parse_args()

    apk = resolve_line_apk_path(args.apk_path)
    if apk is None:
        print(json.dumps({"status": "blocked", "reason": "apk_not_found_or_too_small"}, ensure_ascii=False, indent=2))
        return 1

    digest = sha256_of(apk)
    size_bytes = apk.stat().st_size
    print(json.dumps(
        {
            "step": "computed_hash",
            "apk_path": str(apk),
            "size_bytes": size_bytes,
            "sha256": digest,
        },
        ensure_ascii=False,
        indent=2,
    ))

    if args.expected_sha256 and digest.lower() != args.expected_sha256.lower():
        result = {
            "status": "blocked",
            "reason": "sha256_mismatch",
            "apk_path": str(apk),
            "computed_sha256": digest,
            "expected_sha256": args.expected_sha256,
        }
        append_audit_event(args.customer_id, "line_install_blocked", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    append_audit_event(
        args.customer_id,
        "line_apk_verified",
        {
            "apk_path": str(apk),
            "size_bytes": size_bytes,
            "sha256": digest,
            "expected_sha256_provided": bool(args.expected_sha256),
            "source_url": args.source_url,
            "version_label": args.version_label,
        },
    )

    install_result = install_line_app(args.device_id, apk_path=str(apk))
    install_result["apk_sha256"] = digest
    install_result["apk_size_bytes"] = size_bytes
    if args.source_url:
        install_result["source_url"] = args.source_url
    if args.version_label:
        install_result["version_label"] = args.version_label

    print(json.dumps(install_result, ensure_ascii=False, indent=2))
    return 0 if install_result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
