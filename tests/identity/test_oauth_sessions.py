from __future__ import annotations

import asyncio
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api import create_fixture_app
from apps.api.cookies import CSRF_COOKIE, SESSION_COOKIE
from packages.identity import (
    AuthenticationRequired,
    GoogleIdentity,
    OAuthCallbackRejected,
    validate_internal_return_to,
)
from packages.identity.security import token_hash

from .helpers import MutableClock, api_login, mutation_arguments, service_login


@pytest.mark.parametrize(
    "unsafe",
    [
        "https://evil.example/path",
        "https://dayjaview.vercel.app/themes/thm_1",
        "//evil.example/path",
        "/%2f%2fevil.example/path",
        "/%255c%255cevil.example/path",
        "/\\evil.example",
        "javascript:alert(1)",
        " /themes/thm_1",
        "/themes/thm_1\r\nX-Test: injected",
    ],
)
def test_return_to_rejects_external_and_encoded_redirects(unsafe: str) -> None:
    assert validate_internal_return_to(unsafe) == "/today"


def test_return_to_preserves_internal_path_query_and_fragment() -> None:
    value = "/themes/thm_1?eventId=evt_1#evidence"
    assert validate_internal_return_to(value) == value


def test_oauth_state_is_browser_bound_single_use() -> None:
    environment = create_fixture_app(clock=MutableClock())
    started = environment.service.begin_google_login("/themes/thm_1")
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    identity = GoogleIdentity("google-sub-1", "테스트 사용자")
    environment.oauth_provider.register_code("code-wrong-browser", identity)

    with pytest.raises(OAuthCallbackRejected):
        environment.service.complete_google_login(
            code="code-wrong-browser",
            state=state,
            browser_nonce="attacker-browser",
        )

    completed = environment.service.complete_google_login(
        code="code-wrong-browser",
        state=state,
        browser_nonce=started.browser_nonce,
    )
    assert completed.return_to == "/themes/thm_1"

    environment.oauth_provider.register_code("code-replay", identity)
    with pytest.raises(OAuthCallbackRejected):
        environment.service.complete_google_login(
            code="code-replay",
            state=state,
            browser_nonce=started.browser_nonce,
        )


def test_expired_oauth_state_cannot_create_a_session() -> None:
    clock = MutableClock()
    environment = create_fixture_app(clock=clock)
    started = environment.service.begin_google_login("/today")
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    environment.oauth_provider.register_code(
        "expired-state-code",
        GoogleIdentity("google-expired-state", "만료 로그인 사용자"),
    )
    clock.advance(timedelta(minutes=10, seconds=1))

    with pytest.raises(OAuthCallbackRejected):
        environment.service.complete_google_login(
            code="expired-state-code",
            state=state,
            browser_nonce=started.browser_nonce,
        )


def test_callback_rotates_existing_session_and_raw_tokens_are_not_repository_keys() -> None:
    environment = create_fixture_app(clock=MutableClock())
    identity = GoogleIdentity("google-sub-rotation", "세션 사용자")
    first = service_login(environment, code="rotation-1", identity=identity)
    second = service_login(
        environment,
        code="rotation-2",
        identity=identity,
        current_session_token=first.session_token,
    )

    assert second.session_token != first.session_token
    assert environment.service.authenticate(first.session_token) is None
    assert environment.service.authenticate(second.session_token) is not None
    assert environment.repository.get_session(second.session_token) is None
    assert environment.repository.get_session(token_hash(second.session_token)) is not None


def test_session_expiry_and_logout_prevent_token_reuse() -> None:
    clock = MutableClock()
    environment = create_fixture_app(clock=clock)
    completion = service_login(
        environment,
        code="expiry-code",
        identity=GoogleIdentity("google-sub-expiry", "만료 사용자"),
    )
    assert environment.service.authenticate(completion.session_token) is not None

    clock.advance(timedelta(hours=8, seconds=1))
    assert environment.service.authenticate(completion.session_token) is None

    fresh = service_login(
        environment,
        code="logout-code",
        identity=GoogleIdentity("google-sub-expiry", "만료 사용자"),
    )
    environment.service.logout(**mutation_arguments(fresh))
    assert environment.service.authenticate(fresh.session_token) is None


def test_realtime_ticket_is_short_lived_one_use_and_session_bound() -> None:
    environment = create_fixture_app(clock=MutableClock())
    first = service_login(
        environment,
        code="ticket-code-1",
        identity=GoogleIdentity("google-sub-ticket-1", "티켓 사용자 1"),
    )
    second = service_login(
        environment,
        code="ticket-code-2",
        identity=GoogleIdentity("google-sub-ticket-2", "티켓 사용자 2"),
    )
    ticket = environment.service.issue_realtime_ticket(**mutation_arguments(first))

    with pytest.raises(AuthenticationRequired):
        environment.service.consume_realtime_ticket(
            ticket=ticket.ticket,
            session_token=second.session_token,
            origin="https://dayjaview.vercel.app",
        )

    assert (
        environment.service.consume_realtime_ticket(
            ticket=ticket.ticket,
            session_token=first.session_token,
            origin="https://dayjaview.vercel.app",
        ).user.google_subject
        == "google-sub-ticket-1"
    )
    with pytest.raises(AuthenticationRequired):
        environment.service.consume_realtime_ticket(
            ticket=ticket.ticket,
            session_token=first.session_token,
            origin="https://dayjaview.vercel.app",
        )


def test_api_sets_secure_host_only_cookies_and_safe_redirect() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        environment = create_fixture_app(clock=clock)
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            started, completed = await api_login(
                client,
                environment,
                code="cookie-code",
                identity=GoogleIdentity("google-sub-cookie", "쿠키 사용자"),
                return_to="https://evil.example/steal",
            )

            state_cookie = started.headers.get_list("set-cookie")[0]
            assert "__Host-dayjaview_oauth_state=" in state_cookie
            assert "Secure" in state_cookie and "HttpOnly" in state_cookie
            assert "Path=/" in state_cookie and "Domain=" not in state_cookie

            assert completed.status_code == 302
            assert completed.headers["location"] == "https://dayjaview.vercel.app/today"
            cookies = completed.headers.get_list("set-cookie")
            session_header = next(value for value in cookies if value.startswith(f"{SESSION_COOKIE}="))
            csrf_header = next(value for value in cookies if value.startswith(f"{CSRF_COOKIE}="))
            assert "Secure" in session_header and "HttpOnly" in session_header
            assert "Path=/" in session_header and "Domain=" not in session_header
            assert "SameSite=Lax" in session_header and "Max-Age=28800" in session_header
            assert "Secure" in csrf_header and "HttpOnly" not in csrf_header
            assert "SameSite=Strict" in csrf_header and "Domain=" not in csrf_header

    asyncio.run(scenario())
