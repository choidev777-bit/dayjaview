from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

from packages.identity import IdentityPolicy, parse_operator_bootstrap_emails

TRUSTED_PROXY_HOPS_ENV = "TRUSTED_PROXY_HOPS"


def _parse_trusted_proxy_hops(raw: str | None) -> int:
    if raw is None or not raw.strip():
        return 0
    try:
        hops = int(raw.strip())
    except ValueError as error:
        raise ValueError(f"{TRUSTED_PROXY_HOPS_ENV}는 정수여야 합니다: {raw}") from error
    if hops < 0:
        raise ValueError(f"{TRUSTED_PROXY_HOPS_ENV}는 음수일 수 없습니다: {hops}")
    return hops


@dataclass(frozen=True, slots=True)
class ApiSettings:
    app_base_url: str = "https://dayjaview.vercel.app"
    schema_version: str = "2026-08-14.1"
    session_ttl: timedelta = timedelta(hours=8)
    oauth_state_ttl: timedelta = timedelta(minutes=10)
    realtime_ticket_ttl: timedelta = timedelta(seconds=30)
    realtime_auth_deadline: timedelta = timedelta(seconds=5)
    realtime_maximum_message_bytes: int = 65_536
    recent_authentication_window: timedelta = timedelta(minutes=10)
    operator_bootstrap_emails: frozenset[str] = frozenset()
    # API 앞에 선 프록시 수. 0이면 전송 계층 주소만 믿는다.
    trusted_proxy_hops: int = 0

    def __post_init__(self) -> None:
        if self.realtime_auth_deadline <= timedelta(0):
            raise ValueError("realtime_auth_deadline must be positive")
        if not 1 <= self.realtime_maximum_message_bytes <= 1_048_576:
            raise ValueError("realtime_maximum_message_bytes is outside the safe range")
        if self.trusted_proxy_hops < 0:
            raise ValueError("trusted_proxy_hops must not be negative")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> ApiSettings:
        """Build settings from already-provided process values; no dotenv file is read."""

        return cls(
            app_base_url=environment.get(
                "APP_BASE_URL",
                "https://dayjaview.vercel.app",
            ).rstrip("/"),
            operator_bootstrap_emails=parse_operator_bootstrap_emails(
                environment.get("OPERATOR_BOOTSTRAP_GOOGLE_EMAILS")
            ),
            trusted_proxy_hops=_parse_trusted_proxy_hops(
                environment.get(TRUSTED_PROXY_HOPS_ENV)
            ),
        )

    def identity_policy(self) -> IdentityPolicy:
        return IdentityPolicy(
            app_base_url=self.app_base_url,
            session_ttl=self.session_ttl,
            oauth_state_ttl=self.oauth_state_ttl,
            realtime_ticket_ttl=self.realtime_ticket_ttl,
            recent_authentication_window=self.recent_authentication_window,
            operator_bootstrap_emails=self.operator_bootstrap_emails,
        )
