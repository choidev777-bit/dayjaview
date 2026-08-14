from __future__ import annotations

import json

import httpx
import pytest

from packages.identity import HttpGoogleOAuthProvider, OAuthProviderError

REDIRECT = "https://dayjaview.vercel.app/api/auth/google/callback"


def _provider(handler) -> HttpGoogleOAuthProvider:
    return HttpGoogleOAuthProvider(
        client_id="client-id",
        client_secret="client-secret",
        expected_redirect_uri=REDIRECT,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_authorization_url_contains_required_oauth_parameters() -> None:
    provider = _provider(lambda request: httpx.Response(500))
    url = provider.authorization_url(state="state-1", redirect_uri=REDIRECT)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=client-id" in url
    assert "response_type=code" in url
    assert "state=state-1" in url
    assert "scope=openid+profile+email" in url


def test_exchange_code_returns_identity_from_token_and_userinfo() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "oauth2.googleapis.com":
            body = dict(
                pair.split("=", 1)
                for pair in request.content.decode("utf-8").split("&")
            )
            assert body["code"] == "auth-code"
            assert body["grant_type"] == "authorization_code"
            return httpx.Response(
                200,
                json={"access_token": "token-abc", "token_type": "Bearer"},
            )
        assert request.url.host == "openidconnect.googleapis.com"
        assert request.headers["authorization"] == "Bearer token-abc"
        return httpx.Response(
            200,
            json={
                "sub": "google-sub-1",
                "name": "홍길동",
                "email": "user@example.com",
                "email_verified": True,
            },
        )

    provider = _provider(handler)
    identity = provider.exchange_code(code="auth-code", redirect_uri=REDIRECT)
    assert identity.subject == "google-sub-1"
    assert identity.display_name == "홍길동"
    assert identity.email == "user@example.com"
    assert identity.email_verified is True
    assert len(seen) == 2
    # client secret은 token endpoint 요청 본문 밖으로 나가지 않는다
    assert "client-secret" not in str(seen[1].url)
    assert "authorization" not in json.dumps(dict(seen[0].headers)).lower()


def test_exchange_code_failures_are_opaque() -> None:
    rejected = _provider(lambda request: httpx.Response(400, json={"error": "bad"}))
    with pytest.raises(OAuthProviderError):
        rejected.exchange_code(code="bad-code", redirect_uri=REDIRECT)

    with pytest.raises(OAuthProviderError):
        rejected.exchange_code(code="code", redirect_uri="https://evil.example/cb")

    def token_ok_userinfo_fails(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(401)

    with pytest.raises(OAuthProviderError):
        _provider(token_ok_userinfo_fails).exchange_code(
            code="code", redirect_uri=REDIRECT
        )

    def token_without_access_token(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "Bearer"})

    with pytest.raises(OAuthProviderError):
        _provider(token_without_access_token).exchange_code(
            code="code", redirect_uri=REDIRECT
        )
