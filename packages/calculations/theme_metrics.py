from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from domain.coverage import Coverage, CoveragePart, CoverageStatus
    from domain.models import (
        MembershipRole,
        QualityFlag,
        StockMarketObservation,
        StockReference,
        ThemeMembershipSnapshot,
        UnavailableReason,
        decimal_to_number,
        require_aware,
    )
else:
    _coverage = import_module("packages." + "domain.coverage")
    Coverage = _coverage.Coverage
    CoveragePart = _coverage.CoveragePart
    CoverageStatus = _coverage.CoverageStatus
    _models = import_module("packages." + "domain.models")
    MembershipRole = _models.MembershipRole
    QualityFlag = _models.QualityFlag
    StockMarketObservation = _models.StockMarketObservation
    StockReference = _models.StockReference
    ThemeMembershipSnapshot = _models.ThemeMembershipSnapshot
    UnavailableReason = _models.UnavailableReason
    decimal_to_number = _models.decimal_to_number
    require_aware = _models.require_aware

from .policies import THEME_CALCULATION_POLICY_V1, ThemeCalculationPolicy
from .weights import (
    CapitalizationInput,
    CappedWeight,
    calculate_capped_weights,
    calculate_weighted_return,
)


@dataclass(frozen=True, slots=True)
class MetricIssue:
    metric: str
    reason: UnavailableReason
    stock_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ThemeMetrics:
    calculation_version: str
    policy_maturity: str
    membership_version: str
    weighted_return: Decimal | None
    median_return: Decimal | None
    advancing_count: int | None
    valid_count: int | None
    advancing_ratio: Decimal | None
    coverage: Coverage
    capped_weights: tuple[CappedWeight, ...]
    quality_flags: tuple[str, ...]
    issues: tuple[MetricIssue, ...]
    reference_versions: tuple[str, ...]

    @property
    def rank_eligible(self) -> bool:
        return (
            self.coverage.status is CoverageStatus.SUFFICIENT
            and self.weighted_return is not None
            and self.median_return is not None
            and self.advancing_count is not None
            and self.valid_count is not None
        )

    def to_public_ranking_fields(
        self,
        *,
        additional_quality_flags: Iterable[str] = (),
    ) -> dict[str, object]:
        quality_flags = tuple(
            sorted(set(self.quality_flags).union(additional_quality_flags))
        )
        return {
            "weightedReturn": decimal_to_number(self.weighted_return),
            "weightMethod": "FREE_FLOAT_CAPPED",
            "advancingCount": self.advancing_count,
            "validCount": self.valid_count,
            "coverage": self.coverage.to_public_dict(),
            "qualityFlags": list(quality_flags),
        }

    def to_public_current_reaction(
        self,
        *,
        turnover_multiple: Decimal | None,
        attention_gap_trading_days: int | None,
    ) -> dict[str, object]:
        if attention_gap_trading_days is not None and attention_gap_trading_days < 0:
            raise ValueError("attention_gap_trading_days는 음수일 수 없습니다")
        return {
            "weightedReturn": decimal_to_number(self.weighted_return),
            "weightMethod": "FREE_FLOAT_CAPPED",
            "advancingCount": self.advancing_count,
            "validCount": self.valid_count,
            "turnoverMultiple": decimal_to_number(turnover_multiple),
            "attentionGapTradingDays": attention_gap_trading_days,
        }


def _index_unique[RecordT: (StockReference, StockMarketObservation)](
    records: Iterable[RecordT],
    *,
    label: str,
) -> dict[str, RecordT]:
    result: dict[str, RecordT] = {}
    for record in records:
        if record.stock_id in result:
            raise ValueError(f"{label}에 중복 stock_id가 있습니다: {record.stock_id}")
        result[record.stock_id] = record
    return result


def _median(values: Iterable[Decimal]) -> Decimal | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    with localcontext() as context:
        context.prec = 60
        return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _stock_return(
    *,
    stock_id: str,
    market_date: date,
    as_of: datetime,
    references: Mapping[str, StockReference],
    observations: Mapping[str, StockMarketObservation],
    quality_flags: set[str],
    issue_stock_ids: dict[UnavailableReason, set[str]],
) -> Decimal | None:
    reference = references.get(stock_id)
    observation = observations.get(stock_id)
    if reference is None:
        quality_flags.add(QualityFlag.REFERENCE_PRICE_UNAVAILABLE.value)
        issue_stock_ids.setdefault(
            UnavailableReason.MISSING_REFERENCE_DATA,
            set(),
        ).add(stock_id)
        return None
    if not reference.corporate_action_resolved:
        quality_flags.add(QualityFlag.CORPORATE_ACTION_UNRESOLVED.value)
        issue_stock_ids.setdefault(
            UnavailableReason.CORPORATE_ACTION_UNRESOLVED,
            set(),
        ).add(stock_id)
        return None
    if reference.effective_for != market_date or reference.known_at > as_of:
        quality_flags.add(QualityFlag.POINT_IN_TIME_VIOLATION.value)
        issue_stock_ids.setdefault(
            UnavailableReason.MISSING_REFERENCE_DATA,
            set(),
        ).add(stock_id)
        return None
    previous_close = reference.previous_adjusted_close
    if previous_close is None or previous_close <= 0:
        quality_flags.add(QualityFlag.REFERENCE_PRICE_UNAVAILABLE.value)
        issue_stock_ids.setdefault(
            UnavailableReason.MISSING_REFERENCE_DATA,
            set(),
        ).add(stock_id)
        return None
    if observation is None:
        quality_flags.add(QualityFlag.MARKET_PRICE_UNAVAILABLE.value)
        issue_stock_ids.setdefault(
            UnavailableReason.MISSING_MARKET_DATA,
            set(),
        ).add(stock_id)
        return None
    if observation.market_date != market_date or observation.observed_at > as_of:
        quality_flags.add(QualityFlag.POINT_IN_TIME_VIOLATION.value)
        issue_stock_ids.setdefault(
            UnavailableReason.MISSING_MARKET_DATA,
            set(),
        ).add(stock_id)
        return None
    if observation.trading_halted:
        quality_flags.add(QualityFlag.TRADING_HALTED.value)
        issue_stock_ids.setdefault(UnavailableReason.TRADING_HALTED, set()).add(
            stock_id
        )
        return None
    if observation.corporate_action_unresolved:
        quality_flags.add(QualityFlag.CORPORATE_ACTION_UNRESOLVED.value)
        issue_stock_ids.setdefault(
            UnavailableReason.CORPORATE_ACTION_UNRESOLVED,
            set(),
        ).add(stock_id)
        return None
    if not observation.fresh:
        quality_flags.add(QualityFlag.STALE_MARKET_DATA.value)
        issue_stock_ids.setdefault(
            UnavailableReason.MISSING_MARKET_DATA,
            set(),
        ).add(stock_id)
        return None
    if observation.current_price is None or observation.current_price <= 0:
        quality_flags.add(QualityFlag.MARKET_PRICE_UNAVAILABLE.value)
        issue_stock_ids.setdefault(
            UnavailableReason.MISSING_MARKET_DATA,
            set(),
        ).add(stock_id)
        return None
    with localcontext() as context:
        context.prec = 60
        return observation.current_price / previous_close - Decimal(1)


def determine_coverage_status(
    *,
    core: CoveragePart,
    related: CoveragePart,
    total_valid_count: int,
    policy: ThemeCalculationPolicy,
) -> CoverageStatus:
    """Classify coverage; related represents Core + Related total coverage."""

    if (
        core.observed_weight_ratio is None
        or core.observed_count < policy.minimum_core_observations
        or total_valid_count < policy.minimum_total_observations
        or related.count_ratio is None
    ):
        return CoverageStatus.INSUFFICIENT
    if (
        core.observed_weight_ratio >= policy.sufficient_core_weight_ratio
        and related.count_ratio >= policy.sufficient_related_count_ratio
    ):
        return CoverageStatus.SUFFICIENT
    return CoverageStatus.PARTIAL


def calculate_theme_metrics(
    *,
    market_date: date,
    as_of: datetime,
    membership: ThemeMembershipSnapshot,
    references: Iterable[StockReference],
    observations: Iterable[StockMarketObservation],
    policy: ThemeCalculationPolicy = THEME_CALCULATION_POLICY_V1,
) -> ThemeMetrics:
    """Calculate deterministic current-theme metrics from a PIT membership snapshot."""

    require_aware(as_of, "as_of")
    if membership.effective_from > market_date or membership.known_at > as_of:
        raise ValueError("계산 시점에 알려지지 않은 membership은 사용할 수 없습니다")

    reference_by_stock = _index_unique(references, label="references")
    observation_by_stock = _index_unique(observations, label="observations")
    ordered_members = sorted(membership.members, key=lambda member: member.stock_id)
    core_members = [
        member for member in ordered_members if member.role is MembershipRole.CORE
    ]

    quality_flags: set[str] = set()
    issue_stock_ids: dict[UnavailableReason, set[str]] = {}
    returns_by_stock: dict[str, Decimal] = {}
    for member in ordered_members:
        stock_return = _stock_return(
            stock_id=member.stock_id,
            market_date=market_date,
            as_of=as_of,
            references=reference_by_stock,
            observations=observation_by_stock,
            quality_flags=quality_flags,
            issue_stock_ids=issue_stock_ids,
        )
        if stock_return is not None:
            returns_by_stock[member.stock_id] = stock_return

    capitalization_by_stock: dict[str, Decimal] = {}
    missing_cap_stock_ids: set[str] = set()
    for member in core_members:
        reference = reference_by_stock.get(member.stock_id)
        capitalization = (
            None
            if reference is None
            else reference.free_float_market_cap(
                market_date=market_date,
                as_of=as_of,
            )
        )
        if capitalization is None:
            missing_cap_stock_ids.add(member.stock_id)
        else:
            capitalization_by_stock[member.stock_id] = capitalization

    if missing_cap_stock_ids:
        quality_flags.add(QualityFlag.FREE_FLOAT_UNAVAILABLE.value)
        issue_stock_ids.setdefault(
            UnavailableReason.MISSING_REFERENCE_DATA,
            set(),
        ).update(missing_cap_stock_ids)

    observed_core_ids = [
        member.stock_id
        for member in core_members
        if member.stock_id in returns_by_stock
        and member.stock_id in capitalization_by_stock
    ]
    observed_weight_ratio: Decimal | None = None
    if core_members and not missing_cap_stock_ids:
        total_capitalization = sum(
            capitalization_by_stock.values(),
            start=Decimal(0),
        )
        if total_capitalization > 0:
            with localcontext() as context:
                context.prec = 60
                observed_weight_ratio = sum(
                    (
                        capitalization_by_stock[stock_id]
                        for stock_id in observed_core_ids
                    ),
                    start=Decimal(0),
                ) / total_capitalization

    core_coverage = CoveragePart.from_counts(
        observed_count=len(observed_core_ids),
        total_count=len(core_members),
        observed_weight_ratio=observed_weight_ratio,
    )
    related_coverage = CoveragePart.from_counts(
        observed_count=len(returns_by_stock),
        total_count=len(ordered_members),
    )
    coverage_status = determine_coverage_status(
        core=core_coverage,
        related=related_coverage,
        total_valid_count=len(returns_by_stock),
        policy=policy,
    )
    coverage = Coverage(
        status=coverage_status,
        core=core_coverage,
        related=related_coverage,
    )
    if coverage_status is CoverageStatus.PARTIAL:
        quality_flags.add(QualityFlag.PARTIAL_COVERAGE.value)
    elif coverage_status is CoverageStatus.INSUFFICIENT:
        quality_flags.add(QualityFlag.INSUFFICIENT_COVERAGE.value)

    if (
        len(observed_core_ids) < policy.minimum_core_observations
        or len(returns_by_stock) < policy.minimum_total_observations
    ):
        quality_flags.add(QualityFlag.INSUFFICIENT_OBSERVATIONS.value)
        issue_stock_ids.setdefault(
            UnavailableReason.INSUFFICIENT_OBSERVATIONS,
            set(),
        )

    capped_weights: tuple[CappedWeight, ...] = ()
    weighted_return: Decimal | None = None
    if (
        not missing_cap_stock_ids
        and len(observed_core_ids) >= policy.minimum_core_observations
    ):
        capped_weights = calculate_capped_weights(
            (
                CapitalizationInput(
                    stock_id=stock_id,
                    free_float_market_cap=capitalization_by_stock[stock_id],
                )
                for stock_id in observed_core_ids
            ),
            configured_cap=policy.maximum_constituent_weight,
        )
        weighted_return = calculate_weighted_return(
            capped_weights,
            {stock_id: returns_by_stock[stock_id] for stock_id in observed_core_ids},
        )

    median_return = _median(returns_by_stock.values())
    if returns_by_stock:
        advancing_count_value = sum(
            stock_return > 0 for stock_return in returns_by_stock.values()
        )
        valid_count_value = len(returns_by_stock)
        with localcontext() as context:
            context.prec = 60
            advancing_ratio: Decimal | None = Decimal(
                advancing_count_value
            ) / Decimal(valid_count_value)
        advancing_count: int | None = advancing_count_value
        valid_count: int | None = valid_count_value
    else:
        advancing_count = None
        valid_count = None
        advancing_ratio = None

    issues = [
        MetricIssue(
            metric="constituentData",
            reason=reason,
            stock_ids=tuple(sorted(stock_ids)),
        )
        for reason, stock_ids in issue_stock_ids.items()
        if stock_ids
    ]
    if weighted_return is None:
        weighted_reason = (
            UnavailableReason.EMPTY_MEMBERSHIP
            if not core_members
            else UnavailableReason.INSUFFICIENT_COVERAGE
        )
        issues.append(MetricIssue(metric="weightedReturn", reason=weighted_reason))
    if median_return is None:
        issues.append(
            MetricIssue(
                metric="medianReturn",
                reason=UnavailableReason.INSUFFICIENT_OBSERVATIONS,
            )
        )

    reference_versions = tuple(
        sorted(
            {
                reference_by_stock[member.stock_id].version
                for member in ordered_members
                if member.stock_id in reference_by_stock
            }
        )
    )
    return ThemeMetrics(
        calculation_version=policy.version,
        policy_maturity=policy.maturity.value,
        membership_version=membership.version,
        weighted_return=weighted_return,
        median_return=median_return,
        advancing_count=advancing_count,
        valid_count=valid_count,
        advancing_ratio=advancing_ratio,
        coverage=coverage,
        capped_weights=capped_weights,
        quality_flags=tuple(sorted(quality_flags)),
        issues=tuple(
            sorted(
                issues,
                key=lambda issue: (
                    issue.metric,
                    issue.reason.value,
                    issue.stock_ids,
                ),
            )
        ),
        reference_versions=reference_versions,
    )
