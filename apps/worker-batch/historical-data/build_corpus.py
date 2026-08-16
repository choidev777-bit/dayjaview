#!/usr/bin/env python3
"""수집된 KRX 일별매매 봉투에서 daily_prices corpus(SQLite)를 빌드한다 (E-16).

외부 호출 없이 로컬 봉투만 읽는다. 빌드는 전체 재생성이며, 완성본을 임시 파일에
만들고 마지막에 원자적으로 바꿔치기하므로 중간에 죽어도 기존 corpus는 남는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.historical_data import HistoricalDataError, build_daily_price_corpus


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="수집 봉투를 검증·해석해 원주가+수정주가 daily_prices corpus를 만듭니다."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "data" / "krx-daily",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "data" / "daily_prices.sqlite",
    )
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        report = build_daily_price_corpus(
            input_dir=arguments.input_dir,
            database_path=arguments.database,
            progress=_print,
        )
    except (OSError, HistoricalDataError) as exc:
        _print({"status": "FAILED", "messageKo": str(exc)})
        return 2
    _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
