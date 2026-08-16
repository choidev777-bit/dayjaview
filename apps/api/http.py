from __future__ import annotations

import json
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

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
    client: str = "unknown"

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
            client=_client_key(scope.get("client")),
        )

    def client_key(self, *, trusted_proxy_hops: int) -> str:
        """요청 한도를 세는 단위.

        API 앞에 프록시가 서면 전송 계층 주소는 전부 그 프록시가 되어 모든
        사용자가 한 통을 공유한다. 앞단 프록시 수를 운영자가 선언한 경우에만
        `X-Forwarded-For`의 그 자리 값을 쓴다. 선언이 없거나 항목 수가 모자라면
        위조 가능한 header 대신 전송 계층 주소로 남는다.
        """

        if trusted_proxy_hops <= 0:
            return self.client
        forwarded = [
            entry.strip()
            for value in self.headers.get("x-forwarded-for", ())
            for entry in value.split(",")
            if entry.strip()
        ]
        if len(forwarded) < trusted_proxy_hops:
            return self.client
        return forwarded[-trusted_proxy_hops]

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


def _client_key(client: Any) -> str:
    """요청 한도를 세는 단위. 프록시 header는 위조되므로 전송 계층 주소만 쓴다."""

    if isinstance(client, (tuple, list)) and client:
        return str(client[0])
    return "unknown"


@dataclass(slots=True)
class RateLimiter:
    """진입점 요청 한도.

    프로세스 안에서만 센다. 인스턴스가 여러 개면 인스턴스마다 따로 걸리므로
    실효 한도는 (한도 × 인스턴스 수)다. 저장소 행을 무제한으로 늘리는 것을
    막는 것이 목적이라 그 정도면 충분하다.
    """

    limit: int
    window: timedelta
    maximum_clients: int = 4096
    _hits: dict[str, deque[datetime]] = field(default_factory=dict)

    def allow(self, client: str, now: datetime) -> bool:
        if client not in self._hits and len(self._hits) >= self.maximum_clients:
            self._make_room(now)
        hits = self._hits.setdefault(client, deque())
        threshold = now - self.window
        while hits and hits[0] <= threshold:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def _make_room(self, now: datetime) -> None:
        """한도 표 자체가 메모리를 무제한으로 먹지 않게 한다."""

        threshold = now - self.window
        for client in [
            key for key, hits in self._hits.items() if not hits or hits[-1] <= threshold
        ]:
            del self._hits[client]
        while len(self._hits) >= self.maximum_clients:
            del self._hits[min(self._hits, key=lambda key: self._hits[key][-1])]


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
