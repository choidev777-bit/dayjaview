"""해석된 KRX·OpenDART 기준정보를 파이프라인의 StockReference로 옮긴다.

`packages/reference-data`는 하이픈 디렉터리라 일반 import가 안 되므로
importlib로 불러온다. 값 판단(유동주식비율·전일조정종가·기업행위 상태)은 전부
그 패키지가 소유하고, 이 모듈은 도메인 타입으로 옮기기만 한다. 값이 없으면
None으로 남기고 지어내지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any

from packages.domain import StockReference

REFERENCE_POLICY_VERSION = "free-float-2026.08.1"
ADJUSTED_PRICE_VERSION = "adjusted-price-2026.08.1"

_PACKAGE = "packages." + "reference-data.reference_data"


def _module(name: str) -> Any:
    return import_module(f"{_PACKAGE}.{name}")


def _by_stock(values: Iterable[Any]) -> dict[str, tuple[Any, ...]]:
    grouped: dict[str, list[Any]] = {}
    for value in values:
        grouped.setdefault(value.stock_code, []).append(value)
    return {code: tuple(items) for code, items in grouped.items()}


def _krx_listed_shares(
    observations: Iterable[Any],
    *,
    market_date: date,
    decision_at: datetime,
) -> int | None:
    """유동시가총액에 곱할 상장주식수는 그 시점에 알려진 가장 최근 KRX 값을 쓴다.

    OpenDART 발행주식수는 결산기준일 기준이라 그 뒤의 증자·액면분할을 반영하지
    못한다. 비율은 결산 시점 기준이라도 유효하지만 주식수는 최신이어야 시가총액이
    맞는다. 장중에는 당일 일별매매 row가 아직 없으므로 직전 거래일 값이 최신이다.
    """

    eligible = [
        observation
        for observation in observations
        if observation.market_date <= market_date
        and observation.metadata.known_at <= decision_at
    ]
    if not eligible:
        return None
    latest_key = max(
        (observation.market_date, observation.metadata.known_at, observation.metadata.revision)
        for observation in eligible
    )
    latest = [
        observation
        for observation in eligible
        if (observation.market_date, observation.metadata.known_at, observation.metadata.revision)
        == latest_key
    ]
    values = {observation.listed_shares for observation in latest}
    return values.pop() if len(values) == 1 else None


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
    # 종목 수가 2천 단위라 매 종목마다 전체 관측을 훑지 않도록 먼저 나눠 둔다.
    # 해석 함수들이 어차피 같은 조건으로 다시 거르므로 결과는 같다.
    price_by_stock = _by_stock(daily_prices)
    share_by_stock = _by_stock(share_observations)
    holding_by_stock = _by_stock(holdings)
    declaration_by_stock = _by_stock(coverage_declarations)
    action_by_stock = _by_stock(corporate_actions)
    version = f"reference-{market_date.isoformat()}-{effective_policy.version}"

    references: list[StockReference] = []
    for stock_id in sorted(set(stock_ids)):
        stock_code = stock_id.removeprefix("KRX:")
        price = _module("adjusted_price").resolve_previous_adjusted_close(
            stock_code=stock_code,
            market_date=market_date,
            decision_at=decision_at,
            calendar=calendar,
            daily_prices=price_by_stock.get(stock_code, ()),
            corporate_actions=action_by_stock.get(stock_code, ()),
            version=ADJUSTED_PRICE_VERSION,
        )
        free_float = _module("free_float").calculate_free_float(
            stock_code=stock_code,
            market_date=market_date,
            decision_at=decision_at,
            share_observations=share_by_stock.get(stock_code, ()),
            holdings=holding_by_stock.get(stock_code, ()),
            coverage_declarations=declaration_by_stock.get(stock_code, ()),
            policy=effective_policy,
        )
        references.append(
            StockReference(
                stock_id=stock_id,
                effective_for=market_date,
                known_at=decision_at,
                previous_adjusted_close=price.previous_adjusted_close,
                listed_shares=_krx_listed_shares(
                    price_by_stock.get(stock_code, ()),
                    market_date=market_date,
                    decision_at=decision_at,
                ),
                free_float_ratio=free_float.ratio,
                free_float_validated=free_float.available,
                version=version,
                corporate_action_resolved=(
                    price.state is not models.QualityState.CORPORATE_ACTION_UNRESOLVED
                ),
            )
        )
    return tuple(references)


def load_collected_references(
    directory: Path,
    *,
    market_date: date,
    decision_at: datetime,
    stock_ids: Iterable[str],
    policy: Any | None = None,
) -> tuple[StockReference, ...]:
    """수집 워커가 적재한 원문 봉투를 읽어 그대로 기준정보로 해석한다.

    기업행위 원천이 아직 없으므로, 전일 종가는 해당 거래일의 KRX 일별매매 row가
    이미 확보된 경우(장 마감 이후·재생)에만 나온다. 장중 시점은 그 거래일의
    기업행위 상태를 확인할 방법이 없어 값 없이 남는다.
    """

    models = _module("models")
    parsers = _module("parsers")
    by_dataset: dict[Any, list[Any]] = {}
    for path in sorted(directory.glob("*.json")):
        snapshot = parsers.load_collected_snapshot(
            json.loads(path.read_text(encoding="utf-8"))
        )
        by_dataset.setdefault(snapshot.metadata.dataset, []).append(snapshot)

    krx_snapshots = by_dataset.get(models.SourceDataset.KRX_STOCK_DAILY, [])
    calendar = _module("calendar").TradingCalendar(
        parsers.derive_trading_calendar(
            krx_snapshots,
            version=f"krx-calendar-derived-{market_date.isoformat()}",
        )
    )
    prices = tuple(
        observation
        for snapshot in krx_snapshots
        for observation in parsers.parse_krx_stock_daily(snapshot)
    )
    shares = [observation.listed_share_observation() for observation in prices]
    holdings: list[Any] = []
    declarations: list[Any] = []
    stock_by_corp = {
        corp_code: stock_code
        for snapshot in by_dataset.get(models.SourceDataset.OPENDART_CORP_CODE, [])
        for stock_code, corp_code in parsers.parse_corp_code_index(snapshot).items()
    }
    errors = _module("errors")
    for dataset in (
        models.SourceDataset.OPENDART_STOCK_TOTAL,
        models.SourceDataset.OPENDART_LARGEST_SHAREHOLDER,
        models.SourceDataset.OPENDART_TREASURY_STATUS,
    ):
        for snapshot in by_dataset.get(dataset, []):
            stock_code = stock_by_corp.get(snapshot.metadata.source_key.partition(":")[0])
            if stock_code is None:
                continue
            try:
                normalized = parsers.parse_open_dart(snapshot, stock_code=stock_code)
            except errors.SourceContractError:
                # 정기보고서 표 모양은 회사마다 다르다. 보고서를 안 낸 회사(013),
                # 자사주 거래가 없어 총계 row가 없는 회사, 결산월이 12월이 아닌
                # 회사가 실제로 2,410종목 중 118종목 있다. 한 종목의 공시 모양
                # 때문에 시장 전체 기준정보가 죽으면 안 되므로 그 종목만 관측 없이
                # 남기고, 유동주식비율 없음은 Coverage가 그대로 드러낸다.
                continue
            shares.extend(normalized.issued_share_observations)
            holdings.extend(normalized.non_float_holdings)
            declarations.extend(normalized.coverage_declarations)

    return resolve_stock_references(
        stock_ids,
        market_date=market_date,
        decision_at=decision_at,
        calendar=calendar,
        daily_prices=prices,
        share_observations=tuple(shares),
        holdings=tuple(holdings),
        coverage_declarations=tuple(declarations),
        policy=policy,
    )
