"""F-23 finding: 인증 경계에 요청 한도와 만료 레코드 정리가 없다.

두 테스트 모두 **현재 동작을 그대로 고정한다**. 수리하면 실패하므로, 한도나
정리 작업을 넣을 때 이 파일을 같이 고쳐야 한다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from httpx import ASGITransport, AsyncClient

from apps.api import create_fixture_app
from packages.identity import GoogleIdentity

ORIGIN = "https://dayjaview.vercel.app"


@dataclass(slots=True)
class MutableClock:
    current: datetime = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, duration: timedelta) -> None:
        self.current += duration


def test_unauthenticated_login_start_is_unlimited_and_keeps_every_state() -> None:
    """`GET /auth/google`은 쿠키·CSRF 없이 oauth_state 행을 무제한으로 만든다.

    호출마다 새 state가 저장되고 아무것도 밀려나지 않는다. 즉 외부인이 저장소
    행 수를 요청 수만큼 늘릴 수 있고, 막는 한도가 없다.
    """

    async def scenario() -> None:
        environment = create_fixture_app(clock=MutableClock())
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(transport=transport, base_url=ORIGIN) as client:
            states: list[tuple[str, str]] = []
            for _ in range(40):
                response = await client.get("/auth/google")
                # 한도가 있다면 여기서 429가 나와야 한다. 지금은 전부 통과한다.
                assert response.status_code == 302
                location = response.headers["location"]
                nonce = response.cookies["__Host-dayjaview_oauth_state"]
                states.append((parse_qs(urlsplit(location).query)["state"][0], nonce))

            assert len({state for state, _ in states}) == 40

            # 40번째 요청 뒤에도 첫 state가 살아 있다 = 축출이 전혀 없다.
            first_state, first_nonce = states[0]
            environment.oauth_provider.register_code(
                "code-first",
                GoogleIdentity("sub-first", "첫 사용자"),
            )
            consumed = environment.service.complete_google_login(
                code="code-first",
                state=first_state,
                browser_nonce=first_nonce,
            )
            assert consumed.return_to == "/today"

    asyncio.run(scenario())


def test_expired_sessions_are_revoked_but_never_removed() -> None:
    """TTL이 지난 세션은 revoked 표시만 되고 저장소에 그대로 남는다.

    만료 레코드를 지우는 경로가 코드 어디에도 없어서, 사용자마다 로그인 횟수
    만큼 세션 행이 영구히 쌓인다.
    """

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
        # 로그인마다 세션이 하나씩 늘어난다(이전 세션을 넘기지 않으므로 회전 없음).
        assert environment.repository.session_count_for_user(user_id) == index + 1
        last_token = completion.session_token

    clock.advance(environment.service.policy.session_ttl + timedelta(seconds=1))
    assert environment.service.authenticate(last_token) is None

    # 인증 실패로 revoke까지 됐는데도 행 수는 그대로다.
    assert environment.repository.session_count_for_user(user_id) == 5
