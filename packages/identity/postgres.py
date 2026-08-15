"""PostgreSQL 16 identity/library repository (0001_identity_library.sql 기준).

InMemoryIdentityRepository와 같은 의미를 SQL로 구현한다. 원문 토큰은 절대
저장하지 않으며(해시만), 소비성 레코드(oauth state·realtime ticket)는 행
잠금으로 1회 소비를 보장한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

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

# 만료 시각으로 정리하는 테이블. 문자열 보간에는 이 상수만 들어간다.
_EXPIRING_TABLES = (
    "identity.oauth_states",
    "identity.sessions",
    "identity.realtime_tickets",
)


class DbCursor(Protocol):
    rowcount: int

    def execute(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> object: ...

    def fetchone(self) -> Sequence[Any] | None: ...

    def fetchall(self) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


class DbConnection(Protocol):
    def cursor(self) -> DbCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class PostgresIdentityRepository:
    """단일 connection 기반 IdentityRepository 구현."""

    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def _execute_only(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> None:
        cursor = self._connection.cursor()
        cursor.execute(query, params)
        cursor.close()

    def _fetch_one(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> Sequence[Any] | None:
        cursor = self._connection.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        cursor.close()
        return row

    def _fetch_all(
        self,
        query: str,
        params: Sequence[object] | None = None,
    ) -> Sequence[Sequence[Any]]:
        cursor = self._connection.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows

    def store_oauth_state(self, record: OAuthStateRecord) -> None:
        try:
            self._execute_only(
                """
                INSERT INTO identity.oauth_states
                    (state_hash, browser_nonce_hash, return_to,
                     created_at, expires_at, consumed_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (state_hash) DO UPDATE SET
                    browser_nonce_hash = EXCLUDED.browser_nonce_hash,
                    return_to = EXCLUDED.return_to,
                    created_at = EXCLUDED.created_at,
                    expires_at = EXCLUDED.expires_at,
                    consumed_at = EXCLUDED.consumed_at
                """,
                (
                    record.state_hash,
                    record.browser_nonce_hash,
                    record.return_to,
                    record.created_at,
                    record.expires_at,
                    record.consumed_at,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def consume_oauth_state(
        self,
        *,
        state_hash: str,
        browser_nonce_hash: str,
        now: datetime,
    ) -> OAuthStateRecord | None:
        try:
            row = self._fetch_one(
                """
                SELECT state_hash, browser_nonce_hash, return_to,
                       created_at, expires_at, consumed_at
                FROM identity.oauth_states
                WHERE state_hash = %s
                FOR UPDATE
                """,
                (state_hash,),
            )
            if row is None:
                self._connection.rollback()
                return None
            record = OAuthStateRecord(
                state_hash=str(row[0]),
                browser_nonce_hash=str(row[1]),
                return_to=str(row[2]),
                created_at=row[3],
                expires_at=row[4],
                consumed_at=row[5],
            )
            if record.consumed_at is not None or record.expires_at <= now:
                self._connection.rollback()
                return None
            if not constant_time_equal(
                record.browser_nonce_hash,
                browser_nonce_hash,
            ):
                self._connection.rollback()
                return None
            self._execute_only(
                """
                UPDATE identity.oauth_states
                SET consumed_at = %s
                WHERE state_hash = %s
                """,
                (now, state_hash),
            )
            self._connection.commit()
            return OAuthStateRecord(
                state_hash=record.state_hash,
                browser_nonce_hash=record.browser_nonce_hash,
                return_to=record.return_to,
                created_at=record.created_at,
                expires_at=record.expires_at,
                consumed_at=now,
            )
        except Exception:
            self._connection.rollback()
            raise

    def _user_from_row(self, row: Sequence[Any]) -> User:
        return User(
            user_id=str(row[0]),
            google_subject=str(row[1]),
            display_name=str(row[2]),
            email=None if row[3] is None else str(row[3]),
            email_verified=bool(row[4]),
            created_at=row[5],
            last_authenticated_at=row[6],
        )

    _USER_COLUMNS = (
        "user_id, google_subject, display_name, email, email_verified, "
        "created_at, last_authenticated_at"
    )

    def get_user_by_subject(self, google_subject: str) -> User | None:
        row = self._fetch_one(
            f"SELECT {self._USER_COLUMNS} FROM identity.users "
            "WHERE google_subject = %s",
            (google_subject,),
        )
        self._connection.rollback()
        return None if row is None else self._user_from_row(row)

    def get_user(self, user_id: str) -> User | None:
        row = self._fetch_one(
            f"SELECT {self._USER_COLUMNS} FROM identity.users WHERE user_id = %s",
            (user_id,),
        )
        self._connection.rollback()
        return None if row is None else self._user_from_row(row)

    def upsert_user(
        self,
        *,
        proposed_user_id: str,
        identity: GoogleIdentity,
        now: datetime,
    ) -> User:
        try:
            row = self._fetch_one(
                f"SELECT {self._USER_COLUMNS} FROM identity.users "
                "WHERE google_subject = %s FOR UPDATE",
                (identity.subject,),
            )
            if row is not None:
                updated = self._fetch_one(
                    f"""
                    UPDATE identity.users
                    SET display_name = %s,
                        email = %s,
                        email_verified = %s,
                        last_authenticated_at = %s
                    WHERE google_subject = %s
                    RETURNING {self._USER_COLUMNS}
                    """,
                    (
                        identity.display_name,
                        identity.email,
                        identity.email_verified,
                        now,
                        identity.subject,
                    ),
                )
                assert updated is not None
                self._connection.commit()
                return self._user_from_row(updated)

            existing_id = self._fetch_one(
                "SELECT user_id FROM identity.users WHERE user_id = %s",
                (proposed_user_id,),
            )
            if existing_id is not None:
                self._connection.rollback()
                raise ValueError("proposed user ID already exists")
            inserted = self._fetch_one(
                f"""
                INSERT INTO identity.users
                    (user_id, google_subject, display_name, email,
                     email_verified, created_at, last_authenticated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {self._USER_COLUMNS}
                """,
                (
                    proposed_user_id,
                    identity.subject,
                    identity.display_name,
                    identity.email,
                    identity.email_verified,
                    now,
                    now,
                ),
            )
            assert inserted is not None
            self._execute_only(
                """
                INSERT INTO identity.user_roles (user_id, role, granted_at)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (proposed_user_id, Role.USER.value, now),
            )
            self._connection.commit()
            return self._user_from_row(inserted)
        except Exception:
            self._connection.rollback()
            raise

    def add_role(self, user_id: str, role: Role) -> None:
        try:
            existing = self._fetch_one(
                "SELECT user_id FROM identity.users WHERE user_id = %s",
                (user_id,),
            )
            if existing is None:
                self._connection.rollback()
                raise KeyError(user_id)
            self._execute_only(
                """
                INSERT INTO identity.user_roles (user_id, role, granted_at)
                VALUES (%s, %s, now())
                ON CONFLICT DO NOTHING
                """,
                (user_id, role.value),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def get_roles(self, user_id: str) -> frozenset[Role]:
        rows = self._fetch_all(
            "SELECT role FROM identity.user_roles WHERE user_id = %s",
            (user_id,),
        )
        self._connection.rollback()
        return frozenset(Role(str(row[0])) for row in rows)

    def store_session(self, record: SessionRecord) -> None:
        try:
            self._execute_only(
                """
                INSERT INTO identity.sessions
                    (token_hash, user_id, csrf_token_hash,
                     created_at, expires_at, authenticated_at, revoked_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (token_hash) DO UPDATE SET
                    csrf_token_hash = EXCLUDED.csrf_token_hash,
                    expires_at = EXCLUDED.expires_at,
                    authenticated_at = EXCLUDED.authenticated_at,
                    revoked_at = EXCLUDED.revoked_at
                """,
                (
                    record.token_hash,
                    record.user_id,
                    record.csrf_token_hash,
                    record.created_at,
                    record.expires_at,
                    record.authenticated_at,
                    record.revoked_at,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def get_session(self, token_hash: str) -> SessionRecord | None:
        row = self._fetch_one(
            """
            SELECT token_hash, user_id, csrf_token_hash,
                   created_at, expires_at, authenticated_at, revoked_at
            FROM identity.sessions
            WHERE token_hash = %s
            """,
            (token_hash,),
        )
        self._connection.rollback()
        if row is None:
            return None
        return SessionRecord(
            token_hash=str(row[0]),
            user_id=str(row[1]),
            csrf_token_hash=str(row[2]),
            created_at=row[3],
            expires_at=row[4],
            authenticated_at=row[5],
            revoked_at=row[6],
        )

    def revoke_session(self, token_hash: str, *, now: datetime) -> None:
        try:
            self._execute_only(
                """
                UPDATE identity.sessions
                SET revoked_at = %s
                WHERE token_hash = %s AND revoked_at IS NULL
                """,
                (now, token_hash),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def store_realtime_ticket(self, record: RealtimeTicketRecord) -> None:
        try:
            self._execute_only(
                """
                INSERT INTO identity.realtime_tickets
                    (ticket_hash, session_token_hash, user_id, origin,
                     created_at, expires_at, consumed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticket_hash) DO UPDATE SET
                    origin = EXCLUDED.origin,
                    expires_at = EXCLUDED.expires_at,
                    consumed_at = EXCLUDED.consumed_at
                """,
                (
                    record.ticket_hash,
                    record.session_token_hash,
                    record.user_id,
                    record.origin,
                    record.created_at,
                    record.expires_at,
                    record.consumed_at,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def consume_realtime_ticket(
        self,
        *,
        ticket_hash: str,
        origin: str,
        now: datetime,
    ) -> RealtimeTicketRecord | None:
        try:
            row = self._fetch_one(
                """
                SELECT ticket_hash, session_token_hash, user_id, origin,
                       created_at, expires_at, consumed_at
                FROM identity.realtime_tickets
                WHERE ticket_hash = %s
                FOR UPDATE
                """,
                (ticket_hash,),
            )
            if row is None:
                self._connection.rollback()
                return None
            record = RealtimeTicketRecord(
                ticket_hash=str(row[0]),
                session_token_hash=str(row[1]),
                user_id=str(row[2]),
                origin=str(row[3]),
                created_at=row[4],
                expires_at=row[5],
                consumed_at=row[6],
            )
            if record.consumed_at is not None or record.expires_at <= now:
                self._connection.rollback()
                return None
            if not constant_time_equal(record.origin, origin):
                self._connection.rollback()
                return None
            self._execute_only(
                """
                UPDATE identity.realtime_tickets
                SET consumed_at = %s
                WHERE ticket_hash = %s
                """,
                (now, ticket_hash),
            )
            self._connection.commit()
            return RealtimeTicketRecord(
                ticket_hash=record.ticket_hash,
                session_token_hash=record.session_token_hash,
                user_id=record.user_id,
                origin=record.origin,
                created_at=record.created_at,
                expires_at=record.expires_at,
                consumed_at=now,
            )
        except Exception:
            self._connection.rollback()
            raise

    def upsert_saved(self, record: SavedRecord) -> SavedRecord:
        try:
            inserted = self._fetch_one(
                """
                INSERT INTO library.saved_items
                    (user_id, saved_type, target_id,
                     display_name_snapshot, saved_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, saved_type, target_id) DO NOTHING
                RETURNING user_id, saved_type, target_id,
                          display_name_snapshot, saved_at
                """,
                (
                    record.user_id,
                    record.saved_type.value,
                    record.target_id,
                    record.display_name_snapshot,
                    record.saved_at,
                ),
            )
            if inserted is not None:
                self._connection.commit()
                return record
            existing = self._fetch_one(
                """
                SELECT user_id, saved_type, target_id,
                       display_name_snapshot, saved_at
                FROM library.saved_items
                WHERE user_id = %s AND saved_type = %s AND target_id = %s
                """,
                (record.user_id, record.saved_type.value, record.target_id),
            )
            self._connection.commit()
            assert existing is not None
            return self._saved_from_row(existing)
        except Exception:
            self._connection.rollback()
            raise

    @staticmethod
    def _saved_from_row(row: Sequence[Any]) -> SavedRecord:
        return SavedRecord(
            user_id=str(row[0]),
            saved_type=SavedType(str(row[1])),
            target_id=str(row[2]),
            display_name_snapshot=str(row[3]),
            saved_at=row[4],
        )

    def delete_saved(
        self,
        *,
        user_id: str,
        saved_type: SavedType,
        target_id: str,
    ) -> SavedRecord | None:
        try:
            row = self._fetch_one(
                """
                DELETE FROM library.saved_items
                WHERE user_id = %s AND saved_type = %s AND target_id = %s
                RETURNING user_id, saved_type, target_id,
                          display_name_snapshot, saved_at
                """,
                (user_id, saved_type.value, target_id),
            )
            self._connection.commit()
            return None if row is None else self._saved_from_row(row)
        except Exception:
            self._connection.rollback()
            raise

    def list_saved(
        self,
        *,
        user_id: str,
        saved_type: SavedType | None,
    ) -> tuple[SavedRecord, ...]:
        query = (
            "SELECT user_id, saved_type, target_id, display_name_snapshot, "
            "saved_at FROM library.saved_items WHERE user_id = %s"
        )
        params: list[object] = [user_id]
        if saved_type is not None:
            query += " AND saved_type = %s"
            params.append(saved_type.value)
        query += " ORDER BY saved_at DESC, target_id, saved_type"
        rows = self._fetch_all(query, params)
        self._connection.rollback()
        return tuple(self._saved_from_row(row) for row in rows)

    def delete_account(self, user_id: str) -> bool:
        try:
            row = self._fetch_one(
                "DELETE FROM identity.users WHERE user_id = %s RETURNING user_id",
                (user_id,),
            )
            self._connection.commit()
            return row is not None
        except Exception:
            self._connection.rollback()
            raise

    def purge_expired(self, *, now: datetime) -> int:
        """만료된 state·session·ticket을 지운다. 남겨두면 무한히 쌓인다.

        세 테이블 모두 `expires_at` 인덱스가 있다(`0001_identity_library.sql`).
        """

        removed = 0
        try:
            for table in _EXPIRING_TABLES:
                row = self._fetch_one(
                    f"WITH purged AS (DELETE FROM {table} WHERE expires_at <= %s RETURNING 1)"
                    " SELECT count(*) FROM purged",
                    (now,),
                )
                removed += int(row[0]) if row is not None else 0
            self._connection.commit()
            return removed
        except Exception:
            self._connection.rollback()
            raise
