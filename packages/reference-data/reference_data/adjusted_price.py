"""기업행위 상태와 거래일을 결합한 전일 조정종가 기준정보."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from .calendar import TradingCalendar
from .models import (
    AdjustedPriceResolution,
    CorporateActionReference,
    CorporateActionStatus,
    DailyPriceObservation,
    QualityFlag,
    QualityState,
    require_aware,
)


def _latest_price(
    values: Iterable[DailyPriceObservation],
    *,
    stock_code: str,
    market_date: date,
    decision_at: datetime,
) -> tuple[DailyPriceObservation | None, bool]:
    eligible = [
        value
        for value in values
        if value.stock_code == stock_code
        and value.market_date == market_date
        and value.metadata.known_at <= decision_at
    ]
    if not eligible:
        return None, False
    latest_key = max(
        (value.metadata.known_at, value.metadata.revision) for value in eligible
    )
    latest = [
        value
        for value in eligible
        if (value.metadata.known_at, value.metadata.revision) == latest_key
    ]
    conflict = len({(value.close, value.listed_shares) for value in latest}) != 1
    return (None, True) if conflict else (latest[0], False)


def _latest_action(
    values: Iterable[CorporateActionReference],
    *,
    stock_code: str,
    effective_on: date,
    decision_at: datetime,
) -> tuple[CorporateActionReference | None, bool]:
    eligible = [
        value
        for value in values
        if value.stock_code == stock_code
        and value.effective_on == effective_on
        and value.metadata.known_at <= decision_at
    ]
    if not eligible:
        return None, False
    latest_key = max(
        (value.metadata.known_at, value.metadata.revision) for value in eligible
    )
    latest = [
        value
        for value in eligible
        if (value.metadata.known_at, value.metadata.revision) == latest_key
    ]
    conflict = len({(value.status, value.adjustment_factor) for value in latest}) != 1
    return (None, True) if conflict else (latest[0], False)


def _lineage(*values: object) -> tuple[str, ...]:
    items: set[str] = set()
    for value in values:
        metadata = getattr(value, "metadata", None)
        if metadata is not None:
            items.update(metadata.lineage)
            items.update(f"document:{item}" for item in metadata.source_document_ids)
    return tuple(sorted(items))


def resolve_previous_adjusted_close(
    *,
    stock_code: str,
    market_date: date,
    decision_at: datetime,
    calendar: TradingCalendar,
    daily_prices: Iterable[DailyPriceObservation],
    corporate_actions: Iterable[CorporateActionReference],
    version: str,
) -> AdjustedPriceResolution:
    """명시적 KRX 기준가 또는 기업행위 factor가 없으면 값을 만들지 않는다."""

    require_aware(decision_at, "decision_at")
    if not version:
        raise ValueError("adjusted price version은 비어 있을 수 없습니다.")
    prices = tuple(daily_prices)
    actions = tuple(corporate_actions)

    current, current_conflict = _latest_price(
        prices,
        stock_code=stock_code,
        market_date=market_date,
        decision_at=decision_at,
    )
    if current_conflict:
        return AdjustedPriceResolution(
            stock_code=stock_code,
            effective_for=market_date,
            previous_trading_day=None,
            previous_adjusted_close=None,
            state=QualityState.CONFLICT,
            quality_flags=(QualityFlag.REFERENCE_PRICE_UNAVAILABLE,),
            version=version,
            lineage=(),
        )
    if current is not None and current.implied_previous_adjusted_close is not None:
        previous_day, calendar_resolution = calendar.previous_trading_day(
            market_date,
            decision_at=decision_at,
        )
        if previous_day is None:
            return AdjustedPriceResolution(
                stock_code=stock_code,
                effective_for=market_date,
                previous_trading_day=None,
                previous_adjusted_close=None,
                state=calendar_resolution.state,
                quality_flags=calendar_resolution.quality_flags,
                version=version,
                lineage=calendar_resolution.lineage,
            )
        return AdjustedPriceResolution(
            stock_code=stock_code,
            effective_for=market_date,
            previous_trading_day=previous_day,
            previous_adjusted_close=current.implied_previous_adjusted_close,
            state=QualityState.VERIFIED,
            quality_flags=(),
            version=version,
            lineage=tuple(sorted(set(current.metadata.lineage) | set(calendar_resolution.lineage))),
        )

    previous_day, calendar_resolution = calendar.previous_trading_day(
        market_date,
        decision_at=decision_at,
    )
    if previous_day is None:
        return AdjustedPriceResolution(
            stock_code=stock_code,
            effective_for=market_date,
            previous_trading_day=None,
            previous_adjusted_close=None,
            state=calendar_resolution.state,
            quality_flags=calendar_resolution.quality_flags,
            version=version,
            lineage=calendar_resolution.lineage,
        )
    previous, price_conflict = _latest_price(
        prices,
        stock_code=stock_code,
        market_date=previous_day,
        decision_at=decision_at,
    )
    if price_conflict:
        state = QualityState.CONFLICT
    elif previous is None:
        state = QualityState.MISSING
    else:
        state = QualityState.VERIFIED
    if state is not QualityState.VERIFIED or previous is None:
        return AdjustedPriceResolution(
            stock_code=stock_code,
            effective_for=market_date,
            previous_trading_day=previous_day,
            previous_adjusted_close=None,
            state=state,
            quality_flags=(QualityFlag.REFERENCE_PRICE_UNAVAILABLE,),
            version=version,
            lineage=calendar_resolution.lineage,
        )

    action, action_conflict = _latest_action(
        actions,
        stock_code=stock_code,
        effective_on=market_date,
        decision_at=decision_at,
    )
    if action_conflict:
        return AdjustedPriceResolution(
            stock_code=stock_code,
            effective_for=market_date,
            previous_trading_day=previous_day,
            previous_adjusted_close=None,
            state=QualityState.CONFLICT,
            quality_flags=(
                QualityFlag.CORPORATE_ACTION_UNRESOLVED,
                QualityFlag.REFERENCE_PRICE_UNAVAILABLE,
            ),
            version=version,
            lineage=_lineage(previous),
        )
    if action is None or action.status is CorporateActionStatus.UNRESOLVED:
        return AdjustedPriceResolution(
            stock_code=stock_code,
            effective_for=market_date,
            previous_trading_day=previous_day,
            previous_adjusted_close=None,
            state=QualityState.CORPORATE_ACTION_UNRESOLVED,
            quality_flags=(
                QualityFlag.CORPORATE_ACTION_UNRESOLVED,
                QualityFlag.REFERENCE_PRICE_UNAVAILABLE,
            ),
            version=version,
            lineage=_lineage(previous, action),
        )
    assert action.adjustment_factor is not None
    adjusted = previous.close * action.adjustment_factor
    return AdjustedPriceResolution(
        stock_code=stock_code,
        effective_for=market_date,
        previous_trading_day=previous_day,
        previous_adjusted_close=adjusted,
        state=QualityState.VERIFIED,
        quality_flags=(),
        version=version,
        lineage=tuple(
            sorted(
                set(_lineage(previous, action)) | set(calendar_resolution.lineage)
            )
        ),
    )
