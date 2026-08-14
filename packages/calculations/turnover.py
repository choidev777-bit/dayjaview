from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, localcontext
from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.models import (
        QualityFlag,
        StockTradingValueObservation,
        ThemeMembershipSnapshot,
        UnavailableReason,
        require_aware,
        select_membership_snapshot,
    )
else:
    _models = import_module("packages." + "domain.models")
    QualityFlag = _models.QualityFlag
    StockTradingValueObservation = _models.StockTradingValueObservation
    ThemeMembershipSnapshot = _models.ThemeMembershipSnapshot
    UnavailableReason = _models.UnavailableReason
    require_aware = _models.require_aware
    select_membership_snapshot = _models.select_membership_snapshot

from .policies import RANKING_BASELINE_POLICY_V1, SameTimeBaselinePolicy


class BaselineStatus(StrEnum):
    FULL = "FULL"
    PROVISIONAL = "PROVISIONAL"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class StockTurnoverMetric:
    stock_id: str
    current_trading_value: Decimal
    baseline_median: Decimal
    multiple: Decimal
    observation_count: int


@dataclass(frozen=True, slots=True)
class ThemeTurnoverResult:
    baseline_version: str
    policy_maturity: str
    membership_version: str | None
    turnover_multiple: Decimal | None
    valid_count: int
    total_count: int
    baseline_status: BaselineStatus
    stock_metrics: tuple[StockTurnoverMetric, ...]
    quality_flags: tuple[str, ...]
    unavailable_reason: UnavailableReason | None


def _median(values: list[Decimal]) -> Decimal:
    values.sort()
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    with localcontext() as context:
        context.prec = 60
        return (values[middle - 1] + values[middle]) / Decimal(2)


def _validate_trading_days(
    trading_days: Iterable[date],
    *,
    evaluation_date: date,
    lookback: int,
) -> tuple[date, ...]:
    ordered = tuple(trading_days)
    if tuple(sorted(set(ordered))) != ordered:
        raise ValueError("trading_days는 중복 없이 오름차순이어야 합니다")
    if evaluation_date not in ordered:
        raise ValueError("evaluation_date가 trading_days에 포함되어야 합니다")
    position = ordered.index(evaluation_date)
    return ordered[max(0, position - lookback) : position]


def _index_observations(
    observations: Iterable[StockTradingValueObservation],
    *,
    time_bucket: time,
) -> dict[tuple[str, date], StockTradingValueObservation]:
    result: dict[tuple[str, date], StockTradingValueObservation] = {}
    for observation in observations:
        if observation.time_bucket != time_bucket:
            continue
        key = (observation.stock_id, observation.market_date)
        if key in result:
            raise ValueError(
                "같은 stock_id·market_date·time_bucket 관측이 중복되었습니다"
            )
        result[key] = observation
    return result


def _is_usable(
    observation: StockTradingValueObservation | None,
    *,
    decision_at: datetime,
) -> bool:
    return bool(
        observation is not None
        and observation.observed_at <= decision_at
        and observation.comparable
        and observation.fresh
        and not observation.trading_halted
        and not observation.corporate_action_unresolved
        and observation.cumulative_trading_value is not None
    )


def calculate_theme_turnover(
    *,
    theme_id: str,
    evaluation_date: date,
    evaluation_at: datetime,
    time_bucket: time,
    trading_days: Iterable[date],
    membership_snapshots: Iterable[ThemeMembershipSnapshot],
    observations: Iterable[StockTradingValueObservation],
    policy: SameTimeBaselinePolicy = RANKING_BASELINE_POLICY_V1,
) -> ThemeTurnoverResult:
    """Compare cumulative value only with prior observations from the same minute."""

    require_aware(evaluation_at, "evaluation_at")
    history_dates = _validate_trading_days(
        trading_days,
        evaluation_date=evaluation_date,
        lookback=policy.lookback_trading_days,
    )
    snapshots = tuple(membership_snapshots)
    indexed = _index_observations(observations, time_bucket=time_bucket)

    current_membership = select_membership_snapshot(
        snapshots,
        theme_id=theme_id,
        market_date=evaluation_date,
        decision_at=evaluation_at,
    )
    if current_membership is None:
        return ThemeTurnoverResult(
            baseline_version=policy.version,
            policy_maturity=policy.maturity.value,
            membership_version=None,
            turnover_multiple=None,
            valid_count=0,
            total_count=0,
            baseline_status=BaselineStatus.INSUFFICIENT,
            stock_metrics=(),
            quality_flags=(QualityFlag.MEMBERSHIP_VERSION_MISMATCH.value,),
            unavailable_reason=UnavailableReason.MEMBERSHIP_HISTORY_UNAVAILABLE,
        )

    quality_flags: set[str] = set()
    stock_metrics: list[StockTurnoverMetric] = []
    members = sorted(current_membership.members, key=lambda member: member.stock_id)
    for member in members:
        current = indexed.get((member.stock_id, evaluation_date))
        if not _is_usable(current, decision_at=evaluation_at):
            if current is not None and current.trading_halted:
                quality_flags.add(QualityFlag.TRADING_HALTED.value)
            elif current is not None and current.corporate_action_unresolved:
                quality_flags.add(QualityFlag.CORPORATE_ACTION_UNRESOLVED.value)
            elif current is not None and not current.fresh:
                quality_flags.add(QualityFlag.STALE_MARKET_DATA.value)
            else:
                quality_flags.add(QualityFlag.SOURCE_DEGRADED.value)
            continue
        assert current is not None
        assert current.cumulative_trading_value is not None

        historical_values: list[Decimal] = []
        for history_date in history_dates:
            historical = indexed.get((member.stock_id, history_date))
            if historical is None:
                continue
            membership = select_membership_snapshot(
                snapshots,
                theme_id=theme_id,
                market_date=history_date,
                decision_at=historical.observed_at,
            )
            if membership is None or not membership.contains(member.stock_id):
                continue
            if not _is_usable(
                historical,
                decision_at=historical.observed_at,
            ):
                continue
            assert historical.cumulative_trading_value is not None
            historical_values.append(historical.cumulative_trading_value)

        if len(historical_values) < policy.minimum_observations:
            quality_flags.add(QualityFlag.INSUFFICIENT_OBSERVATIONS.value)
            continue
        baseline_median = _median(historical_values)
        if baseline_median <= 0:
            quality_flags.add(QualityFlag.ZERO_BASELINE.value)
            continue
        with localcontext() as context:
            context.prec = 60
            multiple = current.cumulative_trading_value / baseline_median
        stock_metrics.append(
            StockTurnoverMetric(
                stock_id=member.stock_id,
                current_trading_value=current.cumulative_trading_value,
                baseline_median=baseline_median,
                multiple=multiple,
                observation_count=len(historical_values),
            )
        )

    stock_metrics.sort(key=lambda item: item.stock_id)
    if not stock_metrics:
        return ThemeTurnoverResult(
            baseline_version=policy.version,
            policy_maturity=policy.maturity.value,
            membership_version=current_membership.version,
            turnover_multiple=None,
            valid_count=0,
            total_count=len(members),
            baseline_status=BaselineStatus.INSUFFICIENT,
            stock_metrics=(),
            quality_flags=tuple(sorted(quality_flags)),
            unavailable_reason=UnavailableReason.INSUFFICIENT_OBSERVATIONS,
        )

    denominator = sum(
        (metric.baseline_median for metric in stock_metrics),
        start=Decimal(0),
    )
    numerator = sum(
        (metric.current_trading_value for metric in stock_metrics),
        start=Decimal(0),
    )
    if denominator <= 0:
        turnover_multiple = None
        baseline_status = BaselineStatus.INSUFFICIENT
        unavailable_reason: UnavailableReason | None = UnavailableReason.ZERO_BASELINE
        quality_flags.add(QualityFlag.ZERO_BASELINE.value)
    else:
        with localcontext() as context:
            context.prec = 60
            turnover_multiple = numerator / denominator
        fully_observed = len(stock_metrics) == len(members) and all(
            metric.observation_count == policy.lookback_trading_days
            for metric in stock_metrics
        )
        baseline_status = (
            BaselineStatus.FULL if fully_observed else BaselineStatus.PROVISIONAL
        )
        unavailable_reason = None
        if baseline_status is BaselineStatus.PROVISIONAL:
            quality_flags.add(QualityFlag.PROVISIONAL_BASELINE.value)

    return ThemeTurnoverResult(
        baseline_version=policy.version,
        policy_maturity=policy.maturity.value,
        membership_version=current_membership.version,
        turnover_multiple=turnover_multiple,
        valid_count=len(stock_metrics),
        total_count=len(members),
        baseline_status=baseline_status,
        stock_metrics=tuple(stock_metrics),
        quality_flags=tuple(sorted(quality_flags)),
        unavailable_reason=unavailable_reason,
    )
