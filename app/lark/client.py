from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, parse, request

from app.config import settings


LARK_BASE_URL = "https://open.larksuite.com/open-apis"


class LarkClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class LarkAccessToken:
    token: str
    expire: int | None = None
    token_type: str = "tenant_access_token"


class LarkClient:
    def __init__(self, app_id: str | None = None, app_secret: str | None = None) -> None:
        self.app_id = app_id or settings.lark_app_id
        self.app_secret = app_secret or settings.lark_app_secret
        if not self.app_id or not self.app_secret:
            raise LarkClientError("Missing Lark app credentials. Set LARK_APP_ID and LARK_APP_SECRET.")

    def tenant_access_token(self) -> LarkAccessToken:
        payload = self._post_json(
            "/auth/v3/tenant_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        token = payload.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise LarkClientError("Lark tenant access token missing from response.")
        expire = payload.get("expire")
        return LarkAccessToken(token=token, expire=expire if isinstance(expire, int) else None, token_type="tenant_access_token")

    def app_access_token(self) -> LarkAccessToken:
        payload = self._post_json(
            "/auth/v3/app_access_token/internal",
            {"app_id": self.app_id, "app_secret": self.app_secret},
        )
        token = payload.get("app_access_token")
        if not isinstance(token, str) or not token:
            raise LarkClientError("Lark app access token missing from response.")
        expire = payload.get("expire")
        return LarkAccessToken(token=token, expire=expire if isinstance(expire, int) else None, token_type="app_access_token")

    def send_message(
        self,
        receive_id: str,
        msg_type: str,
        content: dict[str, object],
        receive_id_type: str = "chat_id",
    ) -> dict[str, object]:
        token = self.tenant_access_token().token
        return self._post_json(
            f"/im/v1/messages?{parse.urlencode({'receive_id_type': receive_id_type})}",
            {"receive_id": receive_id, "msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)},
            headers={"Authorization": f"Bearer {token}"},
        )

    def send_card(self, receive_id: str, card: dict[str, object], receive_id_type: str = "chat_id") -> dict[str, object]:
        # Lark/Feishu's `msg_type: interactive` content is the card body directly.
        # Wrapping it in `{"card": card}` triggers `200621 parse card json err`.
        return self.send_message(receive_id, "interactive", card, receive_id_type=receive_id_type)

    def add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> dict[str, object]:
        token = self.tenant_access_token().token
        return self._post_json(
            f"/im/v1/messages/{parse.quote(message_id, safe='')}/reactions",
            {"reaction_type": {"emoji_type": emoji_type}},
            headers={"Authorization": f"Bearer {token}"},
        )

    def _post_json(self, path: str, payload: dict[str, object], headers: dict[str, str] | None = None) -> dict[str, object]:
        data = json.dumps(payload).encode("utf-8")
        request_headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
        http_request = request.Request(f"{LARK_BASE_URL}{path}", data=data, headers=request_headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=30) as response:
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LarkClientError(f"Lark HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise LarkClientError(f"Lark request failed: {exc}") from exc

        payload_response = json.loads(body)
        if payload_response.get("code", 0) not in (0, None):
            raise LarkClientError(f"Lark API error: {payload_response}")
        return payload_response
