"""revision-aware KRX 거래일 calendar 조회."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from .models import (
    CalendarResolution,
    QualityFlag,
    QualityState,
    TradingDayObservation,
    require_aware,
)


def _lineage(values: Iterable[TradingDayObservation]) -> tuple[str, ...]:
    return tuple(
        sorted({item for value in values for item in value.metadata.lineage})
    )


class TradingCalendar:
    """현재 값이 아니라 decision 시각에 알려진 calendar revision만 선택한다."""

    def __init__(self, observations: Iterable[TradingDayObservation]) -> None:
        self._observations = tuple(observations)

    def resolve(self, market_date: date, *, decision_at: datetime) -> CalendarResolution:
        require_aware(decision_at, "decision_at")
        eligible = [
            value
            for value in self._observations
            if value.market_date == market_date and value.metadata.known_at <= decision_at
        ]
        if not eligible:
            return CalendarResolution(
                market_date=market_date,
                is_trading_day=None,
                state=QualityState.POINT_IN_TIME_UNAVAILABLE,
                quality_flags=(
                    QualityFlag.CALENDAR_UNAVAILABLE,
                    QualityFlag.POINT_IN_TIME_FILTERED,
                ),
                version=None,
                lineage=(),
            )
        latest_key = max(
            (value.metadata.known_at, value.metadata.revision) for value in eligible
        )
        latest = [
            value
            for value in eligible
            if (value.metadata.known_at, value.metadata.revision) == latest_key
        ]
        if len({(value.is_trading_day, value.version) for value in latest}) != 1:
            return CalendarResolution(
                market_date=market_date,
                is_trading_day=None,
                state=QualityState.CONFLICT,
                quality_flags=(QualityFlag.CALENDAR_UNAVAILABLE,),
                version=None,
                lineage=_lineage(latest),
            )
        selected = latest[0]
        return CalendarResolution(
            market_date=market_date,
            is_trading_day=selected.is_trading_day,
            state=QualityState.VERIFIED,
            quality_flags=(),
            version=selected.version,
            lineage=_lineage(latest),
        )

    def previous_trading_day(
        self,
        market_date: date,
        *,
        decision_at: datetime,
        max_lookback_days: int = 370,
    ) -> tuple[date | None, CalendarResolution]:
        if max_lookback_days <= 0:
            raise ValueError("max_lookback_days는 1 이상이어야 합니다.")
        for offset in range(1, max_lookback_days + 1):
            candidate = market_date - timedelta(days=offset)
            resolution = self.resolve(candidate, decision_at=decision_at)
            if resolution.state is not QualityState.VERIFIED:
                return None, resolution
            if resolution.is_trading_day:
                return candidate, resolution
        return None, CalendarResolution(
            market_date=market_date,
            is_trading_day=None,
            state=QualityState.MISSING,
            quality_flags=(QualityFlag.CALENDAR_UNAVAILABLE,),
            version=None,
            lineage=(),
        )

    def trading_days_between(
        self,
        start_exclusive: date,
        end_exclusive: date,
        *,
        decision_at: datetime,
    ) -> tuple[date, ...] | None:
        if end_exclusive < start_exclusive:
            raise ValueError("end_exclusive는 start_exclusive보다 빠를 수 없습니다.")
        result: list[date] = []
        current = start_exclusive + timedelta(days=1)
        while current < end_exclusive:
            resolution = self.resolve(current, decision_at=decision_at)
            if resolution.state is not QualityState.VERIFIED:
                return None
            if resolution.is_trading_day:
                result.append(current)
            current += timedelta(days=1)
        return tuple(result)
