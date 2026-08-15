#!/usr/bin/env python3
"""DailyFeaturedTheme 일일 증분: 최근 구간을 수집해 저장소에 적재합니다.

매일 장후에 스케줄러(cron 등)가 실행하는 진입점입니다. 과거 전체는 재수집하지
않고 lookback 구간만 다시 관측합니다. 같은 구간·같은 내용의 재실행은 저장소가
reused로 끝내므로 재시도가 안전합니다. live API 호출에는 --approved가 필요합니다.

상태 어휘는 운영자 콘솔과 같습니다: SUCCEEDED(0) / PARTIAL(2) /
AUTH_REQUIRED(3) / RATE_LIMITED(4) / FAILED(1).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

_EXIT_CODES = {
    "SUCCEEDED": 0,
    "FAILED": 1,
    "PARTIAL": 2,
    "AUTH_REQUIRED": 3,
    "RATE_LIMITED": 4,
}


def _today_kst() -> str:
    return (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y%m%d")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DailyFeaturedTheme 최근 구간을 수집해 PostgreSQL에 증분 적재합니다."
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=7,
        help="end-date로부터 며칠 전까지 다시 관측할지 (수정·삭제 감지 구간)",
    )
    parser.add_argument("--end-date", default=_today_kst(), help="YYYYMMDD")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--request-delay-seconds", type=float, default=1.0)
    parser.add_argument(
        "--approved",
        action="store_true",
        help="사용자가 live API 호출을 승인했음을 명시합니다.",
    )
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="DB에 적재하지 않고 수집·검증까지만 수행합니다.",
    )
    parser.add_argument(
        "--database-url-env",
        default="INFOSTOCK_DATABASE_URL",
        help="PostgreSQL URL을 가진 환경변수 이름(값 자체는 출력하지 않음)",
    )
    return parser.parse_args(argv)


def _emit(payload: dict[str, object]) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return _EXIT_CODES[str(payload["status"])]


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if args.lookback_days < 0:
        raise ValueError("lookback-days는 0 이상이어야 합니다.")
    infostock = importlib.import_module("packages.infostock")

    end = datetime.strptime(str(args.end_date), "%Y%m%d")
    start_date = (end - timedelta(days=args.lookback_days)).strftime("%Y%m%d")
    end_date = str(args.end_date)
    directory = args.output_root / f"window-{start_date}-{end_date}"

    try:
        infostock.collect_daily_api_backfill(
            directory,
            start_date=start_date,
            end_date=end_date,
            approved=args.approved,
            page_size=args.page_size,
            request_delay_seconds=args.request_delay_seconds,
            resume=True,
        )
    except (RuntimeError, infostock.FixtureValidationError) as exc:
        return _emit(
            {
                "status": infostock.classify_collection_error(exc),
                "phase": "COLLECT",
                "window": [start_date, end_date],
                "messageKo": str(exc),
            }
        )

    bundle, window = infostock.build_daily_increment_bundle(directory)
    daily_status = str(bundle.daily.component_status)
    if args.collect_only:
        return _emit(
            {
                "status": "SUCCEEDED" if daily_status == "COMPLETE" else "PARTIAL",
                "phase": "COLLECT_ONLY",
                "window": [start_date, end_date],
                "postsDiscovered": len(bundle.daily.entries),
                "bodiesCaptured": bundle.daily.body_count,
                "outputDir": str(directory.resolve()),
            }
        )

    database_url = os.environ.get(str(args.database_url_env))
    if not database_url:
        raise RuntimeError(
            f"{args.database_url_env} 환경변수에 PostgreSQL URL이 필요합니다."
        )
    psycopg = importlib.import_module("psycopg")
    connection = psycopg.connect(database_url)
    try:
        store = infostock.PostgresInfostockStore(cast(Any, connection))
        result = infostock.import_daily_increment(
            bundle, store, window_start=window[0], window_end=window[1]
        )
    finally:
        connection.close()
    return _emit(
        {
            "status": result.status,
            "phase": "APPLIED",
            "runId": result.run_id,
            "runReused": result.reused,
            "window": [start_date, end_date],
            "dailyStatus": result.daily_status,
            "postsSeen": result.daily_posts_seen,
            "bodiesSeen": result.daily_bodies_seen,
            "relationsSeen": result.daily_relations_seen,
            "revisionsCreated": result.daily_post_revisions_created,
            "outputDir": str(directory.resolve()),
        }
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Daily 증분 수집 실패: {exc}", file=sys.stderr)
        raise SystemExit(1)
