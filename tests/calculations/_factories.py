from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

from packages.domain import (
    MembershipRole,
    StockMarketObservation,
    StockReference,
    StockTradingValueObservation,
    ThemeMember,
    ThemeMembershipSnapshot,
)

MARKET_DATE = date(2026, 8, 14)
AS_OF = datetime(2026, 8, 14, 1, 30, tzinfo=UTC)
TIME_BUCKET = time(10, 30)


def make_membership(
    *,
    core: tuple[str, ...],
    related: tuple[str, ...] = (),
    version: str = "membership-test-v1",
    effective_from: date = MARKET_DATE,
    known_at: datetime | None = None,
) -> ThemeMembershipSnapshot:
    return ThemeMembershipSnapshot(
        theme_id="thm_test",
        version=version,
        effective_from=effective_from,
        known_at=known_at or datetime.combine(
            effective_from,
            time(0),
            tzinfo=UTC,
        ),
        members=tuple(
            [ThemeMember(stock_id=stock_id, role=MembershipRole.CORE) for stock_id in core]
            + [
                ThemeMember(stock_id=stock_id, role=MembershipRole.RELATED)
                for stock_id in related
            ]
        ),
    )


def make_reference(
    stock_id: str,
    *,
    previous_close: Decimal | None = Decimal(100),
    listed_shares: int | None = 100,
    free_float_ratio: Decimal | None = Decimal(1),
    free_float_validated: bool = True,
    corporate_action_resolved: bool = True,
    effective_for: date = MARKET_DATE,
    known_at: datetime = AS_OF,
) -> StockReference:
    return StockReference(
        stock_id=stock_id,
        effective_for=effective_for,
        known_at=known_at,
        previous_adjusted_close=previous_close,
        listed_shares=listed_shares,
        free_float_ratio=free_float_ratio,
        free_float_validated=free_float_validated,
        version="reference-test-v1",
        corporate_action_resolved=corporate_action_resolved,
    )


def make_market_observation(
    stock_id: str,
    *,
    current_price: Decimal | None = Decimal(100),
    cumulative_trading_value: Decimal | None = Decimal(1000),
    fresh: bool = True,
    trading_halted: bool = False,
    corporate_action_unresolved: bool = False,
    market_date: date = MARKET_DATE,
    observed_at: datetime = AS_OF,
) -> StockMarketObservation:
    return StockMarketObservation(
        stock_id=stock_id,
        market_date=market_date,
        observed_at=observed_at,
        current_price=current_price,
        cumulative_trading_value=cumulative_trading_value,
        fresh=fresh,
        trading_halted=trading_halted,
        corporate_action_unresolved=corporate_action_unresolved,
    )


def make_trading_value_observation(
    stock_id: str,
    market_date: date,
    value: Decimal | None,
    *,
    bucket: time = TIME_BUCKET,
    observed_at: datetime | None = None,
    comparable: bool = True,
    fresh: bool = True,
    trading_halted: bool = False,
    corporate_action_unresolved: bool = False,
) -> StockTradingValueObservation:
    return StockTradingValueObservation(
        stock_id=stock_id,
        market_date=market_date,
        observed_at=observed_at
        or datetime.combine(market_date, bucket, tzinfo=UTC),
        time_bucket=bucket,
        cumulative_trading_value=value,
        comparable=comparable,
        fresh=fresh,
        trading_halted=trading_halted,
        corporate_action_unresolved=corporate_action_unresolved,
    )
