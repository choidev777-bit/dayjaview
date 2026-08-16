"""F-23 수리: 인증 경계에 요청 한도와 만료 레코드 정리가 생겼다.

무인증 `GET /auth/google`은 호출마다 `identity.oauth_states` 행을 만든다. 이제
클라이언트 주소 단위 한도가 걸리고, 만료된 state·session·ticket은 로그인
시작 때 지워진다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from httpx import ASGITransport, AsyncClient

from apps.api import create_fixture_app
from apps.api.app import AUTH_RATE_LIMIT, AUTH_RATE_WINDOW
from apps.api.config import ApiSettings
from packages.identity import GoogleIdentity

ORIGIN = "https://dayjaview.vercel.app"


@dataclass(slots=True)
class MutableClock:
    current: datetime = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, duration: timedelta) -> None:
        self.current += duration


def test_unauthenticated_login_start_is_rate_limited_per_client() -> None:
    """한도를 넘기면 429로 끊기고, 창이 지나면 다시 열린다."""

    async def scenario() -> None:
        clock = MutableClock()
        environment = create_fixture_app(clock=clock)
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
            for _ in range(AUTH_RATE_LIMIT):
                assert (await client.get("/auth/google")).status_code == 302

            blocked = await client.get("/auth/google")
            assert blocked.status_code == 429
            assert blocked.json()["error"]["code"] == "RATE_LIMITED"
            # 한도에 걸린 요청은 state 행을 만들지 않는다.
            assert "location" not in blocked.headers

            clock.advance(AUTH_RATE_WINDOW + timedelta(seconds=1))
            assert (await client.get("/auth/google")).status_code == 302

    asyncio.run(scenario())


def test_session_read_does_not_consume_the_login_start_budget() -> None:
    """F-24 발견 2: 세션 조회는 한도를 먹지 않는다.

    웹은 앱이 뜰 때마다 `/auth/session`을 부른다. 이것까지 세면 화면을 몇 번
    새로 열기만 해도 정상 사용자가 429로 막힌다.
    """

    async def scenario() -> None:
        environment = create_fixture_app(clock=MutableClock())
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
            for _ in range(AUTH_RATE_LIMIT * 3):
                assert (await client.get("/auth/session")).status_code == 200

            # 예산은 그대로 남아 있어야 한다.
            assert (await client.get("/auth/google")).status_code == 302

    asyncio.run(scenario())


def test_rate_limit_separates_clients_behind_a_declared_proxy() -> None:
    """F-24 발견 2: 프록시 뒤에서도 사용자마다 따로 센다.

    앞단 프록시 수를 선언하지 않으면 header는 위조 가능하므로 무시한다.
    """

    async def scenario() -> None:
        behind_proxy = create_fixture_app(
            clock=MutableClock(),
            settings=ApiSettings(trusted_proxy_hops=1),
        )
        transport = ASGITransport(app=behind_proxy.app)
        async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
            for _ in range(AUTH_RATE_LIMIT):
                first = await client.get(
                    "/auth/google",
                    headers={"X-Forwarded-For": "203.0.113.7"},
                )
                assert first.status_code == 302

            blocked = await client.get(
                "/auth/google",
                headers={"X-Forwarded-For": "203.0.113.7"},
            )
            assert blocked.status_code == 429
            # 같은 프록시를 지나온 다른 사용자는 자기 예산을 그대로 쓴다.
            other = await client.get(
                "/auth/google",
                headers={"X-Forwarded-For": "203.0.113.8"},
            )
            assert other.status_code == 302

        default = create_fixture_app(clock=MutableClock())
        transport = ASGITransport(app=default.app)
        async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
            for _ in range(AUTH_RATE_LIMIT):
                assert (
                    await client.get(
                        "/auth/google",
                        headers={"X-Forwarded-For": f"198.51.100.{_}"},
                    )
                ).status_code == 302

            forged = await client.get(
                "/auth/google",
                headers={"X-Forwarded-For": "198.51.100.250"},
            )
            assert forged.status_code == 429

    asyncio.run(scenario())


def test_expired_sessions_and_states_are_removed_on_the_next_login() -> None:
    """TTL이 지난 레코드는 다음 로그인 시작 때 저장소에서 사라진다."""

    clock = MutableClock()
    environment = create_fixture_app(clock=clock)
    identity = GoogleIdentity("sub-repeat-login", "반복 로그인 사용자")

    user_id = ""
    for index in range(5):
        started = environment.service.begin_google_login("/today")
        state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
        code = f"code-{index}"
        environment.oauth_provider.register_code(code, identity)
        completion = environment.service.complete_google_login(
            code=code,
            state=state,
            browser_nonce=started.browser_nonce,
        )
        user_id = completion.user.user_id
        assert environment.repository.session_count_for_user(user_id) == index + 1

    clock.advance(environment.service.policy.session_ttl + timedelta(seconds=1))
    environment.service.begin_google_login("/today")

    assert environment.repository.session_count_for_user(user_id) == 0
