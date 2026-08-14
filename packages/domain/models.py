from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum


class MembershipRole(StrEnum):
    CORE = "CORE"
    RELATED = "RELATED"


class DataStatus(StrEnum):
    PREOPEN = "PREOPEN"
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    DEGRADED = "DEGRADED"
    CLOSED = "CLOSED"


class QualityFlag(StrEnum):
    PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    FREE_FLOAT_UNAVAILABLE = "FREE_FLOAT_UNAVAILABLE"
    PROVISIONAL_BASELINE = "PROVISIONAL_BASELINE"
    MEMBERSHIP_VERSION_MISMATCH = "MEMBERSHIP_VERSION_MISMATCH"
    SOURCE_DEGRADED = "SOURCE_DEGRADED"
    REFERENCE_PRICE_UNAVAILABLE = "REFERENCE_PRICE_UNAVAILABLE"
    MARKET_PRICE_UNAVAILABLE = "MARKET_PRICE_UNAVAILABLE"
    TRADING_HALTED = "TRADING_HALTED"
    CORPORATE_ACTION_UNRESOLVED = "CORPORATE_ACTION_UNRESOLVED"
    INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"
    ZERO_BASELINE = "ZERO_BASELINE"
    POINT_IN_TIME_VIOLATION = "POINT_IN_TIME_VIOLATION"


class UnavailableReason(StrEnum):
    EMPTY_MEMBERSHIP = "EMPTY_MEMBERSHIP"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    MISSING_REFERENCE_DATA = "MISSING_REFERENCE_DATA"
    MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
    TRADING_HALTED = "TRADING_HALTED"
    CORPORATE_ACTION_UNRESOLVED = "CORPORATE_ACTION_UNRESOLVED"
    INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"
    ZERO_BASELINE = "ZERO_BASELINE"
    NO_PRIOR_ATTENTION_INTERVAL = "NO_PRIOR_ATTENTION_INTERVAL"
    NO_CURRENT_ATTENTION_INTERVAL = "NO_CURRENT_ATTENTION_INTERVAL"
    MEMBERSHIP_HISTORY_UNAVAILABLE = "MEMBERSHIP_HISTORY_UNAVAILABLE"
    MEMBERSHIP_VERSION_MISMATCH = "MEMBERSHIP_VERSION_MISMATCH"
    INCOMPLETE_ATTENTION_HISTORY = "INCOMPLETE_ATTENTION_HISTORY"


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다")


def require_finite(value: Decimal, field_name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field_name}은 유한한 Decimal이어야 합니다")


def decimal_to_number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class ThemeMember:
    stock_id: str
    role: MembershipRole

    def __post_init__(self) -> None:
        if not self.stock_id:
            raise ValueError("stock_id는 비어 있을 수 없습니다")


@dataclass(frozen=True, slots=True)
class ThemeMembershipSnapshot:
    theme_id: str
    version: str
    effective_from: date
    known_at: datetime
    members: tuple[ThemeMember, ...]

    def __post_init__(self) -> None:
        if not self.theme_id:
            raise ValueError("theme_id는 비어 있을 수 없습니다")
        if not self.version:
            raise ValueError("membership version은 비어 있을 수 없습니다")
        require_aware(self.known_at, "known_at")
        stock_ids = [member.stock_id for member in self.members]
        if len(stock_ids) != len(set(stock_ids)):
            raise ValueError("membership snapshot에 중복 stock_id가 있습니다")

    def contains(self, stock_id: str) -> bool:
        return any(member.stock_id == stock_id for member in self.members)


def select_membership_snapshot(
    snapshots: Iterable[ThemeMembershipSnapshot],
    *,
    theme_id: str,
    market_date: date,
    decision_at: datetime,
) -> ThemeMembershipSnapshot | None:
    """Select only membership that was effective and known at the decision time."""

    require_aware(decision_at, "decision_at")
    eligible = [
        snapshot
        for snapshot in snapshots
        if snapshot.theme_id == theme_id
        and snapshot.effective_from <= market_date
        and snapshot.known_at <= decision_at
    ]
    if not eligible:
        return None
    eligible.sort(
        key=lambda snapshot: (
            snapshot.effective_from,
            snapshot.known_at,
            snapshot.version,
        )
    )
    selected = eligible[-1]
    selected_key = (selected.effective_from, selected.known_at)
    if sum(
        (snapshot.effective_from, snapshot.known_at) == selected_key
        for snapshot in eligible
    ) > 1:
        raise ValueError("같은 시점에 둘 이상의 membership version이 유효합니다")
    return selected


@dataclass(frozen=True, slots=True)
class StockReference:
    stock_id: str
    effective_for: date
    known_at: datetime
    previous_adjusted_close: Decimal | None
    listed_shares: int | None
    free_float_ratio: Decimal | None
    free_float_validated: bool
    version: str
    corporate_action_resolved: bool = True

    def __post_init__(self) -> None:
        if not self.stock_id:
            raise ValueError("stock_id는 비어 있을 수 없습니다")
        if not self.version:
            raise ValueError("reference version은 비어 있을 수 없습니다")
        require_aware(self.known_at, "known_at")
        if self.previous_adjusted_close is not None:
            require_finite(self.previous_adjusted_close, "previous_adjusted_close")
            if self.previous_adjusted_close < 0:
                raise ValueError("previous_adjusted_close는 음수일 수 없습니다")
        if self.listed_shares is not None and self.listed_shares < 0:
            raise ValueError("listed_shares는 음수일 수 없습니다")
        if self.free_float_ratio is not None:
            require_finite(self.free_float_ratio, "free_float_ratio")
            if not Decimal(0) <= self.free_float_ratio <= Decimal(1):
                raise ValueError("free_float_ratio는 0과 1 사이여야 합니다")

    def adjusted_return_reference(self, *, market_date: date, as_of: datetime) -> Decimal | None:
        if (
            self.effective_for != market_date
            or self.known_at > as_of
            or not self.corporate_action_resolved
            or self.previous_adjusted_close is None
            or self.previous_adjusted_close <= 0
        ):
            return None
        return self.previous_adjusted_close

    def free_float_market_cap(self, *, market_date: date, as_of: datetime) -> Decimal | None:
        previous_close = self.adjusted_return_reference(
            market_date=market_date,
            as_of=as_of,
        )
        if (
            previous_close is None
            or self.listed_shares is None
            or self.listed_shares <= 0
            or self.free_float_ratio is None
            or self.free_float_ratio <= 0
            or not self.free_float_validated
        ):
            return None
        return previous_close * self.listed_shares * self.free_float_ratio


@dataclass(frozen=True, slots=True)
class StockMarketObservation:
    stock_id: str
    market_date: date
    observed_at: datetime
    current_price: Decimal | None
    cumulative_trading_value: Decimal | None
    fresh: bool = True
    trading_halted: bool = False
    corporate_action_unresolved: bool = False

    def __post_init__(self) -> None:
        if not self.stock_id:
            raise ValueError("stock_id는 비어 있을 수 없습니다")
        require_aware(self.observed_at, "observed_at")
        if self.current_price is not None:
            require_finite(self.current_price, "current_price")
            if self.current_price < 0:
                raise ValueError("current_price는 음수일 수 없습니다")
        if self.cumulative_trading_value is not None:
            require_finite(
                self.cumulative_trading_value,
                "cumulative_trading_value",
            )
            if self.cumulative_trading_value < 0:
                raise ValueError("cumulative_trading_value는 음수일 수 없습니다")


@dataclass(frozen=True, slots=True)
class StockTradingValueObservation:
    stock_id: str
    market_date: date
    observed_at: datetime
    time_bucket: time
    cumulative_trading_value: Decimal | None
    comparable: bool = True
    fresh: bool = True
    trading_halted: bool = False
    corporate_action_unresolved: bool = False

    def __post_init__(self) -> None:
        if not self.stock_id:
            raise ValueError("stock_id는 비어 있을 수 없습니다")
        require_aware(self.observed_at, "observed_at")
        if self.time_bucket.second or self.time_bucket.microsecond:
            raise ValueError("time_bucket은 1분 경계여야 합니다")
        if self.cumulative_trading_value is not None:
            require_finite(
                self.cumulative_trading_value,
                "cumulative_trading_value",
            )
            if self.cumulative_trading_value < 0:
                raise ValueError("cumulative_trading_value는 음수일 수 없습니다")
