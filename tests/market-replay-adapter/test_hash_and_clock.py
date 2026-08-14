from __future__ import annotations

from typing import Any

from conftest import assert_same_instant

from packages.adapters.kiwoom import IngestDisposition


def test_same_synthetic_input_has_deterministic_ids_and_hash(
    replay: Any,
    record_factory: Any,
) -> None:
    record = record_factory(
        sequence=41,
        payload={
            "values": {"14": "87500000", "10": "+73100"},
            "item": "005930",
            "type": "0B",
        },
    )
    adapter = replay.MarketReplayAdapter(max_records=1)

    first = adapter.adapt([record]).records[0].event
    second = adapter.adapt([record]).records[0].event

    assert first.event_id == second.event_id
    assert first.idempotency_key == second.idempotency_key
    assert first.lineage.raw_payload_sha256 == second.lineage.raw_payload_sha256
    assert first.lineage.raw_payload_sha256 == (
        "bf2e5f34e89a3e4df47904ec688f8211f7e8cb2f69efa8ad98981e903685dcba"
    )


def test_utc_day_boundary_preserves_each_source_clock_without_rounding(
    replay: Any,
    record_factory: Any,
) -> None:
    before = record_factory(
        sequence=1,
        occurred_at="2026-08-13T23:59:59.999999+00:00",
        received_at="2026-08-14T00:00:00+00:00",
    )
    after = record_factory(
        sequence=2,
        occurred_at="2026-08-14T00:00:00+00:00",
        received_at="2026-08-14T00:00:00.000001+00:00",
    )

    batch = replay.MarketReplayAdapter(max_records=2).adapt([before, after])

    assert [item.disposition for item in batch.records] == [
        IngestDisposition.ACCEPTED,
        IngestDisposition.ACCEPTED,
    ]
    assert_same_instant(
        batch.records[0].event.source_timestamp,
        "2026-08-13T23:59:59.999999+00:00",
    )
    assert_same_instant(
        batch.records[1].event.source_timestamp,
        "2026-08-14T00:00:00+00:00",
    )
    assert batch.records[0].event.source_timestamp < batch.records[1].event.source_timestamp


def test_small_provider_clock_lead_is_preserved_not_rewritten(
    replay: Any,
    record_factory: Any,
) -> None:
    record = record_factory(
        occurred_at="2026-08-14T00:00:04.874000+00:00",
        received_at="2026-08-14T00:00:00+00:00",
    )

    event = replay.MarketReplayAdapter(max_records=1).adapt([record]).accepted_events[0]

    assert_same_instant(event.source_timestamp, "2026-08-14T00:00:04.874000+00:00")
    assert_same_instant(event.received_at, "2026-08-14T00:00:00+00:00")
