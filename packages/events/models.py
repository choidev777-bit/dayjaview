"""Canonical Event commands, lineage, and immutable persisted state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain import LifecycleStatus, ReconciliationStatus
else:
    _domain = import_module("packages." + "domain")
    LifecycleStatus = _domain.LifecycleStatus
    ReconciliationStatus = _domain.ReconciliationStatus

EVENT_COMMAND_SCHEMA_VERSION = "event-command-2026.08.1"
EVENT_DOMAIN_EVENT_VERSION = "1"
EVENT_PRODUCER = "dayjaview.events"


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name}은 비어 있을 수 없습니다")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _opaque_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


@dataclass(frozen=True, slots=True)
class LineageRef:
    """One immutable input reference used to make an Event decision."""

    kind: str
    identifier: str
    version: str | None = None
    content_hash: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.kind, "lineage.kind")
        _require_text(self.identifier, "lineage.identifier")
        if self.version is not None:
            _require_text(self.version, "lineage.version")
        if self.content_hash is not None:
            _require_text(self.content_hash, "lineage.content_hash")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "identifier": self.identifier,
            "version": self.version,
            "contentHash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> LineageRef:
        return cls(
            kind=str(value["kind"]),
            identifier=str(value["identifier"]),
            version=None if value.get("version") is None else str(value["version"]),
            content_hash=(
                None if value.get("contentHash") is None else str(value["contentHash"])
            ),
        )


@dataclass(frozen=True, slots=True)
class EventInputMetadata:
    """Idempotency, ordering, source-time, and causal metadata for a command."""

    message_id: str
    source: str
    source_sequence: int
    occurred_at: datetime
    received_at: datetime
    correlation_id: str
    causation_id: str | None = None
    lineage: tuple[LineageRef, ...] = ()

    def __post_init__(self) -> None:
        for field_name, value in (
            ("message_id", self.message_id),
            ("source", self.source),
            ("correlation_id", self.correlation_id),
        ):
            _require_text(value, field_name)
        if self.causation_id is not None:
            _require_text(self.causation_id, "causation_id")
        if self.source_sequence < 0:
            raise ValueError("source_sequence는 음수일 수 없습니다")
        _require_aware(self.occurred_at, "occurred_at")
        _require_aware(self.received_at, "received_at")
        if self.occurred_at > self.received_at:
            raise ValueError("occurred_at은 received_at 이후일 수 없습니다")
        lineage_keys = [
            (item.kind, item.identifier, item.version) for item in self.lineage
        ]
        if len(lineage_keys) != len(set(lineage_keys)):
            raise ValueError("lineage에 중복 참조가 있습니다")

    def to_dict(self) -> dict[str, object]:
        return {
            "messageId": self.message_id,
            "source": self.source,
            "sourceSequence": self.source_sequence,
            "occurredAt": self.occurred_at.isoformat(),
            "receivedAt": self.received_at.isoformat(),
            "correlationId": self.correlation_id,
            "causationId": self.causation_id,
            "lineage": [item.to_dict() for item in self.lineage],
        }


@dataclass(frozen=True, slots=True)
class CanonicalEventIdentity:
    """Natural identity used only for idempotency, never as a public label."""

    market_date: date
    canonical_theme_id: str
    catalyst_key: str

    def __post_init__(self) -> None:
        _require_text(self.canonical_theme_id, "canonical_theme_id")
        _require_text(self.catalyst_key, "catalyst_key")

    @property
    def identity_key(self) -> str:
        raw = _canonical_json(
            {
                "marketDate": self.market_date.isoformat(),
                "canonicalThemeId": self.canonical_theme_id,
                "catalystKey": self.catalyst_key,
            }
        )
        return _opaque_id("eventkey", raw)

    @property
    def event_id(self) -> str:
        return _opaque_id("evt", self.identity_key)

    def to_dict(self) -> dict[str, str]:
        return {
            "marketDate": self.market_date.isoformat(),
            "canonicalThemeId": self.canonical_theme_id,
            "catalystKey": self.catalyst_key,
            "identityKey": self.identity_key,
        }


@dataclass(frozen=True, slots=True)
class EventVersions:
    calculation_version: str
    ranking_model_version: str
    membership_version: str
    baseline_version: str | None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("calculation_version", self.calculation_version),
            ("ranking_model_version", self.ranking_model_version),
            ("membership_version", self.membership_version),
        ):
            _require_text(value, field_name)
        if self.baseline_version is not None:
            _require_text(self.baseline_version, "baseline_version")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "calculationVersion": self.calculation_version,
            "rankingModelVersion": self.ranking_model_version,
            "membershipVersion": self.membership_version,
            "baselineVersion": self.baseline_version,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> EventVersions:
        return cls(
            calculation_version=str(value["calculationVersion"]),
            ranking_model_version=str(value["rankingModelVersion"]),
            membership_version=str(value["membershipVersion"]),
            baseline_version=(
                None
                if value.get("baselineVersion") is None
                else str(value["baselineVersion"])
            ),
        )


class ClassificationKind(StrEnum):
    INFOSTOCK_THEME = "INFOSTOCK_THEME"
    UNCLASSIFIED_CLUSTER = "UNCLASSIFIED_CLUSTER"
    TEMPORARY_THEME = "TEMPORARY_THEME"


class ClassificationCertainty(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    CONFIRMED = "CONFIRMED"


class ClassificationSource(StrEnum):
    LIVE_ENGINE = "LIVE_ENGINE"
    OPERATOR = "OPERATOR"
    INFOSTOCK = "INFOSTOCK"


@dataclass(frozen=True, slots=True)
class EventClassification:
    classification_version: int
    theme_id: str
    display_name: str
    kind: ClassificationKind
    certainty: ClassificationCertainty
    source: ClassificationSource
    changed_at: datetime

    def __post_init__(self) -> None:
        if self.classification_version < 1:
            raise ValueError("classification_version은 1 이상이어야 합니다")
        _require_text(self.theme_id, "theme_id")
        _require_text(self.display_name, "display_name")
        _require_aware(self.changed_at, "changed_at")

    def to_public_dict(self) -> dict[str, object]:
        return {
            "classificationVersion": self.classification_version,
            "themeId": self.theme_id,
            "displayName": self.display_name,
            "kind": self.kind.value,
            "certainty": self.certainty.value,
            "source": self.source.value,
            "changedAt": self.changed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> EventClassification:
        return cls(
            classification_version=int(str(value["classificationVersion"])),
            theme_id=str(value["themeId"]),
            display_name=str(value["displayName"]),
            kind=ClassificationKind(str(value["kind"])),
            certainty=ClassificationCertainty(str(value["certainty"])),
            source=ClassificationSource(str(value["source"])),
            changed_at=datetime.fromisoformat(str(value["changedAt"])),
        )


@dataclass(frozen=True, slots=True)
class CanonicalEvent:
    event_id: str
    identity_key: str
    market_date: date
    canonical_theme_id: str
    catalyst_key: str
    lifecycle_status: LifecycleStatus
    reconciliation_status: ReconciliationStatus
    state_version: int
    state_policy_version: str
    classification: EventClassification
    first_detected_at: datetime
    changed_at: datetime
    last_source: str
    last_source_sequence: int
    last_received_at: datetime
    lineage: tuple[LineageRef, ...]
    versions: EventVersions

    def __post_init__(self) -> None:
        for field_name, value in (
            ("event_id", self.event_id),
            ("identity_key", self.identity_key),
            ("canonical_theme_id", self.canonical_theme_id),
            ("catalyst_key", self.catalyst_key),
            ("state_policy_version", self.state_policy_version),
            ("last_source", self.last_source),
        ):
            _require_text(value, field_name)
        if self.state_version < 1:
            raise ValueError("state_version은 1 이상이어야 합니다")
        if self.last_source_sequence < 0:
            raise ValueError("last_source_sequence는 음수일 수 없습니다")
        for field_name, timestamp in (
            ("first_detected_at", self.first_detected_at),
            ("changed_at", self.changed_at),
            ("last_received_at", self.last_received_at),
        ):
            _require_aware(timestamp, field_name)
        if self.changed_at < self.first_detected_at:
            raise ValueError("changed_at은 first_detected_at보다 빠를 수 없습니다")

    def to_dict(self) -> dict[str, object]:
        return {
            "eventId": self.event_id,
            "identityKey": self.identity_key,
            "marketDate": self.market_date.isoformat(),
            "canonicalThemeId": self.canonical_theme_id,
            "catalystKey": self.catalyst_key,
            "lifecycleStatus": self.lifecycle_status.value,
            "reconciliationStatus": self.reconciliation_status.value,
            "stateVersion": self.state_version,
            "statePolicyVersion": self.state_policy_version,
            "classification": self.classification.to_public_dict(),
            "firstDetectedAt": self.first_detected_at.isoformat(),
            "changedAt": self.changed_at.isoformat(),
            "lastSource": self.last_source,
            "lastSourceSequence": self.last_source_sequence,
            "lastReceivedAt": self.last_received_at.isoformat(),
            "lineage": [item.to_dict() for item in self.lineage],
            "versions": self.versions.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CanonicalEvent:
        classification = value["classification"]
        versions = value["versions"]
        lineage = value["lineage"]
        if not isinstance(classification, dict) or not isinstance(versions, dict):
            raise TypeError(
                "저장된 Event의 version 또는 classification이 잘못되었습니다"
            )
        if not isinstance(lineage, list):
            raise TypeError("저장된 Event lineage가 잘못되었습니다")
        return cls(
            event_id=str(value["eventId"]),
            identity_key=str(value["identityKey"]),
            market_date=date.fromisoformat(str(value["marketDate"])),
            canonical_theme_id=str(value["canonicalThemeId"]),
            catalyst_key=str(value["catalystKey"]),
            lifecycle_status=LifecycleStatus(str(value["lifecycleStatus"])),
            reconciliation_status=ReconciliationStatus(
                str(value["reconciliationStatus"])
            ),
            state_version=int(str(value["stateVersion"])),
            state_policy_version=str(value["statePolicyVersion"]),
            classification=EventClassification.from_dict(classification),
            first_detected_at=datetime.fromisoformat(str(value["firstDetectedAt"])),
            changed_at=datetime.fromisoformat(str(value["changedAt"])),
            last_source=str(value["lastSource"]),
            last_source_sequence=int(str(value["lastSourceSequence"])),
            last_received_at=datetime.fromisoformat(str(value["lastReceivedAt"])),
            lineage=tuple(LineageRef.from_dict(item) for item in lineage),
            versions=EventVersions.from_dict(versions),
        )


@dataclass(frozen=True, slots=True)
class CreateEventCommand:
    metadata: EventInputMetadata
    identity: CanonicalEventIdentity
    display_name: str
    versions: EventVersions
    classification_kind: ClassificationKind = ClassificationKind.INFOSTOCK_THEME
    classification_certainty: ClassificationCertainty = (
        ClassificationCertainty.PROVISIONAL
    )
    classification_source: ClassificationSource = ClassificationSource.LIVE_ENGINE

    def __post_init__(self) -> None:
        _require_text(self.display_name, "display_name")

    def to_dict(self) -> dict[str, object]:
        return {
            "commandType": "CREATE_EVENT",
            "schemaVersion": EVENT_COMMAND_SCHEMA_VERSION,
            "metadata": self.metadata.to_dict(),
            "identity": self.identity.to_dict(),
            "displayName": self.display_name,
            "versions": self.versions.to_dict(),
            "classificationKind": self.classification_kind.value,
            "classificationCertainty": self.classification_certainty.value,
            "classificationSource": self.classification_source.value,
        }


@dataclass(frozen=True, slots=True)
class TransitionLifecycleCommand:
    metadata: EventInputMetadata
    event_id: str
    target: LifecycleStatus
    expected_state_version: int
    reason: str
    policy_version: str

    def __post_init__(self) -> None:
        _require_text(self.event_id, "event_id")
        _require_text(self.reason, "reason")
        _require_text(self.policy_version, "policy_version")
        if self.expected_state_version < 1:
            raise ValueError("expected_state_version은 1 이상이어야 합니다")

    def to_dict(self) -> dict[str, object]:
        return {
            "commandType": "TRANSITION_LIFECYCLE",
            "schemaVersion": EVENT_COMMAND_SCHEMA_VERSION,
            "metadata": self.metadata.to_dict(),
            "eventId": self.event_id,
            "target": self.target.value,
            "expectedStateVersion": self.expected_state_version,
            "reason": self.reason,
            "policyVersion": self.policy_version,
        }


type EventCommand = CreateEventCommand | TransitionLifecycleCommand


def command_fingerprint(command: EventCommand) -> str:
    return hashlib.sha256(
        _canonical_json(command.to_dict()).encode("utf-8")
    ).hexdigest()


class EventWriteDisposition(StrEnum):
    APPLIED = "APPLIED"
    DUPLICATE_IDENTITY = "DUPLICATE_IDENTITY"
    ALREADY_IN_STATE = "ALREADY_IN_STATE"
    STALE_SOURCE_SEQUENCE = "STALE_SOURCE_SEQUENCE"
    STALE_STATE_VERSION = "STALE_STATE_VERSION"
    STALE_OCCURRED_AT = "STALE_OCCURRED_AT"


@dataclass(frozen=True, slots=True)
class EventWriteResult:
    event: CanonicalEvent
    disposition: EventWriteDisposition
    outbox_message_id: str | None

    @property
    def applied(self) -> bool:
        return self.disposition is EventWriteDisposition.APPLIED

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.event.to_dict(),
            "disposition": self.disposition.value,
            "outboxMessageId": self.outbox_message_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> EventWriteResult:
        event = value["event"]
        if not isinstance(event, dict):
            raise TypeError("저장된 command result의 Event가 잘못되었습니다")
        return cls(
            event=CanonicalEvent.from_dict(event),
            disposition=EventWriteDisposition(str(value["disposition"])),
            outbox_message_id=(
                None
                if value.get("outboxMessageId") is None
                else str(value["outboxMessageId"])
            ),
        )


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    message_id: str
    command_fingerprint: str
    event_id: str
    source: str
    source_sequence: int
    received_at: datetime
    result: EventWriteResult


@dataclass(frozen=True, slots=True)
class EventStateLog:
    event_id: str
    state_version: int
    from_status: LifecycleStatus | None
    to_status: LifecycleStatus
    policy_version: str
    reason: str
    occurred_at: datetime
    received_at: datetime
    source: str
    source_sequence: int
    command_message_id: str
    lineage: tuple[LineageRef, ...]


def outbox_message_id(command_message_id: str, event_type: str) -> str:
    return _opaque_id("msg", f"{command_message_id}:{event_type}")
