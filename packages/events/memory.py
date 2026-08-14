"""Deterministic transactional store used by isolated Event foundation tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from threading import RLock

from .models import CanonicalEvent, CommandReceipt, EventStateLog
from .outbox import OutboxMessage, OutboxRecord, OutboxStatus
from .store import (
    ConcurrentEventWriteError,
    EventIdentityConflictError,
    EventTransaction,
    IdempotencyConflictError,
)


class SimulatedCommitFailure(RuntimeError):
    """Failpoint proving that state and outbox roll back together."""


class InMemoryEventStore:
    """Atomic reference store; production uses the PostgreSQL implementation."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._events: dict[str, CanonicalEvent] = {}
        self._identities: dict[str, str] = {}
        self._receipts: dict[str, CommandReceipt] = {}
        self._source_sequences: dict[tuple[str, str], int] = {}
        self._state_logs: list[EventStateLog] = []
        self._outbox: dict[str, OutboxRecord] = {}
        self._fail_next_commit = False

    def fail_next_commit(self) -> None:
        with self._lock:
            self._fail_next_commit = True

    @contextmanager
    def transaction(self) -> Iterator[EventTransaction]:
        with self._lock:
            backup = (
                self._events.copy(),
                self._identities.copy(),
                self._receipts.copy(),
                self._source_sequences.copy(),
                self._state_logs.copy(),
                self._outbox.copy(),
            )
            transaction = _MemoryEventTransaction(self)
            try:
                yield transaction
                if self._fail_next_commit:
                    self._fail_next_commit = False
                    raise SimulatedCommitFailure(
                        "Event transaction commit 실패를 모의했습니다"
                    )
            except BaseException:
                (
                    self._events,
                    self._identities,
                    self._receipts,
                    self._source_sequences,
                    self._state_logs,
                    self._outbox,
                ) = backup
                raise

    def read_event(self, event_id: str) -> CanonicalEvent | None:
        with self._lock:
            return self._events.get(event_id)

    def read_outbox(self, message_id: str) -> OutboxMessage | None:
        with self._lock:
            record = self._outbox.get(message_id)
            return None if record is None else record.message

    def state_logs(self, event_id: str | None = None) -> tuple[EventStateLog, ...]:
        with self._lock:
            return tuple(
                item
                for item in self._state_logs
                if event_id is None or item.event_id == event_id
            )

    def outbox_records(self) -> tuple[OutboxRecord, ...]:
        with self._lock:
            return tuple(self._outbox[key] for key in sorted(self._outbox))

    def claim_pending(
        self,
        *,
        now: datetime,
        limit: int,
        lease: timedelta,
    ) -> tuple[OutboxRecord, ...]:
        if limit < 1:
            raise ValueError("outbox claim limit은 1 이상이어야 합니다")
        with self._lock:
            eligible = [
                record
                for record in self._outbox.values()
                if record.status is not OutboxStatus.PUBLISHED
                and record.available_at <= now
                and (
                    record.status is OutboxStatus.PENDING
                    or record.claimed_until is None
                    or record.claimed_until <= now
                )
            ]
            eligible.sort(
                key=lambda record: (
                    record.available_at,
                    record.message.message_id,
                )
            )
            claimed: list[OutboxRecord] = []
            for record in eligible[:limit]:
                updated = replace(
                    record,
                    status=OutboxStatus.CLAIMED,
                    attempts=record.attempts + 1,
                    claimed_until=now + lease,
                    last_error=None,
                )
                self._outbox[record.message.message_id] = updated
                claimed.append(updated)
            return tuple(claimed)

    def mark_published(self, message_id: str, *, published_at: datetime) -> None:
        with self._lock:
            record = self._outbox.get(message_id)
            if record is None:
                raise KeyError(f"outbox message를 찾을 수 없습니다: {message_id}")
            if record.status is OutboxStatus.PUBLISHED:
                return
            self._outbox[message_id] = replace(
                record,
                status=OutboxStatus.PUBLISHED,
                claimed_until=None,
                published_at=published_at,
                last_error=None,
            )

    def release_after_failure(
        self,
        message_id: str,
        *,
        retry_at: datetime,
        error: str,
    ) -> None:
        with self._lock:
            record = self._outbox.get(message_id)
            if record is None:
                raise KeyError(f"outbox message를 찾을 수 없습니다: {message_id}")
            if record.status is OutboxStatus.PUBLISHED:
                return
            self._outbox[message_id] = replace(
                record,
                status=OutboxStatus.PENDING,
                available_at=retry_at,
                claimed_until=None,
                last_error=error,
            )


class _MemoryEventTransaction:
    def __init__(self, store: InMemoryEventStore) -> None:
        self._store = store

    def get_receipt(self, message_id: str) -> CommandReceipt | None:
        return self._store._receipts.get(message_id)

    def get_event(self, event_id: str) -> CanonicalEvent | None:
        return self._store._events.get(event_id)

    def get_event_by_identity(self, identity_key: str) -> CanonicalEvent | None:
        event_id = self._store._identities.get(identity_key)
        return None if event_id is None else self._store._events[event_id]

    def latest_source_sequence(self, event_id: str, source: str) -> int | None:
        return self._store._source_sequences.get((event_id, source))

    def insert_event(self, event: CanonicalEvent) -> None:
        if event.event_id in self._store._events:
            raise EventIdentityConflictError("Event ID가 이미 존재합니다")
        if event.identity_key in self._store._identities:
            raise EventIdentityConflictError("Event identity가 이미 존재합니다")
        self._store._events[event.event_id] = event
        self._store._identities[event.identity_key] = event.event_id

    def update_event(
        self,
        event: CanonicalEvent,
        *,
        expected_state_version: int,
    ) -> None:
        current = self._store._events.get(event.event_id)
        if current is None or current.state_version != expected_state_version:
            raise ConcurrentEventWriteError(
                "Event stateVersion optimistic write가 충돌했습니다"
            )
        if event.state_version != expected_state_version + 1:
            raise ConcurrentEventWriteError(
                "Event stateVersion은 한 번에 1씩 증가해야 합니다"
            )
        self._store._events[event.event_id] = event

    def append_state_log(self, state_log: EventStateLog) -> None:
        if any(
            item.event_id == state_log.event_id
            and item.state_version == state_log.state_version
            for item in self._store._state_logs
        ):
            raise ConcurrentEventWriteError("Event state log version이 중복되었습니다")
        self._store._state_logs.append(state_log)

    def save_receipt(self, receipt: CommandReceipt) -> None:
        existing = self._store._receipts.get(receipt.message_id)
        if existing is not None:
            if existing.command_fingerprint != receipt.command_fingerprint:
                raise IdempotencyConflictError(
                    "같은 message_id에 서로 다른 command receipt가 있습니다"
                )
            return
        self._store._receipts[receipt.message_id] = receipt
        key = (receipt.event_id, receipt.source)
        previous = self._store._source_sequences.get(key)
        if previous is None or receipt.source_sequence > previous:
            self._store._source_sequences[key] = receipt.source_sequence

    def enqueue_outbox(self, message: OutboxMessage) -> None:
        existing = self._store._outbox.get(message.message_id)
        if existing is not None:
            if existing.message != message:
                raise IdempotencyConflictError(
                    "같은 outbox message_id에 서로 다른 payload가 있습니다"
                )
            return
        self._store._outbox[message.message_id] = OutboxRecord(
            message=message,
            status=OutboxStatus.PENDING,
            attempts=0,
            available_at=message.received_at,
        )
