from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from packages.domain import (
    DataStatus,
    MembershipRole,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.events import InMemoryEventStore
from packages.pipeline import MarketDataPipeline, MarketPublishLoop, PublishedView
from packages.realtime import (
    InMemorySnapshotRepository,
    StockRealtimeUpdate,
    VersionedThemeCatalog,
)

MARKET_DATE = date(2026, 8, 14)
KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)
BASE = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
MEMBERSHIP_VERSION = "membership-test-1"
STOCK_IDS = ("KRX:000001", "KRX:000002", "KRX:000003")


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def _pipeline() -> MarketDataPipeline:
    catalog = VersionedThemeCatalog(
        (
            ThemeMembershipSnapshot(
                theme_id="thm_full",
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=tuple(
                    ThemeMember(stock_id, MembershipRole.CORE)
                    for stock_id in STOCK_IDS
                ),
            ),
        )
    )
    references = tuple(
        StockReference(
            stock_id=stock_id,
            effective_for=MARKET_DATE,
            known_at=KNOWN_AT,
            previous_adjusted_close=Decimal("10000"),
            listed_shares=1_000_000,
            free_float_ratio=Decimal("0.5"),
            free_float_validated=True,
            version="reference-test-1",
        )
        for stock_id in STOCK_IDS
    )
    return MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_loop_test",
        schema_version="2026-08-14.1",
        catalog=catalog,
        references=references,
        membership_version=MEMBERSHIP_VERSION,
        theme_names={"thm_full": "테스트 테마"},
        stock_names={stock_id: stock_id for stock_id in STOCK_IDS},
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )


def _update(stock_id: str, *, seconds: int) -> StockRealtimeUpdate:
    at = BASE + timedelta(seconds=seconds)
    return StockRealtimeUpdate(
        message_id=f"msg_{stock_id}_{seconds}",
        stock_id=stock_id,
        market_date=MARKET_DATE,
        source="test-session",
        source_sequence=seconds,
        occurred_at=at,
        received_at=at,
        current_price=Decimal("10300"),
        cumulative_trading_value=Decimal("1000000"),
    )


def _loop(
    pipeline: MarketDataPipeline,
    clock: FakeClock,
    published: list[PublishedView],
    *,
    pending: list[StockRealtimeUpdate] | None = None,
    market_close_at: datetime | None = None,
) -> MarketPublishLoop:
    def poll_updates() -> list[StockRealtimeUpdate]:
        if pending is None:
            return []
        drained, pending[:] = list(pending), []
        return drained

    return MarketPublishLoop(
        pipeline=pipeline,
        on_published=published.append,
        data_status=lambda: DataStatus.LIVE,
        interval=timedelta(seconds=2),
        poll_updates=poll_updates,
        market_close_at=market_close_at,
        clock=clock,
    )


def test_tick_ingests_new_observations_and_publishes() -> None:
    pipeline = _pipeline()
    clock = FakeClock(BASE + timedelta(seconds=7))
    published: list[PublishedView] = []
    pending = [
        _update(stock_id, seconds=index + 1)
        for index, stock_id in enumerate(STOCK_IDS)
    ]
    loop = _loop(pipeline, clock, published, pending=pending)

    first = loop.tick()
    # 첫 tick: 관측이 ingest되고 후보 상태라 rankings는 비어 있다.
    assert not pending
    assert first.rankings.payload == {"items": []}
    assert first.rankings.sequence == 1

    clock.advance(timedelta(seconds=13))
    second = loop.tick()
    items = second.rankings.payload["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["lifecycleStatus"] == "ACTIVE"
    assert second.rankings.sequence == 2
    assert [view.rankings.sequence for view in published] == [1, 2]


def test_run_repeats_ticks_with_interval_until_cancelled() -> None:
    pipeline = _pipeline()
    clock = FakeClock(BASE + timedelta(seconds=7))
    published: list[PublishedView] = []
    loop = _loop(pipeline, clock, published)
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        clock.advance(timedelta(seconds=delay))
        if len(delays) == 3:
            raise asyncio.CancelledError

    async def scenario() -> None:
        with pytest.raises(asyncio.CancelledError):
            await loop.run(sleep=fake_sleep)

    asyncio.run(scenario())
    assert delays == [2.0, 2.0, 2.0]
    assert [view.rankings.sequence for view in published] == [1, 2, 3]


def test_market_close_is_evaluated_once_after_close_time() -> None:
    pipeline = _pipeline()
    clock = FakeClock(BASE + timedelta(seconds=7))
    published: list[PublishedView] = []
    pending = [
        _update(stock_id, seconds=index + 1)
        for index, stock_id in enumerate(STOCK_IDS)
    ]
    loop = _loop(
        pipeline,
        clock,
        published,
        pending=pending,
        market_close_at=BASE + timedelta(minutes=1),
    )

    loop.tick()
    clock.advance(timedelta(seconds=13))
    active = loop.tick()
    assert active.rankings.payload["items"]
    assert not loop.market_close_applied

    clock.advance(timedelta(minutes=1))
    closed = loop.tick()
    assert loop.market_close_applied
    assert closed.rankings.payload == {"items": []}
    assert {event.lifecycle_status.value for event in closed.events} == {"CLOSED"}

    # 마감 평가는 한 번만 수행되고 이후 tick에서도 발행은 계속된다.
    clock.advance(timedelta(seconds=2))
    after = loop.tick()
    assert after.rankings.sequence == closed.rankings.sequence + 1
    assert {event.lifecycle_status.value for event in after.events} == {"CLOSED"}
