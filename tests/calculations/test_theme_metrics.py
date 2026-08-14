from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from packages.calculations import (
    THEME_CALCULATION_POLICY_V1,
    PolicyMaturity,
    ThemeCalculationPolicy,
    calculate_theme_metrics,
    determine_coverage_status,
)
from packages.domain import (
    CoveragePart,
    CoverageStatus,
    MembershipRole,
    StockMarketObservation,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from scripts.validate_contracts import SHARED_SCHEMA_PATH, validate_instance

from ._factories import (
    AS_OF,
    MARKET_DATE,
    make_market_observation,
    make_membership,
    make_reference,
)

FIXTURES = Path(__file__).parent / "fixtures"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_FIXTURES = REPOSITORY_ROOT / "contracts" / "fixtures"


def test_versioned_theme_metrics_fixture() -> None:
    fixture = json.loads(
        (FIXTURES / "theme_metrics_v1.json").read_text(encoding="utf-8")
    )
    policy_data = fixture["policy"]
    policy = ThemeCalculationPolicy(
        version=policy_data["version"],
        maturity=PolicyMaturity(policy_data["maturity"]),
        maximum_constituent_weight=Decimal(
            policy_data["maximumConstituentWeight"]
        ),
        sufficient_core_weight_ratio=Decimal(
            policy_data["sufficientCoreWeightRatio"]
        ),
        sufficient_related_count_ratio=Decimal(
            policy_data["sufficientRelatedCountRatio"]
        ),
        minimum_core_observations=policy_data["minimumCoreObservations"],
        minimum_total_observations=policy_data["minimumTotalObservations"],
    )
    assert policy == THEME_CALCULATION_POLICY_V1

    fixture_input = fixture["input"]
    market_date = date.fromisoformat(fixture_input["marketDate"])
    as_of = datetime.fromisoformat(fixture_input["asOf"])
    membership_data = fixture_input["membership"]
    membership = ThemeMembershipSnapshot(
        theme_id=fixture_input["themeId"],
        version=membership_data["version"],
        effective_from=date.fromisoformat(membership_data["effectiveFrom"]),
        known_at=datetime.fromisoformat(membership_data["knownAt"]),
        members=tuple(
            ThemeMember(
                stock_id=item["stockId"],
                role=MembershipRole(item["role"]),
            )
            for item in membership_data["members"]
        ),
    )
    references = []
    observations = []
    for item in fixture_input["stocks"]:
        free_float_ratio = item["freeFloatRatio"]
        references.append(
            StockReference(
                stock_id=item["stockId"],
                effective_for=market_date,
                known_at=as_of,
                previous_adjusted_close=Decimal(item["previousAdjustedClose"]),
                listed_shares=item["listedShares"],
                free_float_ratio=(
                    None if free_float_ratio is None else Decimal(free_float_ratio)
                ),
                free_float_validated=free_float_ratio is not None,
                version="fixture-reference-v1",
            )
        )
        observations.append(
            StockMarketObservation(
                stock_id=item["stockId"],
                market_date=market_date,
                observed_at=as_of,
                current_price=Decimal(item["currentPrice"]),
                cumulative_trading_value=Decimal(1000),
            )
        )

    result = calculate_theme_metrics(
        market_date=market_date,
        as_of=as_of,
        membership=membership,
        references=references,
        observations=observations,
        policy=policy,
    )
    expected = fixture["expected"]

    assert fixture["fixtureVersion"] == "theme-metrics-fixture-2026.08.1"
    assert result.weighted_return == Decimal(expected["weightedReturn"])
    assert result.median_return == Decimal(expected["medianReturn"])
    assert result.advancing_count == expected["advancingCount"]
    assert result.valid_count == expected["validCount"]
    assert result.advancing_ratio == Decimal(expected["advancingRatio"])
    assert result.coverage.status.value == expected["coverageStatus"]
    assert result.coverage.core.observed_weight_ratio == Decimal(
        expected["coreObservedWeightRatio"]
    )
    assert {item.stock_id: item.weight for item in result.capped_weights} == {
        stock_id: Decimal(weight) for stock_id, weight in expected["weights"].items()
    }
    assert list(result.quality_flags) == expected["qualityFlags"]
    assert result.policy_maturity == "BACKTEST_PENDING"


def test_related_members_are_included_in_median_and_breadth() -> None:
    membership = make_membership(core=("A", "B"), related=("C",))
    references = [make_reference(stock_id) for stock_id in ("A", "B", "C")]
    observations = [
        make_market_observation("A", current_price=Decimal(110)),
        make_market_observation("B", current_price=Decimal(100)),
        make_market_observation("C", current_price=Decimal(98)),
    ]

    result = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=membership,
        references=references,
        observations=observations,
    )

    assert result.median_return == Decimal(0)
    assert result.advancing_count == 1
    assert result.valid_count == 3
    with localcontext() as context:
        context.prec = 60
        expected_ratio = Decimal(1) / Decimal(3)
    assert result.advancing_ratio == expected_ratio


def test_missing_free_float_never_falls_back_to_total_market_cap() -> None:
    membership = make_membership(core=("A", "B"), related=("C",))
    references = [
        make_reference("A"),
        make_reference("B", free_float_ratio=None, free_float_validated=False),
        make_reference("C"),
    ]
    observations = [
        make_market_observation("A", current_price=Decimal(105)),
        make_market_observation("B", current_price=Decimal(106)),
        make_market_observation("C", current_price=Decimal(107)),
    ]

    result = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=membership,
        references=references,
        observations=observations,
    )

    assert result.weighted_return is None
    assert result.capped_weights == ()
    assert result.coverage.core.observed_weight_ratio is None
    assert result.coverage.status is CoverageStatus.INSUFFICIENT
    assert "FREE_FLOAT_UNAVAILABLE" in result.quality_flags
    assert result.median_return == Decimal("0.06")
    assert result.to_public_ranking_fields()["weightedReturn"] is None


def test_observed_zero_is_not_conflated_with_missing() -> None:
    membership = make_membership(core=("A", "B"), related=("C",))
    references = [make_reference(stock_id) for stock_id in ("A", "B", "C")]
    zero_observations = [make_market_observation(stock_id) for stock_id in ("A", "B", "C")]

    observed_zero = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=membership,
        references=references,
        observations=zero_observations,
    )
    missing = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=membership,
        references=references,
        observations=(),
    )

    assert observed_zero.weighted_return == Decimal(0)
    assert observed_zero.median_return == Decimal(0)
    assert observed_zero.advancing_count == 0
    assert observed_zero.valid_count == 3
    assert missing.weighted_return is None
    assert missing.median_return is None
    assert missing.advancing_count is None
    assert missing.valid_count is None


def test_halt_corporate_action_and_stale_data_are_excluded_not_zeroed() -> None:
    membership = make_membership(core=("A", "B"), related=("C", "D"))
    references = [make_reference(stock_id) for stock_id in ("A", "B", "C", "D")]
    observations = [
        make_market_observation("A", trading_halted=True),
        make_market_observation("B", current_price=Decimal(110)),
        make_market_observation("C", corporate_action_unresolved=True),
        make_market_observation("D", fresh=False),
    ]

    result = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=membership,
        references=references,
        observations=observations,
    )

    assert result.weighted_return is None
    assert result.median_return == Decimal("0.1")
    assert result.advancing_count == 1
    assert result.valid_count == 1
    assert {
        "TRADING_HALTED",
        "CORPORATE_ACTION_UNRESOLVED",
        "STALE_MARKET_DATA",
        "INSUFFICIENT_COVERAGE",
    }.issubset(result.quality_flags)


def test_coverage_threshold_boundaries_are_inclusive_and_versioned() -> None:
    policy = THEME_CALCULATION_POLICY_V1
    related = CoveragePart.from_counts(observed_count=7, total_count=10)
    at_boundary = CoveragePart.from_counts(
        observed_count=2,
        total_count=3,
        observed_weight_ratio=Decimal("0.80"),
    )
    below_boundary = CoveragePart.from_counts(
        observed_count=2,
        total_count=3,
        observed_weight_ratio=Decimal("0.799999"),
    )

    assert determine_coverage_status(
        core=at_boundary,
        related=related,
        total_valid_count=7,
        policy=policy,
    ) is CoverageStatus.SUFFICIENT
    assert determine_coverage_status(
        core=below_boundary,
        related=related,
        total_valid_count=7,
        policy=policy,
    ) is CoverageStatus.PARTIAL
    assert policy.maturity is PolicyMaturity.BACKTEST_PENDING


def test_same_input_and_version_are_deterministic_while_version_is_visible() -> None:
    membership = make_membership(core=("A", "B"), related=("C",))
    references = [make_reference(stock_id) for stock_id in ("A", "B", "C")]
    observations = [
        make_market_observation("A", current_price=Decimal(102)),
        make_market_observation("B", current_price=Decimal(104)),
        make_market_observation("C", current_price=Decimal(99)),
    ]
    first = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=membership,
        references=references,
        observations=observations,
    )
    reordered = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=membership,
        references=reversed(references),
        observations=reversed(observations),
    )
    next_version = replace(
        THEME_CALCULATION_POLICY_V1,
        version="theme-metrics-test-next",
    )
    version_changed = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=membership,
        references=references,
        observations=observations,
        policy=next_version,
    )

    assert first == reordered
    assert version_changed.weighted_return == first.weighted_return
    assert version_changed.calculation_version != first.calculation_version
    assert version_changed != first


def test_public_outputs_use_decimal_return_units_and_contract_nulls() -> None:
    result = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=make_membership(core=("A", "B"), related=("C",)),
        references=[make_reference(stock_id) for stock_id in ("A", "B", "C")],
        observations=[
            make_market_observation("A", current_price=Decimal("102.7")),
            make_market_observation("B", current_price=Decimal("102.7")),
            make_market_observation("C", current_price=Decimal(100)),
        ],
    )

    ranking = result.to_public_ranking_fields()
    reaction = result.to_public_current_reaction(
        turnover_multiple=None,
        attention_gap_trading_days=None,
    )
    assert ranking["weightedReturn"] == pytest.approx(0.027)
    assert ranking["weightMethod"] == "FREE_FLOAT_CAPPED"
    assert ranking["coverage"] == result.coverage.to_public_dict()
    assert reaction["turnoverMultiple"] is None
    assert reaction["attentionGapTradingDays"] is None


def test_public_metric_projections_validate_against_machine_contract() -> None:
    result = calculate_theme_metrics(
        market_date=MARKET_DATE,
        as_of=AS_OF,
        membership=make_membership(core=("A", "B"), related=("C",)),
        references=[make_reference(stock_id) for stock_id in ("A", "B", "C")],
        observations=[
            make_market_observation("A", current_price=Decimal("102.7")),
            make_market_observation("B", current_price=Decimal("102.7")),
            make_market_observation("C", current_price=Decimal(100)),
        ],
    )
    shared = json.loads(SHARED_SCHEMA_PATH.read_text(encoding="utf-8"))

    ranking = json.loads(
        (CONTRACT_FIXTURES / "rankings" / "live.json").read_text(encoding="utf-8")
    )
    ranking["data"]["items"][0].update(result.to_public_ranking_fields())
    validate_instance(
        ranking,
        "RankingResponse",
        shared=shared,
        label="calculated-ranking",
    )

    detail = json.loads(
        (CONTRACT_FIXTURES / "event" / "multi-source.json").read_text(
            encoding="utf-8"
        )
    )
    detail["data"]["currentReaction"] = result.to_public_current_reaction(
        turnover_multiple=Decimal("2.4"),
        attention_gap_trading_days=163,
    )
    validate_instance(
        detail,
        "ThemeDetailResponse",
        shared=shared,
        label="calculated-theme-detail",
    )
