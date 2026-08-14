from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api import create_fixture_app
from apps.api.cookies import CSRF_COOKIE, SESSION_COOKIE
from packages.identity import (
    GoogleIdentity,
    RecentAuthenticationRequired,
    SavedType,
    TargetRecord,
)

from .helpers import MutableClock, api_login, mutation_arguments, service_login


def test_cookie_mutations_require_exact_origin_and_session_bound_csrf() -> None:
    async def scenario() -> None:
        environment = create_fixture_app(
            clock=MutableClock(),
            targets=(TargetRecord(SavedType.THEME, "thm_csrf", "보안 테마"),),
        )
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as unauthenticated:
            response = await unauthenticated.put("/v1/me/saved/themes/thm_csrf")
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            await api_login(
                client,
                environment,
                code="csrf-code",
                identity=GoogleIdentity("google-csrf", "보안 사용자"),
            )
            csrf = client.cookies.get(CSRF_COOKIE)
            assert csrf is not None

            missing_origin = await client.put(
                "/v1/me/saved/themes/thm_csrf",
                headers={"X-CSRF-Token": csrf},
            )
            assert missing_origin.status_code == 403

            wrong_origin = await client.put(
                "/v1/me/saved/themes/thm_csrf",
                headers={
                    "Origin": "https://evil.example",
                    "X-CSRF-Token": csrf,
                },
            )
            assert wrong_origin.status_code == 403

            wrong_token = await client.put(
                "/v1/me/saved/themes/thm_csrf",
                headers={
                    "Origin": "https://dayjaview.vercel.app",
                    "X-CSRF-Token": "attacker-token",
                },
            )
            assert wrong_token.status_code == 403

            saved = await client.put(
                "/v1/me/saved/themes/thm_csrf",
                headers={
                    "Origin": "https://dayjaview.vercel.app",
                    "X-CSRF-Token": csrf,
                },
            )
            assert saved.status_code == 200
            assert saved.json()["data"]["saved"] is True

            idor_query = await client.get("/v1/me/saved", params={"userId": "usr_other"})
            assert idor_query.status_code == 400
            assert idor_query.json()["error"]["code"] == "INVALID_REQUEST"

    asyncio.run(scenario())


def test_logout_is_csrf_protected_idempotent_and_revokes_reuse() -> None:
    async def scenario() -> None:
        environment = create_fixture_app(clock=MutableClock())
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            await api_login(
                client,
                environment,
                code="logout-api",
                identity=GoogleIdentity("google-logout-api", "로그아웃 사용자"),
            )
            session = client.cookies.get(SESSION_COOKIE)
            csrf = client.cookies.get(CSRF_COOKIE)
            assert session is not None and csrf is not None

            rejected = await client.post(
                "/auth/logout",
                headers={"Origin": "https://dayjaview.vercel.app"},
            )
            assert rejected.status_code == 403

            logged_out = await client.post(
                "/auth/logout",
                headers={
                    "Origin": "https://dayjaview.vercel.app",
                    "X-CSRF-Token": csrf,
                },
            )
            assert logged_out.status_code == 200
            assert logged_out.json()["data"] == {"loggedOut": True}

            retried = await client.post(
                "/auth/logout",
                headers={
                    "Origin": "https://dayjaview.vercel.app",
                    "X-CSRF-Token": csrf,
                },
            )
            assert retried.status_code == 200

            reused = await client.get(
                "/v1/me/saved",
                headers={"Cookie": f"{SESSION_COOKIE}={session}"},
            )
            assert reused.status_code == 401

    asyncio.run(scenario())


def test_account_deletion_removes_profile_all_sessions_and_saved_data() -> None:
    environment = create_fixture_app(
        clock=MutableClock(),
        targets=(TargetRecord(SavedType.STOCK, "stk_delete", "삭제 확인 종목"),),
    )
    identity = GoogleIdentity(
        "google-delete",
        "삭제 사용자",
        email="delete@example.test",
        email_verified=True,
    )
    first = service_login(environment, code="delete-1", identity=identity)
    second = service_login(environment, code="delete-2", identity=identity)
    principal = environment.service.require_authenticated(first.session_token)
    environment.service.save_item(
        **mutation_arguments(second),
        saved_type=SavedType.STOCK,
        target_id="stk_delete",
    )
    assert environment.repository.session_count_for_user(principal.user.user_id) == 2
    assert environment.repository.saved_count_for_user(principal.user.user_id) == 1

    environment.service.delete_account(**mutation_arguments(first))

    assert environment.service.authenticate(first.session_token) is None
    assert environment.service.authenticate(second.session_token) is None
    assert not environment.repository.contains_user_subject("google-delete")
    assert environment.repository.session_count_for_user(principal.user.user_id) == 0
    assert environment.repository.saved_count_for_user(principal.user.user_id) == 0


def test_account_deletion_requires_recent_authentication_without_partial_delete() -> None:
    clock = MutableClock()
    environment = create_fixture_app(clock=clock)
    completion = service_login(
        environment,
        code="stale-delete",
        identity=GoogleIdentity("google-stale-delete", "최근 인증 사용자"),
    )
    clock.advance(timedelta(minutes=11))

    with pytest.raises(RecentAuthenticationRequired):
        environment.service.delete_account(**mutation_arguments(completion))

    assert environment.service.authenticate(completion.session_token) is not None
    assert environment.repository.contains_user_subject("google-stale-delete")


def test_identity_migration_cascades_account_data_and_never_stores_raw_tokens() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "infra"
        / "migrations"
        / "0001_identity_library.sql"
    ).read_text(encoding="utf-8")
    normalized = migration.casefold()
    assert normalized.count("on delete cascade") >= 5
    assert "token_hash" in normalized and "csrf_token_hash" in normalized
    assert "access_token" not in normalized and "refresh_token" not in normalized
    assert "primary key (user_id, saved_type, target_id)" in normalized
