from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from httpx import ASGITransport, AsyncClient

from apps.api import create_fixture_app
from apps.api.cookies import CSRF_COOKIE, SESSION_COOKIE
from packages.identity import GoogleIdentity, SavedType, TargetRecord


@dataclass(slots=True)
class MutableClock:
    current: datetime = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


def _login(environment, *, subject: str, display_name: str):
    started = environment.service.begin_google_login("/today")
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    code = f"code-{subject}"
    environment.oauth_provider.register_code(
        code,
        GoogleIdentity(subject, display_name),
    )
    return environment.service.complete_google_login(
        code=code,
        state=state,
        browser_nonce=started.browser_nonce,
    )


def _set_session(client: AsyncClient, session_token: str, csrf_token: str) -> None:
    for name, value in (
        (SESSION_COOKIE, session_token),
        (CSRF_COOKIE, csrf_token),
    ):
        client.cookies.set(
            name,
            value,
            domain="dayjaview.vercel.app",
            path="/",
        )


def _mutation_headers(csrf_token: str) -> dict[str, str]:
    return {
        "Origin": "https://dayjaview.vercel.app",
        "X-CSRF-Token": csrf_token,
    }


def test_saved_library_is_session_owned_and_blocks_idor() -> None:
    async def scenario() -> None:
        environment = create_fixture_app(
            clock=MutableClock(),
            targets=(TargetRecord(SavedType.THEME, "thm_owner", "소유 테마"),),
        )
        owner = _login(environment, subject="google-owner-api", display_name="소유자")
        attacker = _login(
            environment,
            subject="google-attacker-api",
            display_name="다른 사용자",
        )
        transport = ASGITransport(app=environment.app)
        async with (
            AsyncClient(
                transport=transport,
                base_url="https://dayjaview.vercel.app",
            ) as owner_client,
            AsyncClient(
                transport=transport,
                base_url="https://dayjaview.vercel.app",
            ) as attacker_client,
        ):
            _set_session(owner_client, owner.session_token, owner.csrf_token)
            _set_session(attacker_client, attacker.session_token, attacker.csrf_token)
            saved = await owner_client.put(
                "/v1/me/saved/themes/thm_owner",
                headers=_mutation_headers(owner.csrf_token),
            )
            attacker_delete = await attacker_client.delete(
                "/v1/me/saved/themes/thm_owner",
                headers=_mutation_headers(attacker.csrf_token),
            )
            attacker_list = await attacker_client.get("/v1/me/saved")
            owner_list = await owner_client.get("/v1/me/saved")
            idor_query = await owner_client.get(
                "/v1/me/saved",
                params={"userId": "usr_other"},
            )

            assert saved.status_code == 200
            assert attacker_delete.status_code == 200
            assert attacker_delete.json()["data"]["saved"] is False
            assert attacker_list.json()["data"]["items"] == []
            assert [item["targetId"] for item in owner_list.json()["data"]["items"]] == [
                "thm_owner"
            ]
            assert idor_query.status_code == 400

    asyncio.run(scenario())


def test_user_gets_operator_403_without_operator_projection() -> None:
    async def scenario() -> None:
        environment = create_fixture_app(clock=MutableClock())
        user = _login(environment, subject="google-user-api", display_name="일반 사용자")
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            _set_session(client, user.session_token, user.csrf_token)
            for path in (
                "/v1/operator/status",
                "/v1/operator/status?unexpected=1",
                "/v1/operator/jobs",
                "/v1/operator/private-unknown",
            ):
                response = await client.get(path)
                assert response.status_code == 403
                assert response.json()["error"]["code"] == "FEATURE_NOT_ENTITLED"
                serialized = json.dumps(response.json(), ensure_ascii=False)
                assert "services" not in serialized
                assert "reviewStatus" not in serialized
                assert "diagnostic" not in serialized

    asyncio.run(scenario())


def test_realtime_ticket_rest_boundary_requires_csrf_and_exposes_no_session() -> None:
    async def scenario() -> None:
        environment = create_fixture_app(clock=MutableClock())
        user = _login(environment, subject="google-ticket-api", display_name="티켓 사용자")
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
        ) as client:
            _set_session(client, user.session_token, user.csrf_token)
            rejected = await client.post(
                "/v1/auth/realtime-ticket",
                headers={"Origin": "https://dayjaview.vercel.app"},
            )
            issued = await client.post(
                "/v1/auth/realtime-ticket",
                headers=_mutation_headers(user.csrf_token),
            )

            assert rejected.status_code == 403
            assert issued.status_code == 200
            payload = issued.json()
            assert payload["data"]["ticket"]
            assert payload["data"]["expiresAt"] == "2026-08-14T03:00:30.000Z"
            serialized = json.dumps(payload)
            assert user.session_token not in serialized
            assert user.csrf_token not in serialized
            assert "sessionToken" not in serialized
            assert "csrfToken" not in serialized

    asyncio.run(scenario())
