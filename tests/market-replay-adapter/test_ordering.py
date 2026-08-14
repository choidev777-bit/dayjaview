from __future__ import annotations

from typing import Any

from packages.adapters.kiwoom import IngestDisposition


def _trade_payload(price: str) -> dict[str, object]:
    return {
        "type": "0B",
        "item": "005930",
        "values": {"10": price, "14": "100000"},
    }


def test_duplicate_is_rejected_without_becoming_product_input(
    replay: Any,
    record_factory: Any,
) -> None:
    record = record_factory(payload=_trade_payload("1000"))

    batch = replay.MarketReplayAdapter(max_records=2).adapt([record, record])

    assert [item.disposition for item in batch.records] == [
        IngestDisposition.ACCEPTED,
        IngestDisposition.DUPLICATE,
    ]
    assert len(batch.accepted_events) == 1
    assert len(batch.rejected) == 1


def test_same_identity_with_changed_payload_is_a_conflict(
    replay: Any,
    record_factory: Any,
) -> None:
    first = record_factory(payload=_trade_payload("1000"))
    conflicting = record_factory(payload=_trade_payload("1001"))

    batch = replay.MarketReplayAdapter(max_records=2).adapt([first, conflicting])

    assert batch.records[0].disposition is IngestDisposition.ACCEPTED
    assert batch.records[1].disposition is IngestDisposition.CONFLICT
    assert batch.accepted_events == (batch.records[0].event,)


def test_lower_capture_sequence_is_out_of_order_even_if_delivered_later(
    replay: Any,
    record_factory: Any,
) -> None:
    newer = record_factory(
        sequence=2,
        occurred_at="2026-08-14T00:00:02+00:00",
        received_at="2026-08-14T00:00:02.100000+00:00",
        payload=_trade_payload("1002"),
    )
    older = record_factory(
        sequence=1,
        occurred_at="2026-08-14T00:00:01+00:00",
        received_at="2026-08-14T00:00:03+00:00",
        payload=_trade_payload("1001"),
    )

    batch = replay.MarketReplayAdapter(max_records=2).adapt([newer, older])

    assert batch.records[0].disposition is IngestDisposition.ACCEPTED
    assert batch.records[1].disposition is IngestDisposition.OUT_OF_ORDER
    assert batch.accepted_events == (batch.records[0].event,)


def test_old_rest_snapshot_cannot_overwrite_newer_realtime_tick(
    replay: Any,
    record_factory: Any,
) -> None:
    trade = record_factory(
        sequence=1,
        occurred_at="2026-08-14T00:00:02+00:00",
        received_at="2026-08-14T00:00:02.100000+00:00",
        payload=_trade_payload("1002"),
    )
    snapshot_payload = {
        "apiId": "ka10095",
        "source": "REST_SNAPSHOT",
        "asOf": "2026-08-14T00:00:01+00:00",
        "raw": {
            "stk_cd": "005930",
            "cur_prc": "1001",
            "acc_trde_prica": "90000",
        },
    }
    snapshot = record_factory(
        sequence=2,
        event_type="market.snapshot",
        source="synthetic_kiwoom_rest",
        occurred_at="2026-08-14T00:00:01+00:00",
        received_at="2026-08-14T00:00:03+00:00",
        payload=snapshot_payload,
    )

    batch = replay.MarketReplayAdapter(max_records=2).adapt([trade, snapshot])

    assert batch.records[0].disposition is IngestDisposition.ACCEPTED
    assert batch.records[1].disposition is IngestDisposition.OLD_OBSERVATION
    assert batch.accepted_events == (batch.records[0].event,)


def test_same_source_time_snapshot_cannot_downgrade_realtime_tick(
    replay: Any,
    record_factory: Any,
) -> None:
    source_at = "2026-08-14T00:00:02+00:00"
    trade = record_factory(
        sequence=1,
        occurred_at=source_at,
        received_at="2026-08-14T00:00:02.100000+00:00",
        payload=_trade_payload("1002"),
    )
    snapshot_payload = {
        "apiId": "ka10095",
        "source": "REST_SNAPSHOT",
        "asOf": source_at,
        "raw": {
            "stk_cd": "005930",
            "cur_prc": "1001",
            "acc_trde_prica": "90000",
        },
    }
    snapshot = record_factory(
        sequence=2,
        event_type="market.snapshot",
        source="synthetic_kiwoom_rest",
        occurred_at=source_at,
        received_at="2026-08-14T00:00:03+00:00",
        payload=snapshot_payload,
    )

    batch = replay.MarketReplayAdapter(max_records=2).adapt([trade, snapshot])

    assert batch.records[1].disposition is IngestDisposition.OLD_OBSERVATION
