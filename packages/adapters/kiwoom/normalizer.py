"""Normalize supported Kiwoom fixture payloads into canonical market events."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC
from decimal import Decimal, InvalidOperation
from typing import ClassVar

from .contract import (
    ADAPTER_VERSION,
    CANONICAL_EVENT_SCHEMA_VERSION,
    CandidateAction,
    CandidateData,
    CanonicalEventType,
    CanonicalMarketEvent,
    EventLineage,
    KiwoomSourceEnvelope,
    MarketObservation,
    ObservationSource,
    SourceChannel,
)


class NormalizationError(ValueError):
    """A supported source message violated the fixture adapter contract."""


class KiwoomNormalizer:
    """Pure, deterministic source-to-canonical boundary."""

    SOURCE_SCHEMA_BY_CHANNEL: ClassVar[dict[SourceChannel, str]] = {
        SourceChannel.WEBSOCKET: "kiwoom.websocket.v1",
        SourceChannel.REST_SNAPSHOT: "kiwoom.ka10095.v1",
    }

    def normalize(
        self,
        envelope: KiwoomSourceEnvelope,
    ) -> tuple[CanonicalMarketEvent, ...]:
        expected_schema = self.SOURCE_SCHEMA_BY_CHANNEL.get(envelope.channel)
        if envelope.source_schema_version != expected_schema:
            raise NormalizationError(
                "Kiwoom source channel과 schema version이 일치하지 않습니다"
            )
        if envelope.channel is SourceChannel.WEBSOCKET:
            return self._normalize_websocket(envelope)
        if envelope.channel is SourceChannel.REST_SNAPSHOT:
            return self._normalize_snapshot(envelope)
        raise NormalizationError("지원하지 않는 Kiwoom source channel입니다")

    def _normalize_websocket(
        self,
        envelope: KiwoomSourceEnvelope,
    ) -> tuple[CanonicalMarketEvent, ...]:
        transaction = str(envelope.payload.get("trnm") or "").upper()
        raw_data = envelope.payload.get("data")
        if transaction == "CNSRREQ":
            rows = _mapping_rows(raw_data, "CNSRREQ.data")
            condition_id = str(envelope.payload.get("seq") or "").strip()
            if not condition_id:
                raise NormalizationError("CNSRREQ.seq가 비어 있습니다")
            return tuple(
                self._candidate_event(
                    envelope,
                    row,
                    item_index=index,
                    condition_id=condition_id,
                    action=CandidateAction.ENTER,
                )
                for index, row in enumerate(rows)
            )
        if transaction != "REAL":
            return ()

        events: list[CanonicalMarketEvent] = []
        for index, item in enumerate(_mapping_rows(raw_data, "REAL.data")):
            event_type = str(item.get("type") or "")
            values = _as_mapping(item.get("values"), "REAL.data.values")
            if event_type == "0B":
                events.append(self._trade_event(envelope, item, values, index))
            elif event_type == "02":
                action_code = str(values.get("843") or "").upper()
                action_by_code = {"I": CandidateAction.ENTER, "D": CandidateAction.EXIT}
                try:
                    action = action_by_code[action_code]
                except KeyError as exc:
                    raise NormalizationError("조건검색 action은 I 또는 D여야 합니다") from exc
                condition_id = str(values.get("841") or "").strip()
                events.append(
                    self._candidate_event(
                        envelope,
                        item,
                        item_index=index,
                        condition_id=condition_id,
                        action=action,
                    )
                )
        return tuple(events)

    def _normalize_snapshot(
        self,
        envelope: KiwoomSourceEnvelope,
    ) -> tuple[CanonicalMarketEvent, ...]:
        api_id = str(envelope.payload.get("apiId") or "")
        if api_id != "ka10095":
            raise NormalizationError("REST snapshot apiId는 ka10095여야 합니다")
        raw_rows = envelope.payload.get("rows")
        if raw_rows is None:
            raw_rows = envelope.payload.get("atn_stk_infr")
        rows = _mapping_rows(raw_rows, "ka10095.rows")
        return tuple(
            self._snapshot_event(envelope, row, index)
            for index, row in enumerate(rows)
        )

    def _candidate_event(
        self,
        envelope: KiwoomSourceEnvelope,
        row: Mapping[str, object],
        *,
        item_index: int,
        condition_id: str,
        action: CandidateAction,
    ) -> CanonicalMarketEvent:
        values_value = row.get("values")
        values = values_value if isinstance(values_value, Mapping) else {}
        stock_code = _stock_code(row.get("item") or values.get("9001") or row.get("stk_cd"))
        if not condition_id:
            raise NormalizationError("condition_id가 비어 있습니다")
        event_type = (
            CanonicalEventType.CANDIDATE_ENTERED
            if action is CandidateAction.ENTER
            else CanonicalEventType.CANDIDATE_EXITED
        )
        return _make_event(
            envelope,
            row,
            item_index=item_index,
            event_type=event_type,
            stock_code=stock_code,
            data=CandidateData(
                action=action,
                condition_id=condition_id,
                source_stock_code=stock_code,
            ),
        )

    def _trade_event(
        self,
        envelope: KiwoomSourceEnvelope,
        item: Mapping[str, object],
        values: Mapping[str, object],
        item_index: int,
    ) -> CanonicalMarketEvent:
        stock_code = _stock_code(item.get("item") or values.get("9001"))
        observation = _observation(
            source=ObservationSource.REALTIME_0B,
            stock_code=stock_code,
            current_price=_decimal(values.get("10"), absolute=True),
            change_rate=_decimal(values.get("12")),
            trade_volume=_integer(values.get("15"), absolute=True),
            cumulative_volume=_integer(values.get("13"), absolute=True),
            cumulative_trading_value=_decimal(values.get("14"), absolute=True),
            open_price=_decimal(values.get("16"), absolute=True),
            high_price=_decimal(values.get("17"), absolute=True),
            low_price=_decimal(values.get("18"), absolute=True),
            execution_strength=_decimal(values.get("228"), absolute=True),
            market_cap=_decimal(values.get("311"), absolute=True),
        )
        return _make_event(
            envelope,
            item,
            item_index=item_index,
            event_type=CanonicalEventType.TRADE,
            stock_code=stock_code,
            data=observation,
        )

    def _snapshot_event(
        self,
        envelope: KiwoomSourceEnvelope,
        row: Mapping[str, object],
        item_index: int,
    ) -> CanonicalMarketEvent:
        stock_code = _stock_code(row.get("stk_cd") or row.get("code"))
        observation = _observation(
            source=ObservationSource.REST_KA10095,
            stock_code=stock_code,
            current_price=_decimal(row.get("cur_prc"), absolute=True),
            change_rate=_decimal(row.get("flu_rt")),
            trade_volume=_integer(row.get("trde_qty"), absolute=True),
            cumulative_volume=_integer(row.get("acc_trde_qty"), absolute=True),
            cumulative_trading_value=_decimal(
                row.get("acc_trde_prica"), absolute=True
            ),
            open_price=_decimal(row.get("open_pric"), absolute=True),
            high_price=_decimal(row.get("high_pric"), absolute=True),
            low_price=_decimal(row.get("low_pric"), absolute=True),
            execution_strength=_decimal(row.get("cntr_str"), absolute=True),
            market_cap=_decimal(row.get("mac"), absolute=True),
        )
        return _make_event(
            envelope,
            row,
            item_index=item_index,
            event_type=CanonicalEventType.SNAPSHOT,
            stock_code=stock_code,
            data=observation,
        )


def _observation(
    *,
    source: ObservationSource,
    stock_code: str,
    current_price: Decimal | None,
    change_rate: Decimal | None,
    trade_volume: int | None,
    cumulative_volume: int | None,
    cumulative_trading_value: Decimal | None,
    open_price: Decimal | None,
    high_price: Decimal | None,
    low_price: Decimal | None,
    execution_strength: Decimal | None,
    market_cap: Decimal | None,
) -> MarketObservation:
    required = {
        "currentPrice": current_price,
        "cumulativeTradingValue": cumulative_trading_value,
    }
    missing_fields = tuple(sorted(name for name, value in required.items() if value is None))
    return MarketObservation(
        observation_source=source,
        source_stock_code=stock_code,
        current_price=current_price,
        change_rate=change_rate,
        trade_volume=trade_volume,
        cumulative_volume=cumulative_volume,
        cumulative_trading_value=cumulative_trading_value,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        execution_strength=execution_strength,
        market_cap=market_cap,
        missing_fields=missing_fields,
    )


def _make_event(
    envelope: KiwoomSourceEnvelope,
    raw_item: Mapping[str, object],
    *,
    item_index: int,
    event_type: CanonicalEventType,
    stock_code: str,
    data: CandidateData | MarketObservation,
) -> CanonicalMarketEvent:
    raw_hash = hashlib.sha256(_canonical_json(raw_item).encode("utf-8")).hexdigest()
    natural_key = {
        "sessionId": envelope.session_id,
        "type": event_type.value,
        "stockId": f"KRX:{stock_code}",
        "sourceTimestamp": envelope.source_timestamp.astimezone(UTC).isoformat(),
        "sourceSequence": envelope.source_sequence,
        "sourceItemIndex": item_index,
    }
    digest = hashlib.sha256(_canonical_json(natural_key).encode("utf-8")).hexdigest()
    return CanonicalMarketEvent(
        schema_version=CANONICAL_EVENT_SCHEMA_VERSION,
        event_id=f"mkt_{digest[:26]}",
        idempotency_key=f"kiwoom:{digest}",
        event_type=event_type,
        stock_id=f"KRX:{stock_code}",
        source_sequence=envelope.source_sequence,
        source_timestamp=envelope.source_timestamp,
        received_at=envelope.received_at,
        lineage=EventLineage(
            provider="KIWOOM",
            adapter_version=ADAPTER_VERSION,
            source_schema_version=envelope.source_schema_version,
            source_channel=envelope.channel,
            session_id=envelope.session_id,
            source_message_id=envelope.source_message_id,
            source_item_index=item_index,
            request_id=envelope.request_id,
            raw_payload_sha256=raw_hash,
        ),
        data=data,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _mapping_rows(value: object, field_name: str) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise NormalizationError(f"{field_name}는 object 배열이어야 합니다")
    rows: list[Mapping[str, object]] = []
    for row in value:
        if not isinstance(row, Mapping):
            raise NormalizationError(f"{field_name} 항목은 object여야 합니다")
        rows.append(row)
    return tuple(rows)


def _as_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise NormalizationError(f"{field_name}는 object여야 합니다")
    return value


def _stock_code(value: object) -> str:
    text = str(value or "").strip().upper()
    text = text.removeprefix("A")
    if len(text) != 6 or not text.isdigit():
        raise NormalizationError("Kiwoom 종목코드는 6자리 숫자여야 합니다")
    return text


def _decimal(value: object, *, absolute: bool = False) -> Decimal | None:
    text = str(value or "").strip().replace(",", "").replace("%", "")
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise NormalizationError(f"숫자 필드를 Decimal로 변환할 수 없습니다: {value}") from exc
    if not parsed.is_finite():
        raise NormalizationError("숫자 필드는 유한해야 합니다")
    return abs(parsed) if absolute else parsed


def _integer(value: object, *, absolute: bool = False) -> int | None:
    parsed = _decimal(value, absolute=absolute)
    if parsed is None:
        return None
    if parsed != parsed.to_integral_value():
        raise NormalizationError("정수 필드에 소수값이 들어왔습니다")
    return int(parsed)
