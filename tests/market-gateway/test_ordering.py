from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from packages.adapters.kiwoom import (
    EventOrderFence,
    IngestDisposition,
    KiwoomNormalizer,
    KiwoomSourceEnvelope,
    SourceChannel,
)

BASE = datetime(2026, 8, 14, tzinfo=UTC)


def _trade_envelope(
    *,
    session_id: str = "session-current",
    message_id: str = "trade-1",
    sequence: int = 1,
    source_at: datetime = BASE + timedelta(seconds=1),
    received_at: datetime = BASE + timedelta(seconds=2),
    price: str = "1000",
) -> KiwoomSourceEnvelope:
    return KiwoomSourceEnvelope(
        source_schema_version="kiwoom.websocket.v1",
        channel=SourceChannel.WEBSOCKET,
        session_id=session_id,
        source_message_id=message_id,
        source_sequence=sequence,
        source_timestamp=source_at,
        received_at=received_at,
        market_date=date(2026, 8, 14),
        payload={
            "trnm": "REAL",
            "data": [
                {
                    "type": "0B",
                    "item": "005930",
                    "values": {"10": price, "14": "10000"},
                }
            ],
        },
    )


def _snapshot_envelope(
    *,
    session_id: str = "session-current",
    sequence: int = 1,
    source_at: datetime = BASE,
    received_at: datetime = BASE + timedelta(seconds=3),
) -> KiwoomSourceEnvelope:
    return KiwoomSourceEnvelope(
        source_schema_version="kiwoom.ka10095.v1",
        channel=SourceChannel.REST_SNAPSHOT,
        session_id=session_id,
        source_message_id="snapshot-1",
        source_sequence=sequence,
        source_timestamp=source_at,
        received_at=received_at,
        market_date=date(2026, 8, 14),
        request_id="request-1",
        payload={
            "apiId": "ka10095",
            "rows": [
                {
                    "stk_cd": "005930",
                    "cur_prc": "999",
                    "acc_trde_prica": "9999",
                }
            ],
        },
    )


def _candidate_envelope(
    *,
    sequence: int,
    action: str,
    source_at: datetime,
    received_at: datetime,
    condition_id: str = "7",
) -> KiwoomSourceEnvelope:
    return KiwoomSourceEnvelope(
        source_schema_version="kiwoom.websocket.v1",
        channel=SourceChannel.WEBSOCKET,
        session_id="session-current",
        source_message_id=f"candidate-{sequence}",
        source_sequence=sequence,
        source_timestamp=source_at,
        received_at=received_at,
        market_date=date(2026, 8, 14),
        payload={
            "trnm": "REAL",
            "data": [
                {
                    "type": "02",
                    "item": "005930",
                    "values": {"841": condition_id, "843": action},
                }
            ],
        },
    )


def test_duplicate_and_same_key_payload_conflict_are_distinct() -> None:
    event = KiwoomNormalizer().normalize(_trade_envelope())[0]
    fence = EventOrderFence()
    fence.begin_session("session-current")

    accepted = fence.evaluate(event)
    duplicate = fence.evaluate(event)
    conflicting = fence.evaluate(
        replace(
            event,
            lineage=replace(event.lineage, raw_payload_sha256="f" * 64),
        )
    )

    assert accepted.disposition is IngestDisposition.ACCEPTED
    assert duplicate.disposition is IngestDisposition.DUPLICATE
    assert conflicting.disposition is IngestDisposition.CONFLICT


def test_candidate_condition_identity_separates_keys_and_fence_entries() -> None:
    normalizer = KiwoomNormalizer()
    source_at = BASE + timedelta(seconds=1)
    received_at = BASE + timedelta(seconds=2)
    condition_seven = normalizer.normalize(
        _candidate_envelope(
            sequence=1,
            action="I",
            source_at=source_at,
            received_at=received_at,
            condition_id="7",
        )
    )[0]
    condition_eight = normalizer.normalize(
        _candidate_envelope(
            sequence=1,
            action="I",
            source_at=source_at,
            received_at=received_at,
            condition_id="8",
        )
    )[0]
    fence = EventOrderFence()
    fence.begin_session("session-current")

    assert condition_seven.event_id != condition_eight.event_id
    assert condition_seven.idempotency_key != condition_eight.idempotency_key
    assert fence.evaluate(condition_seven).disposition is IngestDisposition.ACCEPTED
    assert fence.evaluate(condition_eight).disposition is IngestDisposition.ACCEPTED


def test_same_condition_source_event_converges_to_duplicate() -> None:
    envelope = _candidate_envelope(
        sequence=1,
        action="I",
        source_at=BASE + timedelta(seconds=1),
        received_at=BASE + timedelta(seconds=2),
        condition_id="7",
    )
    normalizer = KiwoomNormalizer()
    first = normalizer.normalize(envelope)[0]
    redelivered = normalizer.normalize(envelope)[0]
    fence = EventOrderFence()
    fence.begin_session("session-current")

    assert first.event_id == redelivered.event_id
    assert first.idempotency_key == redelivered.idempotency_key
    assert fence.evaluate(first).disposition is IngestDisposition.ACCEPTED
    assert fence.evaluate(redelivered).disposition is IngestDisposition.DUPLICATE


def test_trade_and_snapshot_natural_keys_remain_unchanged() -> None:
    normalizer = KiwoomNormalizer()
    trade = normalizer.normalize(_trade_envelope())[0]
    snapshot = normalizer.normalize(_snapshot_envelope())[0]

    assert trade.idempotency_key == (
        "kiwoom:541c291af5a96a1c227607e6fde4743fd6ca3a9ad448123f5a3e1ab199166f0d"
    )
    assert snapshot.idempotency_key == (
        "kiwoom:149512d6032671172d55554612d79b86bc3d3ec491142cbb9f4f16cd41430505"
    )


def test_lower_sequence_is_rejected_even_if_it_arrives_later() -> None:
    normalizer = KiwoomNormalizer()
    newer = normalizer.normalize(
        _trade_envelope(sequence=2, source_at=BASE + timedelta(seconds=2))
    )[0]
    older = normalizer.normalize(
        _trade_envelope(
            message_id="trade-old",
            sequence=1,
            source_at=BASE + timedelta(seconds=1),
            received_at=BASE + timedelta(seconds=4),
        )
    )[0]
    fence = EventOrderFence()
    fence.begin_session("session-current")

    assert fence.evaluate(newer).disposition is IngestDisposition.ACCEPTED
    assert fence.evaluate(older).disposition is IngestDisposition.OUT_OF_ORDER


def test_received_timestamp_regression_is_rejected_with_higher_sequence() -> None:
    normalizer = KiwoomNormalizer()
    first = normalizer.normalize(
        _trade_envelope(sequence=1, received_at=BASE + timedelta(seconds=5))
    )[0]
    regressed = normalizer.normalize(
        _trade_envelope(
            message_id="trade-2",
            sequence=2,
            source_at=BASE + timedelta(seconds=2),
            received_at=BASE + timedelta(seconds=4),
        )
    )[0]
    fence = EventOrderFence()
    fence.begin_session("session-current")

    assert fence.evaluate(first).disposition is IngestDisposition.ACCEPTED
    assert fence.evaluate(regressed).disposition is IngestDisposition.OUT_OF_ORDER


def test_candidate_enter_and_exit_share_one_ordering_lane() -> None:
    normalizer = KiwoomNormalizer()
    entered = normalizer.normalize(
        _candidate_envelope(
            sequence=2,
            action="I",
            source_at=BASE + timedelta(seconds=2),
            received_at=BASE + timedelta(seconds=2),
        )
    )[0]
    old_exit = normalizer.normalize(
        _candidate_envelope(
            sequence=1,
            action="D",
            source_at=BASE + timedelta(seconds=1),
            received_at=BASE + timedelta(seconds=3),
        )
    )[0]
    fence = EventOrderFence()
    fence.begin_session("session-current")

    assert fence.evaluate(entered).disposition is IngestDisposition.ACCEPTED
    assert fence.evaluate(old_exit).disposition is IngestDisposition.OUT_OF_ORDER


def test_old_snapshot_cannot_overwrite_newer_realtime_trade() -> None:
    normalizer = KiwoomNormalizer()
    trade = normalizer.normalize(
        _trade_envelope(source_at=BASE + timedelta(seconds=2))
    )[0]
    snapshot = normalizer.normalize(
        _snapshot_envelope(source_at=BASE + timedelta(seconds=1))
    )[0]
    fence = EventOrderFence()
    fence.begin_session("session-current")

    assert fence.evaluate(trade).disposition is IngestDisposition.ACCEPTED
    assert fence.evaluate(snapshot).disposition is IngestDisposition.OLD_OBSERVATION


def test_snapshot_at_same_source_time_cannot_downgrade_realtime_trade() -> None:
    normalizer = KiwoomNormalizer()
    source_at = BASE + timedelta(seconds=2)
    trade = normalizer.normalize(_trade_envelope(source_at=source_at))[0]
    snapshot = normalizer.normalize(_snapshot_envelope(source_at=source_at))[0]
    fence = EventOrderFence()
    fence.begin_session("session-current")

    assert fence.evaluate(trade).disposition is IngestDisposition.ACCEPTED
    assert fence.evaluate(snapshot).disposition is IngestDisposition.OLD_OBSERVATION


def test_realtime_trade_at_same_source_time_can_upgrade_snapshot() -> None:
    normalizer = KiwoomNormalizer()
    source_at = BASE + timedelta(seconds=2)
    snapshot = normalizer.normalize(
        _snapshot_envelope(
            source_at=source_at,
            received_at=BASE + timedelta(seconds=3),
        )
    )[0]
    trade = normalizer.normalize(
        _trade_envelope(
            source_at=source_at,
            received_at=BASE + timedelta(seconds=2),
        )
    )[0]
    fence = EventOrderFence()
    fence.begin_session("session-current")

    assert fence.evaluate(snapshot).disposition is IngestDisposition.ACCEPTED
    assert fence.evaluate(trade).disposition is IngestDisposition.ACCEPTED


def test_previous_session_input_is_blocked_after_reconnect() -> None:
    event = KiwoomNormalizer().normalize(
        _trade_envelope(session_id="session-old")
    )[0]
    fence = EventOrderFence()
    fence.begin_session("session-old")
    assert fence.evaluate(event).disposition is IngestDisposition.ACCEPTED

    fence.begin_session("session-new")

    replayed = replace(
        event,
        event_id="mkt_" + "a" * 26,
        idempotency_key="kiwoom:" + "b" * 64,
        lineage=replace(event.lineage, source_message_id="late-old-message"),
    )
    assert fence.evaluate(replayed).disposition is IngestDisposition.OLD_SESSION
