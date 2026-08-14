from __future__ import annotations

from datetime import timedelta

from packages.events import (
    EventWriter,
    InMemoryEventStore,
    OutboxMessage,
    OutboxPublisher,
    OutboxStatus,
)

from ._factories import START, create_command


class RecordingIdempotentTransport:
    def __init__(self, *, lose_first_ack: bool = False) -> None:
        self.delivered: dict[str, OutboxMessage] = {}
        self.calls: list[str] = []
        self._lose_first_ack = lose_first_ack

    def publish(self, message: OutboxMessage) -> None:
        self.calls.append(message.message_id)
        self.delivered.setdefault(message.message_id, message)
        if self._lose_first_ack:
            self._lose_first_ack = False
            raise ConnectionError("broker ack 유실")


def test_outbox_retry_uses_stable_message_id_and_does_not_duplicate_delivery() -> None:
    store = InMemoryEventStore()
    created = EventWriter(store).write(create_command())
    transport = RecordingIdempotentTransport(lose_first_ack=True)
    publisher = OutboxPublisher(store, transport)

    failed = publisher.publish_pending(now=START)
    recovered = publisher.publish_pending(now=START + timedelta(seconds=1))

    assert failed.failed == 1 and failed.published == 0
    assert recovered.published == 1 and recovered.failed == 0
    assert transport.calls == [created.outbox_message_id, created.outbox_message_id]
    assert list(transport.delivered) == [created.outbox_message_id]
    record = store.outbox_records()[0]
    assert record.status is OutboxStatus.PUBLISHED
    assert record.attempts == 2


def test_expired_claim_is_recovered_after_publisher_crash() -> None:
    store = InMemoryEventStore()
    created = EventWriter(store).write(create_command())
    claimed = store.claim_pending(
        now=START,
        limit=10,
        lease=timedelta(seconds=30),
    )

    assert len(claimed) == 1
    assert (
        store.claim_pending(
            now=START + timedelta(seconds=29),
            limit=10,
            lease=timedelta(seconds=30),
        )
        == ()
    )
    reclaimed = store.claim_pending(
        now=START + timedelta(seconds=30),
        limit=10,
        lease=timedelta(seconds=30),
    )
    assert reclaimed[0].message.message_id == created.outbox_message_id
    assert reclaimed[0].attempts == 2


def test_published_outbox_ack_is_idempotent() -> None:
    store = InMemoryEventStore()
    created = EventWriter(store).write(create_command())
    transport = RecordingIdempotentTransport()
    publisher = OutboxPublisher(store, transport)

    assert publisher.publish_pending(now=START).published == 1
    store.mark_published(created.outbox_message_id or "", published_at=START)
    assert publisher.publish_pending(now=START + timedelta(minutes=1)).claimed == 0
    assert len(transport.delivered) == 1
