"""거래일마다 기준정보를 확보하고 그날 파이프라인 입력을 준비한다.

기준정보는 하루에 한 번만 있으면 된다. 유동시가총액은 전일 종가·상장주식수·
유동주식비율로 고정되고 장중에 바뀌지 않기 때문이다
(product_decisions.md PD-001 3항).

확보하지 못하면 **그날 계산을 시작하지 않는다.** 총시가총액이나 어제 기준정보로
조용히 대체하지 않는다(PD-001 10항).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from packages.domain import StockReference

from .references import load_collected_references

# 직전 거래일을 판정하려면 그 사이 날짜의 KRX 응답 유무가 있어야 한다.
# KRX에는 달력 endpoint가 없어 조회한 날짜만 판정된다(fail-closed).
CALENDAR_LOOKBACK_DAYS = 10

# 수집이 끝까지 갔을 때만 이 도장 파일이 생긴다. 파일 유무로 판정하면 중간에
# 죽은 수집의 잔해를 완성본으로 읽는다(2026-08-20 운영 실측).
COLLECTION_COMPLETE_MARKER = "COLLECTION_COMPLETE"


@dataclass(frozen=True, slots=True)
class ReferenceDataPreparation:
    market_date: date
    directory: Path
    collected: bool
    references: tuple[StockReference, ...]


def reference_directory(root: Path, market_date: date) -> Path:
    return root / market_date.isoformat()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _worker() -> Any:
    return import_module("apps.worker-batch.reference-data.collect_daily")


def prepare_reference_data(
    *,
    market_date: date,
    root: Path,
    stock_ids: Iterable[str],
    decision_at: datetime,
    environment: Mapping[str, str],
    business_year: int,
    report_code: str,
    stock_codes_file: Path | None = None,
    collect: Callable[..., dict[str, object]] | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> ReferenceDataPreparation:
    """그날 수집본이 없으면 수집하고, 해석한 기준정보를 돌려준다.

    수집이 `COMPLETE`가 아니면 예외를 올려 그날 파이프라인을 세우지 않는다.
    """

    directory = reference_directory(root, market_date)
    marker = directory / COLLECTION_COMPLETE_MARKER
    collected = False
    if not marker.is_file():
        # 도장이 없으면 수집을 (다시) 돌린다. collect는 파일 단위로 재개하므로
        # 이미 받은 원문은 다시 부르지 않고 빠진 것만 채운다.
        directory.mkdir(parents=True, exist_ok=True)
        runner = collect or _worker().collect
        result = runner(
            argparse.Namespace(
                market_date=market_date,
                output_dir=directory,
                stock_codes_file=stock_codes_file,
                business_year=business_year,
                report_code=report_code,
                calendar_lookback_days=CALENDAR_LOOKBACK_DAYS,
                limit=None,
            ),
            environment=environment,
        )
        if result.get("status") != "COMPLETE":
            raise RuntimeError(
                f"{market_date} 기준정보 수집이 끝나지 않아 그날 계산을 시작하지 "
                f"않습니다: {result}"
            )
        marker.write_text(clock().isoformat(), encoding="utf-8")
        collected = True
        # 방금 수집한 원문의 collected_at(=known_at)은 decision_at보다 늦다.
        # point-in-time 필터가 방금 수집분을 통째로 걸러내지 않도록 결정
        # 시각을 수집이 끝난 시각까지 민다.
        decision_at = max(decision_at, clock())

    references = load_collected_references(
        directory,
        market_date=market_date,
        decision_at=decision_at,
        stock_ids=stock_ids,
    )
    has_previous_close = any(
        reference.previous_adjusted_close is not None for reference in references
    )
    awaiting_corporate_action = any(
        not reference.corporate_action_resolved for reference in references
    )
    if not has_previous_close and not awaiting_corporate_action:
        # KRX가 전일 원문 자체를 아직 안 낸 시각(자정 직후)이다. 도장을 지워
        # 다음 재시도가 빠진 원문만 다시 받게 하고, 그날 계산은 시작하지 않는다.
        #
        # 전일 row는 확보됐는데 기업행위 원천이 없어 전일 종가가 비는 것은
        # 장중의 정상 상태다(2026-08-21 운영 실측: 08-20 942종목 확보에도
        # 미해결 100%로 이 가드가 세션을 하루 종일 막았다). 그 경우는 통과
        # 시킨다 — 파이프라인 `_supplement_base_price`가 키움 기준가(권리락·
        # 액면분할 반영)로 그 자리를 메우고, 못 메운 종목은
        # corporate_action_resolved=False로 계산에서 빠진다.
        marker.unlink(missing_ok=True)
        raise RuntimeError(
            f"{market_date} 전일 종가를 하나도 확보하지 못해 그날 계산을 "
            "시작하지 않습니다"
        )
    return ReferenceDataPreparation(
        market_date=market_date,
        directory=directory,
        collected=collected,
        references=references,
    )
