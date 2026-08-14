from __future__ import annotations

from dataclasses import replace

import pytest

from packages.domain import InvalidStateTransition, LifecycleStatus
from packages.events import (
    ConcurrentEventWriteError,
    EventWriteDisposition,
    EventWriter,
    IdempotencyConflictError,
    InMemoryEventStore,
    SimulatedCommitFailure,
)

from ._factories import create_command, metadata, transition_command


def test_create_is_durable_idempotent_and_preserves_lineage() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    command = create_command()

    first = writer.write(command)
    replay = writer.write(command)

    assert replay == first
    assert first.disposition is EventWriteDisposition.APPLIED
    assert first.event.lifecycle_status is LifecycleStatus.CANDIDATE
    assert first.event.state_version == 1
    assert first.event.last_received_at == command.metadata.received_at
    assert first.event.lineage == command.metadata.lineage
    assert len(store.state_logs(first.event.event_id)) == 1
    assert len(store.outbox_records()) == 1
    assert store.read_outbox(first.outbox_message_id or "") is not None


def test_message_id_reuse_with_different_command_is_rejected() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    writer.write(create_command())

    with pytest.raises(IdempotencyConflictError):
        writer.write(create_command(display_name="서로 다른 표시명"))

    assert len(store.outbox_records()) == 1


def test_natural_identity_duplicate_keeps_one_event_and_no_duplicate_outbox() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    first = writer.write(create_command())
    duplicate_command = create_command(
        message_id="cmd-create-duplicate-identity",
        sequence=2,
    )

    duplicate = writer.write(duplicate_command)

    assert duplicate.event.event_id == first.event.event_id
    assert duplicate.disposition is EventWriteDisposition.DUPLICATE_IDENTITY
    assert duplicate.outbox_message_id is None
    assert writer.write(duplicate_command) == duplicate
    assert len(store.outbox_records()) == 1
    assert len(store.state_logs(first.event.event_id)) == 1


def test_allowed_lifecycle_path_increments_state_and_outbox_once_each() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = writer.write(create_command()).event
    path = (
        ("cmd-active", 2, 20, 1, LifecycleStatus.ACTIVE),
        ("cmd-weak", 3, 90, 2, LifecycleStatus.WEAKENING),
        ("cmd-recover", 4, 100, 3, LifecycleStatus.ACTIVE),
        ("cmd-close", 5, 700, 4, LifecycleStatus.CLOSED),
    )
    for message_id, sequence, seconds, expected, target in path:
        result = writer.write(
            transition_command(
                event_id=event.event_id,
                message_id=message_id,
                sequence=sequence,
                seconds=seconds,
                expected=expected,
                target=target,
            )
        )
        assert result.disposition is EventWriteDisposition.APPLIED
        event = result.event

    assert event.lifecycle_status is LifecycleStatus.CLOSED
    assert event.state_version == 5
    assert len(store.state_logs(event.event_id)) == 5
    assert len(store.outbox_records()) == 5
    assert [item.state_version for item in store.state_logs(event.event_id)] == [
        1,
        2,
        3,
        4,
        5,
    ]


def test_invalid_lifecycle_transition_rolls_back_receipt_and_outbox() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = writer.write(create_command()).event
    invalid = transition_command(
        event_id=event.event_id,
        message_id="cmd-invalid-close",
        sequence=2,
        seconds=20,
        expected=1,
        target=LifecycleStatus.CLOSED,
    )

    with pytest.raises(InvalidStateTransition):
        writer.write(invalid)
    with pytest.raises(InvalidStateTransition):
        writer.write(invalid)

    assert store.read_event(event.event_id) == event
    assert len(store.state_logs(event.event_id)) == 1
    assert len(store.outbox_records()) == 1


def test_stale_source_sequence_is_recorded_as_deterministic_noop() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = writer.write(create_command(sequence=10)).event
    stale = transition_command(
        event_id=event.event_id,
        message_id="cmd-stale-sequence",
        sequence=9,
        seconds=30,
        expected=1,
        target=LifecycleStatus.ACTIVE,
    )

    first = writer.write(stale)
    replay = writer.write(stale)

    assert replay == first
    assert first.disposition is EventWriteDisposition.STALE_SOURCE_SEQUENCE
    assert first.event == event
    assert len(store.outbox_records()) == 1


def test_out_of_order_occurred_time_does_not_rewind_state() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = writer.write(create_command()).event
    active = writer.write(
        transition_command(
            event_id=event.event_id,
            message_id="cmd-active",
            sequence=2,
            seconds=30,
            expected=1,
            target=LifecycleStatus.ACTIVE,
        )
    ).event
    stale_time = transition_command(
        event_id=event.event_id,
        message_id="cmd-old-weak",
        sequence=3,
        seconds=40,
        occurred_offset=20,
        expected=2,
        target=LifecycleStatus.WEAKENING,
    )

    result = writer.write(stale_time)

    assert result.disposition is EventWriteDisposition.STALE_OCCURRED_AT
    assert result.event == active
    assert store.read_event(event.event_id) == active


def test_stale_and_future_expected_versions_are_distinct() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = writer.write(create_command()).event
    active = writer.write(
        transition_command(
            event_id=event.event_id,
            message_id="cmd-active",
            sequence=2,
            seconds=20,
            expected=1,
            target=LifecycleStatus.ACTIVE,
        )
    ).event

    stale = writer.write(
        transition_command(
            event_id=event.event_id,
            message_id="cmd-stale-version",
            sequence=3,
            seconds=30,
            expected=1,
            target=LifecycleStatus.WEAKENING,
        )
    )
    assert stale.disposition is EventWriteDisposition.STALE_STATE_VERSION

    with pytest.raises(ConcurrentEventWriteError):
        writer.write(
            transition_command(
                event_id=event.event_id,
                message_id="cmd-future-version",
                sequence=4,
                seconds=40,
                expected=3,
                target=LifecycleStatus.WEAKENING,
            )
        )
    assert store.read_event(event.event_id) == active


def test_already_in_state_is_noop_but_idempotency_cursor_advances() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    event = writer.write(create_command()).event
    command = transition_command(
        event_id=event.event_id,
        message_id="cmd-candidate-again",
        sequence=2,
        seconds=10,
        expected=1,
        target=LifecycleStatus.CANDIDATE,
    )

    result = writer.write(command)

    assert result.disposition is EventWriteDisposition.ALREADY_IN_STATE
    assert result.event == event
    assert len(store.outbox_records()) == 1


def test_commit_failure_rolls_back_event_receipt_log_and_outbox_together() -> None:
    store = InMemoryEventStore()
    writer = EventWriter(store)
    command = create_command()
    store.fail_next_commit()

    with pytest.raises(SimulatedCommitFailure):
        writer.write(command)

    assert store.read_event(command.identity.event_id) is None
    assert store.state_logs() == ()
    assert store.outbox_records() == ()

    recovered = writer.write(command)
    assert recovered.applied
    assert store.read_event(recovered.event.event_id) == recovered.event
    assert len(store.outbox_records()) == 1


def test_event_metadata_rejects_received_time_before_occurrence() -> None:
    base = metadata(message_id="cmd-time", sequence=1)
    with pytest.raises(ValueError, match="received_at"):
        replace(base, received_at=base.occurred_at.replace(year=2025))
