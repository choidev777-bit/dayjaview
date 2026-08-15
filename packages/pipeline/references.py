"""해석된 KRX·OpenDART 기준정보를 파이프라인의 StockReference로 옮긴다.

`packages/reference-data`는 하이픈 디렉터리라 일반 import가 안 되므로
importlib로 불러온다. 값 판단(유동주식비율·전일조정종가·기업행위 상태)은 전부
그 패키지가 소유하고, 이 모듈은 도메인 타입으로 옮기기만 한다. 값이 없으면
None으로 남기고 지어내지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from importlib import import_module
from typing import Any

from packages.domain import StockReference

REFERENCE_POLICY_VERSION = "free-float-2026.08.1"
ADJUSTED_PRICE_VERSION = "adjusted-price-2026.08.1"

_PACKAGE = "packages." + "reference-data.reference_data"


def _module(name: str) -> Any:
    return import_module(f"{_PACKAGE}.{name}")


def production_reference_policy() -> Any:
    """무료 공식 원천이 실제로 완결 선언을 주는 비유동 범주만 요구한다.

    STRATEGIC_LOCKUP은 KRX·OpenDART 어느 endpoint도 완결 선언을 주지 않으므로
    요구 범주에 넣으면 전 종목이 영구히 MISSING이 된다. 보호예수 보유분 자체는
    최대주주 공시에 잡히면 그대로 차감된다.
    """

    models = _module("models")
    return models.ReferencePolicy(
        version=REFERENCE_POLICY_VERSION,
        free_float_stale_after=timedelta(days=180),
        sufficient_coverage_ratio=Decimal("0.8"),
        required_non_float_categories=(
            models.NonFloatCategory.TREASURY,
            models.NonFloatCategory.CONTROLLING_HOLDER,
        ),
    )


def resolve_stock_references(
    stock_ids: Iterable[str],
    *,
    market_date: date,
    decision_at: datetime,
    calendar: Any,
    daily_prices: Iterable[Any],
    share_observations: Iterable[Any],
    holdings: Iterable[Any],
    coverage_declarations: Iterable[Any],
    corporate_actions: Iterable[Any] = (),
    policy: Any | None = None,
) -> tuple[StockReference, ...]:
    """종목마다 전일 조정종가와 유동주식비율을 해석해 기준정보 1건으로 묶는다.

    유동주식비율이 VERIFIED가 아니면 `free_float_validated=False`가 되어 계산이
    그 종목을 유동시총 가중에서 제외하고 Coverage에 그대로 반영한다.
    """

    models = _module("models")
    effective_policy = policy or production_reference_policy()
    price_values = tuple(daily_prices)
    share_values = tuple(share_observations)
    holding_values = tuple(holdings)
    declaration_values = tuple(coverage_declarations)
    action_values = tuple(corporate_actions)
    version = f"reference-{market_date.isoformat()}-{effective_policy.version}"

    references: list[StockReference] = []
    for stock_id in sorted(set(stock_ids)):
        stock_code = stock_id.removeprefix("KRX:")
        price = _module("adjusted_price").resolve_previous_adjusted_close(
            stock_code=stock_code,
            market_date=market_date,
            decision_at=decision_at,
            calendar=calendar,
            daily_prices=price_values,
            corporate_actions=action_values,
            version=ADJUSTED_PRICE_VERSION,
        )
        free_float = _module("free_float").calculate_free_float(
            stock_code=stock_code,
            market_date=market_date,
            decision_at=decision_at,
            share_observations=share_values,
            holdings=holding_values,
            coverage_declarations=declaration_values,
            policy=effective_policy,
        )
        references.append(
            StockReference(
                stock_id=stock_id,
                effective_for=market_date,
                known_at=decision_at,
                previous_adjusted_close=price.previous_adjusted_close,
                listed_shares=free_float.issued_common_shares,
                free_float_ratio=free_float.ratio,
                free_float_validated=free_float.available,
                version=version,
                corporate_action_resolved=(
                    price.state is not models.QualityState.CORPORATE_ACTION_UNRESOLVED
                ),
            )
        )
    return tuple(references)
