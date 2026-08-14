from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from packages.realtime import (
    HotApplyDisposition,
    HotStateIdempotencyConflict,
    HotStateStore,
)

from ._factories import MARKET_DATE, START, realtime_update


def test_hot_state_applies_once_and_duplicate_is_idempotent() -> None:
    store = HotStateStore()
    update = realtime_update("stk_1")

    first = store.apply(update)
    duplicate = store.apply(update)

    assert first.disposition is HotApplyDisposition.APPLIED
    assert first.current is not None and first.current.version == 1
    assert duplicate.disposition is HotApplyDisposition.DUPLICATE
    assert duplicate.current == first.current


def test_same_message_id_with_different_payload_is_rejected() -> None:
    store = HotStateStore()
    update = realtime_update("stk_1", message_id="same-message")
    store.apply(update)

    with pytest.raises(HotStateIdempotencyConflict):
        store.apply(replace(update, current_price=Decimal(111)))


def test_source_sequence_received_time_and_observation_time_never_rewind_state() -> (
    None
):
    store = HotStateStore()
    current = realtime_update(
        "stk_1",
        sequence=10,
        occurred_seconds=10,
        received_seconds=10,
    )
    store.apply(current)

    stale_sequence = store.apply(
        realtime_update(
            "stk_1",
            sequence=9,
            occurred_seconds=11,
            received_seconds=11,
        )
    )
    stale_received = store.apply(
        realtime_update(
            "stk_1",
            sequence=11,
            occurred_seconds=5,
            received_seconds=5,
        )
    )
    still_stale_received = store.apply(
        realtime_update(
            "stk_1",
            sequence=12,
            occurred_seconds=8,
            received_seconds=8,
        )
    )
    stale_observation = store.apply(
        realtime_update(
            "stk_1",
            sequence=13,
            occurred_seconds=9,
            received_seconds=12,
        )
    )

    assert stale_sequence.disposition is HotApplyDisposition.STALE_SOURCE_SEQUENCE
    assert stale_received.disposition is HotApplyDisposition.STALE_RECEIVED_AT
    assert still_stale_received.disposition is HotApplyDisposition.STALE_RECEIVED_AT
    assert stale_observation.disposition is HotApplyDisposition.STALE_OBSERVATION
    assert store.get(market_date=MARKET_DATE, stock_id="stk_1") == (
        HotStateStore.restore(
            store.checkpoint(created_at=START.replace(minute=11))
        ).get(
            market_date=MARKET_DATE,
            stock_id="stk_1",
        )
    )


def test_newer_observation_increments_stock_state_version() -> None:
    store = HotStateStore()
    store.apply(realtime_update("stk_1", sequence=1))
    result = store.apply(
        realtime_update(
            "stk_1",
            sequence=2,
            occurred_seconds=1,
            received_seconds=2,
            price="120",
        )
    )

    assert result.disposition is HotApplyDisposition.APPLIED
    assert result.current is not None
    assert result.current.version == 2
    assert result.current.current_price == Decimal(120)


def test_null_and_actual_zero_are_distinct_hot_states() -> None:
    store = HotStateStore()
    missing = store.apply(realtime_update("stk_1", price=None, cumulative=None))
    zero = store.apply(
        realtime_update(
            "stk_1",
            sequence=2,
            occurred_seconds=1,
            price="0",
            cumulative="0",
        )
    )

    assert missing.current is not None
    assert missing.current.current_price is None
    assert zero.current is not None
    assert zero.current.current_price == Decimal(0)
    assert zero.current.cumulative_trading_value == Decimal(0)


def test_checkpoint_restore_preserves_dedup_cursor_and_accepts_newer_input() -> None:
    store = HotStateStore()
    first_update = realtime_update("stk_1", sequence=1)
    first = store.apply(first_update).current
    checkpoint = store.checkpoint(created_at=START.replace(minute=11))

    recovered = HotStateStore.restore(checkpoint)
    assert (
        checkpoint.content_hash
        == recovered.checkpoint(created_at=checkpoint.created_at).content_hash
    )
    duplicate = recovered.apply(first_update)
    newer = recovered.apply(
        realtime_update(
            "stk_1",
            sequence=2,
            occurred_seconds=1,
            received_seconds=1,
        )
    )

    assert duplicate.disposition is HotApplyDisposition.DUPLICATE
    assert duplicate.current == first
    assert newer.disposition is HotApplyDisposition.APPLIED
    assert newer.current is not None and newer.current.version == 2


def test_older_rest_snapshot_does_not_overwrite_newer_realtime_tick() -> None:
    store = HotStateStore()
    realtime = realtime_update(
        "stk_1",
        sequence=1,
        occurred_seconds=10,
        received_seconds=10,
        price="120",
    )
    store.apply(realtime)
    rest = realtime_update(
        "stk_1",
        source="REST_SNAPSHOT",
        sequence=1,
        occurred_seconds=5,
        received_seconds=20,
        price="100",
    )

    result = store.apply(rest)

    assert result.disposition is HotApplyDisposition.STALE_OBSERVATION
    current = store.get(market_date=MARKET_DATE, stock_id="stk_1")
    assert current is not None and current.current_price == Decimal(120)
