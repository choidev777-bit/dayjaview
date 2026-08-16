#!/usr/bin/env python3
"""KRX 일별매매 과거 전 구간 백필 CLI (E-16, 외부 API 실호출).

1회 호출 = 1시장 = 1거래일 전 종목이므로 종목을 골라 받는 옵션은 없다. 이미
저장된 봉투는 다시 부르지 않아 중단·한도 초과 뒤 같은 명령으로 이어서 돈다.

기본 시작일은 2010-01-01이다. E-16이 정의한 기간은 2005-03부터지만, 2026-08-16
probe 실측으로 KRX Open API가 2009-12-30(그해 폐장일)까지는 빈 응답을 주고
2010-01-04(다음 첫 거래일)부터 데이터를 주는 것을 확인했다 — 이 원천에는
2005~2009 구간이 없다. 그 이전 구간은 다른 원천이 확보될 때 --start-date로 잇는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from packages.historical_data import collect_krx_daily_history
from scripts.market_replay_common import load_env_file


def _today_kst() -> date:
    return (datetime.now(UTC) + timedelta(hours=9)).date()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KRX 일별매매(코스피·코스닥·코넥스) 과거 전 구간을 실제로 호출해 적재합니다."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "research" / "data" / "krx-daily",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2010, 1, 1))
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=None,
        help="기본값은 오늘(KST)까지. 주말은 어차피 부르지 않는다.",
    )
    parser.add_argument("--max-calls", type=int, default=20_000)
    parser.add_argument("--request-delay-seconds", type=float, default=0.35)
    parser.add_argument("--env-file", default=".env.local")
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    load_env_file(Path(arguments.env_file))
    api_key = os.environ.get("KRX_API_KEY", "").strip()
    if not api_key:
        _print(
            {
                "status": "BLOCKED",
                "blocker": "B-REFDATA-KEYS",
                "messageKo": ".env.local에 KRX_API_KEY가 있어야 수집을 시작합니다.",
            }
        )
        return 2
    try:
        report = collect_krx_daily_history(
            api_key=api_key,
            output_dir=arguments.output_dir,
            start_date=arguments.start_date,
            end_date=arguments.end_date or _today_kst(),
            max_calls=arguments.max_calls,
            request_delay_seconds=arguments.request_delay_seconds,
            progress=_print,
        )
    except (OSError, ValueError) as exc:
        _print({"status": "FAILED", "messageKo": str(exc)})
        return 2
    _print(report)
    return 0 if report["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
