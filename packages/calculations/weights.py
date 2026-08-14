from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext

from packages.domain.models import require_finite


@dataclass(frozen=True, slots=True)
class CapitalizationInput:
    stock_id: str
    free_float_market_cap: Decimal

    def __post_init__(self) -> None:
        if not self.stock_id:
            raise ValueError("stock_id는 비어 있을 수 없습니다")
        require_finite(self.free_float_market_cap, "free_float_market_cap")
        if self.free_float_market_cap <= 0:
            raise ValueError("free_float_market_cap은 0보다 커야 합니다")


@dataclass(frozen=True, slots=True)
class CappedWeight:
    stock_id: str
    weight: Decimal


def _apply_residual(
    weights: dict[str, Decimal],
    *,
    effective_cap: Decimal,
) -> None:
    # Sum at higher precision than the weight calculation so the stored
    # Decimal values, rather than an intermediate rounded sum, total exactly 1.
    with localcontext() as context:
        context.prec = 120
        residual = Decimal(1) - sum(weights.values(), start=Decimal(0))
        if residual == 0:
            return
        ordered_ids = sorted(weights)
        if residual > 0:
            candidates = [
                stock_id
                for stock_id in ordered_ids
                if weights[stock_id] + residual <= effective_cap
            ]
        else:
            candidates = [
                stock_id
                for stock_id in ordered_ids
                if weights[stock_id] + residual >= 0
            ]
        if not candidates:
            raise ArithmeticError("가중치 반올림 잔여분을 안전하게 배분할 수 없습니다")
        selected = candidates[0]
        weights[selected] += residual


def calculate_capped_weights(
    inputs: Iterable[CapitalizationInput],
    *,
    configured_cap: Decimal,
) -> tuple[CappedWeight, ...]:
    """Iteratively cap and redistribute weights in stable stock-id order."""

    require_finite(configured_cap, "configured_cap")
    if not Decimal(0) < configured_cap <= Decimal(1):
        raise ValueError("configured_cap은 0보다 크고 1 이하여야 합니다")

    ordered = sorted(inputs, key=lambda item: item.stock_id)
    if not ordered:
        return ()
    stock_ids = [item.stock_id for item in ordered]
    if len(stock_ids) != len(set(stock_ids)):
        raise ValueError("weight input에 중복 stock_id가 있습니다")

    with localcontext() as context:
        context.prec = 60
        inverse_count = Decimal(1) / Decimal(len(ordered))
        if inverse_count * Decimal(len(ordered)) < Decimal(1):
            # A finite Decimal cannot represent values such as 1/3 exactly. Use
            # the smallest representable upper bound so sum=1 remains feasible.
            inverse_count = inverse_count.next_plus()
        effective_cap = max(configured_cap, inverse_count)
        capitalizations = {
            item.stock_id: item.free_float_market_cap for item in ordered
        }
        remaining_ids = stock_ids.copy()
        fixed: dict[str, Decimal] = {}

        while remaining_ids:
            remaining_mass = Decimal(1) - sum(
                fixed.values(),
                start=Decimal(0),
            )
            remaining_capitalization = sum(
                (capitalizations[stock_id] for stock_id in remaining_ids),
                start=Decimal(0),
            )
            proposed = {
                stock_id: remaining_mass
                * capitalizations[stock_id]
                / remaining_capitalization
                for stock_id in remaining_ids
            }
            newly_capped = [
                stock_id
                for stock_id in remaining_ids
                if proposed[stock_id] > effective_cap
            ]
            if not newly_capped:
                fixed.update(proposed)
                break
            for stock_id in newly_capped:
                fixed[stock_id] = effective_cap
            capped_set = set(newly_capped)
            remaining_ids = [
                stock_id for stock_id in remaining_ids if stock_id not in capped_set
            ]

        _apply_residual(fixed, effective_cap=effective_cap)
        return tuple(
            CappedWeight(stock_id=stock_id, weight=fixed[stock_id])
            for stock_id in stock_ids
        )


def calculate_weighted_return(
    weights: Iterable[CappedWeight],
    returns: Mapping[str, Decimal],
) -> Decimal:
    ordered = tuple(weights)
    weight_ids = {item.stock_id for item in ordered}
    if weight_ids != set(returns):
        raise ValueError("weight와 return의 stock_id 집합이 일치해야 합니다")
    with localcontext() as context:
        context.prec = 60
        return sum(
            (item.weight * returns[item.stock_id] for item in ordered),
            start=Decimal(0),
        )
