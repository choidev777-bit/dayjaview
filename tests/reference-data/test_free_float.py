from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

from conftest import aware


def _inputs(modules: dict[str, Any], load_fixture):
    parsers = modules["parsers"]
    krx = parsers.parse_krx_stock_daily(load_fixture("krx-stock-daily.json"))[0]
    stock_total = parsers.parse_open_dart(
        load_fixture("opendart-stock-total.json"), stock_code="A00001"
    )
    largest = parsers.parse_open_dart(
        load_fixture("opendart-largest-shareholder.json"), stock_code="A00001"
    )
    treasury = parsers.parse_open_dart(
        load_fixture("opendart-treasury.json"), stock_code="A00001"
    )
    models = modules["models"]
    strategic_coverage = models.HoldingCoverageDeclaration(
        stock_code="A00001",
        category=models.NonFloatCategory.STRATEGIC_LOCKUP,
        status=models.CoverageDeclarationStatus.COMPLETE,
        effective_on=largest.non_float_holdings[-1].effective_on,
        metadata=largest.non_float_holdings[-1].metadata,
    )
    return {
        "shares": (
            krx.listed_share_observation(),
            *stock_total.issued_share_observations,
        ),
        "holdings": (
            *stock_total.non_float_holdings,
            *largest.non_float_holdings,
            *treasury.non_float_holdings,
        ),
        "coverage": (
            *stock_total.coverage_declarations,
            *largest.coverage_declarations,
            *treasury.coverage_declarations,
            strategic_coverage,
        ),
    }


def _policy(modules: dict[str, Any], *, days: int = 180, threshold: str = "0.8"):
    models = modules["models"]
    return models.ReferencePolicy(
        version="free-float-2026.08.1",
        free_float_stale_after=timedelta(days=days),
        sufficient_coverage_ratio=Decimal(threshold),
        required_non_float_categories=(
            models.NonFloatCategory.TREASURY,
            models.NonFloatCategory.CONTROLLING_HOLDER,
            models.NonFloatCategory.STRATEGIC_LOCKUP,
        ),
    )


def _calculate(modules: dict[str, Any], values: dict[str, tuple[Any, ...]], **overrides: Any):
    arguments = {
        "stock_code": "A00001",
        "market_date": aware("2026-08-14T10:00:00+09:00").date(),
        "decision_at": aware("2026-08-14T10:00:00+09:00"),
        "share_observations": values["shares"],
        "holdings": values["holdings"],
        "coverage_declarations": values["coverage"],
        "policy": _policy(modules),
    }
    arguments.update(overrides)
    return modules["free_float"].calculate_free_float(**arguments)


def test_verified_ratio_deduplicates_same_treasury_stake_once(
    modules: dict[str, Any], load_fixture
) -> None:
    result = _calculate(modules, _inputs(modules, load_fixture))

    assert result.available is True
    assert result.issued_common_shares == 100_000_000
    assert result.deducted_non_float_shares == 45_000_000
    assert result.free_float_shares == 55_000_000
    assert result.ratio == Decimal("0.55")
    assert result.duplicate_deductions_prevented == 1
    assert [flag.value for flag in result.quality_flags] == [
        "DUPLICATE_DEDUCTION_PREVENTED"
    ]


def test_source_conflict_is_unavailable_not_priority_fallback(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    dart = replace(values["shares"][1], value=99_000_000)
    result = _calculate(
        modules,
        values,
        share_observations=(values["shares"][0], dart),
    )

    assert result.available is False
    assert result.ratio is None
    assert result.state.value == "CONFLICT"
    assert "FREE_FLOAT_SOURCE_CONFLICT" in {flag.value for flag in result.quality_flags}


def test_same_source_same_revision_conflict_is_not_order_dependent(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    dart = values["shares"][1]
    conflicting_duplicate = replace(dart, value=99_000_000)
    result = _calculate(
        modules,
        values,
        share_observations=(*values["shares"], conflicting_duplicate),
    )

    assert result.ratio is None
    assert result.state.value == "CONFLICT"


def test_missing_required_source_does_not_use_remaining_value(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    result = _calculate(
        modules,
        values,
        share_observations=(values["shares"][1],),
    )

    assert result.ratio is None
    assert result.issued_common_shares is None
    assert result.state.value == "MISSING"
    assert "SOURCE_MISSING" in {flag.value for flag in result.quality_flags}


def test_missing_holding_scope_keeps_ratio_unavailable(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    models = modules["models"]
    coverage = tuple(
        item
        for item in values["coverage"]
        if item.category is not models.NonFloatCategory.STRATEGIC_LOCKUP
    )
    result = _calculate(modules, values, coverage_declarations=coverage)

    assert result.ratio is None
    assert result.state.value == "MISSING"


def test_conflicting_coverage_declarations_are_explicit_conflict(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    models = modules["models"]
    strategic = next(
        item
        for item in values["coverage"]
        if item.category is models.NonFloatCategory.STRATEGIC_LOCKUP
    )
    conflicting = replace(
        strategic,
        status=models.CoverageDeclarationStatus.INCOMPLETE,
        metadata=replace(
            strategic.metadata,
            dataset=models.SourceDataset.OPENDART_DISCLOSURE_CLASSIFICATION,
            source_key="strategic-coverage-conflict",
            lineage=("fixture:strategic-coverage-conflict",),
        ),
    )
    result = _calculate(
        modules,
        values,
        coverage_declarations=(*values["coverage"], conflicting),
    )

    assert result.ratio is None
    assert result.state.value == "CONFLICT"


def test_stale_source_is_explicit_and_not_reused(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    result = _calculate(
        modules,
        values,
        market_date=aware("2027-02-01T10:00:00+09:00").date(),
        decision_at=aware("2027-02-01T10:00:00+09:00"),
        policy=_policy(modules, days=30),
    )

    assert result.ratio is None
    assert result.state.value == "STALE"
    assert "FREE_FLOAT_STALE" in {flag.value for flag in result.quality_flags}


def test_conflicting_duplicate_holding_is_not_subtracted(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    changed_treasury = replace(values["holdings"][-1], shares=11_000_000)
    result = _calculate(
        modules,
        values,
        holdings=(*values["holdings"][:-1], changed_treasury),
    )

    assert result.ratio is None
    assert result.deducted_non_float_shares is None
    assert result.state.value == "CONFLICT"


def test_deductions_above_issued_shares_are_conflict(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    controlling = replace(values["holdings"][1], shares=95_000_000)
    result = _calculate(
        modules,
        values,
        holdings=(values["holdings"][0], controlling, *values["holdings"][2:]),
    )

    assert result.ratio is None
    assert result.state.value == "CONFLICT"


def test_later_revision_is_not_applied_to_earlier_decision(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    old_dart = values["shares"][1]
    later_metadata = replace(
        old_dart.metadata,
        collected_at=aware("2026-08-14T12:00:00+09:00"),
        revision=2,
        lineage=("fixture:later-correction",),
    )
    correction = replace(old_dart, value=99_000_000, metadata=later_metadata)
    shares = (*values["shares"], correction)

    before = _calculate(modules, values, share_observations=shares)
    after = _calculate(
        modules,
        values,
        share_observations=shares,
        decision_at=aware("2026-08-14T13:00:00+09:00"),
    )

    assert before.ratio == Decimal("0.55")
    assert after.ratio is None
    assert after.state.value == "CONFLICT"


def test_coverage_has_separate_missing_conflict_and_stale_sets(
    modules: dict[str, Any], load_fixture
) -> None:
    values = _inputs(modules, load_fixture)
    verified = _calculate(modules, values)
    second = replace(verified, stock_code="A00002")
    missing = replace(
        verified,
        stock_code="A00003",
        ratio=None,
        issued_common_shares=None,
        deducted_non_float_shares=None,
        free_float_shares=None,
        state=modules["models"].QualityState.MISSING,
        quality_flags=(modules["models"].QualityFlag.FREE_FLOAT_UNAVAILABLE,),
    )
    coverage = modules["free_float"].evaluate_reference_coverage(
        ("A00001", "A00002", "A00003"),
        (verified, second, missing),
        policy=_policy(modules, threshold="0.6"),
    )

    assert coverage.status.value == "SUFFICIENT"
    assert coverage.observed_count == 2
    assert coverage.total_count == 3
    assert coverage.missing_stock_codes == ("A00003",)
    assert coverage.conflict_stock_codes == ()
    assert coverage.stale_stock_codes == ()


def test_empty_coverage_ratio_is_null_not_zero(modules: dict[str, Any]) -> None:
    coverage = modules["free_float"].evaluate_reference_coverage(
        (),
        (),
        policy=_policy(modules),
    )

    assert coverage.status.value == "INSUFFICIENT"
    assert coverage.count_ratio is None
