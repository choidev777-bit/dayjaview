from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from packages.calculations import (
    ATTENTION_POLICY_V1,
    RANKING_BASELINE_POLICY_V1,
    BaselineStatus,
    calculate_theme_turnover,
)
from packages.domain import (
    DataStatus,
    MembershipRole,
    StockReference,
    StockTradingValueObservation,
    ThemeMember,
    ThemeMembershipSnapshot,
    UnavailableReason,
)
from packages.events import InMemoryEventStore
from packages.pipeline import IntradayHistory, MarketDataPipeline
from packages.pipeline.trading_day import KST
from packages.realtime import (
    InMemorySnapshotRepository,
    StockRealtimeUpdate,
    VersionedThemeCatalog,
)

MEMBERSHIP_VERSION = "membership-test-1"
BUCKET = time(9, 5)
MEMBERS = ("KRX:000001", "KRX:000002", "KRX:000003")


def _snapshot(market_date: date) -> ThemeMembershipSnapshot:
    return ThemeMembershipSnapshot(
        theme_id="thm_full",
        version=f"{MEMBERSHIP_VERSION}-{market_date.isoformat()}",
        effective_from=market_date,
        known_at=datetime.combine(market_date, time(8, 0), tzinfo=KST),
        members=tuple(
            ThemeMember(stock_id, MembershipRole.CORE) for stock_id in MEMBERS
        ),
    )


def _observation(
    stock_id: str,
    market_date: date,
    value: str,
) -> StockTradingValueObservation:
    return StockTradingValueObservation(
        stock_id=stock_id,
        market_date=market_date,
        observed_at=datetime.combine(market_date, BUCKET, tzinfo=KST),
        time_bucket=BUCKET,
        cumulative_trading_value=Decimal(value),
    )


def _accumulate(history: IntradayHistory, days: tuple[date, ...], value: str) -> None:
    for market_date in days:
        history.record_membership(
            market_date=market_date,
            snapshots=(_snapshot(market_date),),
        )
        history.record_turnover(
            market_date=market_date,
            time_bucket=BUCKET,
            observations=[
                _observation(stock_id, market_date, value) for stock_id in MEMBERS
            ],
        )


def _trading_days(count: int, *, until: date) -> tuple[date, ...]:
    days: list[date] = []
    cursor = until
    while len(days) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            days.append(cursor)
    return tuple(sorted(days))


def test_recorded_days_round_trip_through_the_turnover_calculation(
    tmp_path: Path,
) -> None:
    today = date(2026, 8, 14)
    history = IntradayHistory(tmp_path)
    past = _trading_days(RANKING_BASELINE_POLICY_V1.lookback_trading_days, until=today)
    _accumulate(history, past, "1000000")
    _accumulate(history, (today,), "3000000")

    assert history.trading_days() == (*past, today)

    calendar = (*past, today)
    result = calculate_theme_turnover(
        theme_id="thm_full",
        evaluation_date=today,
        evaluation_at=datetime.combine(today, time(9, 6), tzinfo=KST),
        time_bucket=BUCKET,
        trading_days=calendar,
        membership_snapshots=history.load_membership(calendar),
        observations=history.load_turnover(
            time_bucket=BUCKET,
            trading_days=calendar,
        ),
    )
    # 20거래일이 다 찼으므로 잠정이 아닌 정식 기준선이고, 오늘이 3배다.
    assert result.baseline_status is BaselineStatus.FULL
    assert result.turnover_multiple == Decimal(3)
    assert result.valid_count == 3
    assert result.quality_flags == ()


def test_short_history_stays_provisional_instead_of_failing(tmp_path: Path) -> None:
    today = date(2026, 8, 14)
    history = IntradayHistory(tmp_path)
    past = _trading_days(RANKING_BASELINE_POLICY_V1.minimum_observations, until=today)
    _accumulate(history, past, "1000000")
    _accumulate(history, (today,), "2000000")

    calendar = (*past, today)
    result = calculate_theme_turnover(
        theme_id="thm_full",
        evaluation_date=today,
        evaluation_at=datetime.combine(today, time(9, 6), tzinfo=KST),
        time_bucket=BUCKET,
        trading_days=calendar,
        membership_snapshots=history.load_membership(calendar),
        observations=history.load_turnover(
            time_bucket=BUCKET,
            trading_days=calendar,
        ),
    )
    assert result.baseline_status is BaselineStatus.PROVISIONAL
    assert result.turnover_multiple == Decimal(2)
    assert "PROVISIONAL_BASELINE" in result.quality_flags


def test_empty_history_yields_no_multiple_and_does_not_raise(tmp_path: Path) -> None:
    today = date(2026, 8, 14)
    history = IntradayHistory(tmp_path)
    _accumulate(history, (today,), "2000000")

    result = calculate_theme_turnover(
        theme_id="thm_full",
        evaluation_date=today,
        evaluation_at=datetime.combine(today, time(9, 6), tzinfo=KST),
        time_bucket=BUCKET,
        trading_days=(today,),
        membership_snapshots=history.load_membership((today,)),
        observations=history.load_turnover(time_bucket=BUCKET, trading_days=(today,)),
    )
    assert result.turnover_multiple is None
    assert result.baseline_status is BaselineStatus.INSUFFICIENT
    assert result.unavailable_reason is UnavailableReason.INSUFFICIENT_OBSERVATIONS


def test_attention_signals_round_trip_per_theme(tmp_path: Path) -> None:
    history = IntradayHistory(tmp_path)
    first, second = date(2026, 8, 12), date(2026, 8, 13)
    signal = calculate_theme_turnover(
        theme_id="thm_full",
        evaluation_date=first,
        evaluation_at=datetime.combine(first, time(9, 6), tzinfo=KST),
        time_bucket=BUCKET,
        trading_days=(first,),
        membership_snapshots=(_snapshot(first),),
        observations=(),
    )
    assert signal.turnover_multiple is None

    from packages.calculations import AttentionDaySignal

    for market_date, is_attention in ((first, True), (second, False)):
        history.record_attention(
            market_date=market_date,
            signals={
                "thm_full": AttentionDaySignal(
                    market_date=market_date,
                    is_attention=is_attention,
                    membership_version=MEMBERSHIP_VERSION,
                    calculation_version="theme-metrics-2026.08.1",
                    baseline_version=RANKING_BASELINE_POLICY_V1.version,
                    attention_policy_version=ATTENTION_POLICY_V1.version,
                    turnover_multiple=Decimal("2.5"),
                    high_interest_count=2,
                    valid_count=3,
                    high_interest_ratio=Decimal("0.6666"),
                    weighted_return=Decimal("0.03"),
                    unavailable_reason=None,
                )
            },
        )

    loaded = history.load_attention((first, second))
    assert [signal.market_date for signal in loaded["thm_full"]] == [first, second]
    assert [signal.is_attention for signal in loaded["thm_full"]] == [True, False]
    assert loaded["thm_full"][0].turnover_multiple == Decimal("2.5")
    assert loaded["thm_full"][0].unavailable_reason is None


def _pipeline(history: IntradayHistory, market_date: date) -> MarketDataPipeline:
    return MarketDataPipeline(
        market_date=market_date,
        stream_id="stream_history_test",
        schema_version="2026-08-14.1",
        catalog=VersionedThemeCatalog((_snapshot(market_date),)),
        references=tuple(
            StockReference(
                stock_id=stock_id,
                effective_for=market_date,
                known_at=datetime.combine(market_date, time(8, 0), tzinfo=KST),
                previous_adjusted_close=Decimal("10000"),
                listed_shares=1_000_000,
                free_float_ratio=Decimal("0.5"),
                free_float_validated=True,
                version="reference-test-1",
            )
            for stock_id in MEMBERS
        ),
        membership_version=MEMBERSHIP_VERSION,
        theme_names={"thm_full": "테스트 테마"},
        stock_names={stock_id: stock_id for stock_id in MEMBERS},
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
        history=history,
    )


def test_pipeline_accumulates_a_bucket_and_reads_the_baseline_back(
    tmp_path: Path,
) -> None:
    today = date(2026, 8, 14)
    history = IntradayHistory(tmp_path)
    past = _trading_days(RANKING_BASELINE_POLICY_V1.lookback_trading_days, until=today)
    _accumulate(history, past, "1000000")

    pipeline = _pipeline(history, today)
    at = datetime.combine(today, BUCKET, tzinfo=KST).astimezone(UTC)
    for index, stock_id in enumerate(MEMBERS):
        pipeline.apply_update(
            StockRealtimeUpdate(
                message_id=f"msg_{stock_id}",
                stock_id=stock_id,
                market_date=today,
                source="test-session",
                source_sequence=index + 1,
                occurred_at=at,
                received_at=at,
                current_price=Decimal("10300"),
                cumulative_trading_value=Decimal("4000000"),
            )
        )
    pipeline.publish(now=at + timedelta(seconds=1), data_status=DataStatus.LIVE)
    view = pipeline.publish(
        now=at + timedelta(seconds=20),
        data_status=DataStatus.LIVE,
    )

    # 그날 09:05 버킷이 파일로 남고 다음 거래일의 기준선 재료가 된다.
    assert (tmp_path / today.isoformat() / "bucket-0905.json").exists()
    assert (tmp_path / today.isoformat() / "membership.json").exists()

    items = view.rankings.payload["items"]
    assert isinstance(items, list)
    detail = pipeline.theme_detail(items[0]["eventId"])
    assert detail is not None
    # 20거래일 × 100만 대비 오늘 400만 → 4배.
    assert detail["currentReaction"]["turnoverMultiple"] == 4.0
    assert "PROVISIONAL_BASELINE" not in detail["qualityFlags"]

    # 장 마감에 그날 관심 신호가 남아 다음 날 관심 공백 계산의 재료가 된다.
    pipeline.close_market(now=at + timedelta(hours=6))
    stored = history.load_attention((today,))
    assert stored["thm_full"][0].is_attention is True


def test_pipeline_without_history_keeps_publishing_null_baselines(
    tmp_path: Path,
) -> None:
    today = date(2026, 8, 14)
    history = IntradayHistory(tmp_path / "empty")
    pipeline = _pipeline(history, today)
    at = datetime.combine(today, BUCKET, tzinfo=KST).astimezone(UTC)
    for index, stock_id in enumerate(MEMBERS):
        pipeline.apply_update(
            StockRealtimeUpdate(
                message_id=f"msg_{stock_id}",
                stock_id=stock_id,
                market_date=today,
                source="test-session",
                source_sequence=index + 1,
                occurred_at=at,
                received_at=at,
                current_price=Decimal("10300"),
                cumulative_trading_value=Decimal("4000000"),
            )
        )
    pipeline.publish(now=at + timedelta(seconds=1), data_status=DataStatus.LIVE)
    view = pipeline.publish(
        now=at + timedelta(seconds=20),
        data_status=DataStatus.LIVE,
    )
    items = view.rankings.payload["items"]
    assert isinstance(items, list)
    detail = pipeline.theme_detail(items[0]["eventId"])
    assert detail is not None
    # 축적 첫날에는 비교할 과거가 없다. 값을 지어내지 않고 null로 둔다.
    assert detail["currentReaction"]["turnoverMultiple"] is None
    assert detail["currentReaction"]["attentionGapTradingDays"] is None
