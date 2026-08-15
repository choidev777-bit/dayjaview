"""`OPERATOR_BOOTSTRAP_GOOGLE_EMAILS` 환경변수 → OPERATOR 역할 경로 검증(F-22).

기존 role 테스트는 `ApiSettings`를 직접 만들어 정책을 넣는다. 여기서는 배포에서
실제로 쓰는 입구인 환경변수 문자열부터 시작해, 그 값이 로그인 시 역할 부여와
운영자 API 접근으로 이어지는지를 확인한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from httpx import ASGITransport, AsyncClient

from apps.api import ApiSettings, create_fixture_app
from packages.identity import GoogleIdentity, parse_operator_bootstrap_emails

from .helpers import MutableClock, api_login

_OPERATOR_EMAIL = "teamfomc@example.test"


def _run(
    scenario: Callable[[AsyncClient], Awaitable[None]],
    *,
    environment_value: str | None,
    email: str,
    email_verified: bool = True,
) -> None:
    async def main() -> None:
        values = (
            {} if environment_value is None
            else {"OPERATOR_BOOTSTRAP_GOOGLE_EMAILS": environment_value}
        )
        environment = create_fixture_app(
            settings=ApiSettings.from_environment(values),
            clock=MutableClock(),
        )
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            await api_login(
                client,
                environment,
                code="bootstrap-login",
                identity=GoogleIdentity(
                    "google-bootstrap",
                    "운영 사용자",
                    email=email,
                    email_verified=email_verified,
                ),
            )
            await scenario(client)

    asyncio.run(main())


def test_environment_value_grants_the_operator_role_at_login() -> None:
    async def scenario(client: AsyncClient) -> None:
        session = await client.get("/auth/session")
        assert session.json()["data"]["roles"] == ["USER", "OPERATOR"]
        status = await client.get("/v1/operator/status")
        assert status.status_code == 200

    _run(scenario, environment_value=_OPERATOR_EMAIL, email=_OPERATOR_EMAIL)


def test_environment_value_tolerates_spacing_case_and_multiple_addresses() -> None:
    async def scenario(client: AsyncClient) -> None:
        session = await client.get("/auth/session")
        assert "OPERATOR" in session.json()["data"]["roles"]

    _run(
        scenario,
        environment_value=f" 다른@example.test , {_OPERATOR_EMAIL.upper()} ,",
        email=_OPERATOR_EMAIL,
    )


def test_email_outside_the_list_stays_a_regular_user() -> None:
    async def scenario(client: AsyncClient) -> None:
        session = await client.get("/auth/session")
        assert session.json()["data"]["roles"] == ["USER"]
        status = await client.get("/v1/operator/status")
        assert status.status_code == 403

    _run(scenario, environment_value=_OPERATOR_EMAIL, email="other@example.test")


def test_unverified_google_email_never_becomes_an_operator() -> None:
    async def scenario(client: AsyncClient) -> None:
        assert (await client.get("/auth/session")).json()["data"]["roles"] == ["USER"]
        assert (await client.get("/v1/operator/status")).status_code == 403

    _run(
        scenario,
        environment_value=_OPERATOR_EMAIL,
        email=_OPERATOR_EMAIL,
        email_verified=False,
    )


def test_unset_variable_leaves_nobody_as_operator() -> None:
    async def scenario(client: AsyncClient) -> None:
        assert (await client.get("/v1/operator/status")).status_code == 403

    _run(scenario, environment_value=None, email=_OPERATOR_EMAIL)


def test_role_arrives_on_the_next_login_after_the_variable_is_set() -> None:
    """운영에서 실제로 겪는 순서: 이미 로그인한 뒤에 env를 설정한다.

    역할은 로그인할 때 부여되므로 env를 켠 다음 **다시 로그인**해야 운영자가 된다.
    """

    async def scenario() -> None:
        clock = MutableClock()
        before = create_fixture_app(
            settings=ApiSettings.from_environment({}),
            clock=clock,
        )
        identity = GoogleIdentity(
            "google-late-bootstrap",
            "운영 사용자",
            email=_OPERATOR_EMAIL,
            email_verified=True,
        )
        async with AsyncClient(
            transport=ASGITransport(app=before.app),
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            await api_login(client, before, code="before-bootstrap", identity=identity)
            assert (await client.get("/v1/operator/status")).status_code == 403

        after = create_fixture_app(
            settings=ApiSettings.from_environment(
                {"OPERATOR_BOOTSTRAP_GOOGLE_EMAILS": _OPERATOR_EMAIL}
            ),
            clock=clock,
        )
        async with AsyncClient(
            transport=ASGITransport(app=after.app),
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            await api_login(client, after, code="after-bootstrap", identity=identity)
            assert (await client.get("/v1/operator/status")).status_code == 200

    asyncio.run(scenario())


def test_malformed_entries_are_dropped_instead_of_widening_the_list() -> None:
    parsed = parse_operator_bootstrap_emails(
        f",  , 골뱅이없음, {_OPERATOR_EMAIL.upper()} , 다른@example.test"
    )

    assert parsed == frozenset({_OPERATOR_EMAIL, "다른@example.test"})
    assert parse_operator_bootstrap_emails(None) == frozenset()
    assert parse_operator_bootstrap_emails("") == frozenset()
