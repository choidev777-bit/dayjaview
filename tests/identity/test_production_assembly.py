from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api import ApiSettings
from apps.api.production import (
    FIXTURE_MODE,
    GOOGLE_MODE,
    MEMORY_STORE,
    POSTGRES_STORE,
    ProductionIdentityEnvironment,
    create_production_app,
)
from packages.identity import GoogleIdentity
from packages.operator import PostgresOperatorRepository

from .helpers import MutableClock

_CLIENT_ID = "1234-production.apps.googleusercontent.com"
_CLIENT_SECRET = "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eak"
_DSN = "postgresql://identity:pw-must-not-leak@db.internal:5432/dayjaview"
# 공유 저장소 조립은 커서 서명 키를 요구한다(32바이트 이상).
_CURSOR_SECRET = "cursor-signing-secret-must-not-leak-0123"

_TEST_DSN = os.environ.get("IDENTITY_TEST_DSN")
_MIGRATION = (
    Path(__file__).resolve().parents[2] / "infra/migrations/0001_identity_library.sql"
)


class _FakeCursor:
    rowcount = 0

    def execute(self, query: str, params: Any = None) -> None:
        raise AssertionError("조립 단계에서는 질의를 실행하지 않는다")

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[Any]:
        return []

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor()

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def test_without_keys_the_assembly_stays_on_the_fixture_path() -> None:
    environment = create_production_app(
        {},
        settings=ApiSettings(),
        clock=MutableClock(),
    )

    assert environment.google_mode == FIXTURE_MODE
    assert environment.identity_store == MEMORY_STORE
    assert environment.uses_real_google is False
    assert environment.fixture_oauth_provider is not None
    environment.close()


def test_client_keys_switch_the_login_to_real_google() -> None:
    environment = create_production_app(
        {
            "GOOGLE_OAUTH_CLIENT_ID": _CLIENT_ID,
            "GOOGLE_OAUTH_CLIENT_SECRET": _CLIENT_SECRET,
        },
        settings=ApiSettings(),
        clock=MutableClock(),
    )

    assert environment.google_mode == GOOGLE_MODE
    assert environment.uses_real_google is True
    # 실 provider일 때 데모 code를 등록할 자리가 없어야 인증 우회가 남지 않는다.
    assert environment.fixture_oauth_provider is None
    environment.close()


def test_half_configured_google_keys_fail_instead_of_falling_back() -> None:
    for partial in (
        {"GOOGLE_OAUTH_CLIENT_ID": _CLIENT_ID},
        {"GOOGLE_OAUTH_CLIENT_SECRET": _CLIENT_SECRET},
        {"GOOGLE_OAUTH_CLIENT_ID": _CLIENT_ID, "GOOGLE_OAUTH_CLIENT_SECRET": "   "},
    ):
        with pytest.raises(ValueError):
            create_production_app(partial, settings=ApiSettings(), clock=MutableClock())


def test_database_url_moves_identity_to_postgres_and_close_releases_it() -> None:
    connections: list[_FakeConnection] = []

    def connect(dsn: str) -> Any:
        assert dsn == _DSN
        connection = _FakeConnection()
        connections.append(connection)
        return connection

    environment = create_production_app(
        {"DATABASE_URL": _DSN, "SESSION_SIGNING_SECRET": _CURSOR_SECRET},
        settings=ApiSettings(),
        clock=MutableClock(),
        connect=connect,
    )

    assert environment.identity_store == POSTGRES_STORE
    assert len(connections) == 2
    assert all(connection.closed is False for connection in connections)
    assert isinstance(environment.operator_repository, PostgresOperatorRepository)
    environment.close()
    assert all(connection.closed is True for connection in connections)


def test_login_redirects_to_google_with_the_deployment_redirect_uri() -> None:
    async def scenario() -> None:
        settings = ApiSettings(app_base_url="https://dayjaview.vercel.app")
        environment = create_production_app(
            {
                "GOOGLE_OAUTH_CLIENT_ID": _CLIENT_ID,
                "GOOGLE_OAUTH_CLIENT_SECRET": _CLIENT_SECRET,
            },
            settings=settings,
            clock=MutableClock(),
        )
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            response = await client.get("/auth/google", params={"returnTo": "/today"})

        assert response.status_code == 302
        location = response.headers["location"]
        parsed = urlsplit(location)
        query = parse_qs(parsed.query)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
            "https://accounts.google.com/o/oauth2/v2/auth"
        )
        # 구글 콘솔에 등록해야 하는 값과 같은 문자열이다.
        assert query["redirect_uri"] == [
            "https://dayjaview.vercel.app/api/auth/google/callback"
        ]
        assert query["client_id"] == [_CLIENT_ID]
        assert query["response_type"] == ["code"]
        assert _CLIENT_SECRET not in location
        environment.close()

    asyncio.run(scenario())


def test_secrets_never_appear_in_settings_or_assembly_representations() -> None:
    settings = ApiSettings()
    environment = create_production_app(
        {
            "GOOGLE_OAUTH_CLIENT_ID": _CLIENT_ID,
            "GOOGLE_OAUTH_CLIENT_SECRET": _CLIENT_SECRET,
            "DATABASE_URL": _DSN,
            "SESSION_SIGNING_SECRET": _CURSOR_SECRET,
        },
        settings=settings,
        clock=MutableClock(),
        connect=lambda _dsn: _FakeConnection(),
    )

    rendered = "".join(
        (
            repr(settings),
            repr(environment),
            json.dumps(
                {
                    "google": environment.google_mode,
                    "store": environment.identity_store,
                },
                ensure_ascii=False,
            ),
        )
    )
    assert _CLIENT_SECRET not in rendered
    assert "pw-must-not-leak" not in rendered
    assert _CURSOR_SECRET not in rendered
    environment.close()


@pytest.mark.skipif(
    _TEST_DSN is None,
    reason="IDENTITY_TEST_DSN의 disposable PostgreSQL 16이 필요합니다.",
)
def test_postgres_identity_keeps_the_session_across_a_new_assembly() -> None:
    """같은 DSN으로 다시 조립해도 로그인 세션이 살아 있어야 영속이 실제로 된 것이다."""

    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(_TEST_DSN, autocommit=True) as admin:
        admin.execute("DROP SCHEMA IF EXISTS identity CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS library CASCADE")
        admin.execute(_MIGRATION.read_text(encoding="utf-8"))

    settings = ApiSettings()
    environment_values = {
        "DATABASE_URL": str(_TEST_DSN),
        "SESSION_SIGNING_SECRET": _CURSOR_SECRET,
    }

    def assemble() -> ProductionIdentityEnvironment:
        return create_production_app(
            environment_values,
            settings=settings,
            clock=MutableClock(),
        )

    async def scenario() -> None:
        first = assemble()
        assert first.identity_store == POSTGRES_STORE
        assert first.fixture_oauth_provider is not None
        first.fixture_oauth_provider.register_code(
            "postgres-login",
            GoogleIdentity(
                "google-postgres-login",
                "영속 사용자",
                email="persist@example.test",
                email_verified=True,
            ),
        )
        transport = ASGITransport(app=first.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            started = await client.get("/auth/google", params={"returnTo": "/today"})
            state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
            completed = await client.get(
                "/auth/google/callback",
                params={"code": "postgres-login", "state": state},
            )
            assert completed.status_code == 302
            session_cookie = client.cookies["__Host-dayjaview_session"]
        first.close()

        second = assemble()
        transport = ASGITransport(app=second.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
            cookies={"__Host-dayjaview_session": session_cookie},
        ) as client:
            session = await client.get("/auth/session")
        second.close()

        assert session.status_code == 200
        assert session.json()["data"]["authenticated"] is True
        assert session.json()["data"]["user"]["displayName"] == "영속 사용자"

    asyncio.run(scenario())
