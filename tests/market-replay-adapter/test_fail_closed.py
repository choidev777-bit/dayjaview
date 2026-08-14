from __future__ import annotations

import json
from typing import Any

import pytest


def test_malformed_unknown_and_schema_mismatch_fail_closed(
    replay: Any,
    record_factory: Any,
) -> None:
    missing = record_factory()
    del missing["payload"]
    unknown = record_factory(event_type="market.index")
    mismatched = record_factory(schema_version="2.0.0")
    adapter = replay.MarketReplayAdapter(max_records=1)

    with pytest.raises(replay.CaptureRecordError, match="필수 field"):
        adapter.adapt([missing])
    with pytest.raises(replay.UnsupportedEventError, match="market.index"):
        adapter.adapt([unknown])
    with pytest.raises(replay.SchemaMismatchError, match="2.0.0"):
        adapter.adapt([mismatched])


def test_payload_tampering_and_stock_lineage_conflict_fail_closed(
    replay: Any,
    record_factory: Any,
) -> None:
    tampered = record_factory()
    tampered["payload"] = {
        "type": "0B",
        "item": "005930",
        "values": {"10": "99999", "14": "1"},
    }
    wrong_stock = record_factory(stock_code="000660")
    adapter = replay.MarketReplayAdapter(max_records=1)

    with pytest.raises(replay.PayloadIntegrityError, match="일치하지 않습니다"):
        adapter.adapt([tampered])
    with pytest.raises(replay.CaptureRecordError, match="종목코드가 다릅니다"):
        adapter.adapt([wrong_stock])


def test_truncated_ndjson_and_early_stream_end_are_rejected(
    replay: Any,
    record_factory: Any,
) -> None:
    record = record_factory()
    line_without_newline = json.dumps(record, ensure_ascii=False)
    adapter = replay.MarketReplayAdapter(max_records=2)

    with pytest.raises(replay.TruncatedInputError, match="newline"):
        adapter.adapt_ndjson([line_without_newline])
    with pytest.raises(replay.TruncatedInputError, match="expected_count=2"):
        adapter.adapt([record], expected_count=2)


def test_complete_ndjson_line_is_accepted_without_disk_discovery(
    replay: Any,
    record_factory: Any,
) -> None:
    record = record_factory()
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"

    batch = replay.MarketReplayAdapter(max_records=1).adapt_ndjson(
        [line], expected_count=1
    )

    assert len(batch.accepted_events) == 1


def test_record_bound_and_path_like_inputs_are_rejected(
    replay: Any,
    record_factory: Any,
    tmp_path: Any,
) -> None:
    adapter = replay.MarketReplayAdapter(max_records=1)

    with pytest.raises(replay.InputLimitExceededError, match="max_records=1"):
        adapter.adapt([record_factory(sequence=1), record_factory(sequence=2)])
    with pytest.raises(replay.ReplayAdapterError, match="파일 경로"):
        adapter.adapt(tmp_path / "events.ndjson")


def test_failed_batch_does_not_poison_later_clean_adaptation(
    replay: Any,
    record_factory: Any,
) -> None:
    adapter = replay.MarketReplayAdapter(max_records=2)
    malformed = record_factory(sequence=2)
    malformed["payloadSha256"] = "0" * 64

    with pytest.raises(replay.PayloadIntegrityError):
        adapter.adapt([record_factory(sequence=1), malformed])

    clean = adapter.adapt([record_factory(sequence=1)])
    assert len(clean.accepted_events) == 1


def test_naive_clock_and_mixed_sessions_are_rejected(
    replay: Any,
    record_factory: Any,
) -> None:
    naive = record_factory(occurred_at="2026-08-14T09:00:00")
    first = record_factory(sequence=1)
    second = record_factory(sequence=2, run_id="market-2026-08-14-other")
    adapter = replay.MarketReplayAdapter(max_records=2)

    with pytest.raises(replay.CaptureRecordError, match="timezone"):
        adapter.adapt([naive])
    with pytest.raises(replay.MixedSessionError, match="runId"):
        adapter.adapt([first, second])
