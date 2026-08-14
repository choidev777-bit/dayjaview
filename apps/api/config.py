from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from identity import IdentityPolicy
    from identity.security import parse_operator_bootstrap_emails
else:
    from packages.identity import IdentityPolicy, parse_operator_bootstrap_emails


@dataclass(frozen=True, slots=True)
class ApiSettings:
    app_base_url: str = "https://dayjaview.vercel.app"
    schema_version: str = "2026-08-14.1"
    session_ttl: timedelta = timedelta(hours=8)
    oauth_state_ttl: timedelta = timedelta(minutes=10)
    realtime_ticket_ttl: timedelta = timedelta(seconds=30)
    recent_authentication_window: timedelta = timedelta(minutes=10)
    operator_bootstrap_emails: frozenset[str] = frozenset()

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
