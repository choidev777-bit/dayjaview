"""실제 Google OAuth code 교환 provider.

authorization code를 Google token endpoint에서 교환하고, 받은 access token
으로 OpenID userinfo를 조회해 GoogleIdentity를 만든다. token 교환이 서버와
Google 사이 TLS 직결로 이루어지므로 ID token 서명 검증 대신 userinfo
endpoint 응답을 신뢰한다. 실패 사유는 의도적으로 불투명하게 만든다.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from .models import GoogleIdentity
from .oauth import OAuthProviderError

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"


class HttpGoogleOAuthProvider:
    """GoogleOAuthProvider Protocol의 실제 HTTP 구현."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        expected_redirect_uri: str,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("Google OAuth client 설정이 비어 있습니다")
        if not expected_redirect_uri.startswith(("https://", "http://localhost")):
            raise ValueError("redirect URI는 https 또는 localhost여야 합니다")
        self._client_id = client_id
        self._client_secret = client_secret
        self._expected_redirect_uri = expected_redirect_uri
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

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
                "prompt": "select_account",
            }
        )
        return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> GoogleIdentity:
        if redirect_uri != self._expected_redirect_uri:
            raise OAuthProviderError("OAuth redirect URI configuration is invalid")
        try:
            token_response = self._client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
        except httpx.HTTPError as error:
            raise OAuthProviderError("OAuth token exchange failed") from error
        if token_response.status_code != 200:
            raise OAuthProviderError("OAuth authorization code was rejected")
        try:
            access_token = token_response.json().get("access_token")
        except ValueError as error:
            raise OAuthProviderError("OAuth token response is invalid") from error
        if not isinstance(access_token, str) or not access_token:
            raise OAuthProviderError("OAuth token response is invalid")

        try:
            userinfo_response = self._client.get(
                GOOGLE_USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as error:
            raise OAuthProviderError("OAuth userinfo request failed") from error
        if userinfo_response.status_code != 200:
            raise OAuthProviderError("OAuth userinfo request failed")
        try:
            userinfo = userinfo_response.json()
        except ValueError as error:
            raise OAuthProviderError("OAuth userinfo response is invalid") from error

        subject = userinfo.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OAuthProviderError("OAuth userinfo response is invalid")
        display_name = userinfo.get("name")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = "Google 사용자"
        email = userinfo.get("email")
        return GoogleIdentity(
            subject=subject,
            display_name=display_name,
            email=email if isinstance(email, str) and email else None,
            email_verified=userinfo.get("email_verified") is True,
        )

    def close(self) -> None:
        self._client.close()
