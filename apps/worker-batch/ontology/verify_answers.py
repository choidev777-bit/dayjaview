#!/usr/bin/env python3
"""답변 수치를 두 경로로 계산해 대조한다 (단계 0, 겹 B).

같은 답을 서로 다른 경로로 구하고 일치하면 통과, 어긋나면 검수 큐로 올린다.
사람이 전건을 보지 않게 하려는 것이며, 두 경로가 같은 버그를 공유하면 못 잡는
한계가 있다 — 그래서 경로를 최대한 다르게 잡는다.

- 경로 1: 제품이 쓰는 읽기 모델(`PostgresDailyFeaturedReader`)
- 경로 2: 원문 HTML을 그 자리에서 다시 파싱한 결과

두 경로는 DB 적재를 사이에 두고 갈린다. 적재·조회에서 값이 뒤바뀌거나 누락되면
어긋난다. 파서 자체가 틀린 경우는 겹 C(온톨로지 라벨) 검수가 잡는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.infostock import PostgresDailyFeaturedReader, parse_daily_html_body


@dataclass(frozen=True, slots=True)
class Case:
    published: date
    detail_path: Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="답변 수치를 두 경로로 대조하고 불일치만 큐에 올립니다."
    )
    parser.add_argument(
        "--details",
        type=Path,
        default=REPOSITORY_ROOT
        / "data"
        / "infostock"
        / "daily-full-20260814"
        / "details",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "ontology" / "answer_review_queue.json",
    )
    parser.add_argument("--database-url-env", default="INFOSTOCK_DATABASE_URL")
    parser.add_argument(
        "--limit",
        type=int,
        default=136,
        help="대조할 게시물 수. 기본 136은 유형당 8건 기준이다.",
    )
    return parser


def _from_source(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8")).get("data") or {}
    relations, status = parse_daily_html_body(str(payload.get("content") or ""))
    quotes: dict[str, list[str | None]] = {}
    for relation in relations:
        if relation.relation_type != "THEME_STOCK" or not relation.source_stock_code:
            continue
        key = f"{relation.source_theme_name}|{relation.source_stock_code}"
        quotes[key] = [relation.change_rate, relation.close_price and str(relation.close_price)]
    return {
        "sendDate": str(payload.get("sendDate") or ""),
        "parseStatus": status,
        "sectionCount": len({r.source_theme_name for r in relations if r.relation_type == "DESCRIPTION"}),
        "detailCount": sum(1 for r in relations if r.relation_type == "SECTION_DETAIL"),
        "quotes": quotes,
    }


def _from_reader(reader: PostgresDailyFeaturedReader, published: date) -> dict[str, Any]:
    movers = reader.day_movers(published)
    quotes: dict[str, list[str | None]] = {}
    for section in movers.sections:
        for theme in section.themes:
            for stock in theme.stocks:
                if not stock.stock_code:
                    continue
                key = f"{theme.theme_name}|{stock.stock_code}"
                quotes[key] = [
                    stock.change_rate,
                    stock.close_price and str(stock.close_price),
                ]
    return {
        "status": movers.status,
        "sectionCount": sum(1 for s in movers.sections if s.headline),
        "detailCount": sum(len(s.details) for s in movers.sections),
        "quotes": quotes,
    }


def main(argv: list[str] | None = None) -> int:
    import os

    arguments = _parser().parse_args(argv)
    dsn = os.environ.get(str(arguments.database_url_env), "").strip()
    if not dsn:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "messageKo": f"{arguments.database_url_env} 환경변수가 필요합니다.",
                },
                ensure_ascii=False,
            )
        )
        return 2

    paths = sorted(arguments.details.glob("*.json"))
    if not paths:
        print(
            json.dumps(
                {"status": "FAILED", "messageKo": "Daily 본문을 찾지 못했습니다."},
                ensure_ascii=False,
            )
        )
        return 2
    # 연도별로 고르게 뽑는다. 파일 순서대로 등간격을 뜨면 표본이 2007~2012년
    # 서술형에 몰려 시세 비교가 한 건도 없는 헛검사가 된다.
    by_year: dict[str, list[Path]] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8")).get("data") or {}
        year = str(payload.get("sendDate") or "")[:4]
        if len(year) == 4:
            by_year.setdefault(year, []).append(path)
    years = sorted(by_year)
    per_year = max(1, arguments.limit // max(1, len(years)))
    sampled: list[Path] = []
    for year in years:
        bucket = by_year[year]
        stride = max(1, len(bucket) // per_year)
        sampled.extend(bucket[::stride][:per_year])
    sampled = sampled[: arguments.limit]

    import psycopg

    mismatches: list[dict[str, Any]] = []
    checked = 0
    compared_quotes = 0
    with psycopg.connect(dsn) as connection:
        reader = PostgresDailyFeaturedReader(connection)
        for path in sampled:
            source = _from_source(path)
            send_date = source["sendDate"]
            if len(send_date) != 8:
                continue
            published = date(
                int(send_date[:4]), int(send_date[4:6]), int(send_date[6:])
            )
            served = _from_reader(reader, published)
            checked += 1
            compared_quotes += len(source["quotes"])
            reasons: list[str] = []
            if served["status"] != "PUBLISHED":
                reasons.append(f"발행일 조회 실패: {served['status']}")
            if served["detailCount"] != source["detailCount"]:
                reasons.append(
                    "상세 문단 수 불일치: "
                    f"원문 {source['detailCount']} vs 조회 {served['detailCount']}"
                )
            differing = [
                key
                for key, value in source["quotes"].items()
                if served["quotes"].get(key) != value
            ]
            if differing:
                reasons.append(f"시세 불일치 {len(differing)}건: {differing[:3]}")
            missing = sorted(set(source["quotes"]) - set(served["quotes"]))
            if missing:
                reasons.append(f"조회에 없는 종목 {len(missing)}건: {missing[:3]}")
            if reasons:
                mismatches.append(
                    {
                        "publishedDate": published.isoformat(),
                        "sourceFile": path.name,
                        "parseStatus": source["parseStatus"],
                        "reasons": reasons,
                        "reviewStatus": "AI_DRAFT",
                    }
                )

    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "checked": checked,
                "comparedQuotes": compared_quotes,
                "mismatchCount": len(mismatches),
                "mismatches": mismatches,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": (
                    "VACUOUS"
                    if compared_quotes == 0
                    else "SUCCEEDED"
                    if not mismatches
                    else "REVIEW_NEEDED"
                ),
                "checked": checked,
                "comparedQuotes": compared_quotes,
                "mismatchCount": len(mismatches),
                "queuePath": str(arguments.out),
                "messageKo": (
                    "비교한 시세가 0건이면 표본에 시세가 없는 것이라 통과로 읽으면 안 된다."
                    if compared_quotes == 0
                    else "일치하는 건은 사람이 보지 않는다. 큐에 오른 것만 확인하면 된다."
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
