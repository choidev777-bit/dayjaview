from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from packages.calculations import (
    RANKING_BASELINE_POLICY_V1,
    BaselineStatus,
    calculate_theme_turnover,
)
from packages.domain import UnavailableReason

from ._factories import make_membership, make_trading_value_observation

BUCKET = time(10, 30)


def business_days(start: date, count: int) -> list[date]:
    result: list[date] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def evaluation_at(market_date: date) -> datetime:
    return datetime.combine(market_date, BUCKET, tzinfo=UTC)


def test_turnover_uses_same_time_and_sum_of_stock_medians() -> None:
    trading_days = business_days(date(2026, 7, 1), 21)
    evaluation_date = trading_days[-1]
    membership = make_membership(
        core=("A", "B"),
        related=("C",),
        effective_from=trading_days[0],
    )
    observations = []
    for market_date in trading_days[:-1]:
        observations.extend(
            [
                make_trading_value_observation("A", market_date, Decimal(100)),
                make_trading_value_observation("B", market_date, Decimal(200)),
                make_trading_value_observation("C", market_date, Decimal(300)),
                make_trading_value_observation(
                    "A",
                    market_date,
                    Decimal(999999),
                    bucket=time(15, 30),
                ),
            ]
        )
    observations.extend(
        [
            make_trading_value_observation("A", evaluation_date, Decimal(200)),
            make_trading_value_observation("B", evaluation_date, Decimal(400)),
            make_trading_value_observation("C", evaluation_date, Decimal(600)),
        ]
    )

    result = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=evaluation_at(evaluation_date),
        time_bucket=BUCKET,
        trading_days=trading_days,
        membership_snapshots=(membership,),
        observations=observations,
    )

    assert result.turnover_multiple == Decimal(2)
    assert result.baseline_status is BaselineStatus.FULL
    assert result.valid_count == 3
    assert result.total_count == 3
    assert [metric.baseline_median for metric in result.stock_metrics] == [
        Decimal(100),
        Decimal(200),
        Decimal(300),
    ]


def test_five_to_nineteen_days_are_provisional_and_fewer_are_null() -> None:
    trading_days = business_days(date(2026, 7, 1), 7)
    evaluation_date = trading_days[-1]
    membership = make_membership(
        core=("A",),
        effective_from=trading_days[0],
    )
    observations = [
        make_trading_value_observation("A", market_date, Decimal(100))
        for market_date in trading_days
    ]
    provisional = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=evaluation_at(evaluation_date),
        time_bucket=BUCKET,
        trading_days=trading_days,
        membership_snapshots=(membership,),
        observations=observations,
    )
    insufficient = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=evaluation_at(evaluation_date),
        time_bucket=BUCKET,
        trading_days=trading_days[-5:],
        membership_snapshots=(membership,),
        observations=observations[-5:],
    )

    assert provisional.turnover_multiple == Decimal(1)
    assert provisional.baseline_status is BaselineStatus.PROVISIONAL
    assert "PROVISIONAL_BASELINE" in provisional.quality_flags
    assert insufficient.turnover_multiple is None
    assert insufficient.unavailable_reason is UnavailableReason.INSUFFICIENT_OBSERVATIONS


def test_zero_current_value_is_real_zero_not_missing() -> None:
    trading_days = business_days(date(2026, 7, 1), 6)
    evaluation_date = trading_days[-1]
    membership = make_membership(
        core=("A",),
        effective_from=trading_days[0],
    )
    observations = [
        make_trading_value_observation("A", market_date, Decimal(100))
        for market_date in trading_days[:-1]
    ]
    observations.append(
        make_trading_value_observation("A", evaluation_date, Decimal(0))
    )

    observed_zero = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=evaluation_at(evaluation_date),
        time_bucket=BUCKET,
        trading_days=trading_days,
        membership_snapshots=(membership,),
        observations=observations,
    )
    missing = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=evaluation_at(evaluation_date),
        time_bucket=BUCKET,
        trading_days=trading_days,
        membership_snapshots=(membership,),
        observations=observations[:-1],
    )

    assert observed_zero.turnover_multiple == Decimal(0)
    assert missing.turnover_multiple is None


def test_current_membership_is_not_retroactively_used_for_baseline() -> None:
    trading_days = business_days(date(2026, 7, 1), 21)
    evaluation_date = trading_days[-1]
    old_membership = make_membership(
        core=("A",),
        version="membership-v1",
        effective_from=trading_days[0],
    )
    current_membership = make_membership(
        core=("A", "B"),
        version="membership-v2",
        effective_from=evaluation_date,
    )
    observations = []
    for market_date in trading_days:
        observations.extend(
            [
                make_trading_value_observation("A", market_date, Decimal(100)),
                make_trading_value_observation("B", market_date, Decimal(100)),
            ]
        )

    result = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=evaluation_at(evaluation_date),
        time_bucket=BUCKET,
        trading_days=trading_days,
        membership_snapshots=(current_membership, old_membership),
        observations=observations,
    )

    assert result.membership_version == "membership-v2"
    assert [metric.stock_id for metric in result.stock_metrics] == ["A"]
    assert result.valid_count == 1
    assert result.total_count == 2
    assert "INSUFFICIENT_OBSERVATIONS" in result.quality_flags


def test_membership_is_selected_at_each_stock_observation_time() -> None:
    trading_days = business_days(date(2026, 7, 1), 6)
    first_day = trading_days[0]
    evaluation_date = trading_days[-1]
    old_membership = make_membership(
        core=("A",),
        version="membership-v1",
        effective_from=first_day,
        known_at=datetime.combine(first_day, time(0), tzinfo=UTC),
    )
    revised_membership = make_membership(
        core=("A", "B"),
        version="membership-v2",
        effective_from=first_day,
        known_at=datetime.combine(first_day, time(10, 31), tzinfo=UTC),
    )
    observations = []
    for market_date in trading_days:
        observations.extend(
            [
                make_trading_value_observation(
                    "A",
                    market_date,
                    Decimal(100),
                    observed_at=datetime.combine(
                        market_date,
                        time(10, 32),
                        tzinfo=UTC,
                    ),
                ),
                make_trading_value_observation(
                    "B",
                    market_date,
                    Decimal(100),
                    observed_at=datetime.combine(
                        market_date,
                        time(10, 30),
                        tzinfo=UTC,
                    ),
                ),
            ]
        )

    result = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=datetime.combine(evaluation_date, time(10, 32), tzinfo=UTC),
        time_bucket=BUCKET,
        trading_days=trading_days,
        membership_snapshots=(revised_membership, old_membership),
        observations=observations,
    )

    assert result.membership_version == "membership-v2"
    assert [metric.stock_id for metric in result.stock_metrics] == ["A"]
    assert result.total_count == 2


def test_baseline_policy_version_change_is_visible_without_changing_data() -> None:
    trading_days = business_days(date(2026, 7, 1), 21)
    evaluation_date = trading_days[-1]
    membership = make_membership(
        core=("A",),
        effective_from=trading_days[0],
    )
    observations = [
        make_trading_value_observation("A", market_date, Decimal(100))
        for market_date in trading_days
    ]
    changed_policy = replace(
        RANKING_BASELINE_POLICY_V1,
        version="same-time-turnover-test-next",
    )

    original = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=evaluation_at(evaluation_date),
        time_bucket=BUCKET,
        trading_days=trading_days,
        membership_snapshots=(membership,),
        observations=observations,
    )
    changed = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=evaluation_at(evaluation_date),
        time_bucket=BUCKET,
        trading_days=trading_days,
        membership_snapshots=(membership,),
        observations=observations,
        policy=changed_policy,
    )

    assert changed.turnover_multiple == original.turnover_multiple
    assert changed.baseline_version != original.baseline_version
    assert changed != original
