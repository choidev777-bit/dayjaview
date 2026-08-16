"""수집 봉투에서 daily_prices corpus(SQLite)를 빌드한다 (E-16).

리서치 스펙 6.4의 필드(원주가 OHLC + 수정주가 OHLC + adjustment_version)를 그대로
저장하고, 감사용으로 market·전일대비·factor·break 기록을 함께 남긴다.

수정주가는 KRX 전일대비가 함의하는 기준가로 만든다. 그날 기준가(종가−전일대비)가
직전 종가와 다르면 그 사이에 기업행위(액면분할·병합·권리락·감자 등)가 있었던
것이고 factor = 기준가/직전종가다. 과거 row의 수정가 = 원주가 × (그 뒤 모든
factor의 곱). 현금배당은 KRX 기준가가 조정하지 않으므로 수정가에도 반영되지
않는다(통상 수정주가 관례와 동일). A-8 ①이 키움 FID 11로 검증한 것과 같은 성질의
계산이다.

전일대비가 없거나, 기준가가 0 이하이거나, factor가 정상 범위 밖이면 그 지점 이전
구간의 수정가를 만들지 않고 NULL로 남긴다(adjustment_breaks에 기록). 값이 없으면
없다고 표시하고 지어내지 않는다.

빌드는 전체 재생성이며 결정적이다: 같은 입력이면 daily_prices·adjustment_factors
내용이 같고, adjustment_version은 알고리즘 이름과 입력의 마지막 거래일로 정한다.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time as time_module
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from pathlib import Path

from .krx_daily import load_daily_envelope, parse_daily_envelope
from .models import KRX_MARKETS, HistoricalDataError

ADJUSTMENT_ALGORITHM = "krx-cmpprevdd-1"
ADJUSTED_PLACES = Decimal("0.000001")
# 실존 기업행위 factor의 안전 범위. 이 밖이면 원천 데이터 충돌로 보고 만들지 않는다.
FACTOR_MIN = Fraction(1, 1000)
FACTOR_MAX = Fraction(1000)

_FILENAME_RE = re.compile(
    r"^KRX_STOCK_DAILY\.(KOSPI|KOSDAQ|KONEX)_(\d{4}-\d{2}-\d{2})\.json$"
)

_SCHEMA = """
CREATE TABLE corpus_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE daily_prices (
  stock_id TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  market TEXT NOT NULL,
  open INTEGER,
  high INTEGER,
  low INTEGER,
  close INTEGER NOT NULL,
  change_from_previous INTEGER,
  volume INTEGER NOT NULL,
  trading_value INTEGER NOT NULL,
  adjusted_open TEXT,
  adjusted_high TEXT,
  adjusted_low TEXT,
  adjusted_close TEXT,
  adjustment_version TEXT NOT NULL,
  PRIMARY KEY (stock_id, trade_date)
) WITHOUT ROWID;
CREATE TABLE adjustment_factors (
  stock_id TEXT NOT NULL,
  effective_date TEXT NOT NULL,
  factor_numerator INTEGER NOT NULL,
  factor_denominator INTEGER NOT NULL,
  previous_close INTEGER NOT NULL,
  base_price INTEGER NOT NULL,
  PRIMARY KEY (stock_id, effective_date)
) WITHOUT ROWID;
CREATE TABLE adjustment_breaks (
  stock_id TEXT NOT NULL,
  break_date TEXT NOT NULL,
  reason TEXT NOT NULL,
  PRIMARY KEY (stock_id, break_date)
) WITHOUT ROWID;
CREATE TABLE market_days (
  market TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  skipped_rows INTEGER NOT NULL,
  PRIMARY KEY (market, trade_date)
) WITHOUT ROWID;
"""

_INSERT_PRICE = (
    "INSERT INTO daily_prices (stock_id, trade_date, market, open, high, low, close,"
    " change_from_previous, volume, trading_value, adjusted_open, adjusted_high,"
    " adjusted_low, adjusted_close, adjustment_version)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)"
)
_UPDATE_ADJUSTED = (
    "UPDATE daily_prices SET adjusted_open = ?, adjusted_high = ?, adjusted_low = ?,"
    " adjusted_close = ? WHERE stock_id = ? AND trade_date = ?"
)


def _discover(input_dir: Path) -> dict[str, list[tuple[date, Path]]]:
    found: dict[str, list[tuple[date, Path]]] = {}
    for market in KRX_MARKETS:
        market_dir = input_dir / market
        if not market_dir.is_dir():
            continue
        entries: list[tuple[date, Path]] = []
        seen: set[date] = set()
        for path in sorted(market_dir.rglob("KRX_STOCK_DAILY.*.json")):
            match = _FILENAME_RE.fullmatch(path.name)
            if match is None or match.group(1) != market:
                raise HistoricalDataError(str(path), "corpus 입력이 아닌 파일이 섞여 있습니다.")
            entry_date = date.fromisoformat(match.group(2))
            if entry_date in seen:
                raise HistoricalDataError(str(path), "같은 거래일 봉투가 중복되었습니다.")
            seen.add(entry_date)
            entries.append((entry_date, path))
        if entries:
            found[market] = sorted(entries)
    if not found:
        raise HistoricalDataError(str(input_dir), "수집된 KRX 일별매매 봉투가 없습니다.")
    return found


def _require_weekday_coverage(market: str, entries: list[tuple[date, Path]]) -> None:
    """수집 범위 안의 모든 주중 날짜가 있어야 factor 사슬이 완전하다."""

    present = {entry_date for entry_date, _ in entries}
    day = min(present)
    last = max(present)
    missing: list[date] = []
    while day <= last:
        if day.weekday() < 5 and day not in present:
            missing.append(day)
        day += timedelta(days=1)
    if missing:
        examples = ", ".join(value.isoformat() for value in missing[:10])
        raise HistoricalDataError(
            market,
            f"주중 {len(missing)}일의 수집 봉투가 없습니다 (처음: {examples})."
            " 백필을 이어서 실행해 채운 뒤 다시 빌드하십시오.",
        )


def build_daily_price_corpus(
    *,
    input_dir: Path,
    database_path: Path,
    progress: Callable[[Mapping[str, object]], None] | None = None,
    progress_every_files: int = 250,
) -> dict[str, object]:
    started = time_module.monotonic()
    found = _discover(input_dir)
    for market, entries in found.items():
        _require_weekday_coverage(market, entries)
    max_trade_date = max(
        entry_date for entries in found.values() for entry_date, _ in entries
    )
    version = f"{ADJUSTMENT_ALGORITHM}@{max_trade_date.isoformat()}"

    database_path.parent.mkdir(parents=True, exist_ok=True)
    building = database_path.with_name(database_path.name + ".building")
    if building.exists():
        building.unlink()
    connection = sqlite3.connect(building)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.executescript(_SCHEMA)

        # 1단계: 원주가 적재. 수정가는 factor를 다 본 뒤에만 만들 수 있다.
        files_processed = 0
        total_rows = 0
        skipped_count = 0
        skipped_examples: list[dict[str, str]] = []
        for market, entries in found.items():
            for entry_date, path in entries:
                snapshot = load_daily_envelope(path)
                expected_key = f"{market}:{entry_date.isoformat()}"
                if snapshot.metadata.source_key != expected_key:
                    raise HistoricalDataError(
                        str(path), "파일 이름과 봉투 sourceKey가 다릅니다."
                    )
                parsed = parse_daily_envelope(snapshot)
                try:
                    connection.executemany(
                        _INSERT_PRICE,
                        [
                            (
                                row.stock_id,
                                row.trade_date.isoformat(),
                                row.market,
                                row.open,
                                row.high,
                                row.low,
                                row.close,
                                row.change_from_previous,
                                row.volume,
                                row.trading_value,
                                version,
                            )
                            for row in parsed.rows
                        ],
                    )
                except sqlite3.IntegrityError as exc:
                    raise HistoricalDataError(
                        str(path), f"같은 종목·거래일 row가 이미 있습니다 ({exc})."
                    ) from exc
                connection.execute(
                    "INSERT INTO market_days VALUES (?, ?, ?, ?)",
                    (
                        market,
                        entry_date.isoformat(),
                        len(parsed.rows),
                        len(parsed.skipped_stock_codes),
                    ),
                )
                skipped_count += len(parsed.skipped_stock_codes)
                for code in parsed.skipped_stock_codes:
                    if len(skipped_examples) < 20:
                        skipped_examples.append(
                            {"sourceKey": expected_key, "stockCode": code}
                        )
                total_rows += len(parsed.rows)
                files_processed += 1
                if progress is not None and files_processed % progress_every_files == 0:
                    progress(
                        {
                            "stage": "LOAD",
                            "filesProcessed": files_processed,
                            "rows": total_rows,
                        }
                    )
        connection.commit()

        # 2단계: 종목별 시계열을 훑어 기준가 불일치(factor)와 사슬 끊김을 찾는다.
        factor_rows: list[tuple[str, str, int, int, int, int]] = []
        break_rows: list[tuple[str, str, str]] = []
        factor_map: dict[str, list[tuple[str, Fraction]]] = {}
        break_map: dict[str, str] = {}
        stock_ids: set[str] = set()
        previous: tuple[str, int] | None = None
        cursor = connection.execute(
            "SELECT stock_id, trade_date, close, change_from_previous"
            " FROM daily_prices ORDER BY stock_id, trade_date"
        )
        for stock_id, trade_date, close, change in cursor:
            if previous is None or previous[0] != stock_id:
                stock_ids.add(stock_id)
                previous = (stock_id, close)
                continue
            previous_close = previous[1]
            previous = (stock_id, close)
            if change is None:
                break_rows.append((stock_id, trade_date, "NO_CHANGE_FIELD"))
                break_map[stock_id] = trade_date
                continue
            base = close - change
            if base <= 0:
                break_rows.append((stock_id, trade_date, "NON_POSITIVE_BASE"))
                break_map[stock_id] = trade_date
                continue
            if base == previous_close:
                continue
            factor = Fraction(base, previous_close)
            if not (FACTOR_MIN <= factor <= FACTOR_MAX):
                break_rows.append((stock_id, trade_date, "FACTOR_OUT_OF_RANGE"))
                break_map[stock_id] = trade_date
                continue
            factor_rows.append(
                (
                    stock_id,
                    trade_date,
                    factor.numerator,
                    factor.denominator,
                    previous_close,
                    base,
                )
            )
            factor_map.setdefault(stock_id, []).append((trade_date, factor))
        connection.executemany(
            "INSERT INTO adjustment_factors VALUES (?, ?, ?, ?, ?, ?)", factor_rows
        )
        connection.executemany(
            "INSERT INTO adjustment_breaks VALUES (?, ?, ?)", break_rows
        )
        connection.commit()
        if progress is not None:
            progress(
                {
                    "stage": "FACTORS",
                    "factorEvents": len(factor_rows),
                    "adjustmentBreaks": len(break_rows),
                }
            )

        # 3단계: 수정가. 대다수(무보정·무단절) 종목은 원주가 그대로다.
        connection.execute(
            "UPDATE daily_prices SET"
            " adjusted_open = CASE WHEN open IS NULL THEN NULL"
            "   ELSE printf('%d.000000', open) END,"
            " adjusted_high = CASE WHEN high IS NULL THEN NULL"
            "   ELSE printf('%d.000000', high) END,"
            " adjusted_low = CASE WHEN low IS NULL THEN NULL"
            "   ELSE printf('%d.000000', low) END,"
            " adjusted_close = printf('%d.000000', close)"
        )
        rows_without_adjusted = 0
        affected = sorted(set(factor_map) | set(break_map))
        with localcontext() as context:
            context.prec = 50

            def adjust(value: int | None, cumulative: Fraction) -> str | None:
                if value is None:
                    return None
                exact = (
                    Decimal(value)
                    * Decimal(cumulative.numerator)
                    / Decimal(cumulative.denominator)
                )
                return str(exact.quantize(ADJUSTED_PLACES, rounding=ROUND_HALF_UP))

            for stock_id in affected:
                stock_factors = factor_map.get(stock_id, [])
                # suffix_products[i] = i번째 factor부터 끝까지의 곱.
                suffix_products: list[Fraction] = [Fraction(1)] * (
                    len(stock_factors) + 1
                )
                for index in range(len(stock_factors) - 1, -1, -1):
                    suffix_products[index] = (
                        stock_factors[index][1] * suffix_products[index + 1]
                    )
                latest_break = break_map.get(stock_id)
                updates: list[
                    tuple[str | None, str | None, str | None, str | None, str, str]
                ] = []
                factor_index = 0
                stock_cursor = connection.execute(
                    "SELECT trade_date, open, high, low, close FROM daily_prices"
                    " WHERE stock_id = ? ORDER BY trade_date",
                    (stock_id,),
                )
                for trade_date, open_, high, low, close in stock_cursor.fetchall():
                    if latest_break is not None and trade_date < latest_break:
                        updates.append((None, None, None, None, stock_id, trade_date))
                        rows_without_adjusted += 1
                        continue
                    while (
                        factor_index < len(stock_factors)
                        and stock_factors[factor_index][0] <= trade_date
                    ):
                        factor_index += 1
                    cumulative = suffix_products[factor_index]
                    if cumulative == 1:
                        continue
                    updates.append(
                        (
                            adjust(open_, cumulative),
                            adjust(high, cumulative),
                            adjust(low, cumulative),
                            adjust(close, cumulative),
                            stock_id,
                            trade_date,
                        )
                    )
                if updates:
                    connection.executemany(_UPDATE_ADJUSTED, updates)
        connection.execute("CREATE INDEX daily_prices_by_date ON daily_prices (trade_date)")

        market_ranges = {
            market: {
                "firstDate": entries[0][0].isoformat(),
                "lastDate": entries[-1][0].isoformat(),
                "files": len(entries),
            }
            for market, entries in found.items()
        }
        meta = {
            "adjustmentVersion": version,
            "adjustmentAlgorithm": ADJUSTMENT_ALGORITHM,
            "builtAt": datetime.now(UTC).isoformat(),
            "inputDir": str(input_dir),
            "files": str(files_processed),
            "rows": str(total_rows),
            "stocks": str(len(stock_ids)),
            "factorEvents": str(len(factor_rows)),
            "adjustmentBreaks": str(len(break_rows)),
            "rowsWithoutAdjusted": str(rows_without_adjusted),
            "skippedRows": str(skipped_count),
        }
        connection.executemany(
            "INSERT INTO corpus_meta VALUES (?, ?)", sorted(meta.items())
        )
        connection.commit()
    except BaseException:
        connection.close()
        building.unlink(missing_ok=True)
        raise
    connection.close()
    os.replace(building, database_path)

    return {
        "status": "COMPLETE",
        "databasePath": str(database_path),
        "adjustmentVersion": version,
        "files": files_processed,
        "rows": total_rows,
        "stocks": len(stock_ids),
        "factorEvents": len(factor_rows),
        "adjustmentBreaks": len(break_rows),
        "rowsWithoutAdjusted": rows_without_adjusted,
        "skippedRows": skipped_count,
        "skippedExamples": skipped_examples,
        "breakExamples": [
            {"stockId": stock_id, "breakDate": break_date, "reason": break_reason}
            for stock_id, break_date, break_reason in break_rows[:10]
        ],
        "marketRanges": market_ranges,
        "elapsedSeconds": round(time_module.monotonic() - started, 1),
    }
