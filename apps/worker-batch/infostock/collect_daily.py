#!/usr/bin/env python3
"""Capture the approved DailyFeaturedTheme API history with resume checkpoints."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def _today_kst() -> str:
    return (datetime.now(UTC) + timedelta(hours=9)).strftime("%Y%m%d")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DailyFeaturedTheme 전체 기간 목록·본문을 raw JSON과 checkpoint로 수집합니다."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-date", default="20000101", help="YYYYMMDD")
    parser.add_argument("--end-date", default=_today_kst(), help="YYYYMMDD")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--request-delay-seconds", type=float, default=1.0)
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="기존 manifest/checkpoint에서 이어서 수집합니다.",
    )
    parser.add_argument(
        "--approved",
        action="store_true",
        help="사용자가 live API 전체 수집을 승인했음을 명시합니다.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    infostock = importlib.import_module("packages.infostock")
    manifest = cast(
        dict[str, Any],
        infostock.collect_daily_api_backfill(
            args.output_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            approved=args.approved,
            page_size=args.page_size,
            request_delay_seconds=args.request_delay_seconds,
            resume=args.resume,
        ),
    )
    posts = cast(dict[str, object], manifest.get("posts") or {})
    failures = cast(dict[str, object], manifest.get("failures") or {})
    print(
        json.dumps(
            {
                "status": (
                    "COMPLETE" if manifest.get("coverageComplete") else "PARTIAL"
                ),
                "startDate": manifest.get("startDate"),
                "endDate": manifest.get("endDate"),
                "listPages": len(cast(list[object], manifest.get("pages") or [])),
                "postsDiscovered": manifest.get("postsDiscovered"),
                "bodiesCaptured": len(posts),
                "failures": len(failures),
                "outputDir": str(args.output_dir.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if manifest.get("coverageComplete") is True else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Daily 전체 수집 실패: {exc}", file=sys.stderr)
        raise SystemExit(1)
