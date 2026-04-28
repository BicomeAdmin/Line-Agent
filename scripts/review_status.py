from __future__ import annotations

import json

import _bootstrap  # noqa: F401

from app.workflows.review_status import get_review_status


def main() -> int:
    print(json.dumps(get_review_status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
