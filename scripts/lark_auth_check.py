from __future__ import annotations

import json

import _bootstrap  # noqa: F401

from app.lark.client import LarkClient, LarkClientError


def main() -> int:
    client = None
    try:
        client = LarkClient()
        tenant_token = client.tenant_access_token()
    except LarkClientError as exc:
        tenant_error = str(exc)
    else:
        tenant_error = None

    try:
        if client is None:
            client = LarkClient()
        app_token = client.app_access_token()
    except LarkClientError as exc:
        app_error = str(exc)
        app_token = None
    else:
        app_error = None

    if tenant_error and app_error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "tenant_access_token_error": tenant_error,
                    "app_access_token_error": app_error,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    payload = {"status": "ok"}
    if tenant_error:
        payload["tenant_access_token_error"] = tenant_error
    else:
        payload["tenant_token_prefix"] = tenant_token.token[:12]
        payload["tenant_expire"] = tenant_token.expire
    if app_error:
        payload["app_access_token_error"] = app_error
    else:
        payload["app_token_prefix"] = app_token.token[:12]
        payload["app_expire"] = app_token.expire
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
