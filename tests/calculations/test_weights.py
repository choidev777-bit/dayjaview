from __future__ import annotations

import random
from decimal import Decimal, localcontext

import pytest

from packages.calculations import (
    CapitalizationInput,
    calculate_capped_weights,
    calculate_weighted_return,
)


def as_map(inputs: list[CapitalizationInput]) -> dict[str, Decimal]:
    return {
        item.stock_id: item.weight
        for item in calculate_capped_weights(
            inputs,
            configured_cap=Decimal("0.30"),
        )
    }


def test_iterative_cap_redistribution_repeats_until_no_excess_remains() -> None:
    weights = as_map(
        [
            CapitalizationInput("A", Decimal(70)),
            CapitalizationInput("B", Decimal(20)),
            CapitalizationInput("C", Decimal(5)),
            CapitalizationInput("D", Decimal(5)),
        ]
    )

    assert weights == {
        "A": Decimal("0.30"),
        "B": Decimal("0.30"),
        "C": Decimal("0.20"),
        "D": Decimal("0.20"),
    }


@pytest.mark.parametrize(
    ("count", "expected_cap"),
    [
        (1, Decimal(1)),
        (2, Decimal("0.5")),
        (3, Decimal(1) / Decimal(3)),
        (4, Decimal("0.30")),
    ],
)
def test_effective_cap_boundary_supports_small_themes(
    count: int,
    expected_cap: Decimal,
) -> None:
    inputs = [
        CapitalizationInput(str(index), Decimal(10 ** (count - index)))
        for index in range(count)
    ]
    weights = calculate_capped_weights(
        inputs,
        configured_cap=Decimal("0.30"),
    )

    with localcontext() as context:
        context.prec = 120
        total_weight = sum(
            (item.weight for item in weights),
            start=Decimal(0),
        )
    assert total_weight == Decimal(1)
    assert max(item.weight for item in weights) == pytest.approx(expected_cap)


def test_cap_properties_hold_for_deterministic_generated_inputs() -> None:
    generator = random.Random(20260814)
    for count in range(1, 31):
        for sample in range(8):
            inputs = [
                CapitalizationInput(
                    stock_id=f"S{index:02d}",
                    free_float_market_cap=Decimal(generator.randint(1, 10_000)),
                )
                for index in range(count)
            ]
            weights = calculate_capped_weights(
                inputs,
                configured_cap=Decimal("0.30"),
            )
            with localcontext() as context:
                context.prec = 60
                inverse_count = Decimal(1) / Decimal(count)
                if inverse_count * Decimal(count) < Decimal(1):
                    inverse_count = inverse_count.next_plus()
                effective_cap = max(Decimal("0.30"), inverse_count)
            with localcontext() as context:
                context.prec = 120
                total_weight = sum(
                    (item.weight for item in weights),
                    start=Decimal(0),
                )
            assert total_weight == Decimal(1), (count, sample)
            assert all(Decimal(0) <= item.weight <= effective_cap for item in weights)
            assert [item.stock_id for item in weights] == sorted(
                item.stock_id for item in weights
            )


def test_weight_result_is_independent_of_input_order() -> None:
    inputs = [
        CapitalizationInput("C", Decimal(5)),
        CapitalizationInput("A", Decimal(70)),
        CapitalizationInput("D", Decimal(5)),
        CapitalizationInput("B", Decimal(20)),
    ]

    assert calculate_capped_weights(
        inputs,
        configured_cap=Decimal("0.30"),
    ) == calculate_capped_weights(
        reversed(inputs),
        configured_cap=Decimal("0.30"),
    )


def test_weighted_return_is_monotonic_in_each_constituent_return() -> None:
    weights = calculate_capped_weights(
        [
            CapitalizationInput("A", Decimal(70)),
            CapitalizationInput("B", Decimal(20)),
            CapitalizationInput("C", Decimal(5)),
            CapitalizationInput("D", Decimal(5)),
        ],
        configured_cap=Decimal("0.30"),
    )
    base_returns = {
        "A": Decimal("0.01"),
        "B": Decimal("-0.02"),
        "C": Decimal(0),
        "D": Decimal("0.03"),
    }
    base = calculate_weighted_return(weights, base_returns)

    for stock_id in base_returns:
        increased = dict(base_returns)
        increased[stock_id] += Decimal("0.01")
        assert calculate_weighted_return(weights, increased) >= base
