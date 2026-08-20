"""시연본 `legacyDemo.json`의 과거 사례를 실제 매칭 결과로 바꾼다.

구 DB에서 뽑을 때는 같은 테마의 최근 사건을 날짜순으로 담았을 뿐이라 오늘
소재와 닮았는지를 보지 않았다. 여기서는 운영과 같은 엔진을 써서 같은 테마의
과거 원인문 중 소재 유형이 겹치는 것만 고르고, 고른 뒤에 일봉 corpus의 실제
등락을 붙인다.

    uv run python scripts/rebuild_demo_similar.py

`similar` 배열만 다시 쓴다. 오늘 순위·주도주·근거·소재 유형은 건드리지 않는다.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from packages.historical_matching import (
    HORIZONS,
    MAX_ITEMS,
    HistoricalCaseIndex,
    PastCase,
    rank_cases,
    today_context,
)
from packages.infostock import load_existing_collection
from packages.ontology import SqliteOutcomeReader

DEMO_PATH = Path("apps/web/src/adapters/legacyDemo.json")
IMPORT_DIR = Path("data/infostock/import")
CORPUS_PATH = Path("research/data/daily_prices.sqlite")


def _ratio(value: Decimal | None) -> float | None:
    return None if value is None else round(float(value) / 100.0, 6)


def _item(
    case: PastCase,
    reasons: tuple[str, ...],
    reader: SqliteOutcomeReader,
) -> dict[str, object]:
    priced: list[tuple[str, str, dict[int, Decimal | None]]] = []
    for stock_code, name in case.basket:
        _, base_close, computed, _ = reader.returns(
            stock_code, case.market_date, HORIZONS
        )
        if base_close is None:
            continue
        priced.append((stock_code, name, dict(computed)))
    outcomes: list[dict[str, object]] = []
    for horizon in HORIZONS:
        values = [
            computed[horizon]
            for _, _, computed in priced
            if computed.get(horizon) is not None
        ]
        average = (
            None
            if not values
            else sum(values, Decimal(0)) / Decimal(len(values))
        )
        outcomes.append(
            {
                "horizonTradingDays": horizon,
                "return": _ratio(average),
                "status": "OBSERVED" if average is not None else "PENDING",
                "unavailableReason": None,
            }
        )
    leader_codes = case.leader_codes
    leaders: list[dict[str, object]] = []
    for stock_code, name, _ in priced:
        daily = reader.daily_return(stock_code, case.market_date)
        if daily is None:
            continue
        leaders.append(
            {
                "stockId": f"stk_{stock_code}",
                "symbol": stock_code,
                "name": name,
                "return": _ratio(daily),
                "role": "LEADER" if stock_code in leader_codes else "RELATED",
            }
        )
    return {
        "matchedEventId": case.matched_event_id,
        "marketDate": case.market_date.isoformat(),
        "displayNameAtEvent": case.display_name_at_event,
        "normalizedCatalystSummary": case.catalyst_summary,
        "similarityReasons": list(reasons),
        "outcomes": outcomes,
        "leaders": leaders,
    }


def main() -> int:
    demo = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
    market_date = date.fromisoformat(str(demo["marketDate"]))
    index = HistoricalCaseIndex(load_existing_collection(IMPORT_DIR).details)
    reader = SqliteOutcomeReader(CORPUS_PATH)
    for theme in demo["themes"]:
        theme_id = str(theme["themeId"])
        today = today_context(
            str(theme["reason"]),
            [
                str(leader["symbol"])
                for leader in theme["leaders"]
                if leader.get("symbol")
            ],
        )
        if today is None:
            theme["similar"] = []
            print(f"{theme_id} {theme['displayName']}: 오늘 소재를 분류하지 못했다")
            continue
        ranked = rank_cases(
            today,
            index.cases(theme_id),
            before=market_date,
            limit=MAX_ITEMS,
        )
        theme["similar"] = [
            _item(scored.case, scored.reasons, reader) for scored in ranked
        ]
        print(
            f"{theme_id} {theme['displayName']}: {len(ranked)}건 "
            f"({'/'.join(today.ordered_type_ids)})"
        )
    DEMO_PATH.write_text(
        json.dumps(demo, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
