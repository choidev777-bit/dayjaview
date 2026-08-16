"""KRX 일별매매에서 뽑은 종목명 이력 (회사 온톨로지 단계 2).

인포스탁은 과거 기록의 종목명을 현재 이름으로 소급 정규화한 코드가 있다.
012450은 2006년 기록에도 "한화에어로스페이스"로 적혀 있어, 그 원천만으로는
사명 이력을 만들 수 없다. KRX 일별매매는 거래일마다 그날의 종목명(`ISU_NM`)을
그대로 담으므로 이름이 언제부터 언제까지 쓰였는지가 날짜로 나온다.

E-16이 이미 받아 둔 봉투(2010-01-04~)를 다시 읽을 뿐 외부를 호출하지 않는다.
같은 봉투면 같은 결과다.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from packages.historical_data.krx_daily import load_daily_envelope

KRX_NAME_INDEX_VERSION = "krx-name-windows/1.0.0"


@dataclass(frozen=True, slots=True)
class KrxNameWindow:
    """한 종목코드가 한 이름으로 거래된 구간."""

    stock_code: str
    name: str
    market: str
    first_date: date
    last_date: date
    day_count: int


@dataclass(frozen=True, slots=True)
class KrxNameIndex:
    """종목명 이력 전체와 시장별 마지막 거래일."""

    index_version: str
    market_last_dates: Mapping[str, date]
    windows: tuple[KrxNameWindow, ...]

    def is_open(self, window: KrxNameWindow) -> bool:
        """마지막 거래일까지 이름이 살아 있으면 아직 쓰는 이름이다."""

        return window.last_date == self.market_last_dates.get(window.market)

    def by_code(self) -> dict[str, tuple[KrxNameWindow, ...]]:
        grouped: dict[str, list[KrxNameWindow]] = {}
        for window in self.windows:
            grouped.setdefault(window.stock_code, []).append(window)
        return {
            code: tuple(sorted(items, key=lambda item: (item.first_date, item.name)))
            for code, items in grouped.items()
        }


def scan_krx_name_windows(paths: Iterable[Path]) -> KrxNameIndex:
    """봉투를 읽어 (종목코드, 이름) 구간을 만든다. 봉투 hash를 검증한다."""

    seen: dict[tuple[str, str], list[Any]] = {}
    market_last: dict[str, date] = {}
    for path in paths:
        snapshot = load_daily_envelope(path)
        metadata = snapshot.metadata
        if metadata.dataset.value != "KRX_STOCK_DAILY":
            raise ValueError(f"KRX 일별매매 봉투가 아닙니다: {path}")
        market = metadata.source_key.partition(":")[0]
        trade_date = metadata.as_of.date()
        rows = json.loads(snapshot.raw_payload_text).get("OutBlock_1") or []
        if not rows:
            continue
        known = market_last.get(market)
        if known is None or trade_date > known:
            market_last[market] = trade_date
        for row in rows:
            code = str(row.get("ISU_CD", "")).strip()
            name = str(row.get("ISU_NM", "")).strip()
            if not code or not name:
                continue
            entry = seen.get((code, name))
            if entry is None:
                seen[(code, name)] = [trade_date, trade_date, 1, market]
                continue
            if trade_date < entry[0]:
                entry[0] = trade_date
            if trade_date >= entry[1]:
                entry[1] = trade_date
                entry[3] = market
            entry[2] += 1
    windows = tuple(
        KrxNameWindow(
            stock_code=code,
            name=name,
            market=str(entry[3]),
            first_date=entry[0],
            last_date=entry[1],
            day_count=int(entry[2]),
        )
        for (code, name), entry in sorted(seen.items())
    )
    return KrxNameIndex(
        index_version=KRX_NAME_INDEX_VERSION,
        market_last_dates=dict(sorted(market_last.items())),
        windows=windows,
    )


def name_index_payload(index: KrxNameIndex) -> dict[str, Any]:
    """파일로 남길 형태. 같은 색인이면 같은 JSON이다."""

    return {
        "indexVersion": index.index_version,
        "marketLastDates": {
            market: value.isoformat()
            for market, value in sorted(index.market_last_dates.items())
        },
        "windows": [
            {
                "stockCode": window.stock_code,
                "name": window.name,
                "market": window.market,
                "firstDate": window.first_date.isoformat(),
                "lastDate": window.last_date.isoformat(),
                "dayCount": window.day_count,
            }
            for window in index.windows
        ],
    }


def load_name_index(payload: Mapping[str, Any]) -> KrxNameIndex:
    """`name_index_payload`가 쓴 파일을 되읽는다."""

    version = str(payload.get("indexVersion", ""))
    if version != KRX_NAME_INDEX_VERSION:
        raise ValueError(f"지원하지 않는 종목명 색인 버전입니다: {version!r}")
    return KrxNameIndex(
        index_version=version,
        market_last_dates={
            str(market): date.fromisoformat(str(value))
            for market, value in dict(payload.get("marketLastDates") or {}).items()
        },
        windows=tuple(
            KrxNameWindow(
                stock_code=str(row["stockCode"]),
                name=str(row["name"]),
                market=str(row["market"]),
                first_date=date.fromisoformat(str(row["firstDate"])),
                last_date=date.fromisoformat(str(row["lastDate"])),
                day_count=int(row["dayCount"]),
            )
            for row in payload.get("windows") or []
        ),
    )
