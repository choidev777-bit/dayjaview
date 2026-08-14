from __future__ import annotations

from decimal import Decimal
from typing import Any

from conftest import assert_same_instant

from packages.adapters.kiwoom import (
    CandidateData,
    CanonicalEventType,
    IngestDisposition,
    MarketObservation,
    ObservationSource,
)


def test_trade_preserves_capture_order_clock_session_hash_and_lineage(
    replay: Any,
    record_factory: Any,
) -> None:
    record = record_factory(
        sequence=17,
        source_sequence="provider-message-91",
        occurred_at="2026-08-14T09:00:00.123000+09:00",
        received_at="2026-08-14T00:00:00.456000+00:00",
    )

    result = replay.MarketReplayAdapter(max_records=1).adapt(
        [record], expected_count=1
    )
    adapted = result.records[0]
    event = adapted.event

    assert result.session_id == "market-2026-08-14-synthetic"
    assert result.input_count == 1
    assert result.accepted_events == (event,)
    assert adapted.disposition is IngestDisposition.ACCEPTED
    assert event.event_type is CanonicalEventType.TRADE
    assert event.source_sequence == 17
    assert_same_instant(event.source_timestamp, "2026-08-14T09:00:00.123000+09:00")
    assert_same_instant(event.received_at, "2026-08-14T00:00:00.456000+00:00")
    assert event.lineage.session_id == result.session_id
    assert event.lineage.source_message_id.endswith(":17")
    assert event.lineage.adapter_version == replay.REPLAY_ADAPTER_VERSION
    assert event.lineage.raw_payload_sha256 == record["payloadSha256"]
    assert adapted.capture_lineage.capture_sequence == 17
    assert adapted.capture_lineage.provider_source_sequence == "provider-message-91"
    assert adapted.capture_lineage.source == "synthetic_kiwoom_websocket"
    assert adapted.capture_lineage.schema_version == replay.CAPTURE_SCHEMA_VERSION


def test_null_and_actual_numeric_zero_remain_distinct(
    replay: Any,
    record_factory: Any,
) -> None:
    payload = {
        "type": "0B",
        "item": "005930",
        "values": {
            "10": 0,
            "12": "0",
            "13": 0,
            "14": None,
            "15": 0,
        },
    }
    record = record_factory(payload=payload)

    event = replay.MarketReplayAdapter(max_records=1).adapt([record]).accepted_events[0]

    assert isinstance(event.data, MarketObservation)
    assert event.data.current_price == Decimal(0)
    assert event.data.change_rate == Decimal(0)
    assert event.data.trade_volume == 0
    assert event.data.cumulative_volume == 0
    assert event.data.cumulative_trading_value is None
    assert event.data.missing_fields == ("cumulativeTradingValue",)
    assert event.lineage.raw_payload_sha256 == replay.payload_sha256(payload)


def test_candidate_condition_id_is_part_of_identity_and_ordering_lane(
    replay: Any,
    record_factory: Any,
) -> None:
    def candidate(condition_id: str) -> dict[str, object]:
        payload = {
            "type": "02",
            "item": "005930",
            "values": {"841": condition_id, "843": "I", "9001": "005930"},
        }
        return record_factory(
            event_type="candidate.condition",
            payload=payload,
        )

    batch = replay.MarketReplayAdapter(max_records=2).adapt(
        [candidate("7"), candidate("8")]
    )
    first, second = batch.records

    assert first.disposition is IngestDisposition.ACCEPTED
    assert second.disposition is IngestDisposition.ACCEPTED
    assert first.event.event_id != second.event.event_id
    assert first.event.idempotency_key != second.event.idempotency_key
    assert isinstance(first.event.data, CandidateData)
    assert isinstance(second.event.data, CandidateData)
    assert first.event.data.condition_id == "7"
    assert second.event.data.condition_id == "8"


def test_initial_candidate_record_maps_to_candidate_entered(
    replay: Any,
    record_factory: Any,
) -> None:
    payload = {
        "conditionId": "12",
        "action": "INITIAL",
        "rank": 1,
        "raw": {"stk_cd": "A000660"},
    }
    record = record_factory(
        event_type="candidate.condition",
        payload=payload,
        stock_code="000660",
    )

    event = replay.MarketReplayAdapter(max_records=1).adapt([record]).accepted_events[0]

    assert event.event_type is CanonicalEventType.CANDIDATE_ENTERED
    assert event.stock_id == "KRX:000660"
    assert isinstance(event.data, CandidateData)
    assert event.data.condition_id == "12"


def test_snapshot_uses_rest_observation_semantics(
    replay: Any,
    record_factory: Any,
) -> None:
    payload = {
        "apiId": "ka10095",
        "source": "REST_SNAPSHOT",
        "asOf": "2026-08-14T00:00:00+00:00",
        "cycle": 1,
        "batchPosition": 0,
        "raw": {
            "stk_cd": "005930",
            "cur_prc": "73000",
            "acc_trde_prica": "80000000",
        },
    }
    record = record_factory(
        event_type="market.snapshot",
        source="synthetic_kiwoom_rest",
        payload=payload,
    )

    event = replay.MarketReplayAdapter(max_records=1).adapt([record]).accepted_events[0]

    assert event.event_type is CanonicalEventType.SNAPSHOT
    assert isinstance(event.data, MarketObservation)
    assert event.data.observation_source is ObservationSource.REST_KA10095
    assert event.data.current_price == Decimal(73000)
