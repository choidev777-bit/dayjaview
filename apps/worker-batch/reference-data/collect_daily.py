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
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

from packages.pipeline.trading_day import KST

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
    # `.`을 남기면 `..`가 살아남아 상위 경로로 나갈 수 있다. source_key에는 점이 없다.
    return re.sub(r"[^0-9A-Za-z_-]", "_", value)


def _stamp_settlement(snapshot: Any) -> Any:
    """정기보고서 as_of를 응답이 실제로 말한 결산기준일(stlm_dt)로 맞춘다.

    보고서코드에서 계산한 날짜는 12월 결산 회사에만 맞는다. 3월·6월 결산 회사는
    같은 보고서코드라도 stlm_dt가 다르고, 파서는 as_of가 stlm_dt와 같기를
    요구하므로 가정한 날짜로 저장하면 나중에 읽히지 않는다.
    """

    models = _package("models")
    rows = json.loads(snapshot.raw_payload_text).get("list")
    if not isinstance(rows, list) or not rows:
        return snapshot
    settlements = {
        row.get("stlm_dt") for row in rows if isinstance(row, dict) and row.get("stlm_dt")
    }
    if len(settlements) != 1:
        return snapshot
    try:
        settlement = date.fromisoformat(str(settlements.pop()))
    except ValueError:
        return snapshot
    if settlement == snapshot.metadata.as_of.date():
        return snapshot
    return models.SourceSnapshot(
        metadata=replace(
            snapshot.metadata,
            as_of=datetime(settlement.year, settlement.month, settlement.day, tzinfo=UTC),
        ),
        raw_payload_text=snapshot.raw_payload_text,
        raw_hash=snapshot.raw_hash,
    )


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


def _empty_daily_trustworthy(queried: date, *, at: datetime) -> bool:
    """빈 일별매매 응답을 시각만으로 휴장이라 믿어도 되는가.

    확실한 증거는 같은 실행에서 이 날짜보다 뒤 날짜의 데이터를 이미 본
    것이고(호출부의 later_rows_seen — KRX는 순차 발행이라 뒤 날짜가 나왔는데
    이 날짜가 비면 휴장이다), 이 함수는 그 증거가 없을 때의 시각 폴백이다.
    `다음날 06:00` 기준은 2026-08-21 운영에서 깨졌다 — 정상 거래일(08-20)
    발행이 06:04 이후였는데 06:00을 넘겼다는 이유로 빈 응답이 휴장으로
    저장·고착되어 달력이 전일을 휴장으로 오판했고 장중 내내 세션이 서지
    못했다. 폴백은 다음날 저녁까지 미룬다.
    """

    return at >= datetime.combine(
        queried + timedelta(days=1), time(18, 0), tzinfo=KST
    )


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
    # market_dates는 최신 → 과거 순이다. 더 최신 날짜의 데이터를 이미 봤다면
    # 그보다 앞 날짜의 빈 응답은 미발행이 아니라 휴장이다.
    later_rows_seen = False
    for market_date in market_dates:
        date_has_rows = False
        for market in adapters.KRX_MARKET_PATHS:
            source_key = f"{market}:{market_date.isoformat()}"
            path = output_dir / f"KRX_STOCK_DAILY.{_slug(source_key)}.json"
            if path.is_file():
                stored = parsers.load_collected_snapshot(
                    json.loads(path.read_text(encoding="utf-8"))
                )
                if parsers.parse_krx_stock_daily(stored):
                    date_has_rows = True
                    krx_snapshots.append(stored)
                    continue
                if later_rows_seen:
                    # 뒤 날짜 데이터가 확인된 빈 파일은 휴장 확정이라 유지한다.
                    krx_snapshots.append(stored)
                    continue
                # 미발행 시점에 저장된 빈 응답 잔해다. 지우고 다시 받는다.
                path.unlink()
            snapshot = krx.fetch_stock_daily(
                market=market,
                market_date=market_date,
                as_of=datetime.combine(
                    market_date,
                    datetime.min.time(),
                    tzinfo=KST,
                ),
                collected_at=collected_at,
            )
            krx_calls += 1
            if parsers.parse_krx_stock_daily(snapshot):
                date_has_rows = True
                _store(snapshot, output_dir=output_dir)
                krx_snapshots.append(snapshot)
                continue
            if later_rows_seen:
                # 뒤 날짜 데이터가 이미 나온 상태의 빈 응답은 휴장 확정이다.
                _store(snapshot, output_dir=output_dir)
                krx_snapshots.append(snapshot)
                continue
            if _empty_daily_trustworthy(market_date, at=collected_at):
                # 시각 폴백만으로 믿은 휴장은 이 실행의 달력 증거로만 쓰고
                # 저장하지 않는다. 발행이 늦어진 것이면 다음 재시도가 다시
                # 확인한다 — 저장하면 오판이 파일로 고착된다(2026-08-21 운영).
                krx_snapshots.append(snapshot)
            # 휴장인지 미발행인지 모르는 빈 응답은 저장도, 달력 증거도
            # 남기지 않는다. 다음 재시도가 다시 확인한다.
        later_rows_seen = later_rows_seen or date_has_rows

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
                _stamp_settlement(
                    dart.fetch_periodic_report(
                        dataset=models.SourceDataset(dataset_name),
                        corp_code=corp_code,
                        business_year=arguments.business_year,
                        report_code=arguments.report_code,
                        as_of=settlement_as_of,
                        collected_at=collected_at,
                    )
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
