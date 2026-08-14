from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import StrEnum

from .models import decimal_to_number, require_finite


class CoverageStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class CoveragePart:
    observed_count: int
    total_count: int
    count_ratio: Decimal | None
    observed_weight_ratio: Decimal | None = None

    def __post_init__(self) -> None:
        if self.observed_count < 0 or self.total_count < 0:
            raise ValueError("Coverage count는 음수일 수 없습니다")
        if self.observed_count > self.total_count:
            raise ValueError("observed_count는 total_count를 초과할 수 없습니다")
        expected_ratio = self._ratio(self.observed_count, self.total_count)
        if self.count_ratio != expected_ratio:
            raise ValueError("count_ratio가 count와 일치하지 않습니다")
        if self.observed_weight_ratio is not None:
            require_finite(self.observed_weight_ratio, "observed_weight_ratio")
            if not Decimal(0) <= self.observed_weight_ratio <= Decimal(1):
                raise ValueError("observed_weight_ratio는 0과 1 사이여야 합니다")

    @classmethod
    def from_counts(
        cls,
        *,
        observed_count: int,
        total_count: int,
        observed_weight_ratio: Decimal | None = None,
    ) -> CoveragePart:
        return cls(
            observed_count=observed_count,
            total_count=total_count,
            count_ratio=cls._ratio(observed_count, total_count),
            observed_weight_ratio=observed_weight_ratio,
        )

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> Decimal | None:
        if denominator == 0:
            return None
        with localcontext() as context:
            context.prec = 50
            return Decimal(numerator) / Decimal(denominator)

    def to_public_dict(self, *, include_weight: bool) -> dict[str, int | float | None]:
        result: dict[str, int | float | None] = {
            "observedCount": self.observed_count,
            "totalCount": self.total_count,
            "countRatio": decimal_to_number(self.count_ratio),
        }
        if include_weight:
            result["observedWeightRatio"] = decimal_to_number(
                self.observed_weight_ratio
            )
        return result


@dataclass(frozen=True, slots=True)
class Coverage:
    status: CoverageStatus
    core: CoveragePart
    related: CoveragePart

    def to_public_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "core": self.core.to_public_dict(include_weight=True),
            "related": self.related.to_public_dict(include_weight=False),
        }
