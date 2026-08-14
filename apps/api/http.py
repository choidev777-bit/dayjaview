from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

if TYPE_CHECKING:
    from identity import InvalidRequest
else:
    from packages.identity import InvalidRequest

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApiRequest:
    method: str
    path: str
    query: Mapping[str, tuple[str, ...]]
    headers: Mapping[str, tuple[str, ...]]
    cookies: Mapping[str, str]
    body: bytes

    @classmethod
    async def from_asgi(
        cls,
        scope: Mapping[str, Any],
        receive: Receive,
        *,
        maximum_body_bytes: int = 1_048_576,
    ) -> ApiRequest:
        headers: dict[str, list[str]] = {}
        for raw_name, raw_value in scope.get("headers", []):
            name = raw_name.decode("latin-1").casefold()
            headers.setdefault(name, []).append(raw_value.decode("latin-1"))
        try:
            query_values = parse_qs(
                scope.get("query_string", b"").decode("utf-8"),
                keep_blank_values=True,
            )
        except UnicodeDecodeError as error:
            raise InvalidRequest("query 문자열이 올바른 UTF-8이 아닙니다.") from error
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > maximum_body_bytes:
                raise InvalidRequest("요청 본문이 너무 큽니다.")
            more_body = bool(message.get("more_body", False))
        frozen_headers = {key: tuple(values) for key, values in headers.items()}
        return cls(
            method=str(scope.get("method", "GET")).upper(),
            path=str(scope.get("path", "/")),
            query={key: tuple(values) for key, values in query_values.items()},
            headers=frozen_headers,
            cookies=_parse_cookies(frozen_headers.get("cookie", ())),
            body=bytes(body),
        )

    def header(self, name: str) -> str | None:
        values = self.headers.get(name.casefold(), ())
        if len(values) > 1:
            raise InvalidRequest("중복된 요청 header를 사용할 수 없습니다.")
        return None if not values else values[0]

    def query_value(self, name: str) -> str | None:
        values = self.query.get(name, ())
        if len(values) > 1:
            raise InvalidRequest("중복된 query 값을 사용할 수 없습니다.")
        return None if not values else values[0]

    def require_query_keys(self, allowed: set[str]) -> None:
        if not set(self.query).issubset(allowed):
            raise InvalidRequest("지원하지 않는 query 값이 포함되어 있습니다.")

    def require_empty_body(self) -> None:
        if self.body.strip():
            raise InvalidRequest("이 요청에는 본문을 보낼 수 없습니다.")


@dataclass(slots=True)
class ApiResponse:
    status_code: int
    body: bytes = b""
    headers: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def json(
        cls,
        status_code: int,
        payload: Mapping[str, object],
        *,
        private: bool = True,
    ) -> ApiResponse:
        cache_control = "private, no-store" if private else "no-store"
        return cls(
            status_code,
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8"),
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Cache-Control", cache_control),
                ("X-Content-Type-Options", "nosniff"),
                ("Referrer-Policy", "no-referrer"),
            ],
        )

    @classmethod
    def redirect(cls, location: str) -> ApiResponse:
        return cls(
            302,
            headers=[
                ("Location", location),
                ("Cache-Control", "no-store"),
                ("Referrer-Policy", "no-referrer"),
            ],
        )

    def add_cookie(self, value: str) -> None:
        self.headers.append(("Set-Cookie", value))

    async def send_asgi(self, send: Send) -> None:
        headers = [(name.encode("latin-1"), value.encode("latin-1")) for name, value in self.headers]
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": self.body, "more_body": False})


def _parse_cookies(cookie_headers: tuple[str, ...]) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for header in cookie_headers:
        for part in header.split(";"):
            name, separator, value = part.strip().partition("=")
            if not separator or not name:
                continue
            if name in cookies:
                raise InvalidRequest("중복된 cookie를 사용할 수 없습니다.")
            cookies[name] = value
    return cookies
