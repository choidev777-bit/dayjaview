from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from packages.calculations import (
    ATTENTION_BASELINE_POLICY_V1,
    ATTENTION_POLICY_V1,
    AttentionDaySignal,
    build_attention_timeline,
    calculate_theme_metrics,
    calculate_theme_turnover,
    evaluate_attention_day,
)
from packages.domain import UnavailableReason

from ._factories import (
    make_market_observation,
    make_membership,
    make_reference,
    make_trading_value_observation,
)

BUCKET = time(10, 30)
FIXTURES = Path(__file__).parent / "fixtures"


def business_days(start: date, count: int) -> list[date]:
    result: list[date] = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def signal(
    market_date: date,
    is_attention: bool | None,
    *,
    membership_version: str = "membership-v1",
) -> AttentionDaySignal:
    available = is_attention is not None
    return AttentionDaySignal(
        market_date=market_date,
        is_attention=is_attention,
        membership_version=membership_version,
        calculation_version="theme-metrics-2026.08.1",
        baseline_version="same-time-attention-60d-2026.08.1",
        attention_policy_version=ATTENTION_POLICY_V1.version,
        turnover_multiple=Decimal(2) if available else None,
        high_interest_count=2 if available else None,
        valid_count=3 if available else None,
        high_interest_ratio=Decimal("0.666666666666666666") if available else None,
        weighted_return=Decimal("0.01") if available else None,
        unavailable_reason=(
            None if available else UnavailableReason.INSUFFICIENT_OBSERVATIONS
        ),
    )


def test_attention_thresholds_are_inclusive_at_versioned_boundary() -> None:
    trading_days = business_days(date(2026, 5, 1), 61)
    evaluation_date = trading_days[-1]
    evaluation_at = datetime.combine(evaluation_date, BUCKET, tzinfo=UTC)
    membership = make_membership(
        core=("A", "B"),
        related=("C",),
        effective_from=trading_days[0],
        known_at=datetime.combine(trading_days[0], time(0), tzinfo=UTC),
    )
    price_observations = [
        make_market_observation(
            stock_id,
            current_price=Decimal(101),
            market_date=evaluation_date,
            observed_at=evaluation_at,
        )
        for stock_id in ("A", "B", "C")
    ]
    references = [
        make_reference(
            stock_id,
            effective_for=evaluation_date,
            known_at=evaluation_at,
        )
        for stock_id in ("A", "B", "C")
    ]
    metrics = calculate_theme_metrics(
        market_date=evaluation_date,
        as_of=evaluation_at,
        membership=membership,
        references=references,
        observations=price_observations,
    )
    trading_observations = []
    for market_date in trading_days[:-1]:
        trading_observations.extend(
            make_trading_value_observation(stock_id, market_date, Decimal(100))
            for stock_id in ("A", "B", "C")
        )
    trading_observations.extend(
        make_trading_value_observation(
            stock_id,
            evaluation_date,
            Decimal(200),
        )
        for stock_id in ("A", "B", "C")
    )
    turnover = calculate_theme_turnover(
        theme_id="thm_test",
        evaluation_date=evaluation_date,
        evaluation_at=evaluation_at,
        time_bucket=BUCKET,
        trading_days=trading_days,
        membership_snapshots=(membership,),
        observations=trading_observations,
        policy=ATTENTION_BASELINE_POLICY_V1,
    )

    at_boundary = evaluate_attention_day(
        market_date=evaluation_date,
        metrics=metrics,
        turnover=turnover,
    )
    below_return = evaluate_attention_day(
        market_date=evaluation_date,
        metrics=replace(metrics, weighted_return=Decimal("0.009999")),
        turnover=turnover,
    )

    assert metrics.weighted_return == Decimal("0.01")
    assert turnover.turnover_multiple == Decimal(2)
    assert at_boundary.is_attention is True
    assert below_return.is_attention is False
    assert ATTENTION_POLICY_V1.maturity.value == "BACKTEST_PENDING"


def test_versioned_attention_timeline_fixture_and_stable_current_gap() -> None:
    fixture = json.loads(
        (FIXTURES / "attention_timeline_v1.json").read_text(encoding="utf-8")
    )
    policy_data = fixture["policy"]
    assert policy_data["version"] == ATTENTION_POLICY_V1.version
    assert policy_data["maturity"] == ATTENTION_POLICY_V1.maturity.value
    assert policy_data["closeAfterAbsentTradingDays"] == (
        ATTENTION_POLICY_V1.close_after_absent_trading_days
    )
    trading_days = [date.fromisoformat(value) for value in fixture["tradingDays"]]
    signals = [
        signal(
            date.fromisoformat(item["marketDate"]),
            item["isAttention"],
            membership_version=item["membershipVersion"],
        )
        for item in fixture["signals"]
    ]

    timeline = build_attention_timeline(
        signals=signals,
        trading_days=trading_days,
    )
    expected = fixture["expected"]
    current = timeline.current_interval

    assert fixture["fixtureVersion"] == "attention-timeline-fixture-2026.08.1"
    assert len(timeline.intervals) == expected["intervalCount"]
    assert timeline.intervals[0].closed_confirmation_date == date.fromisoformat(
        expected["firstClosedConfirmationDate"]
    )
    assert current is not None
    assert current.first_attention_date == date.fromisoformat(
        expected["currentFirstAttentionDate"]
    )
    assert current.last_attention_date == date.fromisoformat(
        expected["currentLastAttentionDate"]
    )
    assert timeline.current_gap_trading_days == expected["currentGapTradingDays"]
    assert list(current.membership_versions) == expected["currentMembershipVersions"]


def test_unknown_day_does_not_count_as_absence_and_makes_later_gap_unknown() -> None:
    trading_days = business_days(date(2026, 6, 1), 18)
    signals = [signal(trading_days[0], True)]
    signals.extend(signal(day, False) for day in trading_days[1:6])
    signals.append(signal(trading_days[6], None))
    signals.extend(signal(day, False) for day in trading_days[7:17])
    signals.append(signal(trading_days[17], True, membership_version="membership-v2"))

    timeline = build_attention_timeline(
        signals=signals,
        trading_days=trading_days,
    )

    assert timeline.intervals[0].closed_confirmation_date == trading_days[16]
    assert timeline.current_interval is not None
    assert timeline.current_gap_trading_days is None
    assert timeline.current_gap_unavailable_reason is (
        UnavailableReason.INCOMPLETE_ATTENTION_HISTORY
    )


def test_gap_property_counts_strictly_intervening_confirmed_trading_days() -> None:
    observed_gaps = []
    for gap_length in range(10, 21):
        trading_days = business_days(date(2026, 1, 2), gap_length + 2)
        signals = [signal(trading_days[0], True)]
        signals.extend(signal(day, False) for day in trading_days[1:-1])
        signals.append(signal(trading_days[-1], True))
        timeline = build_attention_timeline(
            signals=signals,
            trading_days=trading_days,
        )
        observed_gaps.append(timeline.current_gap_trading_days)
        assert timeline.current_gap_trading_days == gap_length

    assert observed_gaps == sorted(observed_gaps)


def test_no_attention_is_empty_collection_not_null_or_zero_interval() -> None:
    trading_days = business_days(date(2026, 7, 1), 5)
    timeline = build_attention_timeline(signals=(), trading_days=trading_days)

    assert timeline.intervals == ()
    assert timeline.current_interval is None
    assert timeline.current_gap_trading_days is None
    assert timeline.current_gap_unavailable_reason is (
        UnavailableReason.NO_CURRENT_ATTENTION_INTERVAL
    )
