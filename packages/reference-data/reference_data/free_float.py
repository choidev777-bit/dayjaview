"""무료 공식 원천만 사용하는 DAYJAVIEW 유동주식비율 산출."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Final

from .models import (
    CoverageDeclarationStatus,
    CoverageStatus,
    EconomicField,
    FieldObservation,
    FieldResolution,
    FreeFloatResult,
    HoldingCoverageDeclaration,
    NonFloatHolding,
    QualityFlag,
    QualityState,
    ReferenceCoverage,
    ReferencePolicy,
    SourceDataset,
    require_aware,
)

# 유동주식비율의 분모는 차감분과 **같은 공시**의 발행주식수여야 자기완결이다.
# KRX 상장주식수는 결산기준일보다 뒤인 거래일 기준이라 증자·액면분할이 있으면
# 정상적으로 어긋난다. 실측 2,411종목 중 176종목이 그랬다. 비율 계산에는 쓰지
# 않고, 유동시가총액을 곱할 주식수로만 쓴다(packages/pipeline/references.py).
FIELD_SOURCE_PRIORITY: Final[tuple[SourceDataset, ...]] = (
    SourceDataset.OPENDART_STOCK_TOTAL,
)

HOLDING_SOURCE_PRIORITY: Final[tuple[SourceDataset, ...]] = (
    SourceDataset.OPENDART_TREASURY_STATUS,
    SourceDataset.OPENDART_STOCK_TOTAL,
    SourceDataset.OPENDART_LARGEST_SHAREHOLDER,
    SourceDataset.OPENDART_DISCLOSURE_CLASSIFICATION,
)

def _pit_eligible(
    value: FieldObservation | NonFloatHolding | HoldingCoverageDeclaration,
    *,
    market_date: date,
    decision_at: datetime,
) -> bool:
    return value.effective_on <= market_date and value.metadata.known_at <= decision_at


def _lineage(values: Iterable[FieldObservation | NonFloatHolding | HoldingCoverageDeclaration]) -> tuple[str, ...]:
    items: set[str] = set()
    for value in values:
        items.update(value.metadata.lineage)
        items.update(f"document:{item}" for item in value.metadata.source_document_ids)
    return tuple(sorted(items))


def _flags(*values: QualityFlag) -> tuple[QualityFlag, ...]:
    return tuple(sorted(set(values), key=lambda item: item.value))


def resolve_listed_common_shares(
    observations: Iterable[FieldObservation],
    *,
    stock_code: str,
    market_date: date,
    decision_at: datetime,
    policy: ReferencePolicy,
) -> FieldResolution:
    """비유동 차감분과 같은 공시의 발행 보통주식수를 비율 분모로 확정한다."""

    require_aware(decision_at, "decision_at")
    candidates = [
        value
        for value in observations
        if value.stock_code == stock_code
        and value.field is EconomicField.LISTED_COMMON_SHARES
        and _pit_eligible(value, market_date=market_date, decision_at=decision_at)
    ]
    selected_by_source: dict[SourceDataset, FieldObservation] = {}
    internal_conflict = False
    for source in FIELD_SOURCE_PRIORITY:
        source_values = [value for value in candidates if value.metadata.dataset is source]
        if not source_values:
            continue
        latest_key = max(
            (value.effective_on, value.metadata.known_at, value.metadata.revision)
            for value in source_values
        )
        latest = [
            value
            for value in source_values
            if (value.effective_on, value.metadata.known_at, value.metadata.revision)
            == latest_key
        ]
        if len({value.value for value in latest}) != 1:
            internal_conflict = True
        selected_by_source[source] = latest[0]
    missing = [source for source in FIELD_SOURCE_PRIORITY if source not in selected_by_source]
    selected = tuple(
        selected_by_source[source]
        for source in FIELD_SOURCE_PRIORITY
        if source in selected_by_source
    )
    if missing:
        return FieldResolution(
            value=None,
            state=QualityState.MISSING,
            quality_flags=_flags(QualityFlag.FREE_FLOAT_UNAVAILABLE, QualityFlag.SOURCE_MISSING),
            selected=selected,
            lineage=_lineage(selected),
        )
    stale = [
        value
        for value in selected
        if market_date - value.effective_on > policy.free_float_stale_after
    ]
    if stale:
        return FieldResolution(
            value=None,
            state=QualityState.STALE,
            quality_flags=_flags(
                QualityFlag.FREE_FLOAT_UNAVAILABLE,
                QualityFlag.FREE_FLOAT_STALE,
                QualityFlag.SOURCE_STALE,
            ),
            selected=selected,
            lineage=_lineage(selected),
        )
    values = {value.value for value in selected}
    if internal_conflict or len(values) != 1:
        return FieldResolution(
            value=None,
            state=QualityState.CONFLICT,
            quality_flags=_flags(
                QualityFlag.FREE_FLOAT_UNAVAILABLE,
                QualityFlag.FREE_FLOAT_SOURCE_CONFLICT,
            ),
            selected=selected,
            lineage=_lineage(selected),
        )
    return FieldResolution(
        value=selected[0].value,
        state=QualityState.VERIFIED,
        quality_flags=(),
        selected=selected,
        lineage=_lineage(selected),
    )


def _coverage_for_categories(
    declarations: Iterable[HoldingCoverageDeclaration],
    *,
    stock_code: str,
    market_date: date,
    decision_at: datetime,
    policy: ReferencePolicy,
) -> tuple[bool, bool, bool, tuple[HoldingCoverageDeclaration, ...]]:
    candidates = [
        value
        for value in declarations
        if value.stock_code == stock_code
        and _pit_eligible(value, market_date=market_date, decision_at=decision_at)
    ]
    selected: list[HoldingCoverageDeclaration] = []
    missing = False
    stale = False
    conflict = False
    priority = {dataset: index for index, dataset in enumerate(HOLDING_SOURCE_PRIORITY)}
    for category in policy.required_non_float_categories:
        category_values = [value for value in candidates if value.category is category]
        if not category_values:
            missing = True
            continue
        by_source: dict[SourceDataset, HoldingCoverageDeclaration] = {}
        for source in {value.metadata.dataset for value in category_values}:
            source_values = [
                value for value in category_values if value.metadata.dataset is source
            ]
            latest_key = max(
                (value.effective_on, value.metadata.known_at, value.metadata.revision)
                for value in source_values
            )
            source_latest = [
                value
                for value in source_values
                if (value.effective_on, value.metadata.known_at, value.metadata.revision)
                == latest_key
            ]
            if len({value.status for value in source_latest}) != 1:
                conflict = True
            by_source[source] = source_latest[0]
        latest_effective = max(value.effective_on for value in by_source.values())
        comparable = [
            value for value in by_source.values() if value.effective_on == latest_effective
        ]
        if len({value.status for value in comparable}) != 1:
            conflict = True
        latest = min(
            comparable,
            key=lambda value: (
                priority.get(value.metadata.dataset, len(priority)),
                -value.metadata.revision,
                value.metadata.source_key,
            ),
        )
        selected.append(latest)
        if latest.status is CoverageDeclarationStatus.INCOMPLETE:
            missing = True
        if market_date - latest.effective_on > policy.free_float_stale_after:
            stale = True
    return missing, stale, conflict, tuple(selected)


def _select_holdings(
    holdings: Iterable[NonFloatHolding],
    *,
    stock_code: str,
    market_date: date,
    decision_at: datetime,
) -> tuple[
    tuple[NonFloatHolding, ...],
    int,
    bool,
    tuple[NonFloatHolding, ...],
]:
    groups: defaultdict[tuple[str, str, object], list[NonFloatHolding]] = defaultdict(list)
    for holding in holdings:
        if holding.stock_code != stock_code or not _pit_eligible(
            holding,
            market_date=market_date,
            decision_at=decision_at,
        ):
            continue
        groups[holding.economic_key].append(holding)

    priority = {dataset: index for index, dataset in enumerate(HOLDING_SOURCE_PRIORITY)}
    selected: list[NonFloatHolding] = []
    considered: list[NonFloatHolding] = []
    duplicate_count = 0
    conflict = False
    for values in groups.values():
        latest_effective = max(value.effective_on for value in values)
        latest = [value for value in values if value.effective_on == latest_effective]
        latest_by_source_values: list[NonFloatHolding] = []
        for source in {value.metadata.dataset for value in latest}:
            source_values = [
                value for value in latest if value.metadata.dataset is source
            ]
            latest_key = max(
                (value.metadata.known_at, value.metadata.revision)
                for value in source_values
            )
            source_latest = [
                value
                for value in source_values
                if (value.metadata.known_at, value.metadata.revision) == latest_key
            ]
            considered.extend(source_latest)
            if len({(value.shares, value.category) for value in source_latest}) != 1:
                conflict = True
                continue
            duplicate_count += len(source_latest) - 1
            latest_by_source_values.append(source_latest[0])
        latest_by_source = tuple(latest_by_source_values)
        if not latest_by_source:
            continue
        if len({value.shares for value in latest_by_source}) != 1:
            conflict = True
            continue
        if len({value.category for value in latest_by_source}) != 1:
            conflict = True
            continue
        ordered = sorted(
            latest_by_source,
            key=lambda value: (
                priority.get(value.metadata.dataset, len(priority)),
                -value.metadata.revision,
                value.metadata.source_key,
            ),
        )
        selected.append(ordered[0])
        duplicate_count += len(ordered) - 1
    selected.sort(key=lambda value: (value.holder_id, value.share_class.value))
    return tuple(selected), duplicate_count, conflict, tuple(considered)


def _unavailable(
    *,
    stock_code: str,
    market_date: date,
    state: QualityState,
    flags: Sequence[QualityFlag],
    policy: ReferencePolicy,
    issued: int | None,
    duplicate_count: int,
    lineage: tuple[str, ...],
) -> FreeFloatResult:
    return FreeFloatResult(
        stock_code=stock_code,
        effective_on=market_date,
        ratio=None,
        issued_common_shares=issued,
        deducted_non_float_shares=None,
        free_float_shares=None,
        state=state,
        quality_flags=tuple(sorted(set(flags), key=lambda item: item.value)),
        duplicate_deductions_prevented=duplicate_count,
        calculation_version=policy.version,
        lineage=lineage,
    )


def calculate_free_float(
    *,
    stock_code: str,
    market_date: date,
    decision_at: datetime,
    share_observations: Iterable[FieldObservation],
    holdings: Iterable[NonFloatHolding],
    coverage_declarations: Iterable[HoldingCoverageDeclaration],
    policy: ReferencePolicy,
) -> FreeFloatResult:
    """확인 가능 비유동 보통주를 economic holder 단위로 한 번만 차감한다."""

    require_aware(decision_at, "decision_at")
    share_values = tuple(share_observations)
    holding_values = tuple(holdings)
    declaration_values = tuple(coverage_declarations)
    shares = resolve_listed_common_shares(
        share_values,
        stock_code=stock_code,
        market_date=market_date,
        decision_at=decision_at,
        policy=policy,
    )
    if shares.state is not QualityState.VERIFIED or shares.value is None:
        return _unavailable(
            stock_code=stock_code,
            market_date=market_date,
            state=shares.state,
            flags=shares.quality_flags,
            policy=policy,
            issued=None,
            duplicate_count=0,
            lineage=shares.lineage,
        )

    (
        coverage_missing,
        coverage_stale,
        coverage_conflict,
        selected_coverage,
    ) = _coverage_for_categories(
        declaration_values,
        stock_code=stock_code,
        market_date=market_date,
        decision_at=decision_at,
        policy=policy,
    )
    (
        selected_holdings,
        duplicate_count,
        holding_conflict,
        considered_holdings,
    ) = _select_holdings(
        holding_values,
        stock_code=stock_code,
        market_date=market_date,
        decision_at=decision_at,
    )
    lineage = tuple(
        sorted(
            set(shares.lineage)
            | set(_lineage(selected_coverage))
            | set(_lineage(considered_holdings))
        )
    )
    duplicate_flags = (
        (QualityFlag.DUPLICATE_DEDUCTION_PREVENTED,)
        if duplicate_count
        else ()
    )
    if coverage_conflict or holding_conflict:
        return _unavailable(
            stock_code=stock_code,
            market_date=market_date,
            state=QualityState.CONFLICT,
            flags=(
                QualityFlag.FREE_FLOAT_UNAVAILABLE,
                QualityFlag.FREE_FLOAT_SOURCE_CONFLICT,
                *duplicate_flags,
            ),
            policy=policy,
            issued=shares.value,
            duplicate_count=duplicate_count,
            lineage=lineage,
        )
    if coverage_missing:
        return _unavailable(
            stock_code=stock_code,
            market_date=market_date,
            state=QualityState.MISSING,
            flags=(QualityFlag.FREE_FLOAT_UNAVAILABLE, QualityFlag.SOURCE_MISSING, *duplicate_flags),
            policy=policy,
            issued=shares.value,
            duplicate_count=duplicate_count,
            lineage=lineage,
        )
    if coverage_stale or any(
        market_date - value.effective_on > policy.free_float_stale_after
        for value in selected_holdings
    ):
        return _unavailable(
            stock_code=stock_code,
            market_date=market_date,
            state=QualityState.STALE,
            flags=(
                QualityFlag.FREE_FLOAT_UNAVAILABLE,
                QualityFlag.FREE_FLOAT_STALE,
                QualityFlag.SOURCE_STALE,
                *duplicate_flags,
            ),
            policy=policy,
            issued=shares.value,
            duplicate_count=duplicate_count,
            lineage=lineage,
        )
    deducted = sum((value.shares for value in selected_holdings), start=0)
    if deducted > shares.value:
        return _unavailable(
            stock_code=stock_code,
            market_date=market_date,
            state=QualityState.CONFLICT,
            flags=(
                QualityFlag.FREE_FLOAT_UNAVAILABLE,
                QualityFlag.FREE_FLOAT_SOURCE_CONFLICT,
                *duplicate_flags,
            ),
            policy=policy,
            issued=shares.value,
            duplicate_count=duplicate_count,
            lineage=lineage,
        )
    free_float_shares = shares.value - deducted
    with localcontext() as context:
        context.prec = 60
        ratio = Decimal(free_float_shares) / Decimal(shares.value)
    return FreeFloatResult(
        stock_code=stock_code,
        effective_on=market_date,
        ratio=ratio,
        issued_common_shares=shares.value,
        deducted_non_float_shares=deducted,
        free_float_shares=free_float_shares,
        state=QualityState.VERIFIED,
        quality_flags=duplicate_flags,
        duplicate_deductions_prevented=duplicate_count,
        calculation_version=policy.version,
        lineage=lineage,
    )


def evaluate_reference_coverage(
    stock_codes: Iterable[str],
    results: Iterable[FreeFloatResult],
    *,
    policy: ReferencePolicy,
) -> ReferenceCoverage:
    ordered_codes = tuple(sorted(stock_codes))
    if len(ordered_codes) != len(set(ordered_codes)):
        raise ValueError("Coverage stock_code가 중복되었습니다.")
    result_values = tuple(results)
    result_by_stock = {result.stock_code: result for result in result_values}
    if len(result_by_stock) != len(result_values):
        raise ValueError("Coverage result stock_code가 중복되었습니다.")
    unknown = set(result_by_stock) - set(ordered_codes)
    if unknown:
        raise ValueError("Coverage 분모에 없는 stock_code 결과가 있습니다.")
    observed = tuple(
        code for code in ordered_codes if result_by_stock.get(code) is not None and result_by_stock[code].available
    )
    total = len(ordered_codes)
    ratio = None if total == 0 else Decimal(len(observed)) / Decimal(total)
    if ratio is not None and ratio >= policy.sufficient_coverage_ratio:
        status = CoverageStatus.SUFFICIENT
    elif observed:
        status = CoverageStatus.PARTIAL
    else:
        status = CoverageStatus.INSUFFICIENT
    missing = tuple(
        code
        for code in ordered_codes
        if code not in result_by_stock
        or result_by_stock[code].state
        in {QualityState.MISSING, QualityState.POINT_IN_TIME_UNAVAILABLE}
    )
    conflict = tuple(
        code
        for code in ordered_codes
        if code in result_by_stock and result_by_stock[code].state is QualityState.CONFLICT
    )
    stale = tuple(
        code
        for code in ordered_codes
        if code in result_by_stock and result_by_stock[code].state is QualityState.STALE
    )
    flags = {
        flag
        for result in result_by_stock.values()
        for flag in result.quality_flags
    }
    if len(observed) < total:
        flags.add(QualityFlag.FREE_FLOAT_UNAVAILABLE)
    return ReferenceCoverage(
        status=status,
        observed_count=len(observed),
        total_count=total,
        count_ratio=ratio,
        missing_stock_codes=missing,
        conflict_stock_codes=conflict,
        stale_stock_codes=stale,
        quality_flags=tuple(sorted(flags, key=lambda item: item.value)),
        policy_version=policy.version,
    )
