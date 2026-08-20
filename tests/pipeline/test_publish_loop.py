from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
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
from packages.pipeline import (
    MarketDataPipeline,
    MarketPublishLoop,
    PublishedView,
    TradingDayLoop,
    session_close_at,
)
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


def _pipeline(
    market_date: date = MARKET_DATE,
    *,
    previous_close: Decimal = Decimal("10000"),
) -> MarketDataPipeline:
    catalog = VersionedThemeCatalog(
        (
            ThemeMembershipSnapshot(
                theme_id="thm_full",
                version=MEMBERSHIP_VERSION,
                effective_from=market_date,
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
            effective_for=market_date,
            known_at=KNOWN_AT,
            previous_adjusted_close=previous_close,
            listed_shares=1_000_000,
            free_float_ratio=Decimal("0.5"),
            free_float_validated=True,
            version="reference-test-1",
        )
        for stock_id in STOCK_IDS
    )
    return MarketDataPipeline(
        market_date=market_date,
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


def _update(
    stock_id: str,
    *,
    seconds: int,
    market_date: date = MARKET_DATE,
    current_price: Decimal = Decimal("10300"),
) -> StockRealtimeUpdate:
    at = datetime.combine(market_date, time(0, 0), tzinfo=UTC) + timedelta(
        seconds=seconds
    )
    return StockRealtimeUpdate(
        message_id=f"msg_{stock_id}_{market_date}_{seconds}",
        stock_id=stock_id,
        market_date=market_date,
        source="test-session",
        source_sequence=seconds,
        occurred_at=at,
        received_at=at,
        current_price=current_price,
        cumulative_trading_value=Decimal("1000000"),
    )


def _loop(
    pipeline: MarketDataPipeline,
    clock: FakeClock,
    published: list[PublishedView],
    *,
    pending: list[StockRealtimeUpdate] | None = None,
    market_open_at: datetime | None = None,
    market_close_at: datetime | None = None,
    before_publish=None,
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
        before_publish=before_publish,
        market_open_at=market_open_at,
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


def test_tick_refreshes_persisted_evidence_before_publishing() -> None:
    pipeline = _pipeline()
    clock = FakeClock(BASE + timedelta(seconds=7))
    published: list[PublishedView] = []
    order: list[str] = []

    loop = _loop(
        pipeline,
        clock,
        published,
        before_publish=lambda: order.append("evidence"),
    )
    loop.tick()

    assert order == ["evidence"]
    assert len(published) == 1


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


def test_data_status_follows_the_session_window() -> None:
    """개장 전은 PREOPEN, 마감 후는 CLOSED. 게이트웨이 health는 장중에만 쓴다."""

    pipeline = _pipeline()
    clock = FakeClock(BASE)
    published: list[PublishedView] = []
    loop = _loop(
        pipeline,
        clock,
        published,
        market_open_at=BASE + timedelta(minutes=1),
        market_close_at=BASE + timedelta(minutes=5),
    )

    assert loop.tick().rankings.data_status is DataStatus.PREOPEN

    clock.advance(timedelta(minutes=2))
    assert loop.tick().rankings.data_status is DataStatus.LIVE

    clock.advance(timedelta(minutes=4))
    assert loop.tick().rankings.data_status is DataStatus.CLOSED


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
    # 마감 뒤에도 그날 최종 순위는 세워 둔다 (screen_spec 4.1·5.7).
    assert closed.rankings.payload["items"]
    assert closed.treemap.payload["items"]
    assert {event.lifecycle_status.value for event in closed.events} == {"CLOSED"}

    # 마감 평가는 한 번만 수행되고 이후 tick에서도 발행은 계속된다.
    clock.advance(timedelta(seconds=2))
    after = loop.tick()
    assert after.rankings.sequence == closed.rankings.sequence + 1
    assert {event.lifecycle_status.value for event in after.events} == {"CLOSED"}


# --- 거래일 전환 (A-8) ---------------------------------------------------

# 2026-08-14는 금요일, 08-15·08-16은 주말, 08-17은 월요일이다.
FRIDAY = date(2026, 8, 14)
SATURDAY = date(2026, 8, 15)
MONDAY = date(2026, 8, 17)
CLOSE_KST = time(15, 30)


def _session_builder(
    clock: FakeClock,
    published: list[PublishedView],
    built: list[date],
    *,
    previous_close_by_date: dict[date, Decimal] | None = None,
    sessions: dict[date, MarketPublishLoop] | None = None,
    pending: list[StockRealtimeUpdate] | None = None,
):
    def build(market_date: date) -> MarketPublishLoop:
        built.append(market_date)
        closes = previous_close_by_date or {}
        loop = _loop(
            _pipeline(
                market_date,
                previous_close=closes.get(market_date, Decimal("10000")),
            ),
            clock,
            published,
            pending=pending,
            market_close_at=session_close_at(market_date, close_time=CLOSE_KST),
        )
        if sessions is not None:
            sessions[market_date] = loop
        return loop

    return build


def test_trading_day_loop_rolls_over_to_the_next_session() -> None:
    """날짜가 바뀌면 이전 장을 닫고 그날 파이프라인으로 갈아끼운다."""

    clock = FakeClock(datetime(2026, 8, 14, 1, 0, tzinfo=UTC))  # 금 10:00 KST
    published: list[PublishedView] = []
    built: list[date] = []
    sessions: dict[date, MarketPublishLoop] = {}
    loop = TradingDayLoop(
        build_session=_session_builder(clock, published, built, sessions=sessions),
        interval=timedelta(seconds=2),
        clock=clock,
    )

    assert loop.tick() is not None
    assert loop.market_date == FRIDAY
    assert built == [FRIDAY]

    # 주말에는 세션을 세우지 않는다.
    clock.now = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)
    assert loop.tick() is None
    assert loop.market_date == SATURDAY
    assert loop.session is None
    # 넘어가면서 금요일 장은 닫혔다.
    assert sessions[FRIDAY].market_close_applied

    # 월요일에는 그날 파이프라인을 새로 세운다.
    clock.now = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)
    assert loop.tick() is not None
    assert loop.market_date == MONDAY
    assert built == [FRIDAY, MONDAY]


def test_trading_day_loop_uses_the_new_days_previous_close() -> None:
    """전환 뒤 수익률은 새 거래일 전일종가로 계산된다.

    어제 전일종가(10,000)를 물고 있으면 같은 12,600원이 +26%로 잡힌다.
    월요일 전일종가 12,000을 써야 +5%가 나온다.
    """

    clock = FakeClock(datetime(2026, 8, 14, 1, 0, tzinfo=UTC))
    published: list[PublishedView] = []
    built: list[date] = []
    pending: list[StockRealtimeUpdate] = []
    loop = TradingDayLoop(
        build_session=_session_builder(
            clock,
            published,
            built,
            previous_close_by_date={
                FRIDAY: Decimal("10000"),
                MONDAY: Decimal("12000"),
            },
            pending=pending,
        ),
        interval=timedelta(seconds=2),
        clock=clock,
    )
    loop.tick()

    monday_open = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)
    clock.now = monday_open
    pending.extend(
        _update(
            stock_id,
            seconds=index + 1,
            market_date=MONDAY,
            current_price=Decimal("12600"),
        )
        for index, stock_id in enumerate(STOCK_IDS)
    )
    loop.tick()
    # hysteresis activate_after(10초)가 지나야 ACTIVE가 되고 순위에 값이 실린다.
    clock.now = monday_open + timedelta(seconds=20)
    view = loop.tick()

    assert view is not None
    items = view.rankings.payload["items"]
    assert isinstance(items, list) and len(items) == 1
    assert items[0]["weightedReturn"] == pytest.approx(0.05)


def test_trading_day_loop_closes_the_previous_session_without_a_late_tick() -> None:
    """마감 시각 뒤 tick이 한 번도 없었어도 전환할 때 장이 닫힌다."""

    clock = FakeClock(datetime(2026, 8, 14, 1, 0, tzinfo=UTC))
    published: list[PublishedView] = []
    built: list[date] = []
    sessions: dict[date, MarketPublishLoop] = {}
    loop = TradingDayLoop(
        build_session=_session_builder(clock, published, built, sessions=sessions),
        interval=timedelta(seconds=2),
        clock=clock,
    )
    loop.tick()
    assert not sessions[FRIDAY].market_close_applied

    clock.now = datetime(2026, 8, 17, 1, 0, tzinfo=UTC)
    loop.tick()

    assert sessions[FRIDAY].market_close_applied


def test_trading_day_loop_skips_a_day_the_calendar_says_is_closed() -> None:
    """달력이 휴장으로 아는 평일에는 세션을 세우지 않는다."""

    clock = FakeClock(datetime(2026, 8, 17, 1, 0, tzinfo=UTC))
    published: list[PublishedView] = []
    built: list[date] = []
    loop = TradingDayLoop(
        build_session=_session_builder(clock, published, built),
        interval=timedelta(seconds=2),
        clock=clock,
        known_trading_days=lambda value: False if value == MONDAY else None,
    )

    assert loop.tick() is None
    assert loop.session is None
    assert built == []


def test_intraday_base_price_fills_the_missing_previous_close() -> None:
    """장중에는 당일 KRX row가 없어 전일종가가 빈다. 시세의 기준가가 그 자리를 메운다.

    기준가가 없으면 유동시총을 못 구해 Coverage INSUFFICIENT로 순위가 빈다.
    """

    catalog = VersionedThemeCatalog(
        (
            ThemeMembershipSnapshot(
                theme_id="thm_full",
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=tuple(
                    ThemeMember(stock_id, MembershipRole.CORE) for stock_id in STOCK_IDS
                ),
            ),
        )
    )
    # 장중 기준정보: 유동주식비율은 공시로 확보했지만 전일종가는 아직 없다.
    references = tuple(
        StockReference(
            stock_id=stock_id,
            effective_for=MARKET_DATE,
            known_at=KNOWN_AT,
            previous_adjusted_close=None,
            listed_shares=1_000_000,
            free_float_ratio=Decimal("0.5"),
            free_float_validated=True,
            version="reference-intraday",
        )
        for stock_id in STOCK_IDS
    )
    pipeline = MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_base_price",
        schema_version="2026-08-14.1",
        catalog=catalog,
        references=references,
        membership_version=MEMBERSHIP_VERSION,
        theme_names={"thm_full": "테스트 테마"},
        stock_names={stock_id: stock_id for stock_id in STOCK_IDS},
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )

    for index, stock_id in enumerate(STOCK_IDS):
        pipeline.apply_update(
            replace(
                _update(stock_id, seconds=index + 1, current_price=Decimal("10500")),
                base_price=Decimal("10000"),
            )
        )
    pipeline.publish(now=BASE + timedelta(seconds=7), data_status=DataStatus.LIVE)
    view = pipeline.publish(now=BASE + timedelta(seconds=20), data_status=DataStatus.LIVE)

    items = view.rankings.payload["items"]
    assert isinstance(items, list) and len(items) == 1
    assert items[0]["weightedReturn"] == pytest.approx(0.05)


def test_intraday_base_price_does_not_override_a_known_previous_close() -> None:
    """전일종가가 이미 있으면 기준가로 덮지 않는다."""

    pipeline = _pipeline()  # previous_adjusted_close=10,000
    for index, stock_id in enumerate(STOCK_IDS):
        pipeline.apply_update(
            replace(
                _update(stock_id, seconds=index + 1, current_price=Decimal("10500")),
                base_price=Decimal("20000"),
            )
        )
    pipeline.publish(now=BASE + timedelta(seconds=7), data_status=DataStatus.LIVE)
    view = pipeline.publish(now=BASE + timedelta(seconds=20), data_status=DataStatus.LIVE)

    items = view.rankings.payload["items"]
    assert isinstance(items, list) and len(items) == 1
    # 기준가 20,000을 썼다면 -47.5%가 됐을 것이다.
    assert items[0]["weightedReturn"] == pytest.approx(0.05)


def test_trading_day_loop_retries_a_failed_session_within_the_day() -> None:
    """자정 준비 실패를 그날 안에 다시 시도한다.

    2026-08-20 운영: 자정 수집 실패 후 재시도가 없어 수동 재시작(14:46)까지
    엔진이 서지 않았다.
    """

    clock = FakeClock(datetime(2026, 8, 13, 15, 0, tzinfo=UTC))  # 금 00:00 KST
    built: list[date] = []
    published: list[PublishedView] = []
    sessions: dict[date, MarketPublishLoop] = {}
    working_builder = _session_builder(clock, published, built, sessions=sessions)

    def flaky_builder(market_date: date) -> MarketPublishLoop | None:
        if len(built) < 1:
            built.append(market_date)
            return None  # 자정 수집 실패
        return working_builder(market_date)

    loop = TradingDayLoop(
        build_session=flaky_builder,
        interval=timedelta(seconds=2),
        clock=clock,
    )

    assert loop.tick() is None
    assert built == [FRIDAY]

    # 재시도 간격 전에는 다시 세우지 않는다.
    clock.now += timedelta(minutes=1)
    assert loop.tick() is None
    assert built == [FRIDAY]

    # 간격이 지나면 같은 날 안에서 다시 세운다.
    clock.now += timedelta(minutes=5)
    assert loop.tick() is not None
    assert built == [FRIDAY, FRIDAY]
    assert loop.session is not None


def test_trading_day_loop_survives_a_failing_tick() -> None:
    """tick 하나가 실패해도 루프를 멈추지 않는다.

    이 루프는 lifespan에서 create_task로 떠 있고 아무도 await하지 않는다.
    예외를 그대로 올리면 task가 조용히 끝나 장중 내내 순위가 멎는다
    (2026-08-18 두 차례, 로그에도 남지 않았다).
    """

    clock = FakeClock(datetime(2026, 8, 14, 1, 0, tzinfo=UTC))
    calls: list[int] = []

    class _Exploding:
        def tick(self) -> PublishedView | None:
            calls.append(len(calls))
            if len(calls) == 1:
                raise RuntimeError("장중 한 주기 실패")
            return None

        def close(self) -> None:
            return None

    loop = TradingDayLoop(
        build_session=lambda _market_date: _Exploding(),
        interval=timedelta(seconds=2),
        clock=clock,
    )

    async def drive() -> None:
        slept = 0

        async def sleep(_seconds: float) -> None:
            nonlocal slept
            slept += 1
            if slept >= 3:
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await loop.run(sleep=sleep)

    asyncio.run(drive())

    assert len(calls) == 3
