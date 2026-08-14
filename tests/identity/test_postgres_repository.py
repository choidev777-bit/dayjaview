from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.identity import GoogleIdentity, Role, SavedType
from packages.identity.models import (
    OAuthStateRecord,
    RealtimeTicketRecord,
    SavedRecord,
    SessionRecord,
)
from packages.identity.postgres import PostgresIdentityRepository

TEST_DSN = os.environ.get("IDENTITY_TEST_DSN")
MIGRATION = (
    Path(__file__).resolve().parents[2] / "infra/migrations/0001_identity_library.sql"
)
NOW = datetime(2026, 8, 14, 1, 0, tzinfo=UTC)

pytestmark = pytest.mark.skipif(
    TEST_DSN is None,
    reason="IDENTITY_TEST_DSN의 disposable PostgreSQL 16이 필요합니다.",
)


@pytest.fixture()
def repository():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(TEST_DSN, autocommit=True) as admin:
        admin.execute("DROP SCHEMA IF EXISTS identity CASCADE")
        admin.execute("DROP SCHEMA IF EXISTS library CASCADE")
        admin.execute(MIGRATION.read_text(encoding="utf-8"))
    connection = psycopg.connect(TEST_DSN)
    try:
        yield PostgresIdentityRepository(connection)
    finally:
        connection.close()


def _hash(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _user(repository: PostgresIdentityRepository, seed: str = "user-1"):
    return repository.upsert_user(
        proposed_user_id=f"usr_{seed}",
        identity=GoogleIdentity(
            subject=f"google-{seed}",
            display_name="테스트 사용자",
            email=f"{seed}@example.com",
            email_verified=True,
        ),
        now=NOW,
    )


def test_user_upsert_roles_and_reauthentication(repository) -> None:
    created = _user(repository)
    assert created.user_id == "usr_user-1"
    assert repository.get_user_by_subject("google-user-1") == created
    assert repository.get_roles(created.user_id) == frozenset({Role.USER})

    later = NOW + timedelta(hours=1)
    updated = repository.upsert_user(
        proposed_user_id="usr_ignored",
        identity=GoogleIdentity(
            subject="google-user-1",
            display_name="이름 변경",
            email="user-1@example.com",
            email_verified=True,
        ),
        now=later,
    )
    assert updated.user_id == created.user_id
    assert updated.display_name == "이름 변경"
    assert updated.last_authenticated_at == later

    repository.add_role(created.user_id, Role.OPERATOR)
    assert repository.get_roles(created.user_id) == frozenset(
        {Role.USER, Role.OPERATOR}
    )
    with pytest.raises(KeyError):
        repository.add_role("usr_missing", Role.OPERATOR)


def test_oauth_state_is_consumed_exactly_once(repository) -> None:
    record = OAuthStateRecord(
        state_hash=_hash("state"),
        browser_nonce_hash=_hash("nonce"),
        return_to="/today",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    repository.store_oauth_state(record)

    wrong_nonce = repository.consume_oauth_state(
        state_hash=record.state_hash,
        browser_nonce_hash=_hash("other"),
        now=NOW + timedelta(minutes=1),
    )
    assert wrong_nonce is None

    consumed = repository.consume_oauth_state(
        state_hash=record.state_hash,
        browser_nonce_hash=record.browser_nonce_hash,
        now=NOW + timedelta(minutes=1),
    )
    assert consumed is not None
    assert consumed.return_to == "/today"

    replayed = repository.consume_oauth_state(
        state_hash=record.state_hash,
        browser_nonce_hash=record.browser_nonce_hash,
        now=NOW + timedelta(minutes=2),
    )
    assert replayed is None


def test_sessions_and_realtime_tickets(repository) -> None:
    user = _user(repository)
    session = SessionRecord(
        token_hash=_hash("session"),
        user_id=user.user_id,
        csrf_token_hash=_hash("csrf"),
        created_at=NOW,
        expires_at=NOW + timedelta(hours=8),
        authenticated_at=NOW,
    )
    repository.store_session(session)
    assert repository.get_session(session.token_hash) == session

    ticket = RealtimeTicketRecord(
        ticket_hash=_hash("ticket"),
        session_token_hash=session.token_hash,
        user_id=user.user_id,
        origin="https://dayjaview.vercel.app",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    repository.store_realtime_ticket(ticket)

    wrong_origin = repository.consume_realtime_ticket(
        ticket_hash=ticket.ticket_hash,
        origin="https://evil.example",
        now=NOW + timedelta(seconds=5),
    )
    assert wrong_origin is None

    consumed = repository.consume_realtime_ticket(
        ticket_hash=ticket.ticket_hash,
        origin=ticket.origin,
        now=NOW + timedelta(seconds=5),
    )
    assert consumed is not None and consumed.consumed_at is not None
    assert (
        repository.consume_realtime_ticket(
            ticket_hash=ticket.ticket_hash,
            origin=ticket.origin,
            now=NOW + timedelta(seconds=6),
        )
        is None
    )

    expired = RealtimeTicketRecord(
        ticket_hash=_hash("ticket-expired"),
        session_token_hash=session.token_hash,
        user_id=user.user_id,
        origin=ticket.origin,
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=1),
    )
    repository.store_realtime_ticket(expired)
    assert (
        repository.consume_realtime_ticket(
            ticket_hash=expired.ticket_hash,
            origin=ticket.origin,
            now=NOW + timedelta(seconds=30),
        )
        is None
    )

    repository.revoke_session(session.token_hash, now=NOW + timedelta(hours=1))
    revoked = repository.get_session(session.token_hash)
    assert revoked is not None
    assert revoked.revoked_at == NOW + timedelta(hours=1)


def test_saved_library_upsert_order_and_account_deletion(repository) -> None:
    user = _user(repository)
    first = SavedRecord(
        user_id=user.user_id,
        saved_type=SavedType.THEME,
        target_id="thm_1",
        display_name_snapshot="테마 1",
        saved_at=NOW,
    )
    second = SavedRecord(
        user_id=user.user_id,
        saved_type=SavedType.STOCK,
        target_id="stk_1",
        display_name_snapshot="종목 1",
        saved_at=NOW + timedelta(minutes=1),
    )
    assert repository.upsert_saved(first) == first
    assert repository.upsert_saved(second) == second
    duplicate = SavedRecord(
        user_id=user.user_id,
        saved_type=SavedType.THEME,
        target_id="thm_1",
        display_name_snapshot="나중 이름",
        saved_at=NOW + timedelta(minutes=5),
    )
    # 이미 저장된 항목은 원래 저장 시점을 유지한다
    assert repository.upsert_saved(duplicate) == first

    everything = repository.list_saved(user_id=user.user_id, saved_type=None)
    assert [record.target_id for record in everything] == ["stk_1", "thm_1"]
    themes_only = repository.list_saved(
        user_id=user.user_id,
        saved_type=SavedType.THEME,
    )
    assert [record.target_id for record in themes_only] == ["thm_1"]

    other = _user(repository, seed="user-2")
    assert repository.list_saved(user_id=other.user_id, saved_type=None) == ()

    deleted = repository.delete_saved(
        user_id=user.user_id,
        saved_type=SavedType.THEME,
        target_id="thm_1",
    )
    assert deleted == first

    assert repository.delete_account(user.user_id) is True
    assert repository.delete_account(user.user_id) is False
    assert repository.get_user(user.user_id) is None
    assert repository.list_saved(user_id=user.user_id, saved_type=None) == ()
    assert repository.get_session(_hash("session")) is None
