#!/usr/bin/env python3
"""실공급원에서 특징주 뉴스를 주기 수집하는 worker 진입점.

실행 예:
    uv run python apps/worker-news/collect.py --stock-directory stocks.json --once

`--stock-directory`는 ``{"종목명": "stock_id"}`` JSON. 외부 API를 실제로
호출하므로 CLAUDE.md 승인 규칙을 따른다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta, timezone
from datetime import time as datetime_time
from importlib import import_module
from os import environ
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

_SEOUL = timezone(timedelta(hours=9))


def _default_window_start(now: datetime) -> datetime:
    """전일 장 마감(15:30 KST) 이후 기사만 수집 대상으로 삼는다."""

    local = now.astimezone(_SEOUL)
    previous_day = (local - timedelta(days=1)).date()
    return datetime.combine(previous_day, datetime_time(15, 30), tzinfo=_SEOUL)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="허용된 RSS·NAVER API HUB 공급원에서 특징주 뉴스를 수집합니다."
    )
    parser.add_argument(
        "--stock-directory",
        type=Path,
        required=True,
        help='{"종목명": "stock_id"} JSON 파일',
    )
    parser.add_argument(
        "--entity-vocabulary",
        type=Path,
        default=None,
        help="Entity 용어 JSON 배열 파일 (선택)",
    )
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument(
        "--window-start",
        default=None,
        help="ISO 8601 시각. 생략하면 전일 15:30 KST",
    )
    parser.add_argument(
        "--once", action="store_true", help="1회 polling 후 종료 (검증용)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    news = import_module("packages.news")

    stock_directory = json.loads(args.stock_directory.read_text(encoding="utf-8"))
    vocabulary: list[str] = []
    if args.entity_vocabulary is not None:
        vocabulary = json.loads(args.entity_vocabulary.read_text(encoding="utf-8"))

    sources = news.create_live_news_sources(environ)
    if not sources:
        print(
            "설정된 공급원이 없습니다. NEWS_RSS_SOURCES 또는 "
            "NAVER_API_HUB_CLIENT_ID/SECRET을 설정하세요.",
            file=sys.stderr,
        )
        return 1

    store = news.InMemoryNewsStore()
    ingestor = news.NewsIngestor(
        store, stock_directory=stock_directory, entity_vocabulary=vocabulary
    )
    poller = news.SourcePoller(sources)
    window_start = (
        datetime.fromisoformat(args.window_start)
        if args.window_start
        else _default_window_start(datetime.now(UTC))
    )
    print(f"공급원 {len(sources)}개, window_start={window_start.isoformat()}")

    while True:
        now = datetime.now(UTC)
        cursors = {
            source_id: cursor
            for source_id in poller.source_ids
            if (cursor := store.get_cursor(source_id)) is not None
        }
        result = poller.poll(cursors, now=now)
        for cursor in result.cursors:
            store.put_cursor(cursor)
        report = ingestor.ingest(result.items, now=now, window_start=window_start)
        print(
            json.dumps(
                {
                    "at": now.isoformat(),
                    "stored": len(report.stored),
                    "duplicates": len(report.duplicates),
                    "rejected": len(report.rejected),
                    "degraded": list(result.degraded_source_ids),
                },
                ensure_ascii=False,
            )
        )
        for failure in result.failures:
            print(f"공급원 실패 {failure.source_id}: {failure.message}", file=sys.stderr)
        if args.once:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
