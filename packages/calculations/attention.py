from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.coverage import CoverageStatus
    from domain.models import UnavailableReason
else:
    CoverageStatus = import_module(
        "packages." + "domain.coverage"
    ).CoverageStatus
    UnavailableReason = import_module(
        "packages." + "domain.models"
    ).UnavailableReason

from .policies import ATTENTION_POLICY_V1, AttentionPolicy
from .theme_metrics import ThemeMetrics
from .turnover import ThemeTurnoverResult


@dataclass(frozen=True, slots=True)
class AttentionDaySignal:
    market_date: date
    is_attention: bool | None
    membership_version: str | None
    calculation_version: str
    baseline_version: str
    attention_policy_version: str
    turnover_multiple: Decimal | None
    high_interest_count: int | None
    valid_count: int | None
    high_interest_ratio: Decimal | None
    weighted_return: Decimal | None
    unavailable_reason: UnavailableReason | None


@dataclass(frozen=True, slots=True)
class AttentionInterval:
    first_attention_date: date
    last_attention_date: date
    closed_confirmation_date: date | None
    gap_trading_days: int | None
    gap_unavailable_reason: UnavailableReason | None
    membership_versions: tuple[str, ...]
    calculation_versions: tuple[str, ...]
    baseline_versions: tuple[str, ...]
    attention_policy_version: str

    @property
    def is_open(self) -> bool:
        return self.closed_confirmation_date is None


@dataclass(frozen=True, slots=True)
class AttentionTimeline:
    policy_version: str
    policy_maturity: str
    intervals: tuple[AttentionInterval, ...]

    @property
    def current_interval(self) -> AttentionInterval | None:
        if not self.intervals or not self.intervals[-1].is_open:
            return None
        return self.intervals[-1]

    @property
    def current_gap_trading_days(self) -> int | None:
        current = self.current_interval
        return None if current is None else current.gap_trading_days

    @property
    def current_gap_unavailable_reason(self) -> UnavailableReason | None:
        current = self.current_interval
        if current is None:
            return UnavailableReason.NO_CURRENT_ATTENTION_INTERVAL
        return current.gap_unavailable_reason


@dataclass(slots=True)
class _MutableInterval:
    first_attention_date: date
    last_attention_date: date
    gap_trading_days: int | None
    gap_unavailable_reason: UnavailableReason | None
    membership_versions: set[str]
    calculation_versions: set[str]
    baseline_versions: set[str]
    attention_policy_version: str
    closed_confirmation_date: date | None = None

    def freeze(self) -> AttentionInterval:
        return AttentionInterval(
            first_attention_date=self.first_attention_date,
            last_attention_date=self.last_attention_date,
            closed_confirmation_date=self.closed_confirmation_date,
            gap_trading_days=self.gap_trading_days,
            gap_unavailable_reason=self.gap_unavailable_reason,
            membership_versions=tuple(sorted(self.membership_versions)),
            calculation_versions=tuple(sorted(self.calculation_versions)),
            baseline_versions=tuple(sorted(self.baseline_versions)),
            attention_policy_version=self.attention_policy_version,
        )


def evaluate_attention_day(
    *,
    market_date: date,
    metrics: ThemeMetrics,
    turnover: ThemeTurnoverResult,
    policy: AttentionPolicy = ATTENTION_POLICY_V1,
) -> AttentionDaySignal:
    membership_version = turnover.membership_version
    unavailable_reason: UnavailableReason | None = None
    if membership_version != metrics.membership_version:
        unavailable_reason = UnavailableReason.MEMBERSHIP_VERSION_MISMATCH
    elif metrics.coverage.status is not CoverageStatus.SUFFICIENT:
        unavailable_reason = UnavailableReason.INSUFFICIENT_COVERAGE
    elif (
        turnover.turnover_multiple is None
        or metrics.weighted_return is None
        or turnover.valid_count < policy.minimum_valid_members
    ):
        unavailable_reason = UnavailableReason.INSUFFICIENT_OBSERVATIONS

    if unavailable_reason is not None:
        return AttentionDaySignal(
            market_date=market_date,
            is_attention=None,
            membership_version=membership_version,
            calculation_version=metrics.calculation_version,
            baseline_version=turnover.baseline_version,
            attention_policy_version=policy.version,
            turnover_multiple=turnover.turnover_multiple,
            high_interest_count=None,
            valid_count=(turnover.valid_count or None),
            high_interest_ratio=None,
            weighted_return=metrics.weighted_return,
            unavailable_reason=unavailable_reason,
        )

    assert turnover.turnover_multiple is not None
    assert metrics.weighted_return is not None
    high_interest_count = sum(
        metric.multiple >= policy.stock_multiple_threshold
        for metric in turnover.stock_metrics
    )
    with localcontext() as context:
        context.prec = 60
        high_interest_ratio = Decimal(high_interest_count) / Decimal(
            turnover.valid_count
        )
    is_attention = bool(
        turnover.turnover_multiple >= policy.turnover_multiple_threshold
        and high_interest_count >= policy.minimum_high_interest_stocks
        and high_interest_ratio >= policy.spread_ratio_threshold
        and metrics.weighted_return >= policy.weighted_return_threshold
    )
    return AttentionDaySignal(
        market_date=market_date,
        is_attention=is_attention,
        membership_version=membership_version,
        calculation_version=metrics.calculation_version,
        baseline_version=turnover.baseline_version,
        attention_policy_version=policy.version,
        turnover_multiple=turnover.turnover_multiple,
        high_interest_count=high_interest_count,
        valid_count=turnover.valid_count,
        high_interest_ratio=high_interest_ratio,
        weighted_return=metrics.weighted_return,
        unavailable_reason=None,
    )


def _validate_calendar(trading_days: Iterable[date]) -> tuple[date, ...]:
    ordered = tuple(trading_days)
    if tuple(sorted(set(ordered))) != ordered:
        raise ValueError("trading_days는 중복 없이 오름차순이어야 합니다")
    return ordered


def _new_interval(
    signal: AttentionDaySignal,
    *,
    gap_trading_days: int | None,
    gap_unavailable_reason: UnavailableReason | None,
) -> _MutableInterval:
    membership_versions = (
        set() if signal.membership_version is None else {signal.membership_version}
    )
    return _MutableInterval(
        first_attention_date=signal.market_date,
        last_attention_date=signal.market_date,
        gap_trading_days=gap_trading_days,
        gap_unavailable_reason=gap_unavailable_reason,
        membership_versions=membership_versions,
        calculation_versions={signal.calculation_version},
        baseline_versions={signal.baseline_version},
        attention_policy_version=signal.attention_policy_version,
    )


def build_attention_timeline(
    *,
    signals: Iterable[AttentionDaySignal],
    trading_days: Iterable[date],
    policy: AttentionPolicy = ATTENTION_POLICY_V1,
) -> AttentionTimeline:
    """Build intervals without treating missing days as confirmed non-attention."""

    calendar = _validate_calendar(trading_days)
    calendar_positions = {market_date: index for index, market_date in enumerate(calendar)}
    signal_by_date: dict[date, AttentionDaySignal] = {}
    for input_signal in signals:
        if input_signal.market_date not in calendar_positions:
            raise ValueError("attention signal 날짜가 trading calendar에 없습니다")
        if input_signal.market_date in signal_by_date:
            raise ValueError("같은 거래일의 attention signal이 중복되었습니다")
        if input_signal.attention_policy_version != policy.version:
            raise ValueError("attention signal과 policy version이 일치하지 않습니다")
        signal_by_date[input_signal.market_date] = input_signal

    completed: list[AttentionInterval] = []
    active: _MutableInterval | None = None
    absent_streak = 0

    for market_date in calendar:
        day_signal = signal_by_date.get(market_date)
        state = None if day_signal is None else day_signal.is_attention
        if active is None:
            if state is not True:
                continue
            previous = completed[-1] if completed else None
            if previous is None:
                gap = None
                gap_reason = UnavailableReason.NO_PRIOR_ATTENTION_INTERVAL
            else:
                previous_position = calendar_positions[previous.last_attention_date]
                current_position = calendar_positions[market_date]
                between = calendar[previous_position + 1 : current_position]
                history_complete = all(
                    day in signal_by_date
                    and signal_by_date[day].is_attention is False
                    for day in between
                )
                if history_complete:
                    gap = len(between)
                    gap_reason = None
                else:
                    gap = None
                    gap_reason = UnavailableReason.INCOMPLETE_ATTENTION_HISTORY
            assert day_signal is not None
            active = _new_interval(
                day_signal,
                gap_trading_days=gap,
                gap_unavailable_reason=gap_reason,
            )
            absent_streak = 0
            continue

        if state is True:
            assert day_signal is not None
            active.last_attention_date = market_date
            if day_signal.membership_version is not None:
                active.membership_versions.add(day_signal.membership_version)
            active.calculation_versions.add(day_signal.calculation_version)
            active.baseline_versions.add(day_signal.baseline_version)
            absent_streak = 0
        elif state is False:
            absent_streak += 1
            if absent_streak >= policy.close_after_absent_trading_days:
                active.closed_confirmation_date = market_date
                completed.append(active.freeze())
                active = None
                absent_streak = 0
        else:
            # Unknown cannot prove a consecutive absence interval.
            absent_streak = 0

    if active is not None:
        completed.append(active.freeze())

    return AttentionTimeline(
        policy_version=policy.version,
        policy_maturity=policy.maturity.value,
        intervals=tuple(completed),
    )
