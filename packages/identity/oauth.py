from __future__ import annotations

from threading import RLock
from typing import Protocol
from urllib.parse import urlencode

from .models import GoogleIdentity


class OAuthProviderError(Exception):
    """A deliberately opaque provider failure."""


class GoogleOAuthProvider(Protocol):
    def authorization_url(self, *, state: str, redirect_uri: str) -> str: ...

    def exchange_code(self, *, code: str, redirect_uri: str) -> GoogleIdentity: ...


class FixtureGoogleOAuthProvider:
    """Deterministic, network-free Google provider used only by fixture tests."""

    def __init__(
        self,
        *,
        client_id: str = "fixture-google-client",
        authorization_endpoint: str = "https://accounts.google.test/o/oauth2/v2/auth",
        expected_redirect_uri: str = "https://dayjaview.vercel.app/api/auth/google/callback",
    ) -> None:
        self._client_id = client_id
        self._authorization_endpoint = authorization_endpoint
        self._expected_redirect_uri = expected_redirect_uri
        self._codes: dict[str, GoogleIdentity] = {}
        self._lock = RLock()

    def register_code(self, code: str, identity: GoogleIdentity) -> None:
        if not code:
            raise ValueError("fixture authorization code must not be empty")
        with self._lock:
            self._codes[code] = identity

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        if redirect_uri != self._expected_redirect_uri:
            raise OAuthProviderError("OAuth redirect URI configuration is invalid")
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid profile email",
                "state": state,
            }
        )
        return f"{self._authorization_endpoint}?{query}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> GoogleIdentity:
        if redirect_uri != self._expected_redirect_uri:
            raise OAuthProviderError("OAuth redirect URI configuration is invalid")
        with self._lock:
            identity = self._codes.pop(code, None)
        if identity is None:
            raise OAuthProviderError("OAuth authorization code was rejected")
        return identity
