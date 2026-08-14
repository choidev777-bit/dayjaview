from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from threading import RLock
from typing import Protocol

from .models import (
    GoogleIdentity,
    OAuthStateRecord,
    RealtimeTicketRecord,
    Role,
    SavedRecord,
    SavedType,
    SessionRecord,
    User,
)
from .security import constant_time_equal


class IdentityRepository(Protocol):
    def store_oauth_state(self, record: OAuthStateRecord) -> None: ...

    def consume_oauth_state(
        self,
        *,
        state_hash: str,
        browser_nonce_hash: str,
        now: datetime,
    ) -> OAuthStateRecord | None: ...

    def get_user_by_subject(self, google_subject: str) -> User | None: ...

    def get_user(self, user_id: str) -> User | None: ...

    def upsert_user(
        self,
        *,
        proposed_user_id: str,
        identity: GoogleIdentity,
        now: datetime,
    ) -> User: ...

    def add_role(self, user_id: str, role: Role) -> None: ...

    def get_roles(self, user_id: str) -> frozenset[Role]: ...

    def store_session(self, record: SessionRecord) -> None: ...

    def get_session(self, token_hash: str) -> SessionRecord | None: ...

    def revoke_session(self, token_hash: str, *, now: datetime) -> None: ...

    def store_realtime_ticket(self, record: RealtimeTicketRecord) -> None: ...

    def consume_realtime_ticket(
        self,
        *,
        ticket_hash: str,
        session_token_hash: str,
        user_id: str,
        origin: str,
        now: datetime,
    ) -> RealtimeTicketRecord | None: ...

    def upsert_saved(self, record: SavedRecord) -> SavedRecord: ...

    def delete_saved(
        self,
        *,
        user_id: str,
        saved_type: SavedType,
        target_id: str,
    ) -> SavedRecord | None: ...

    def list_saved(
        self,
        *,
        user_id: str,
        saved_type: SavedType | None,
    ) -> tuple[SavedRecord, ...]: ...

    def delete_account(self, user_id: str) -> bool: ...


class InMemoryIdentityRepository:
    """Thread-safe fixture repository with database-like atomic operations."""

    def __init__(self) -> None:
        self._oauth_states: dict[str, OAuthStateRecord] = {}
        self._users: dict[str, User] = {}
        self._user_ids_by_subject: dict[str, str] = {}
        self._roles: dict[str, set[Role]] = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._tickets: dict[str, RealtimeTicketRecord] = {}
        self._saved: dict[tuple[str, SavedType, str], SavedRecord] = {}
        self._lock = RLock()

    def store_oauth_state(self, record: OAuthStateRecord) -> None:
        with self._lock:
            self._oauth_states[record.state_hash] = record

    def consume_oauth_state(
        self,
        *,
        state_hash: str,
        browser_nonce_hash: str,
        now: datetime,
    ) -> OAuthStateRecord | None:
        with self._lock:
            record = self._oauth_states.get(state_hash)
            if record is None or record.consumed_at is not None or record.expires_at <= now:
                return None
            if not constant_time_equal(record.browser_nonce_hash, browser_nonce_hash):
                return None
            consumed = replace(record, consumed_at=now)
            self._oauth_states[state_hash] = consumed
            return consumed

    def get_user_by_subject(self, google_subject: str) -> User | None:
        with self._lock:
            user_id = self._user_ids_by_subject.get(google_subject)
            return None if user_id is None else self._users.get(user_id)

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def upsert_user(
        self,
        *,
        proposed_user_id: str,
        identity: GoogleIdentity,
        now: datetime,
    ) -> User:
        with self._lock:
            existing_id = self._user_ids_by_subject.get(identity.subject)
            if existing_id is not None:
                existing = self._users[existing_id]
                updated = replace(
                    existing,
                    display_name=identity.display_name,
                    email=identity.email,
                    email_verified=identity.email_verified,
                    last_authenticated_at=now,
                )
                self._users[existing_id] = updated
                return updated
            if proposed_user_id in self._users:
                raise ValueError("proposed user ID already exists")
            user = User(
                user_id=proposed_user_id,
                google_subject=identity.subject,
                display_name=identity.display_name,
                email=identity.email,
                email_verified=identity.email_verified,
                created_at=now,
                last_authenticated_at=now,
            )
            self._users[user.user_id] = user
            self._user_ids_by_subject[user.google_subject] = user.user_id
            self._roles[user.user_id] = {Role.USER}
            return user

    def add_role(self, user_id: str, role: Role) -> None:
        with self._lock:
            if user_id not in self._users:
                raise KeyError(user_id)
            self._roles.setdefault(user_id, {Role.USER}).add(role)

    def get_roles(self, user_id: str) -> frozenset[Role]:
        with self._lock:
            return frozenset(self._roles.get(user_id, set()))

    def store_session(self, record: SessionRecord) -> None:
        with self._lock:
            self._sessions[record.token_hash] = record

    def get_session(self, token_hash: str) -> SessionRecord | None:
        with self._lock:
            return self._sessions.get(token_hash)

    def revoke_session(self, token_hash: str, *, now: datetime) -> None:
        with self._lock:
            record = self._sessions.get(token_hash)
            if record is not None and record.revoked_at is None:
                self._sessions[token_hash] = replace(record, revoked_at=now)

    def store_realtime_ticket(self, record: RealtimeTicketRecord) -> None:
        with self._lock:
            self._tickets[record.ticket_hash] = record

    def consume_realtime_ticket(
        self,
        *,
        ticket_hash: str,
        session_token_hash: str,
        user_id: str,
        origin: str,
        now: datetime,
    ) -> RealtimeTicketRecord | None:
        with self._lock:
            record = self._tickets.get(ticket_hash)
            if record is None or record.consumed_at is not None or record.expires_at <= now:
                return None
            matches = (
                constant_time_equal(record.session_token_hash, session_token_hash)
                and record.user_id == user_id
                and constant_time_equal(record.origin, origin)
            )
            if not matches:
                return None
            consumed = replace(record, consumed_at=now)
            self._tickets[ticket_hash] = consumed
            return consumed

    def upsert_saved(self, record: SavedRecord) -> SavedRecord:
        key = (record.user_id, record.saved_type, record.target_id)
        with self._lock:
            existing = self._saved.get(key)
            if existing is not None:
                return existing
            self._saved[key] = record
            return record

    def delete_saved(
        self,
        *,
        user_id: str,
        saved_type: SavedType,
        target_id: str,
    ) -> SavedRecord | None:
        with self._lock:
            return self._saved.pop((user_id, saved_type, target_id), None)

    def list_saved(
        self,
        *,
        user_id: str,
        saved_type: SavedType | None,
    ) -> tuple[SavedRecord, ...]:
        with self._lock:
            records = [
                record
                for record in self._saved.values()
                if record.user_id == user_id
                and (saved_type is None or record.saved_type is saved_type)
            ]
        records.sort(key=lambda item: (item.target_id, item.saved_type.value))
        records.sort(key=lambda item: item.saved_at, reverse=True)
        return tuple(records)

    def delete_account(self, user_id: str) -> bool:
        with self._lock:
            user = self._users.pop(user_id, None)
            if user is None:
                return False
            self._user_ids_by_subject.pop(user.google_subject, None)
            self._roles.pop(user_id, None)
            self._sessions = {
                key: value for key, value in self._sessions.items() if value.user_id != user_id
            }
            self._tickets = {
                key: value for key, value in self._tickets.items() if value.user_id != user_id
            }
            self._saved = {
                key: value for key, value in self._saved.items() if value.user_id != user_id
            }
            return True

    # Fixture-only evidence helpers. They expose counts, never raw tokens.
    def contains_user_subject(self, google_subject: str) -> bool:
        with self._lock:
            return google_subject in self._user_ids_by_subject

    def session_count_for_user(self, user_id: str) -> int:
        with self._lock:
            return sum(record.user_id == user_id for record in self._sessions.values())

    def saved_count_for_user(self, user_id: str) -> int:
        with self._lock:
            return sum(record.user_id == user_id for record in self._saved.values())
