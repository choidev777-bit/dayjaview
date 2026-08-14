from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from packages.adapters.kiwoom import (
    CANONICAL_EVENT_SCHEMA_VERSION,
    CandidateData,
    CanonicalEventType,
    FixtureKiwoomAdapter,
    KiwoomNormalizer,
    KiwoomSourceEnvelope,
    MarketObservation,
    NormalizationError,
    ObservationSource,
    SourceChannel,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kiwoom-market-v1.json"


def _messages() -> tuple[KiwoomSourceEnvelope, ...]:
    adapter = FixtureKiwoomAdapter.from_path(FIXTURE)
    connection = adapter.connect(now=datetime(2026, 8, 14, tzinfo=UTC))
    return tuple(
        message
        for message in (adapter.read(connection.session_id) for _ in range(3))
        if message is not None
    )


def test_condition_payload_becomes_canonical_candidate_events() -> None:
    envelope = _messages()[0]
    events = KiwoomNormalizer().normalize(envelope)

    assert [event.event_type for event in events] == [
        CanonicalEventType.CANDIDATE_ENTERED,
        CanonicalEventType.CANDIDATE_ENTERED,
    ]
    assert [event.stock_id for event in events] == ["KRX:005930", "KRX:000660"]
    assert all(event.schema_version == CANONICAL_EVENT_SCHEMA_VERSION for event in events)
    assert all(isinstance(event.data, CandidateData) for event in events)
    assert events[0].lineage.source_message_id == "ws-condition-initial-001"
    assert events[0].source_sequence == 1


def test_same_source_event_has_same_event_and_idempotency_id() -> None:
    envelope = _messages()[1]
    normalizer = KiwoomNormalizer()

    first = normalizer.normalize(envelope)
    second = normalizer.normalize(envelope)

    assert [event.event_id for event in first] == [event.event_id for event in second]
    assert [event.idempotency_key for event in first] == [
        event.idempotency_key for event in second
    ]
    assert [event.lineage.raw_payload_sha256 for event in first] == [
        event.lineage.raw_payload_sha256 for event in second
    ]


def test_0b_trade_preserves_times_sequence_lineage_and_decimal_values() -> None:
    envelope = _messages()[1]
    event = KiwoomNormalizer().normalize(envelope)[0]

    assert event.event_type is CanonicalEventType.TRADE
    assert event.source_timestamp == envelope.source_timestamp
    assert event.received_at == envelope.received_at
    assert event.lineage.session_id == envelope.session_id
    assert event.lineage.source_item_index == 0
    assert isinstance(event.data, MarketObservation)
    assert event.data.observation_source is ObservationSource.REALTIME_0B
    assert event.data.current_price == Decimal(73100)
    assert event.data.change_rate == Decimal("1.25")
    assert event.data.cumulative_trading_value == Decimal(87500000)
    assert event.data.missing_fields == ()
    assert event.to_dict()["sourceTimestamp"] == envelope.source_timestamp.isoformat()


def test_missing_market_values_remain_null_and_are_not_coerced_to_zero() -> None:
    envelope = KiwoomSourceEnvelope(
        source_schema_version="kiwoom.websocket.v1",
        channel=SourceChannel.WEBSOCKET,
        session_id="session-missing",
        source_message_id="message-missing",
        source_sequence=1,
        source_timestamp=datetime(2026, 8, 14, 0, 0, 1, tzinfo=UTC),
        received_at=datetime(2026, 8, 14, 0, 0, 2, tzinfo=UTC),
        market_date=date(2026, 8, 14),
        payload={
            "trnm": "REAL",
            "data": [{"type": "0B", "item": "005930", "values": {"20": "090001"}}],
        },
    )

    event = KiwoomNormalizer().normalize(envelope)[0]

    assert isinstance(event.data, MarketObservation)
    assert event.data.current_price is None
    assert event.data.cumulative_trading_value is None
    assert event.data.missing_fields == ("cumulativeTradingValue", "currentPrice")
    assert event.to_dict()["data"]["currentPrice"] is None  # type: ignore[index]


def test_realtime_condition_exit_is_not_treated_as_an_enter() -> None:
    envelope = KiwoomSourceEnvelope(
        source_schema_version="kiwoom.websocket.v1",
        channel=SourceChannel.WEBSOCKET,
        session_id="session-exit",
        source_message_id="message-exit",
        source_sequence=10,
        source_timestamp=datetime(2026, 8, 14, 0, 1, tzinfo=UTC),
        received_at=datetime(2026, 8, 14, 0, 1, 0, 1000, tzinfo=UTC),
        market_date=date(2026, 8, 14),
        payload={
            "trnm": "REAL",
            "data": [
                {
                    "type": "02",
                    "item": "005930",
                    "values": {"841": "7", "843": "D"},
                }
            ],
        },
    )

    event = KiwoomNormalizer().normalize(envelope)[0]

    assert event.event_type is CanonicalEventType.CANDIDATE_EXITED


def test_supported_message_with_invalid_condition_action_fails_closed() -> None:
    base = _messages()[1]
    envelope = KiwoomSourceEnvelope(
        source_schema_version=base.source_schema_version,
        channel=base.channel,
        session_id=base.session_id,
        source_message_id="invalid-action",
        source_sequence=99,
        source_timestamp=base.source_timestamp,
        received_at=base.received_at,
        market_date=base.market_date,
        payload={
            "trnm": "REAL",
            "data": [
                {
                    "type": "02",
                    "item": "005930",
                    "values": {"841": "7", "843": "?"},
                }
            ],
        },
    )

    with pytest.raises(NormalizationError, match="I 또는 D"):
        KiwoomNormalizer().normalize(envelope)


def test_source_channel_and_schema_version_must_match() -> None:
    websocket = _messages()[0]
    mismatched = replace(
        websocket,
        source_schema_version="kiwoom.ka10095.v1",
    )

    with pytest.raises(NormalizationError, match="일치하지 않습니다"):
        KiwoomNormalizer().normalize(mismatched)
