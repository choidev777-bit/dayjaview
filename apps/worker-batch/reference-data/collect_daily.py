"""KRX·OpenDART 당일 기준정보 수집 entrypoint (외부 API 실호출).

키가 없으면 transport를 시작하기 전에 B-REFDATA-KEYS로 멈춘다. 수집한 원문은
dataset·source_key마다 파일 하나로 남기고, 이미 있는 파일은 다시 부르지 않아
중단된 수집을 그대로 이어서 돌릴 수 있다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

STOCK_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
REPORT_DATASETS = (
    "OPENDART_STOCK_TOTAL",
    "OPENDART_LARGEST_SHAREHOLDER",
    "OPENDART_TREASURY_STATUS",
)
# 정기보고서 row의 stlm_dt(결산기준일). 파서는 as_of.date()가 이 값과 같기를
# 요구하므로 수집 시각이 아니라 이 날짜를 as_of로 저장해야 나중에 읽힌다.
REPORT_SETTLEMENT_MONTH_DAY = {
    "11011": (12, 31),
    "11012": (6, 30),
    "11013": (3, 31),
    "11014": (9, 30),
}


def _package(name: str) -> Any:
    return import_module("packages." + f"reference-data.reference_data.{name}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KRX·OpenDART 기준정보를 실제로 호출해 원문 그대로 적재합니다."
    )
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--stock-codes-file",
        type=Path,
        help="줄마다 6자리 종목코드. 없으면 KRX 응답에 나온 전 종목을 쓴다.",
    )
    parser.add_argument("--business-year", type=int, required=True)
    parser.add_argument(
        "--report-code",
        default="11012",
        choices=("11011", "11012", "11013", "11014"),
    )
    parser.add_argument(
        "--calendar-lookback-days",
        type=int,
        default=10,
        help="직전 거래일 판정을 위해 거슬러 조회할 일수.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="OpenDART를 호출할 종목 수 상한. 소량 검증에 쓴다.",
    )
    return parser


def _report(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _slug(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]", "_", value)


def _store(snapshot: Any, *, output_dir: Path) -> Path:
    parsers = _package("parsers")
    metadata = snapshot.metadata
    path = output_dir / f"{metadata.dataset.value}.{_slug(metadata.source_key)}.json"
    path.write_text(
        json.dumps(
            parsers.dump_collected_snapshot(snapshot),
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _existing(dataset: str, source_key: str, *, output_dir: Path) -> bool:
    return (output_dir / f"{dataset}.{_slug(source_key)}.json").is_file()


def collect(
    arguments: argparse.Namespace,
    *,
    environment: Mapping[str, str],
    krx_client: Any = None,
    dart_client: Any = None,
    now: datetime | None = None,
) -> dict[str, object]:
    adapters = _package("adapters")
    models = _package("models")
    parsers = _package("parsers")
    readiness = adapters.assess_live_readiness(environment)
    if readiness.missing_credentials:
        return {
            "status": "BLOCKED",
            "blocker": readiness.blocker,
            "missingCredentials": list(readiness.missing_credentials),
            "liveRequestAttempted": False,
            "messageKo": ".env.local에 KRX·OpenDART 키를 넣어야 수집을 시작합니다.",
        }

    collected_at = now or datetime.now(UTC)
    output_dir: Path = arguments.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    krx = adapters.KrxOpenApiAdapter(
        api_key=environment["KRX_API_KEY"],
        client=krx_client,
    )
    dart = adapters.OpenDartAdapter(
        api_key=environment["OPENDART_API_KEY"],
        client=dart_client,
    )

    market_dates = [
        arguments.market_date - timedelta(days=offset)
        for offset in range(arguments.calendar_lookback_days + 1)
    ]
    krx_snapshots: list[Any] = []
    krx_calls = 0
    for market_date in market_dates:
        for market in adapters.KRX_MARKET_PATHS:
            source_key = f"{market}:{market_date.isoformat()}"
            if _existing("KRX_STOCK_DAILY", source_key, output_dir=output_dir):
                krx_snapshots.append(
                    parsers.load_collected_snapshot(
                        json.loads(
                            (
                                output_dir
                                / f"KRX_STOCK_DAILY.{_slug(source_key)}.json"
                            ).read_text(encoding="utf-8")
                        )
                    )
                )
                continue
            snapshot = krx.fetch_stock_daily(
                market=market,
                market_date=market_date,
                as_of=datetime.combine(
                    market_date,
                    datetime.min.time(),
                    tzinfo=UTC,
                ),
                collected_at=collected_at,
            )
            krx_calls += 1
            _store(snapshot, output_dir=output_dir)
            krx_snapshots.append(snapshot)

    calendar = parsers.derive_trading_calendar(
        krx_snapshots,
        version=f"krx-calendar-derived-{arguments.market_date.isoformat()}",
    )
    prices = tuple(
        observation
        for snapshot in krx_snapshots
        for observation in parsers.parse_krx_stock_daily(snapshot)
    )

    corp_source_key = f"corp-code:{collected_at.date().isoformat()}"
    corp_path = output_dir / f"OPENDART_CORP_CODE.{_slug(corp_source_key)}.json"
    if corp_path.is_file():
        corp_snapshot = parsers.load_collected_snapshot(
            json.loads(corp_path.read_text(encoding="utf-8"))
        )
    else:
        corp_snapshot = dart.fetch_corp_code_index(
            as_of=collected_at,
            collected_at=collected_at,
        )
        _store(corp_snapshot, output_dir=output_dir)
    corp_codes = parsers.parse_corp_code_index(corp_snapshot)

    if arguments.stock_codes_file is not None:
        requested = [
            line.strip()
            for line in arguments.stock_codes_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        requested = sorted({observation.stock_code for observation in prices})
    invalid = [code for code in requested if not STOCK_CODE_RE.fullmatch(code)]
    if invalid:
        raise ValueError(f"6자리 종목코드가 아닙니다: {invalid[:3]}")
    unmapped = [code for code in requested if code not in corp_codes]
    targets = [code for code in requested if code in corp_codes]
    if arguments.limit is not None:
        targets = targets[: arguments.limit]

    month, day = REPORT_SETTLEMENT_MONTH_DAY[arguments.report_code]
    settlement_as_of = datetime(
        arguments.business_year, month, day, tzinfo=UTC
    )
    dart_calls = 0
    for stock_code in targets:
        corp_code = corp_codes[stock_code]
        for dataset_name in REPORT_DATASETS:
            source_key = f"{corp_code}:{arguments.business_year}:{arguments.report_code}"
            if _existing(dataset_name, source_key, output_dir=output_dir):
                continue
            dart_calls += 1
            _store(
                dart.fetch_periodic_report(
                    dataset=models.SourceDataset(dataset_name),
                    corp_code=corp_code,
                    business_year=arguments.business_year,
                    report_code=arguments.report_code,
                    as_of=settlement_as_of,
                    collected_at=collected_at,
                ),
                output_dir=output_dir,
            )

    return {
        "status": "COMPLETE",
        "marketDate": arguments.market_date.isoformat(),
        "outputDir": str(output_dir),
        "krxRequests": krx_calls,
        "openDartRequests": dart_calls,
        "tradingDays": sum(item.is_trading_day for item in calendar),
        "priceObservations": len(prices),
        "requestedStocks": len(requested),
        "collectedStocks": len(targets),
        "unmappedStockCodes": unmapped[:10],
        "unmappedStockCount": len(unmapped),
        "liveValidationStatus": "UNVERIFIED",
    }


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    errors = _package("errors")
    try:
        result = collect(arguments, environment=os.environ)
    except (OSError, ValueError, errors.ReferenceDataError) as exc:
        _report({"status": "FAILED", "messageKo": str(exc)})
        return 2
    _report(result)
    return 0 if result["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
