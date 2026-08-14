from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    USER = "USER"
    HISTORICAL_PILOT = "HISTORICAL_PILOT"
    OPERATOR = "OPERATOR"


class SavedType(StrEnum):
    THEME = "THEME"
    STOCK = "STOCK"
    EVENT = "EVENT"


class Availability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str
    display_name: str
    email: str | None = None
    email_verified: bool = False


@dataclass(frozen=True, slots=True)
class User:
    user_id: str
    google_subject: str
    display_name: str
    email: str | None
    email_verified: bool
    created_at: datetime
    last_authenticated_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthStateRecord:
    state_hash: str
    browser_nonce_hash: str
    return_to: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionRecord:
    token_hash: str
    user_id: str
    csrf_token_hash: str
    created_at: datetime
    expires_at: datetime
    authenticated_at: datetime
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RealtimeTicketRecord:
    ticket_hash: str
    session_token_hash: str
    user_id: str
    origin: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SavedRecord:
    user_id: str
    saved_type: SavedType
    target_id: str
    display_name_snapshot: str
    saved_at: datetime


@dataclass(frozen=True, slots=True)
class SavedCurrentState:
    event_id: str
    event_state: str
    weighted_return: float | None
    data_status: str
    as_of: datetime


@dataclass(frozen=True, slots=True)
class TargetRecord:
    saved_type: SavedType
    target_id: str
    display_name: str
    availability: Availability = Availability.AVAILABLE
    unavailable_reason: str | None = None
    current_state: SavedCurrentState | None = None
    required_role: Role | None = None


@dataclass(frozen=True, slots=True)
class SessionPrincipal:
    session_token_hash: str
    user: User
    roles: frozenset[Role]
    authenticated_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthLoginStart:
    authorization_url: str
    browser_nonce: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginCompletion:
    session_token: str
    csrf_token: str
    expires_at: datetime
    return_to: str
    user: User
    roles: frozenset[Role]


@dataclass(frozen=True, slots=True)
class RealtimeTicket:
    ticket: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SavedItem:
    saved_type: SavedType
    target_id: str
    display_name: str
    saved_at: datetime
    availability: Availability
    unavailable_reason: str | None
    current_state: SavedCurrentState | None


@dataclass(frozen=True, slots=True)
class SavedPage:
    items: tuple[SavedItem, ...]
    next_cursor: str | None
    has_more: bool
    limit: int


@dataclass(frozen=True, slots=True)
class SavedMutation:
    saved_type: SavedType
    target_id: str
    saved: bool
    saved_at: datetime | None


@dataclass(frozen=True, slots=True)
class RuntimeServiceStatus:
    """Internal runtime state; diagnostic_context is never externally projected."""

    name: str
    status: str
    last_succeeded_at: datetime | None
    error_code: str | None
    diagnostic_context: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class RuntimeOperatorStatus:
    deployment_version: str
    commit: str
    started_at: datetime
    services: tuple[RuntimeServiceStatus, ...]
    internal_context: Mapping[str, object] = field(default_factory=dict, repr=False)
