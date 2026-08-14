from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from packages.domain import (
    MembershipRole,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.events import LineageRef
from packages.realtime import StockRealtimeUpdate

MARKET_DATE = date(2026, 8, 14)
PREVIOUS_DATE = date(2026, 8, 13)
START = datetime(2026, 8, 14, 0, 10, tzinfo=UTC)


def realtime_update(
    stock_id: str,
    *,
    message_id: str | None = None,
    sequence: int = 1,
    occurred_seconds: int = 0,
    received_seconds: int | None = None,
    market_date: date = MARKET_DATE,
    source: str = "REALTIME_0B",
    price: str | None = "110",
    cumulative: str | None = "1000000",
    fresh: bool = True,
) -> StockRealtimeUpdate:
    received_offset = occurred_seconds if received_seconds is None else received_seconds
    return StockRealtimeUpdate(
        message_id=message_id or f"tick-{stock_id}-{source}-{sequence}",
        stock_id=stock_id,
        market_date=market_date,
        source=source,
        source_sequence=sequence,
        occurred_at=START + timedelta(seconds=occurred_seconds),
        received_at=START + timedelta(seconds=received_offset),
        current_price=None if price is None else Decimal(price),
        cumulative_trading_value=(None if cumulative is None else Decimal(cumulative)),
        fresh=fresh,
        lineage=(
            LineageRef(
                kind="MARKET_EVENT",
                identifier=message_id or f"tick-{stock_id}-{source}-{sequence}",
                version="kiwoom-canonical-2026.08.1",
            ),
        ),
    )


def membership(
    *,
    theme_id: str = "thm_a",
    version: str = "membership-a-v1",
    market_date: date = MARKET_DATE,
    known_at: datetime | None = None,
    members: tuple[tuple[str, MembershipRole], ...] = (
        ("stk_1", MembershipRole.CORE),
        ("stk_2", MembershipRole.CORE),
        ("stk_3", MembershipRole.RELATED),
    ),
) -> ThemeMembershipSnapshot:
    return ThemeMembershipSnapshot(
        theme_id=theme_id,
        version=version,
        effective_from=market_date,
        known_at=known_at or START - timedelta(days=1),
        members=tuple(
            ThemeMember(stock_id=stock_id, role=role) for stock_id, role in members
        ),
    )


def reference(
    stock_id: str,
    *,
    market_date: date = MARKET_DATE,
    version: str = "reference-v1",
    known_at: datetime | None = None,
    validated: bool = True,
) -> StockReference:
    return StockReference(
        stock_id=stock_id,
        effective_for=market_date,
        known_at=known_at or START - timedelta(days=1),
        previous_adjusted_close=Decimal(100),
        listed_shares=1_000_000,
        free_float_ratio=Decimal("0.50"),
        free_float_validated=validated,
        version=version,
    )
