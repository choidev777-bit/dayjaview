from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from httpx import AsyncClient

from apps.api import FixtureIdentityEnvironment
from packages.identity import GoogleIdentity
from packages.identity.models import LoginCompletion


@dataclass(slots=True)
class MutableClock:
    current: datetime = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current

    def advance(self, duration: timedelta) -> None:
        self.current += duration


def service_login(
    environment: FixtureIdentityEnvironment,
    *,
    code: str,
    identity: GoogleIdentity,
    return_to: str | None = "/today",
    current_session_token: str | None = None,
) -> LoginCompletion:
    started = environment.service.begin_google_login(return_to)
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    environment.oauth_provider.register_code(code, identity)
    return environment.service.complete_google_login(
        code=code,
        state=state,
        browser_nonce=started.browser_nonce,
        current_session_token=current_session_token,
    )


async def api_login(
    client: AsyncClient,
    environment: FixtureIdentityEnvironment,
    *,
    code: str,
    identity: GoogleIdentity,
    return_to: str = "/today",
):
    started = await client.get("/auth/google", params={"returnTo": return_to})
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    environment.oauth_provider.register_code(code, identity)
    completed = await client.get(
        "/auth/google/callback",
        params={"code": code, "state": state},
    )
    return started, completed


def mutation_arguments(completion: LoginCompletion) -> dict[str, str]:
    return {
        "session_token": completion.session_token,
        "origin": "https://dayjaview.vercel.app",
        "csrf_token": completion.csrf_token,
        "csrf_cookie": completion.csrf_token,
    }
