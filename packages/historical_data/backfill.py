"""KRX 일별매매 과거 전 구간 수집 (E-16 백필).

1회 호출 = 1시장 = 1거래일 전 종목이다. 주말은 부르지 않고, 공휴일의 빈 응답은
그대로 저장해 거래일 달력의 근거로 남긴다. 이미 저장된 파일은 다시 부르지
않으므로 중단·한도 초과 뒤 같은 명령으로 그대로 이어서 돌릴 수 있다.
"""

from __future__ import annotations

import json
import time as time_module
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
MARKETS: tuple[str, ...] = ("KOSPI", "KOSDAQ", "KONEX")
# 코넥스 개장일(2013-07-01). 그 전 날짜는 호출 자체를 하지 않는다.
KONEX_OPENED = date(2013, 7, 1)
# 한국 시장에 주중 15일(3주) 연속 휴장은 없다. 한 시장의 빈 응답이 이보다 길게
# 이어지면 원천이 그 구간을 제공하지 않는 것이므로, 지어내지 않고 멈춰 보고한다.
EMPTY_STREAK_LIMIT = 15
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (5.0, 30.0, 120.0)
STATUS_FILENAME = "backfill_status.json"

_REFERENCE_PACKAGE = "packages." + "reference-data.reference_data"


def _reference(name: str) -> Any:
    return import_module(f"{_REFERENCE_PACKAGE}.{name}")


def envelope_path(output_dir: Path, market: str, market_date: date) -> Path:
    return (
        output_dir
        / market
        / str(market_date.year)
        / f"KRX_STOCK_DAILY.{market}_{market_date.isoformat()}.json"
    )


class _Stop(Exception):
    pass


def _write_atomic(path: Path, text: str) -> None:
    """도중에 죽어도 깨진 파일이 남지 않게 임시 파일에 쓰고 바꿔치기한다.

    깨진 봉투가 남으면 resume이 그 날짜를 건너뛰어 corpus 빌드에서야 드러난다.
    """

    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def collect_krx_daily_history(
    *,
    api_key: str,
    output_dir: Path,
    start_date: date,
    end_date: date,
    max_calls: int = 20_000,
    request_delay_seconds: float = 0.35,
    client: Any = None,
    sleeper: Callable[[float], None] = time_module.sleep,
    now: Callable[[], datetime] | None = None,
    progress: Callable[[Mapping[str, object]], None] | None = None,
    status_every_calls: int = 50,
) -> dict[str, object]:
    if start_date > end_date:
        raise ValueError("start_date는 end_date보다 늦을 수 없습니다.")
    if max_calls <= 0:
        raise ValueError("max_calls는 0보다 커야 합니다.")
    adapters = _reference("adapters")
    errors = _reference("errors")
    parsers = _reference("parsers")
    adapter = adapters.KrxOpenApiAdapter(api_key=api_key, client=client)
    clock = now or (lambda: datetime.now(UTC))

    calls_made = 0
    files_written = 0
    files_skipped = 0
    empty_responses = 0
    empty_streak: dict[str, int] = {market: 0 for market in MARKETS}
    failure: dict[str, object] | None = None
    status = "COMPLETE"
    reason: str | None = None
    last_completed: date | None = None
    next_date: date | None = None

    def report(*, final: bool = False) -> dict[str, object]:
        return {
            "status": status if final else "RUNNING",
            "reason": reason,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "callsMade": calls_made,
            "filesWritten": files_written,
            "filesSkipped": files_skipped,
            "emptyResponses": empty_responses,
            "lastCompletedDate": (
                last_completed.isoformat() if last_completed is not None else None
            ),
            "nextDate": next_date.isoformat() if next_date is not None else None,
            "failure": failure,
            "updatedAt": clock().isoformat(),
        }

    def write_status(*, final: bool = False) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_atomic(
            output_dir / STATUS_FILENAME,
            json.dumps(report(final=final), ensure_ascii=False, sort_keys=True, indent=1),
        )

    def fetch(market: str, market_date: date) -> Any:
        # 계약 오류도 fetch 단계에서는 재시도한다. 2026-08-16 백필 실측에서 KRX가
        # 간헐적으로 JSON이 아닌 본문을 한 번 주고 다음 호출엔 정상 응답을 줬다
        # (2015-01-27 KOSPI). 재시도 후에도 계속되면 그대로 올려 fail-closed.
        attempts = 0
        while True:
            try:
                return adapter.fetch_stock_daily(
                    market=market,
                    market_date=market_date,
                    as_of=datetime.combine(market_date, time(15, 30), tzinfo=KST),
                    collected_at=clock(),
                )
            except (errors.SourceTransportError, errors.SourceContractError):
                if attempts >= len(RETRY_BACKOFF_SECONDS):
                    raise
                sleeper(RETRY_BACKOFF_SECONDS[attempts])
                attempts += 1

    day = start_date
    calls_since_status = 0
    try:
        while day <= end_date:
            if day.weekday() >= 5:
                day += timedelta(days=1)
                continue
            for market in MARKETS:
                if market == "KONEX" and day < KONEX_OPENED:
                    continue
                path = envelope_path(output_dir, market, day)
                if path.is_file():
                    files_skipped += 1
                    continue
                if calls_made >= max_calls:
                    status, reason, next_date = "PARTIAL", "CALL_BUDGET_EXHAUSTED", day
                    raise _Stop()
                try:
                    snapshot = fetch(market, day)
                except errors.SourceTransportError as exc:
                    status, reason, next_date = "PARTIAL", "TRANSPORT_FAILURE", day
                    failure = {
                        "market": market,
                        "date": day.isoformat(),
                        "messageKo": str(exc),
                    }
                    raise _Stop() from None
                except errors.SourceContractError as exc:
                    status, reason, next_date = "FAILED", "SOURCE_CONTRACT_FAILURE", day
                    failure = {
                        "market": market,
                        "date": day.isoformat(),
                        "messageKo": str(exc),
                    }
                    raise _Stop() from None
                calls_made += 1
                calls_since_status += 1
                path.parent.mkdir(parents=True, exist_ok=True)
                _write_atomic(
                    path,
                    json.dumps(
                        parsers.dump_collected_snapshot(snapshot),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
                files_written += 1
                row_count = len(
                    json.loads(snapshot.raw_payload_text).get("OutBlock_1") or []
                )
                if row_count == 0:
                    empty_responses += 1
                    empty_streak[market] += 1
                    if empty_streak[market] >= EMPTY_STREAK_LIMIT:
                        status, reason, next_date = "FAILED", "DATA_UNAVAILABLE", day
                        failure = {
                            "market": market,
                            "date": day.isoformat(),
                            "consecutiveEmptyWeekdays": empty_streak[market],
                            "messageKo": (
                                "주중 빈 응답이 휴장으로 설명할 수 없게 이어집니다. "
                                "원천이 이 구간을 제공하지 않는 것으로 보입니다."
                            ),
                        }
                        raise _Stop()
                else:
                    empty_streak[market] = 0
                if calls_since_status >= status_every_calls:
                    calls_since_status = 0
                    write_status()
                    if progress is not None:
                        progress(report())
                sleeper(request_delay_seconds)
            last_completed = day
            day += timedelta(days=1)
    except _Stop:
        pass
    except KeyboardInterrupt:
        status, reason, next_date = "PARTIAL", "INTERRUPTED", day

    final = report(final=True)
    write_status(final=True)
    return final
