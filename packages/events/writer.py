"""The only component allowed to create or mutate canonical Events."""

from __future__ import annotations

from dataclasses import replace
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain import (
        LifecycleStatus,
        ReconciliationStatus,
        transition_lifecycle,
    )
else:
    _domain = import_module("packages." + "domain")
    LifecycleStatus = _domain.LifecycleStatus
    ReconciliationStatus = _domain.ReconciliationStatus
    transition_lifecycle = _domain.transition_lifecycle

from .models import (
    EVENT_DOMAIN_EVENT_VERSION,
    EVENT_PRODUCER,
    CanonicalEvent,
    CommandReceipt,
    CreateEventCommand,
    EventClassification,
    EventCommand,
    EventStateLog,
    EventWriteDisposition,
    EventWriteResult,
    TransitionLifecycleCommand,
    command_fingerprint,
    outbox_message_id,
)
from .outbox import OutboxMessage
from .store import (
    ConcurrentEventWriteError,
    EventNotFoundError,
    EventStore,
    EventTransaction,
    IdempotencyConflictError,
)


class EventWriter:
    """Serializes Event decisions and commits domain state with its outbox row."""

    def __init__(self, store: EventStore) -> None:
        self._store = store

    def write(self, command: EventCommand) -> EventWriteResult:
        fingerprint = command_fingerprint(command)
        with self._store.transaction() as transaction:
            receipt = transaction.get_receipt(command.metadata.message_id)
            if receipt is not None:
                if receipt.command_fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        "같은 message_id에 서로 다른 Event command가 제출되었습니다"
                    )
                return receipt.result
            if isinstance(command, CreateEventCommand):
                result = self._create(transaction, command)
            else:
                result = self._transition(transaction, command)
            transaction.save_receipt(
                CommandReceipt(
                    message_id=command.metadata.message_id,
                    command_fingerprint=fingerprint,
                    event_id=result.event.event_id,
                    source=command.metadata.source,
                    source_sequence=command.metadata.source_sequence,
                    received_at=command.metadata.received_at,
                    result=result,
                )
            )
            return result

    def _create(
        self,
        transaction: EventTransaction,
        command: CreateEventCommand,
    ) -> EventWriteResult:
        existing = transaction.get_event_by_identity(command.identity.identity_key)
        if existing is not None:
            return EventWriteResult(
                event=existing,
                disposition=EventWriteDisposition.DUPLICATE_IDENTITY,
                outbox_message_id=None,
            )

        metadata = command.metadata
        classification = EventClassification(
            classification_version=1,
            theme_id=command.identity.canonical_theme_id,
            display_name=command.display_name,
            kind=command.classification_kind,
            certainty=command.classification_certainty,
            source=command.classification_source,
            changed_at=metadata.occurred_at,
        )
        event = CanonicalEvent(
            event_id=command.identity.event_id,
            identity_key=command.identity.identity_key,
            market_date=command.identity.market_date,
            canonical_theme_id=command.identity.canonical_theme_id,
            catalyst_key=command.identity.catalyst_key,
            lifecycle_status=LifecycleStatus.CANDIDATE,
            reconciliation_status=ReconciliationStatus.PENDING,
            state_version=1,
            state_policy_version=command.versions.ranking_model_version,
            classification=classification,
            first_detected_at=metadata.occurred_at,
            changed_at=metadata.occurred_at,
            last_source=metadata.source,
            last_source_sequence=metadata.source_sequence,
            last_received_at=metadata.received_at,
            lineage=metadata.lineage,
            versions=command.versions,
        )
        transaction.insert_event(event)
        transaction.append_state_log(
            EventStateLog(
                event_id=event.event_id,
                state_version=event.state_version,
                from_status=None,
                to_status=LifecycleStatus.CANDIDATE,
                policy_version=event.state_policy_version,
                reason="최초 후보 탐지",
                occurred_at=metadata.occurred_at,
                received_at=metadata.received_at,
                source=metadata.source,
                source_sequence=metadata.source_sequence,
                command_message_id=metadata.message_id,
                lineage=metadata.lineage,
            )
        )
        message = self._event_message(
            event=event,
            command=command,
            event_type="canonical_event.created",
            payload={
                "eventId": event.event_id,
                "lifecycleStatus": event.lifecycle_status.value,
                "stateVersion": event.state_version,
                "classificationVersion": event.classification.classification_version,
                "versions": event.versions.to_dict(),
            },
        )
        transaction.enqueue_outbox(message)
        return EventWriteResult(
            event=event,
            disposition=EventWriteDisposition.APPLIED,
            outbox_message_id=message.message_id,
        )

    def _transition(
        self,
        transaction: EventTransaction,
        command: TransitionLifecycleCommand,
    ) -> EventWriteResult:
        event = transaction.get_event(command.event_id)
        if event is None:
            raise EventNotFoundError(f"Event를 찾을 수 없습니다: {command.event_id}")

        metadata = command.metadata
        latest_sequence = transaction.latest_source_sequence(
            event.event_id, metadata.source
        )
        if latest_sequence is not None and metadata.source_sequence <= latest_sequence:
            return EventWriteResult(
                event=event,
                disposition=EventWriteDisposition.STALE_SOURCE_SEQUENCE,
                outbox_message_id=None,
            )
        if command.expected_state_version < event.state_version:
            return EventWriteResult(
                event=event,
                disposition=EventWriteDisposition.STALE_STATE_VERSION,
                outbox_message_id=None,
            )
        if command.expected_state_version > event.state_version:
            raise ConcurrentEventWriteError(
                "expected_state_version이 현재 Event stateVersion보다 큽니다"
            )
        if metadata.occurred_at < event.changed_at:
            return EventWriteResult(
                event=event,
                disposition=EventWriteDisposition.STALE_OCCURRED_AT,
                outbox_message_id=None,
            )
        if command.target is event.lifecycle_status:
            return EventWriteResult(
                event=event,
                disposition=EventWriteDisposition.ALREADY_IN_STATE,
                outbox_message_id=None,
            )

        transition_lifecycle(
            event.lifecycle_status,
            command.target,
            policy_version=command.policy_version,
        )
        updated = replace(
            event,
            lifecycle_status=command.target,
            state_version=event.state_version + 1,
            state_policy_version=command.policy_version,
            changed_at=metadata.occurred_at,
            last_source=metadata.source,
            last_source_sequence=metadata.source_sequence,
            last_received_at=metadata.received_at,
            lineage=metadata.lineage,
        )
        transaction.update_event(updated, expected_state_version=event.state_version)
        transaction.append_state_log(
            EventStateLog(
                event_id=updated.event_id,
                state_version=updated.state_version,
                from_status=event.lifecycle_status,
                to_status=updated.lifecycle_status,
                policy_version=command.policy_version,
                reason=command.reason,
                occurred_at=metadata.occurred_at,
                received_at=metadata.received_at,
                source=metadata.source,
                source_sequence=metadata.source_sequence,
                command_message_id=metadata.message_id,
                lineage=metadata.lineage,
            )
        )
        message = self._event_message(
            event=updated,
            command=command,
            event_type="canonical_event.lifecycle_changed",
            payload={
                "eventId": updated.event_id,
                "fromLifecycleStatus": event.lifecycle_status.value,
                "lifecycleStatus": updated.lifecycle_status.value,
                "stateVersion": updated.state_version,
                "policyVersion": command.policy_version,
                "reason": command.reason,
            },
        )
        transaction.enqueue_outbox(message)
        return EventWriteResult(
            event=updated,
            disposition=EventWriteDisposition.APPLIED,
            outbox_message_id=message.message_id,
        )

    @staticmethod
    def _event_message(
        *,
        event: CanonicalEvent,
        command: EventCommand,
        event_type: str,
        payload: dict[str, object],
    ) -> OutboxMessage:
        metadata = command.metadata
        return OutboxMessage(
            message_id=outbox_message_id(metadata.message_id, event_type),
            event_type=event_type,
            event_version=EVENT_DOMAIN_EVENT_VERSION,
            aggregate_id=event.event_id,
            occurred_at=metadata.occurred_at,
            received_at=metadata.received_at,
            producer=EVENT_PRODUCER,
            correlation_id=metadata.correlation_id,
            causation_id=metadata.causation_id,
            payload=payload,
        )
