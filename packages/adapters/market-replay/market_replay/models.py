"""저장 capture 입력과 replay adaptation 결과 모델."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from packages.adapters.kiwoom import (
    CanonicalMarketEvent,
    IngestDisposition,
)

from .errors import CaptureRecordError, PayloadIntegrityError

CAPTURE_SCHEMA_VERSION = "1.0.0"
REPLAY_ADAPTER_VERSION = "market-replay.v1"

_CAPTURE_FIELDS = frozenset(
    {
        "sequence",
        "runId",
        "eventType",
        "source",
        "occurredAt",
        "receivedAt",
        "stockCode",
        "sourceSequence",
        "payload",
        "payloadSha256",
        "schemaVersion",
    }
)


def canonical_json(value: object) -> str:
    """Capture writer와 같은 key ordering으로 JSON을 직렬화한다."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CaptureRecordError("payload는 유한한 JSON 값이어야 합니다") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CaptureRecordError(f"{field_name}은 비어 있지 않은 문자열이어야 합니다")
    return value


def _aware_datetime(value: object, field_name: str) -> datetime:
    text = _required_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CaptureRecordError(f"{field_name}은 ISO 8601 시각이어야 합니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CaptureRecordError(f"{field_name}에는 timezone 정보가 필요합니다")
    return parsed


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    """Append-only capture의 한 NDJSON/SQLite event envelope."""

    sequence: int
    run_id: str
    event_type: str
    source: str
    occurred_at: datetime
    received_at: datetime
    stock_code: str | None
    source_sequence: str | None
    payload: Mapping[str, object]
    payload_sha256: str
    schema_version: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise CaptureRecordError("sequence는 0 이상의 정수여야 합니다")
        for field_name, text_value in (
            ("runId", self.run_id),
            ("eventType", self.event_type),
            ("source", self.source),
            ("schemaVersion", self.schema_version),
        ):
            _required_text(text_value, field_name)
        for field_name, datetime_value in (
            ("occurredAt", self.occurred_at),
            ("receivedAt", self.received_at),
        ):
            if not isinstance(datetime_value, datetime):
                raise CaptureRecordError(f"{field_name}은 datetime이어야 합니다")
            if datetime_value.tzinfo is None or datetime_value.utcoffset() is None:
                raise CaptureRecordError(f"{field_name}에는 timezone 정보가 필요합니다")
        if self.stock_code is not None and (
            not isinstance(self.stock_code, str)
            or (
                len(self.stock_code) != 6 or not self.stock_code.isdigit()
            )
        ):
            raise CaptureRecordError("stockCode는 null 또는 6자리 숫자여야 합니다")
        if self.source_sequence is not None and (
            not isinstance(self.source_sequence, str)
            or not self.source_sequence.strip()
        ):
            raise CaptureRecordError(
                "sourceSequence는 null 또는 비어 있지 않은 문자열이어야 합니다"
            )
        if not isinstance(self.payload, Mapping):
            raise CaptureRecordError("payload는 JSON object여야 합니다")
        if (
            len(self.payload_sha256) != 64
            or self.payload_sha256.lower() != self.payload_sha256
            or any(character not in "0123456789abcdef" for character in self.payload_sha256)
        ):
            raise CaptureRecordError("payloadSha256은 소문자 SHA-256 hex여야 합니다")
        actual_hash = payload_sha256(self.payload)
        if actual_hash != self.payload_sha256:
            raise PayloadIntegrityError(
                "payloadSha256이 canonical payload hash와 일치하지 않습니다"
            )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CaptureRecord:
        if any(not isinstance(field, str) for field in value):
            raise CaptureRecordError("capture record field 이름은 문자열이어야 합니다")
        fields = set(value)
        missing = sorted(_CAPTURE_FIELDS - fields)
        unknown = sorted(fields - _CAPTURE_FIELDS)
        if missing:
            raise CaptureRecordError(
                f"capture record 필수 field가 없습니다: {', '.join(missing)}"
            )
        if unknown:
            raise CaptureRecordError(
                f"capture record에 알 수 없는 field가 있습니다: {', '.join(unknown)}"
            )

        sequence = value["sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise CaptureRecordError("sequence는 0 이상의 정수여야 합니다")
        stock_code_value = value["stockCode"]
        if stock_code_value is not None and not isinstance(stock_code_value, str):
            raise CaptureRecordError("stockCode는 null 또는 문자열이어야 합니다")
        source_sequence_value = value["sourceSequence"]
        if source_sequence_value is not None and not isinstance(
            source_sequence_value, str
        ):
            raise CaptureRecordError("sourceSequence는 null 또는 문자열이어야 합니다")
        payload_value = value["payload"]
        if not isinstance(payload_value, Mapping):
            raise CaptureRecordError("payload는 JSON object여야 합니다")

        # JSON round-trip으로 caller mutation과 비 JSON key/value를 경계에서 차단한다.
        payload_copy = json.loads(canonical_json(payload_value))
        if not isinstance(payload_copy, dict):
            raise CaptureRecordError("payload는 JSON object여야 합니다")
        return cls(
            sequence=sequence,
            run_id=_required_text(value["runId"], "runId"),
            event_type=_required_text(value["eventType"], "eventType"),
            source=_required_text(value["source"], "source"),
            occurred_at=_aware_datetime(value["occurredAt"], "occurredAt"),
            received_at=_aware_datetime(value["receivedAt"], "receivedAt"),
            stock_code=cast(str | None, stock_code_value),
            source_sequence=cast(str | None, source_sequence_value),
            payload=cast(dict[str, object], payload_copy),
            payload_sha256=_required_text(value["payloadSha256"], "payloadSha256"),
            schema_version=_required_text(value["schemaVersion"], "schemaVersion"),
        )


@dataclass(frozen=True, slots=True)
class CaptureLineage:
    """Canonical EventLineage에 없는 capture 고유 provenance."""

    session_id: str
    capture_sequence: int
    source: str
    provider_source_sequence: str | None
    schema_version: str
    payload_sha256: str

    @classmethod
    def from_record(cls, record: CaptureRecord) -> CaptureLineage:
        return cls(
            session_id=record.run_id,
            capture_sequence=record.sequence,
            source=record.source,
            provider_source_sequence=record.source_sequence,
            schema_version=record.schema_version,
            payload_sha256=record.payload_sha256,
        )


@dataclass(frozen=True, slots=True)
class AdaptedMarketEvent:
    event: CanonicalMarketEvent
    disposition: IngestDisposition
    detail: str
    capture_lineage: CaptureLineage

    @property
    def accepted(self) -> bool:
        return self.disposition is IngestDisposition.ACCEPTED


@dataclass(frozen=True, slots=True)
class ReplayBatch:
    session_id: str | None
    records: tuple[AdaptedMarketEvent, ...]

    @property
    def input_count(self) -> int:
        return len(self.records)

    @property
    def accepted_events(self) -> tuple[CanonicalMarketEvent, ...]:
        return tuple(item.event for item in self.records if item.accepted)

    @property
    def rejected(self) -> tuple[AdaptedMarketEvent, ...]:
        return tuple(item for item in self.records if not item.accepted)
