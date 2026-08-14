from __future__ import annotations

from decimal import Decimal, localcontext

import pytest

from packages.domain import Coverage, CoveragePart, CoverageStatus


def test_zero_denominator_is_null_but_observed_zero_is_numeric_zero() -> None:
    absent = CoveragePart.from_counts(observed_count=0, total_count=0)
    observed_zero = CoveragePart.from_counts(observed_count=0, total_count=3)

    assert absent.count_ratio is None
    assert observed_zero.count_ratio == Decimal(0)


def test_coverage_public_shape_matches_machine_contract() -> None:
    coverage = Coverage(
        status=CoverageStatus.SUFFICIENT,
        core=CoveragePart.from_counts(
            observed_count=4,
            total_count=5,
            observed_weight_ratio=Decimal("0.8"),
        ),
        related=CoveragePart.from_counts(observed_count=7, total_count=10),
    )

    assert coverage.to_public_dict() == {
        "status": "SUFFICIENT",
        "core": {
            "observedCount": 4,
            "totalCount": 5,
            "countRatio": 0.8,
            "observedWeightRatio": 0.8,
        },
        "related": {
            "observedCount": 7,
            "totalCount": 10,
            "countRatio": 0.7,
        },
    }


@pytest.mark.parametrize(
    ("observed_count", "total_count"),
    [(-1, 1), (0, -1), (2, 1)],
)
def test_invalid_count_relationship_is_rejected(
    observed_count: int,
    total_count: int,
) -> None:
    with pytest.raises(ValueError):
        CoveragePart.from_counts(
            observed_count=observed_count,
            total_count=total_count,
        )


def test_count_ratio_property_for_all_small_count_pairs() -> None:
    for total_count in range(31):
        for observed_count in range(total_count + 1):
            part = CoveragePart.from_counts(
                observed_count=observed_count,
                total_count=total_count,
            )
            assert part.observed_count <= part.total_count
            if total_count == 0:
                assert part.count_ratio is None
            else:
                with localcontext() as context:
                    context.prec = 50
                    expected = Decimal(observed_count) / Decimal(total_count)
                assert part.count_ratio == expected
                assert Decimal(0) <= part.count_ratio <= Decimal(1)
