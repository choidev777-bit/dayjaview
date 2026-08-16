"""E-16 과거 일봉 corpus: parser·백필·빌더 테스트."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

import httpx
import pytest

from packages.historical_data import (
    STATUS_FILENAME,
    HistoricalDataError,
    build_daily_price_corpus,
    collect_krx_daily_history,
    envelope_path,
    load_daily_envelope,
    parse_daily_envelope,
)

KST = timezone(timedelta(hours=9))


def _reference(name: str) -> Any:
    return import_module("packages." + f"reference-data.reference_data.{name}")


def make_row(
    stock_code: str, market: str, market_date: date, **overrides: object
) -> dict[str, str]:
    row = {
        "ACC_TRDVAL": "1000000",
        "ACC_TRDVOL": "100",
        "BAS_DD": market_date.strftime("%Y%m%d"),
        "CMPPREVDD_PRC": "0",
        "FLUC_RT": "0.00",
        "ISU_CD": stock_code,
        "ISU_NM": "테스트",
        "LIST_SHRS": "1000",
        "MKTCAP": "1",
        "MKT_NM": market,
        "SECT_TP_NM": "",
        "TDD_CLSPRC": "10000",
        "TDD_HGPRC": "10000",
        "TDD_LWPRC": "10000",
        "TDD_OPNPRC": "10000",
    }
    row.update({key: str(value) for key, value in overrides.items()})
    return row


def make_snapshot(market: str, market_date: date, rows: list[dict[str, str]]) -> Any:
    adapters = _reference("adapters")
    models = _reference("models")
    hashing = _reference("hashing")
    raw_text = hashing.canonical_json({"OutBlock_1": rows})
    metadata = models.SourceMetadata(
        provider=models.SourceProvider.KRX_OPEN_API,
        dataset=models.SourceDataset.KRX_STOCK_DAILY,
        endpoint=f"{adapters.KRX_BASE_URL}{adapters.KRX_MARKET_PATHS[market]}",
        source_key=f"{market}:{market_date.isoformat()}",
        as_of=datetime.combine(market_date, time(15, 30), tzinfo=KST),
        collected_at=datetime(2026, 8, 16, 3, 0, tzinfo=UTC),
        parser_version=adapters.PARSER_VERSION,
        revision=1,
        lineage=(f"krx-open-api:{market}:{market_date.isoformat()}",),
        source_document_ids=(),
        live_validation_status=models.LiveValidationStatus.UNVERIFIED,
    )
    return models.SourceSnapshot(
        metadata=metadata,
        raw_payload_text=raw_text,
        raw_hash=hashing.sha256_text(raw_text),
    )


def write_envelope(
    output_dir: Path, market: str, market_date: date, rows: list[dict[str, str]]
) -> Path:
    parsers = _reference("parsers")
    path = envelope_path(output_dir, market, market_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            parsers.dump_collected_snapshot(make_snapshot(market, market_date, rows)),
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def make_client(respond: Any) -> tuple[httpx.Client, list[tuple[str, date]]]:
    adapters = _reference("adapters")
    market_by_path = {path: market for market, path in adapters.KRX_MARKET_PATHS.items()}
    calls: list[tuple[str, date]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        market = market_by_path[request.url.path]
        raw = request.url.params["basDd"]
        day = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        calls.append((market, day))
        result = respond(market, day)
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json={"OutBlock_1": result})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


# --- parser ---


def test_parse_reads_full_fields_and_no_trade_rows() -> None:
    day = date(2026, 8, 3)
    traded = make_row(
        "000001",
        "KOSPI",
        day,
        TDD_OPNPRC="9900",
        TDD_HGPRC="10100",
        TDD_LWPRC="9850",
        TDD_CLSPRC="10050",
        CMPPREVDD_PRC="50",
        ACC_TRDVOL="12345",
        ACC_TRDVAL="123450000",
    )
    quoted_only = make_row(
        "000002",
        "KOSPI",
        day,
        TDD_OPNPRC="0",
        TDD_HGPRC="0",
        TDD_LWPRC="0",
        TDD_CLSPRC="380",
        CMPPREVDD_PRC="-10",
        ACC_TRDVOL="0",
        ACC_TRDVAL="0",
    )
    parsed = parse_daily_envelope(make_snapshot("KOSPI", day, [traded, quoted_only]))

    assert parsed.market == "KOSPI"
    assert parsed.trade_date == day
    assert parsed.skipped_stock_codes == ()
    first, second = parsed.rows
    assert (first.open, first.high, first.low, first.close) == (9900, 10100, 9850, 10050)
    assert first.change_from_previous == 50
    assert (first.volume, first.trading_value) == (12345, 123450000)
    assert first.stock_id == "KRX:000001"
    assert (second.open, second.high, second.low) == (None, None, None)
    assert second.close == 380
    assert second.change_from_previous == -10


def test_parse_skips_rows_without_close_price() -> None:
    day = date(2026, 8, 3)
    broken = make_row("000009", "KOSPI", day, TDD_CLSPRC="0")
    parsed = parse_daily_envelope(
        make_snapshot("KOSPI", day, [make_row("000001", "KOSPI", day), broken])
    )

    assert [row.stock_code for row in parsed.rows] == ["000001"]
    assert parsed.skipped_stock_codes == ("000009",)


def test_parse_rejects_market_mismatch() -> None:
    day = date(2026, 8, 3)
    row = make_row("000001", "KOSDAQ", day)
    with pytest.raises(HistoricalDataError, match="MKT_NM"):
        parse_daily_envelope(make_snapshot("KOSPI", day, [row]))


def test_parse_rejects_trade_date_mismatch() -> None:
    day = date(2026, 8, 3)
    row = make_row("000001", "KOSPI", day, BAS_DD="20260804")
    with pytest.raises(HistoricalDataError, match="BAS_DD"):
        parse_daily_envelope(make_snapshot("KOSPI", day, [row]))


def test_parse_rejects_duplicate_stock_rows() -> None:
    day = date(2026, 8, 3)
    rows = [make_row("000001", "KOSPI", day), make_row("000001", "KOSPI", day)]
    with pytest.raises(HistoricalDataError, match="중복"):
        parse_daily_envelope(make_snapshot("KOSPI", day, rows))


# --- 백필 ---


def test_backfill_calls_weekdays_only_and_respects_konex_opening(
    tmp_path: Path,
) -> None:
    client, calls = make_client(
        lambda market, day: [make_row("000001", market, day)]
    )
    report = collect_krx_daily_history(
        api_key="krx-secret",
        output_dir=tmp_path,
        start_date=date(2013, 6, 27),
        end_date=date(2013, 7, 2),
        client=client,
        sleeper=lambda _: None,
    )

    assert report["status"] == "COMPLETE"
    assert report["callsMade"] == 10
    assert report["filesWritten"] == 10
    assert report["nextDate"] is None
    weekend = {date(2013, 6, 29), date(2013, 6, 30)}
    assert not [call for call in calls if call[1] in weekend]
    konex_days = sorted(day for market, day in calls if market == "KONEX")
    assert konex_days == [date(2013, 7, 1), date(2013, 7, 2)]

    stored = load_daily_envelope(envelope_path(tmp_path, "KOSPI", date(2013, 6, 27)))
    parsed = parse_daily_envelope(stored)
    assert parsed.rows[0].stock_code == "000001"
    assert (tmp_path / STATUS_FILENAME).is_file()
    status = json.loads((tmp_path / STATUS_FILENAME).read_text(encoding="utf-8"))
    assert status["status"] == "COMPLETE"


def test_backfill_resumes_by_skipping_existing_files(tmp_path: Path) -> None:
    def run() -> dict[str, object]:
        client, _ = make_client(lambda market, day: [make_row("000001", market, day)])
        return collect_krx_daily_history(
            api_key="krx-secret",
            output_dir=tmp_path,
            start_date=date(2005, 2, 7),
            end_date=date(2005, 2, 8),
            client=client,
            sleeper=lambda _: None,
        )

    first = run()
    second = run()

    assert first["callsMade"] == 4
    assert second["callsMade"] == 0
    assert second["filesSkipped"] == 4
    assert second["status"] == "COMPLETE"


def test_backfill_call_budget_stops_with_resume_point(tmp_path: Path) -> None:
    client, _ = make_client(lambda market, day: [make_row("000001", market, day)])
    partial = collect_krx_daily_history(
        api_key="krx-secret",
        output_dir=tmp_path,
        start_date=date(2005, 2, 7),
        end_date=date(2005, 2, 8),
        max_calls=3,
        client=client,
        sleeper=lambda _: None,
    )

    assert partial["status"] == "PARTIAL"
    assert partial["reason"] == "CALL_BUDGET_EXHAUSTED"
    assert partial["callsMade"] == 3
    assert partial["lastCompletedDate"] == "2005-02-07"
    assert partial["nextDate"] == "2005-02-08"

    client, calls = make_client(lambda market, day: [make_row("000001", market, day)])
    resumed = collect_krx_daily_history(
        api_key="krx-secret",
        output_dir=tmp_path,
        start_date=date(2005, 2, 7),
        end_date=date(2005, 2, 8),
        client=client,
        sleeper=lambda _: None,
    )

    assert resumed["status"] == "COMPLETE"
    assert resumed["callsMade"] == 1
    assert calls == [("KOSDAQ", date(2005, 2, 8))]


def test_backfill_transport_failure_retries_then_reports_partial(
    tmp_path: Path,
) -> None:
    client, calls = make_client(lambda market, day: httpx.Response(500))
    sleeps: list[float] = []
    report = collect_krx_daily_history(
        api_key="krx-secret",
        output_dir=tmp_path,
        start_date=date(2005, 2, 7),
        end_date=date(2005, 2, 7),
        client=client,
        sleeper=sleeps.append,
    )

    assert report["status"] == "PARTIAL"
    assert report["reason"] == "TRANSPORT_FAILURE"
    assert report["callsMade"] == 0
    assert report["filesWritten"] == 0
    failure = report["failure"]
    assert isinstance(failure, dict)
    assert failure["market"] == "KOSPI"
    assert failure["date"] == "2005-02-07"
    assert len(calls) == 4
    assert sleeps == [5.0, 30.0, 120.0]


def test_backfill_reports_running_until_finalized(tmp_path: Path) -> None:
    client, _ = make_client(lambda market, day: [make_row("000001", market, day)])
    interim: list[object] = []
    report = collect_krx_daily_history(
        api_key="krx-secret",
        output_dir=tmp_path,
        start_date=date(2005, 2, 7),
        end_date=date(2005, 2, 8),
        client=client,
        sleeper=lambda _: None,
        status_every_calls=1,
        progress=lambda payload: interim.append(payload["status"]),
    )

    assert interim == ["RUNNING"] * 4
    assert report["status"] == "COMPLETE"
    status = json.loads((tmp_path / STATUS_FILENAME).read_text(encoding="utf-8"))
    assert status["status"] == "COMPLETE"
    assert not list(tmp_path.rglob("*.tmp"))


def test_backfill_stops_when_source_has_no_data_for_the_era(tmp_path: Path) -> None:
    client, _ = make_client(lambda market, day: [])
    report = collect_krx_daily_history(
        api_key="krx-secret",
        output_dir=tmp_path,
        start_date=date(2005, 2, 1),
        end_date=date(2005, 2, 25),
        client=client,
        sleeper=lambda _: None,
    )

    assert report["status"] == "FAILED"
    assert report["reason"] == "DATA_UNAVAILABLE"
    failure = report["failure"]
    assert isinstance(failure, dict)
    assert failure["consecutiveEmptyWeekdays"] == 15
    assert failure["market"] == "KOSPI"


# --- corpus 빌드 ---


def _week_days() -> list[date]:
    return [date(2026, 8, 3) + timedelta(days=offset) for offset in range(4)]


def _query(database: Path, sql: str, *params: object) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return [tuple(row) for row in connection.execute(sql, params)]
    finally:
        connection.close()


def test_corpus_keeps_raw_prices_and_stamps_version(tmp_path: Path) -> None:
    input_dir = tmp_path / "krx-daily"
    days = _week_days()
    closes = [10000, 10100, 10050, 10200]
    changes = ["0", "100", "-50", "150"]
    for day, close, change in zip(days, closes, changes):
        write_envelope(
            input_dir,
            "KOSPI",
            day,
            [
                make_row(
                    "000001",
                    "KOSPI",
                    day,
                    TDD_CLSPRC=close,
                    CMPPREVDD_PRC=change,
                    TDD_OPNPRC=close - 10,
                    TDD_HGPRC=close + 20,
                    TDD_LWPRC=close - 30,
                )
            ],
        )
    database = tmp_path / "corpus.sqlite"
    report = build_daily_price_corpus(input_dir=input_dir, database_path=database)

    assert report["status"] == "COMPLETE"
    assert report["rows"] == 4
    assert report["factorEvents"] == 0
    assert report["adjustmentBreaks"] == 0
    version = f"krx-cmpprevdd-1@{days[-1].isoformat()}"
    assert report["adjustmentVersion"] == version
    rows = _query(
        database,
        "SELECT trade_date, open, close, adjusted_open, adjusted_close,"
        " adjustment_version FROM daily_prices WHERE stock_id = ? ORDER BY trade_date",
        "KRX:000001",
    )
    assert rows[0] == (
        "2026-08-03",
        9990,
        10000,
        "9990.000000",
        "10000.000000",
        version,
    )
    assert rows[3][3:5] == ("10190.000000", "10200.000000")
    market_days = _query(database, "SELECT market, row_count FROM market_days")
    assert ("KOSPI", 1) in market_days


def test_corpus_applies_split_factor_to_prior_rows(tmp_path: Path) -> None:
    input_dir = tmp_path / "krx-daily"
    days = _week_days()
    # 3일째 5:1 액면분할: 직전 종가 100,000 → 그날 기준가 20,000.
    specs = [
        ("100000", "0", "99000"),
        ("100000", "0", "100500"),
        ("20000", "0", "20100"),
        ("21000", "1000", "20900"),
    ]
    for day, (close, change, high) in zip(days, specs):
        write_envelope(
            input_dir,
            "KOSPI",
            day,
            [
                make_row(
                    "000001",
                    "KOSPI",
                    day,
                    TDD_CLSPRC=close,
                    CMPPREVDD_PRC=change,
                    TDD_OPNPRC=close,
                    TDD_HGPRC=high,
                    TDD_LWPRC=close,
                )
            ],
        )
    database = tmp_path / "corpus.sqlite"
    report = build_daily_price_corpus(input_dir=input_dir, database_path=database)

    assert report["factorEvents"] == 1
    factors = _query(
        database,
        "SELECT effective_date, factor_numerator, factor_denominator,"
        " previous_close, base_price FROM adjustment_factors",
    )
    assert factors == [("2026-08-05", 1, 5, 100000, 20000)]
    rows = _query(
        database,
        "SELECT trade_date, adjusted_close, adjusted_high FROM daily_prices"
        " WHERE stock_id = ? ORDER BY trade_date",
        "KRX:000001",
    )
    assert rows[0] == ("2026-08-03", "20000.000000", "19800.000000")
    assert rows[1] == ("2026-08-04", "20000.000000", "20100.000000")
    assert rows[2] == ("2026-08-05", "20000.000000", "20100.000000")
    assert rows[3] == ("2026-08-06", "21000.000000", "20900.000000")


def test_corpus_rounds_adjusted_prices_to_six_places(tmp_path: Path) -> None:
    input_dir = tmp_path / "krx-daily"
    days = _week_days()[:2]
    write_envelope(
        input_dir,
        "KOSPI",
        days[0],
        [
            make_row(
                "000001",
                "KOSPI",
                days[0],
                TDD_CLSPRC="3000",
                TDD_OPNPRC="10000",
                TDD_HGPRC="10000",
                TDD_LWPRC="2999",
                CMPPREVDD_PRC="0",
            )
        ],
    )
    write_envelope(
        input_dir,
        "KOSPI",
        days[1],
        [
            make_row(
                "000001",
                "KOSPI",
                days[1],
                TDD_CLSPRC="1000",
                TDD_OPNPRC="1000",
                TDD_HGPRC="1000",
                TDD_LWPRC="1000",
                CMPPREVDD_PRC="0",
            )
        ],
    )
    database = tmp_path / "corpus.sqlite"
    build_daily_price_corpus(input_dir=input_dir, database_path=database)

    rows = _query(
        database,
        "SELECT adjusted_open, adjusted_low FROM daily_prices"
        " WHERE stock_id = ? AND trade_date = ?",
        "KRX:000001",
        "2026-08-03",
    )
    assert rows == [("3333.333333", "999.666667")]


def test_corpus_preserves_delisted_stock_rows(tmp_path: Path) -> None:
    input_dir = tmp_path / "krx-daily"
    days = _week_days()
    for index, day in enumerate(days):
        rows = [
            make_row(
                "000001",
                "KOSPI",
                day,
                TDD_CLSPRC="10000",
                CMPPREVDD_PRC="0",
            )
        ]
        # 000002는 둘째 날까지만 존재한다(상장폐지). row는 corpus에 남아야 한다.
        if index < 2:
            rows.append(
                make_row(
                    "000002",
                    "KOSPI",
                    day,
                    TDD_CLSPRC="500",
                    CMPPREVDD_PRC="0",
                )
            )
        write_envelope(input_dir, "KOSPI", day, rows)
    database = tmp_path / "corpus.sqlite"
    report = build_daily_price_corpus(input_dir=input_dir, database_path=database)

    assert report["stocks"] == 2
    delisted = _query(
        database,
        "SELECT trade_date, adjusted_close FROM daily_prices"
        " WHERE stock_id = ? ORDER BY trade_date",
        "KRX:000002",
    )
    assert delisted == [
        ("2026-08-03", "500.000000"),
        ("2026-08-04", "500.000000"),
    ]


def test_corpus_chain_break_leaves_prior_rows_unadjusted(tmp_path: Path) -> None:
    input_dir = tmp_path / "krx-daily"
    days = _week_days()
    specs = [("10000", "0"), ("10100", "100"), ("20000", ""), ("20500", "500")]
    for day, (close, change) in zip(days, specs):
        write_envelope(
            input_dir,
            "KOSPI",
            day,
            [
                make_row(
                    "000001",
                    "KOSPI",
                    day,
                    TDD_CLSPRC=close,
                    CMPPREVDD_PRC=change,
                    TDD_OPNPRC=close,
                    TDD_HGPRC=close,
                    TDD_LWPRC=close,
                )
            ],
        )
    database = tmp_path / "corpus.sqlite"
    report = build_daily_price_corpus(input_dir=input_dir, database_path=database)

    assert report["adjustmentBreaks"] == 1
    assert report["rowsWithoutAdjusted"] == 2
    breaks = _query(database, "SELECT break_date, reason FROM adjustment_breaks")
    assert breaks == [("2026-08-05", "NO_CHANGE_FIELD")]
    rows = _query(
        database,
        "SELECT trade_date, adjusted_close FROM daily_prices"
        " WHERE stock_id = ? ORDER BY trade_date",
        "KRX:000001",
    )
    assert rows == [
        ("2026-08-03", None),
        ("2026-08-04", None),
        ("2026-08-05", "20000.000000"),
        ("2026-08-06", "20500.000000"),
    ]


def test_corpus_rejects_out_of_range_factor_as_break(tmp_path: Path) -> None:
    input_dir = tmp_path / "krx-daily"
    days = _week_days()[:2]
    write_envelope(
        input_dir,
        "KOSPI",
        days[0],
        [make_row("000001", "KOSPI", days[0], TDD_CLSPRC="10", CMPPREVDD_PRC="0")],
    )
    # 기준가 100,000 = 직전 종가 10의 10,000배 — 실존 기업행위 범위 밖.
    write_envelope(
        input_dir,
        "KOSPI",
        days[1],
        [
            make_row(
                "000001",
                "KOSPI",
                days[1],
                TDD_CLSPRC="100000",
                CMPPREVDD_PRC="0",
            )
        ],
    )
    database = tmp_path / "corpus.sqlite"
    report = build_daily_price_corpus(input_dir=input_dir, database_path=database)

    assert report["factorEvents"] == 0
    assert report["adjustmentBreaks"] == 1
    breaks = _query(database, "SELECT reason FROM adjustment_breaks")
    assert breaks == [("FACTOR_OUT_OF_RANGE",)]
    rows = _query(
        database,
        "SELECT trade_date, adjusted_close FROM daily_prices ORDER BY trade_date",
    )
    assert rows == [("2026-08-03", None), ("2026-08-04", "100000.000000")]


def test_corpus_requires_every_weekday_in_range(tmp_path: Path) -> None:
    input_dir = tmp_path / "krx-daily"
    days = _week_days()
    for day in (days[0], days[2], days[3]):
        write_envelope(
            input_dir,
            "KOSPI",
            day,
            [make_row("000001", "KOSPI", day, CMPPREVDD_PRC="0")],
        )
    database = tmp_path / "corpus.sqlite"
    with pytest.raises(HistoricalDataError, match="2026-08-04"):
        build_daily_price_corpus(input_dir=input_dir, database_path=database)
    assert not database.exists()
    assert not database.with_name(database.name + ".building").exists()


def test_corpus_rejects_same_stock_in_two_markets_on_one_day(tmp_path: Path) -> None:
    input_dir = tmp_path / "krx-daily"
    day = _week_days()[0]
    write_envelope(
        input_dir, "KOSPI", day, [make_row("000001", "KOSPI", day, CMPPREVDD_PRC="0")]
    )
    write_envelope(
        input_dir, "KOSDAQ", day, [make_row("000001", "KOSDAQ", day, CMPPREVDD_PRC="0")]
    )
    database = tmp_path / "corpus.sqlite"
    with pytest.raises(HistoricalDataError, match="이미 있습니다"):
        build_daily_price_corpus(input_dir=input_dir, database_path=database)


def test_corpus_rebuild_is_deterministic(tmp_path: Path) -> None:
    input_dir = tmp_path / "krx-daily"
    days = _week_days()
    specs = [("100000", "0"), ("100000", "0"), ("20000", "0"), ("21000", "1000")]
    for day, (close, change) in zip(days, specs):
        write_envelope(
            input_dir,
            "KOSPI",
            day,
            [
                make_row(
                    "000001",
                    "KOSPI",
                    day,
                    TDD_CLSPRC=close,
                    CMPPREVDD_PRC=change,
                )
            ],
        )
    first_db = tmp_path / "first.sqlite"
    second_db = tmp_path / "second.sqlite"
    build_daily_price_corpus(input_dir=input_dir, database_path=first_db)
    build_daily_price_corpus(input_dir=input_dir, database_path=second_db)

    select_prices = "SELECT * FROM daily_prices ORDER BY stock_id, trade_date"
    select_factors = "SELECT * FROM adjustment_factors ORDER BY stock_id, effective_date"
    assert _query(first_db, select_prices) == _query(second_db, select_prices)
    assert _query(first_db, select_factors) == _query(second_db, select_factors)
