"""Driver-neutral PostgreSQL 16 Event writer and outbox implementation."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Protocol, cast

from .models import (
    CanonicalEvent,
    CommandReceipt,
    EventStateLog,
    EventWriteResult,
)
from .outbox import OutboxMessage, OutboxRecord, OutboxStatus
from .store import (
    ConcurrentEventWriteError,
    EventIdentityConflictError,
    EventTransaction,
    IdempotencyConflictError,
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


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_object(value: object, label: str) -> dict[str, object]:
    decoded: object
    if isinstance(value, str):
        decoded = json.loads(value)
    else:
        decoded = value
    if not isinstance(decoded, dict):
        raise TypeError(f"{label} JSON은 object여야 합니다")
    return cast(dict[str, object], decoded)


def _required_row(cursor: DbCursor, operation: str) -> Sequence[Any]:
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"{operation}: PostgreSQL이 row를 반환하지 않았습니다")
    return row


def _outbox_record(row: Sequence[Any]) -> OutboxRecord:
    message = OutboxMessage.from_dict(_json_object(row[0], "outbox message"))
    return OutboxRecord(
        message=message,
        status=OutboxStatus(str(row[1])),
        attempts=int(row[2]),
        available_at=cast(datetime, row[3]),
        claimed_until=cast(datetime | None, row[4]),
        published_at=cast(datetime | None, row[5]),
        last_error=None if row[6] is None else str(row[6]),
    )


class PostgresEventStore:
    """One PostgreSQL connection facade; Event writes take an advisory lock."""

    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    @contextmanager
    def transaction(self) -> Iterator[EventTransaction]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("dayjaview:event-writer",),
            )
            yield _PostgresEventTransaction(cursor)
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()
        finally:
            cursor.close()

    def read_event(self, event_id: str) -> CanonicalEvent | None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT event_json FROM event.events WHERE event_id = %s",
                (event_id,),
            )
            row = cursor.fetchone()
            return (
                None
                if row is None
                else CanonicalEvent.from_dict(_json_object(row[0], "Event"))
            )
        finally:
            cursor.close()

    def read_outbox(self, message_id: str) -> OutboxMessage | None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "SELECT message_json FROM event.outbox WHERE message_id = %s",
                (message_id,),
            )
            row = cursor.fetchone()
            return (
                None
                if row is None
                else OutboxMessage.from_dict(_json_object(row[0], "outbox message"))
            )
        finally:
            cursor.close()

    def claim_pending(
        self,
        *,
        now: datetime,
        limit: int,
        lease: timedelta,
    ) -> tuple[OutboxRecord, ...]:
        if limit < 1:
            raise ValueError("outbox claim limit은 1 이상이어야 합니다")
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                """
                SELECT message_id
                  FROM event.outbox
                 WHERE status <> 'PUBLISHED'
                   AND available_at <= %s
                   AND (
                       status = 'PENDING'
                       OR claimed_until IS NULL
                       OR claimed_until <= %s
                   )
                 ORDER BY available_at, message_id
                 FOR UPDATE SKIP LOCKED
                 LIMIT %s
                """,
                (now, now, limit),
            )
            message_ids = [str(row[0]) for row in cursor.fetchall()]
            records: list[OutboxRecord] = []
            for message_id in message_ids:
                cursor.execute(
                    """
                    UPDATE event.outbox
                       SET status = 'CLAIMED',
                           attempts = attempts + 1,
                           claimed_until = %s,
                           last_error = NULL
                     WHERE message_id = %s
                    RETURNING message_json, status, attempts, available_at,
                              claimed_until, published_at, last_error
                    """,
                    (now + lease, message_id),
                )
                records.append(_outbox_record(_required_row(cursor, "claim outbox")))
            self._connection.commit()
            return tuple(records)
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def mark_published(self, message_id: str, *, published_at: datetime) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE event.outbox
                   SET status = 'PUBLISHED', published_at = %s,
                       claimed_until = NULL, last_error = NULL
                 WHERE message_id = %s
                   AND status <> 'PUBLISHED'
                """,
                (published_at, message_id),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "SELECT 1 FROM event.outbox WHERE message_id = %s",
                    (message_id,),
                )
                if cursor.fetchone() is None:
                    raise KeyError(f"outbox message를 찾을 수 없습니다: {message_id}")
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def release_after_failure(
        self,
        message_id: str,
        *,
        retry_at: datetime,
        error: str,
    ) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE event.outbox
                   SET status = 'PENDING', available_at = %s,
                       claimed_until = NULL, last_error = %s
                 WHERE message_id = %s
                   AND status <> 'PUBLISHED'
                """,
                (retry_at, error, message_id),
            )
            if cursor.rowcount == 0:
                cursor.execute(
                    "SELECT 1 FROM event.outbox WHERE message_id = %s",
                    (message_id,),
                )
                if cursor.fetchone() is None:
                    raise KeyError(f"outbox message를 찾을 수 없습니다: {message_id}")
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            cursor.close()


class _PostgresEventTransaction:
    def __init__(self, cursor: DbCursor) -> None:
        self._cursor = cursor

    def get_receipt(self, message_id: str) -> CommandReceipt | None:
        self._cursor.execute(
            """
            SELECT command_fingerprint, event_id, source, source_sequence,
                   received_at, result_json
              FROM event.command_receipts
             WHERE message_id = %s
            """,
            (message_id,),
        )
        row = self._cursor.fetchone()
        if row is None:
            return None
        return CommandReceipt(
            message_id=message_id,
            command_fingerprint=str(row[0]),
            event_id=str(row[1]),
            source=str(row[2]),
            source_sequence=int(row[3]),
            received_at=cast(datetime, row[4]),
            result=EventWriteResult.from_dict(_json_object(row[5], "command result")),
        )

    def get_event(self, event_id: str) -> CanonicalEvent | None:
        self._cursor.execute(
            "SELECT event_json FROM event.events WHERE event_id = %s FOR UPDATE",
            (event_id,),
        )
        row = self._cursor.fetchone()
        return (
            None
            if row is None
            else CanonicalEvent.from_dict(_json_object(row[0], "Event"))
        )

    def get_event_by_identity(self, identity_key: str) -> CanonicalEvent | None:
        self._cursor.execute(
            """
            SELECT event_json
              FROM event.events
             WHERE identity_key = %s
             FOR UPDATE
            """,
            (identity_key,),
        )
        row = self._cursor.fetchone()
        return (
            None
            if row is None
            else CanonicalEvent.from_dict(_json_object(row[0], "Event"))
        )

    def latest_source_sequence(self, event_id: str, source: str) -> int | None:
        self._cursor.execute(
            """
            SELECT max(source_sequence)
              FROM event.command_receipts
             WHERE event_id = %s AND source = %s
            """,
            (event_id, source),
        )
        row = _required_row(self._cursor, "read source sequence")
        return None if row[0] is None else int(row[0])

    def insert_event(self, event: CanonicalEvent) -> None:
        self._cursor.execute(
            """
            INSERT INTO event.events (
                event_id, identity_key, market_date, canonical_theme_id,
                catalyst_key, lifecycle_status, reconciliation_status,
                state_version, classification_version, first_detected_at,
                changed_at, last_received_at, event_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            ON CONFLICT DO NOTHING
            """,
            (
                event.event_id,
                event.identity_key,
                event.market_date,
                event.canonical_theme_id,
                event.catalyst_key,
                event.lifecycle_status.value,
                event.reconciliation_status.value,
                event.state_version,
                event.classification.classification_version,
                event.first_detected_at,
                event.changed_at,
                event.last_received_at,
                _json(event.to_dict()),
            ),
        )
        if self._cursor.rowcount != 1:
            raise EventIdentityConflictError("Event ID 또는 identity가 이미 존재합니다")

    def update_event(
        self,
        event: CanonicalEvent,
        *,
        expected_state_version: int,
    ) -> None:
        self._cursor.execute(
            """
            UPDATE event.events
               SET lifecycle_status = %s,
                   reconciliation_status = %s,
                   state_version = %s,
                   classification_version = %s,
                   changed_at = %s,
                   last_received_at = %s,
                   event_json = %s::jsonb,
                   updated_at = now()
             WHERE event_id = %s AND state_version = %s
            """,
            (
                event.lifecycle_status.value,
                event.reconciliation_status.value,
                event.state_version,
                event.classification.classification_version,
                event.changed_at,
                event.last_received_at,
                _json(event.to_dict()),
                event.event_id,
                expected_state_version,
            ),
        )
        if self._cursor.rowcount != 1:
            raise ConcurrentEventWriteError(
                "Event stateVersion optimistic write가 충돌했습니다"
            )

    def append_state_log(self, state_log: EventStateLog) -> None:
        self._cursor.execute(
            """
            INSERT INTO event.state_logs (
                event_id, state_version, from_status, to_status,
                policy_version, reason, occurred_at, received_at,
                source, source_sequence, command_message_id, lineage
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            """,
            (
                state_log.event_id,
                state_log.state_version,
                None if state_log.from_status is None else state_log.from_status.value,
                state_log.to_status.value,
                state_log.policy_version,
                state_log.reason,
                state_log.occurred_at,
                state_log.received_at,
                state_log.source,
                state_log.source_sequence,
                state_log.command_message_id,
                _json([item.to_dict() for item in state_log.lineage]),
            ),
        )

    def save_receipt(self, receipt: CommandReceipt) -> None:
        self._cursor.execute(
            """
            INSERT INTO event.command_receipts (
                message_id, command_fingerprint, event_id, source,
                source_sequence, received_at, result_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (message_id) DO NOTHING
            """,
            (
                receipt.message_id,
                receipt.command_fingerprint,
                receipt.event_id,
                receipt.source,
                receipt.source_sequence,
                receipt.received_at,
                _json(receipt.result.to_dict()),
            ),
        )
        if self._cursor.rowcount != 1:
            self._cursor.execute(
                """
                SELECT command_fingerprint
                  FROM event.command_receipts
                 WHERE message_id = %s
                """,
                (receipt.message_id,),
            )
            row = _required_row(self._cursor, "read duplicate receipt")
            if str(row[0]) != receipt.command_fingerprint:
                raise IdempotencyConflictError(
                    "같은 message_id에 서로 다른 command가 저장되었습니다"
                )

    def enqueue_outbox(self, message: OutboxMessage) -> None:
        self._cursor.execute(
            """
            INSERT INTO event.outbox (
                message_id, aggregate_id, event_type, event_version,
                occurred_at, received_at, producer, correlation_id,
                causation_id, message_json, status, available_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s::jsonb, 'PENDING', %s
            )
            ON CONFLICT (message_id) DO NOTHING
            """,
            (
                message.message_id,
                message.aggregate_id,
                message.event_type,
                message.event_version,
                message.occurred_at,
                message.received_at,
                message.producer,
                message.correlation_id,
                message.causation_id,
                _json(message.to_dict()),
                message.received_at,
            ),
        )
        if self._cursor.rowcount != 1:
            self._cursor.execute(
                "SELECT message_json FROM event.outbox WHERE message_id = %s",
                (message.message_id,),
            )
            row = _required_row(self._cursor, "read duplicate outbox")
            existing = OutboxMessage.from_dict(_json_object(row[0], "outbox message"))
            if existing != message:
                raise IdempotencyConflictError(
                    "같은 outbox message_id에 서로 다른 payload가 저장되었습니다"
                )
