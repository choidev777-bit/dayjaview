"""Transactional outbox records and an idempotent-message publisher."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from .models import _require_aware, _require_text


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PUBLISHED = "PUBLISHED"


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    message_id: str
    event_type: str
    event_version: str
    aggregate_id: str
    occurred_at: datetime
    received_at: datetime
    producer: str
    correlation_id: str
    causation_id: str | None
    payload: dict[str, object]

    def __post_init__(self) -> None:
        for field_name, value in (
            ("message_id", self.message_id),
            ("event_type", self.event_type),
            ("event_version", self.event_version),
            ("aggregate_id", self.aggregate_id),
            ("producer", self.producer),
            ("correlation_id", self.correlation_id),
        ):
            _require_text(value, field_name)
        if self.causation_id is not None:
            _require_text(self.causation_id, "causation_id")
        _require_aware(self.occurred_at, "occurred_at")
        _require_aware(self.received_at, "received_at")
        if self.occurred_at > self.received_at:
            raise ValueError("occurred_at은 received_at 이후일 수 없습니다")
        try:
            copied_payload = json.loads(
                json.dumps(
                    self.payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError("outbox payload는 유효한 JSON이어야 합니다") from error
        if not isinstance(copied_payload, dict):
            raise TypeError("outbox payload는 JSON object여야 합니다")
        object.__setattr__(self, "payload", copied_payload)

    def to_dict(self) -> dict[str, object]:
        return {
            "eventType": self.event_type,
            "eventVersion": self.event_version,
            "messageId": self.message_id,
            "occurredAt": self.occurred_at.isoformat(),
            "receivedAt": self.received_at.isoformat(),
            "producer": self.producer,
            "correlationId": self.correlation_id,
            "causationId": self.causation_id,
            "aggregateId": self.aggregate_id,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> OutboxMessage:
        payload = value["payload"]
        if not isinstance(payload, dict):
            raise TypeError("저장된 outbox payload가 object가 아닙니다")
        return cls(
            message_id=str(value["messageId"]),
            event_type=str(value["eventType"]),
            event_version=str(value["eventVersion"]),
            aggregate_id=str(value["aggregateId"]),
            occurred_at=datetime.fromisoformat(str(value["occurredAt"])),
            received_at=datetime.fromisoformat(str(value["receivedAt"])),
            producer=str(value["producer"]),
            correlation_id=str(value["correlationId"]),
            causation_id=(
                None if value.get("causationId") is None else str(value["causationId"])
            ),
            payload=payload,
        )


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    message: OutboxMessage
    status: OutboxStatus
    attempts: int
    available_at: datetime
    claimed_until: datetime | None = None
    published_at: datetime | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        if self.attempts < 0:
            raise ValueError("outbox attempts는 음수일 수 없습니다")
        _require_aware(self.available_at, "available_at")
        if self.claimed_until is not None:
            _require_aware(self.claimed_until, "claimed_until")
        if self.published_at is not None:
            _require_aware(self.published_at, "published_at")


class OutboxDeliveryStore(Protocol):
    def claim_pending(
        self,
        *,
        now: datetime,
        limit: int,
        lease: timedelta,
    ) -> tuple[OutboxRecord, ...]: ...

    def mark_published(self, message_id: str, *, published_at: datetime) -> None: ...

    def release_after_failure(
        self,
        message_id: str,
        *,
        retry_at: datetime,
        error: str,
    ) -> None: ...


class IdempotentOutboxTransport(Protocol):
    """Transport must deduplicate retries by the stable ``message_id``."""

    def publish(self, message: OutboxMessage) -> None: ...


@dataclass(frozen=True, slots=True)
class PublishBatchResult:
    claimed: int
    published: int
    failed: int


class OutboxPublisher:
    """At-least-once relay with stable IDs for broker-side deduplication."""

    def __init__(
        self,
        store: OutboxDeliveryStore,
        transport: IdempotentOutboxTransport,
        *,
        lease: timedelta = timedelta(seconds=30),
        retry_delay: timedelta = timedelta(seconds=1),
    ) -> None:
        if lease <= timedelta(0):
            raise ValueError("outbox lease는 0보다 커야 합니다")
        if retry_delay < timedelta(0):
            raise ValueError("outbox retry_delay는 음수일 수 없습니다")
        self._store = store
        self._transport = transport
        self._lease = lease
        self._retry_delay = retry_delay

    def publish_pending(
        self,
        *,
        now: datetime,
        limit: int = 100,
    ) -> PublishBatchResult:
        _require_aware(now, "now")
        if limit < 1:
            raise ValueError("outbox publish limit은 1 이상이어야 합니다")
        records = self._store.claim_pending(
            now=now,
            limit=limit,
            lease=self._lease,
        )
        published = 0
        failed = 0
        for record in records:
            try:
                self._transport.publish(record.message)
            except Exception as error:  # noqa: BLE001 - transport boundary
                failed += 1
                self._store.release_after_failure(
                    record.message.message_id,
                    retry_at=now + self._retry_delay,
                    error=f"{type(error).__name__}: {error}",
                )
            else:
                self._store.mark_published(
                    record.message.message_id,
                    published_at=now,
                )
                published += 1
        return PublishBatchResult(
            claimed=len(records),
            published=published,
            failed=failed,
        )
