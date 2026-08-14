"""Bounded 저장 capture iterable을 S2 canonical market event로 변환한다."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from datetime import timedelta, timezone

from packages.adapters.kiwoom import (
    EventOrderFence,
    KiwoomNormalizer,
    KiwoomSourceEnvelope,
    SourceChannel,
)

from .errors import (
    CaptureRecordError,
    InputLimitExceededError,
    MixedSessionError,
    ReplayAdapterError,
    SchemaMismatchError,
    TruncatedInputError,
    UnsupportedEventError,
)
from .models import (
    CAPTURE_SCHEMA_VERSION,
    REPLAY_ADAPTER_VERSION,
    AdaptedMarketEvent,
    CaptureLineage,
    CaptureRecord,
    ReplayBatch,
)

_KST = timezone(timedelta(hours=9))
_SUPPORTED_EVENT_TYPES = frozenset(
    {"market.trade", "market.snapshot", "candidate.condition"}
)


class MarketReplayAdapter:
    """I/O 없이 caller가 제공한 유한 iterable만 원자적으로 변환한다."""

    def __init__(self, *, max_records: int) -> None:
        if isinstance(max_records, bool) or max_records <= 0:
            raise ValueError("max_records는 0보다 큰 정수여야 합니다")
        self.max_records = max_records

    def adapt(
        self,
        records: Iterable[CaptureRecord | Mapping[str, object]],
        *,
        expected_count: int | None = None,
    ) -> ReplayBatch:
        """전체 배치를 검증한 뒤 accepted canonical event만 안전하게 노출한다."""

        if isinstance(records, (str, bytes, bytearray, os.PathLike)):
            raise ReplayAdapterError(
                "파일 경로나 원문 문자열 대신 bounded record iterable을 제공해야 합니다"
            )
        self._validate_expected_count(expected_count)

        fence = EventOrderFence()
        adapted: list[AdaptedMarketEvent] = []
        session_id: str | None = None
        for position, raw_record in enumerate(records):
            if position >= self.max_records:
                raise InputLimitExceededError(
                    f"입력이 max_records={self.max_records} 상한을 초과했습니다"
                )
            record = self._capture_record(raw_record)
            if record.schema_version != CAPTURE_SCHEMA_VERSION:
                raise SchemaMismatchError(
                    "지원하지 않는 capture schema version입니다: "
                    f"{record.schema_version}"
                )
            if record.event_type not in _SUPPORTED_EVENT_TYPES:
                raise UnsupportedEventError(
                    f"지원하지 않는 capture eventType입니다: {record.event_type}"
                )
            if session_id is None:
                session_id = record.run_id
                fence.begin_session(session_id)
            elif record.run_id != session_id:
                raise MixedSessionError(
                    "한 replay adapter 배치에는 하나의 runId만 허용됩니다"
                )

            event = self._normalize(record)
            result = fence.evaluate(event)
            adapted.append(
                AdaptedMarketEvent(
                    event=result.event,
                    disposition=result.disposition,
                    detail=result.detail,
                    capture_lineage=CaptureLineage.from_record(record),
                )
            )

        if expected_count is not None and len(adapted) != expected_count:
            raise TruncatedInputError(
                f"expected_count={expected_count}이지만 {len(adapted)}개 record에서 끝났습니다"
            )
        return ReplayBatch(session_id=session_id, records=tuple(adapted))

    def adapt_ndjson(
        self,
        lines: Iterable[str | bytes],
        *,
        expected_count: int | None = None,
    ) -> ReplayBatch:
        """완전한 newline 경계가 보장된 NDJSON line stream만 변환한다."""

        if isinstance(lines, (str, bytes, bytearray, os.PathLike)):
            raise ReplayAdapterError(
                "파일 경로나 NDJSON 전체 문자열 대신 bounded line iterable을 제공해야 합니다"
            )
        return self.adapt(
            self._iter_ndjson(lines),
            expected_count=expected_count,
        )

    def _validate_expected_count(self, expected_count: int | None) -> None:
        if expected_count is None:
            return
        if (
            isinstance(expected_count, bool)
            or expected_count < 0
            or expected_count > self.max_records
        ):
            raise ValueError(
                "expected_count는 0 이상 max_records 이하의 정수여야 합니다"
            )

    @staticmethod
    def _capture_record(
        value: CaptureRecord | Mapping[str, object],
    ) -> CaptureRecord:
        if isinstance(value, CaptureRecord):
            return value
        if not isinstance(value, Mapping):
            raise CaptureRecordError("입력 항목은 CaptureRecord 또는 JSON object여야 합니다")
        return CaptureRecord.from_mapping(value)

    @staticmethod
    def _iter_ndjson(lines: Iterable[str | bytes]) -> Iterator[Mapping[str, object]]:
        for line_number, raw_line in enumerate(lines, start=1):
            if not isinstance(raw_line, (str, bytes)):
                raise CaptureRecordError(
                    f"NDJSON {line_number}번째 항목은 str 또는 bytes여야 합니다"
                )
            if isinstance(raw_line, bytes):
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise CaptureRecordError(
                        f"NDJSON {line_number}번째 줄은 UTF-8이어야 합니다"
                    ) from exc
            else:
                line = raw_line
            if not line.endswith("\n"):
                raise TruncatedInputError(
                    f"NDJSON {line_number}번째 줄이 newline 전에 끝났습니다"
                )
            body = line[:-1].removesuffix("\r")
            if not body or "\n" in body or "\r" in body:
                raise CaptureRecordError(
                    f"NDJSON {line_number}번째 줄은 정확히 한 JSON object여야 합니다"
                )
            try:
                value = json.loads(body)
            except json.JSONDecodeError as exc:
                raise CaptureRecordError(
                    f"NDJSON {line_number}번째 줄의 JSON이 잘못되었습니다"
                ) from exc
            if not isinstance(value, dict):
                raise CaptureRecordError(
                    f"NDJSON {line_number}번째 줄은 JSON object여야 합니다"
                )
            yield value

    def _normalize(self, record: CaptureRecord):
        channel, provider_schema, payload = self._provider_envelope(record)
        envelope = KiwoomSourceEnvelope(
            source_schema_version=provider_schema,
            channel=channel,
            session_id=record.run_id,
            source_message_id=f"capture:{record.run_id}:{record.sequence}",
            source_sequence=record.sequence,
            source_timestamp=record.occurred_at,
            received_at=record.received_at,
            market_date=record.occurred_at.astimezone(_KST).date(),
            payload=payload,
        )
        try:
            events = KiwoomNormalizer().normalize(envelope)
        except (TypeError, ValueError) as exc:
            raise CaptureRecordError(
                f"{record.event_type} payload를 canonical event로 변환할 수 없습니다: {exc}"
            ) from exc
        if len(events) != 1:
            raise CaptureRecordError(
                f"capture record 하나는 canonical event 하나여야 합니다: {len(events)}개"
            )
        event = events[0]
        if record.stock_code is not None and event.stock_code != record.stock_code:
            raise CaptureRecordError(
                "capture stockCode와 payload에서 정규화한 종목코드가 다릅니다"
            )
        return replace(
            event,
            lineage=replace(
                event.lineage,
                adapter_version=REPLAY_ADAPTER_VERSION,
                raw_payload_sha256=record.payload_sha256,
            ),
        )

    @staticmethod
    def _provider_envelope(
        record: CaptureRecord,
    ) -> tuple[SourceChannel, str, Mapping[str, object]]:
        if record.event_type == "market.trade":
            item = _trade_item(record.payload)
            return (
                SourceChannel.WEBSOCKET,
                "kiwoom.websocket.v1",
                {"trnm": "REAL", "data": [item]},
            )
        if record.event_type == "candidate.condition":
            payload = _candidate_payload(record.payload)
            return SourceChannel.WEBSOCKET, "kiwoom.websocket.v1", payload
        if record.event_type == "market.snapshot":
            row = _snapshot_row(record.payload)
            return (
                SourceChannel.REST_SNAPSHOT,
                "kiwoom.ka10095.v1",
                {"apiId": "ka10095", "rows": [row]},
            )
        raise UnsupportedEventError(
            f"지원하지 않는 capture eventType입니다: {record.event_type}"
        )


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise CaptureRecordError(f"{field_name}는 JSON object여야 합니다")
    return value


def _numeric_zero_safe(values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise CaptureRecordError("provider payload field 이름은 문자열이어야 합니다")
        if isinstance(value, bool):
            raise CaptureRecordError(f"숫자 provider field {key}에 boolean을 사용할 수 없습니다")
        if isinstance(value, (int, float)) and value == 0:
            result[key] = str(value)
        else:
            result[key] = value
    return result


def _trade_item(payload: Mapping[str, object]) -> Mapping[str, object]:
    if payload.get("type") != "0B":
        raise CaptureRecordError("market.trade payload.type은 0B여야 합니다")
    values = _numeric_zero_safe(_mapping(payload.get("values"), "market.trade.values"))
    item = dict(payload)
    item["values"] = values
    return item


def _candidate_payload(payload: Mapping[str, object]) -> Mapping[str, object]:
    if payload.get("type") == "02":
        values = _mapping(payload.get("values"), "candidate.condition.values")
        action = values.get("843")
        if action not in {"I", "D"}:
            raise CaptureRecordError("candidate condition action은 I 또는 D여야 합니다")
        condition_id = values.get("841")
        if not isinstance(condition_id, str) or not condition_id.strip():
            raise CaptureRecordError("candidate conditionId가 비어 있습니다")
        item = dict(payload)
        item["values"] = dict(values)
        return {"trnm": "REAL", "data": [item]}

    if payload.get("action") != "INITIAL":
        raise CaptureRecordError(
            "초기 candidate condition action은 INITIAL이어야 합니다"
        )
    condition_id = payload.get("conditionId")
    if not isinstance(condition_id, str) or not condition_id.strip():
        raise CaptureRecordError("candidate conditionId가 비어 있습니다")
    row = _mapping(payload.get("raw"), "candidate.condition.raw")
    return {"trnm": "CNSRREQ", "seq": condition_id, "data": [dict(row)]}


def _snapshot_row(payload: Mapping[str, object]) -> Mapping[str, object]:
    if payload.get("apiId") != "ka10095":
        raise CaptureRecordError("market.snapshot payload.apiId는 ka10095여야 합니다")
    if payload.get("source") != "REST_SNAPSHOT":
        raise CaptureRecordError(
            "market.snapshot payload.source는 REST_SNAPSHOT이어야 합니다"
        )
    row = _mapping(payload.get("raw"), "market.snapshot.raw")
    return _numeric_zero_safe(row)
