from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime

SESSION_COOKIE = "__Host-dayjaview_session"
CSRF_COOKIE = "__Host-dayjaview_csrf"
OAUTH_STATE_COOKIE = "__Host-dayjaview_oauth_state"


def session_cookie(value: str, *, now: datetime, expires_at: datetime) -> str:
    return _cookie(
        SESSION_COOKIE,
        value,
        now=now,
        expires_at=expires_at,
        http_only=True,
        same_site="Lax",
    )


def csrf_cookie(value: str, *, now: datetime, expires_at: datetime) -> str:
    return _cookie(
        CSRF_COOKIE,
        value,
        now=now,
        expires_at=expires_at,
        http_only=False,
        same_site="Strict",
    )


def oauth_state_cookie(value: str, *, now: datetime, expires_at: datetime) -> str:
    return _cookie(
        OAUTH_STATE_COOKIE,
        value,
        now=now,
        expires_at=expires_at,
        http_only=True,
        same_site="Lax",
    )


def expire_session_cookie() -> str:
    return _expired_cookie(SESSION_COOKIE, http_only=True, same_site="Lax")


def expire_csrf_cookie() -> str:
    return _expired_cookie(CSRF_COOKIE, http_only=False, same_site="Strict")


def expire_oauth_state_cookie() -> str:
    return _expired_cookie(OAUTH_STATE_COOKIE, http_only=True, same_site="Lax")


def _cookie(
    name: str,
    value: str,
    *,
    now: datetime,
    expires_at: datetime,
    http_only: bool,
    same_site: str,
) -> str:
    max_age = max(0, int((expires_at - now).total_seconds()))
    attributes = [
        f"{name}={value}",
        "Path=/",
        "Secure",
        f"SameSite={same_site}",
        f"Max-Age={max_age}",
        f"Expires={_http_date(expires_at)}",
    ]
    if http_only:
        attributes.insert(3, "HttpOnly")
    return "; ".join(attributes)


def _expired_cookie(name: str, *, http_only: bool, same_site: str) -> str:
    attributes = [
        f"{name}=",
        "Path=/",
        "Secure",
        f"SameSite={same_site}",
        "Max-Age=0",
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT",
    ]
    if http_only:
        attributes.insert(3, "HttpOnly")
    return "; ".join(attributes)


def _http_date(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("cookie expiry must be timezone-aware")
    return format_datetime(value.astimezone(UTC), usegmt=True)
