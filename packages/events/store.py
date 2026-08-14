"""Persistence boundary for the sole canonical Event writer."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from .models import CanonicalEvent, CommandReceipt, EventStateLog
from .outbox import OutboxDeliveryStore, OutboxMessage


class EventStoreError(RuntimeError):
    pass


class EventNotFoundError(EventStoreError):
    pass


class EventIdentityConflictError(EventStoreError):
    pass


class IdempotencyConflictError(EventStoreError):
    pass


class ConcurrentEventWriteError(EventStoreError):
    pass


class EventTransaction(Protocol):
    """All methods are called while the store's single-writer lock is held."""

    def get_receipt(self, message_id: str) -> CommandReceipt | None: ...

    def get_event(self, event_id: str) -> CanonicalEvent | None: ...

    def get_event_by_identity(self, identity_key: str) -> CanonicalEvent | None: ...

    def latest_source_sequence(self, event_id: str, source: str) -> int | None: ...

    def insert_event(self, event: CanonicalEvent) -> None: ...

    def update_event(
        self,
        event: CanonicalEvent,
        *,
        expected_state_version: int,
    ) -> None: ...

    def append_state_log(self, state_log: EventStateLog) -> None: ...

    def save_receipt(self, receipt: CommandReceipt) -> None: ...

    def enqueue_outbox(self, message: OutboxMessage) -> None: ...


class EventStore(OutboxDeliveryStore, Protocol):
    def transaction(self) -> AbstractContextManager[EventTransaction]: ...

    def read_event(self, event_id: str) -> CanonicalEvent | None: ...

    def read_outbox(self, message_id: str) -> OutboxMessage | None: ...
