from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.models import require_finite
else:
    require_finite = import_module("packages." + "domain.models").require_finite


class PolicyMaturity(StrEnum):
    ADOPTED = "ADOPTED"
    BACKTEST_PENDING = "BACKTEST_PENDING"


@dataclass(frozen=True, slots=True)
class ThemeCalculationPolicy:
    version: str
    maturity: PolicyMaturity
    maximum_constituent_weight: Decimal
    sufficient_core_weight_ratio: Decimal
    sufficient_related_count_ratio: Decimal
    minimum_core_observations: int
    minimum_total_observations: int

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("calculation policy version은 비어 있을 수 없습니다")
        for field_name, value in (
            ("maximum_constituent_weight", self.maximum_constituent_weight),
            ("sufficient_core_weight_ratio", self.sufficient_core_weight_ratio),
            ("sufficient_related_count_ratio", self.sufficient_related_count_ratio),
        ):
            require_finite(value, field_name)
            if not Decimal(0) < value <= Decimal(1):
                raise ValueError(f"{field_name}은 0보다 크고 1 이하여야 합니다")
        if self.minimum_core_observations < 1:
            raise ValueError("minimum_core_observations는 1 이상이어야 합니다")
        if self.minimum_total_observations < self.minimum_core_observations:
            raise ValueError(
                "minimum_total_observations는 core 최소 관측 수 이상이어야 합니다"
            )


@dataclass(frozen=True, slots=True)
class SameTimeBaselinePolicy:
    version: str
    maturity: PolicyMaturity
    lookback_trading_days: int
    minimum_observations: int

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("baseline policy version은 비어 있을 수 없습니다")
        if self.lookback_trading_days < 1:
            raise ValueError("lookback_trading_days는 1 이상이어야 합니다")
        if not 1 <= self.minimum_observations <= self.lookback_trading_days:
            raise ValueError(
                "minimum_observations는 1 이상 lookback 이하이어야 합니다"
            )


@dataclass(frozen=True, slots=True)
class AttentionPolicy:
    version: str
    maturity: PolicyMaturity
    turnover_multiple_threshold: Decimal
    stock_multiple_threshold: Decimal
    spread_ratio_threshold: Decimal
    minimum_high_interest_stocks: int
    weighted_return_threshold: Decimal
    minimum_valid_members: int
    close_after_absent_trading_days: int

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("attention policy version은 비어 있을 수 없습니다")
        for field_name, value in (
            ("turnover_multiple_threshold", self.turnover_multiple_threshold),
            ("stock_multiple_threshold", self.stock_multiple_threshold),
            ("spread_ratio_threshold", self.spread_ratio_threshold),
            ("weighted_return_threshold", self.weighted_return_threshold),
        ):
            require_finite(value, field_name)
        if self.turnover_multiple_threshold <= 0 or self.stock_multiple_threshold <= 0:
            raise ValueError("거래 관심 배수 threshold는 0보다 커야 합니다")
        if not Decimal(0) < self.spread_ratio_threshold <= Decimal(1):
            raise ValueError("spread_ratio_threshold는 0보다 크고 1 이하여야 합니다")
        if self.minimum_high_interest_stocks < 1:
            raise ValueError("minimum_high_interest_stocks는 1 이상이어야 합니다")
        if self.minimum_valid_members < 1:
            raise ValueError("minimum_valid_members는 1 이상이어야 합니다")
        if self.close_after_absent_trading_days < 1:
            raise ValueError("close_after_absent_trading_days는 1 이상이어야 합니다")


THEME_CALCULATION_POLICY_V1 = ThemeCalculationPolicy(
    version="theme-metrics-2026.08.1",
    maturity=PolicyMaturity.BACKTEST_PENDING,
    maximum_constituent_weight=Decimal("0.30"),
    sufficient_core_weight_ratio=Decimal("0.80"),
    sufficient_related_count_ratio=Decimal("0.70"),
    minimum_core_observations=2,
    minimum_total_observations=3,
)

RANKING_BASELINE_POLICY_V1 = SameTimeBaselinePolicy(
    version="same-time-turnover-20d-2026.08.1",
    maturity=PolicyMaturity.BACKTEST_PENDING,
    lookback_trading_days=20,
    minimum_observations=5,
)

ATTENTION_BASELINE_POLICY_V1 = SameTimeBaselinePolicy(
    version="same-time-attention-60d-2026.08.1",
    maturity=PolicyMaturity.BACKTEST_PENDING,
    lookback_trading_days=60,
    minimum_observations=40,
)

ATTENTION_POLICY_V1 = AttentionPolicy(
    version="attention-interval-2026.08.1",
    maturity=PolicyMaturity.BACKTEST_PENDING,
    turnover_multiple_threshold=Decimal("2.0"),
    stock_multiple_threshold=Decimal("2.0"),
    spread_ratio_threshold=Decimal("0.30"),
    minimum_high_interest_stocks=2,
    weighted_return_threshold=Decimal("0.01"),
    minimum_valid_members=3,
    close_after_absent_trading_days=10,
)
