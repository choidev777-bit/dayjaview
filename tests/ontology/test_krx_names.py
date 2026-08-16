"""KRX 일별매매에서 뽑은 종목명 이력 (회사 온톨로지 단계 2)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from packages.infostock.hashing import sha256_text
from packages.ontology.krx_names import (
    KRX_NAME_INDEX_VERSION,
    load_name_index,
    name_index_payload,
    scan_krx_name_windows,
)

ENDPOINTS = {
    "KOSPI": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
    "KOSDAQ": "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd",
}


def _envelope(tmp_path: Path, market: str, day: date, rows: list[dict[str, str]]) -> Path:
    raw = json.dumps(
        {
            "OutBlock_1": [
                {
                    "BAS_DD": day.strftime("%Y%m%d"),
                    "MKT_NM": market,
                    "ISU_CD": row["code"],
                    "ISU_NM": row["name"],
                    "TDD_CLSPRC": "1000",
                }
                for row in rows
            ]
        },
        ensure_ascii=False,
    )
    payload = {
        "asOf": f"{day.isoformat()}T15:30:00+09:00",
        "collectedAt": f"{day.isoformat()}T18:00:00+09:00",
        "dataset": "KRX_STOCK_DAILY",
        "endpoint": ENDPOINTS[market],
        "envelopeVersion": "reference-collection-2026.08.1",
        "lineage": [f"krx-open-api:{market}:{day.isoformat()}"],
        "liveValidationStatus": "UNVERIFIED",
        "parserVersion": "reference-source-2026.08.1",
        "provider": "KRX_OPEN_API",
        "rawHash": sha256_text(raw),
        "rawPayloadText": raw,
        "revision": 1,
        "sourceDocumentIds": [],
        "sourceKey": f"{market}:{day.isoformat()}",
    }
    path = tmp_path / f"KRX_STOCK_DAILY.{market}_{day.isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _index(tmp_path: Path):  # type: ignore[no-untyped-def]
    paths = [
        _envelope(
            tmp_path,
            "KOSPI",
            date(2015, 7, 13),
            [{"code": "012450", "name": "삼성테크윈"}, {"code": "005930", "name": "삼성전자"}],
        ),
        _envelope(
            tmp_path,
            "KOSPI",
            date(2015, 7, 14),
            [{"code": "012450", "name": "한화테크윈"}, {"code": "005930", "name": "삼성전자"}],
        ),
        _envelope(
            tmp_path,
            "KOSPI",
            date(2015, 7, 15),
            [{"code": "012450", "name": "한화테크윈"}, {"code": "005930", "name": "삼성전자"}],
        ),
        _envelope(
            tmp_path, "KOSDAQ", date(2015, 7, 14), [{"code": "087730", "name": "네패스신소재"}]
        ),
    ]
    return scan_krx_name_windows(paths)


def test_scan_makes_one_window_for_each_name_with_its_trading_days(tmp_path: Path) -> None:
    index = _index(tmp_path)

    windows = {
        (window.stock_code, window.name): window for window in index.windows
    }
    old = windows[("012450", "삼성테크윈")]
    new = windows[("012450", "한화테크윈")]
    assert (old.first_date, old.last_date, old.day_count) == (
        date(2015, 7, 13),
        date(2015, 7, 13),
        1,
    )
    assert (new.first_date, new.last_date, new.day_count) == (
        date(2015, 7, 14),
        date(2015, 7, 15),
        2,
    )
    assert index.market_last_dates == {
        "KOSDAQ": date(2015, 7, 14),
        "KOSPI": date(2015, 7, 15),
    }


def test_only_the_name_alive_on_the_last_trading_day_stays_open(tmp_path: Path) -> None:
    index = _index(tmp_path)
    open_names = {window.name for window in index.windows if index.is_open(window)}

    assert open_names == {"한화테크윈", "삼성전자", "네패스신소재"}
    # 시장마다 마지막 거래일이 다르므로 KOSDAQ은 제 시장 기준으로 본다.
    kosdaq = next(window for window in index.windows if window.stock_code == "087730")
    assert kosdaq.market == "KOSDAQ"


def test_by_code_orders_windows_by_when_the_name_started(tmp_path: Path) -> None:
    grouped = _index(tmp_path).by_code()

    assert [window.name for window in grouped["012450"]] == ["삼성테크윈", "한화테크윈"]


def test_payload_round_trip_keeps_every_window(tmp_path: Path) -> None:
    index = _index(tmp_path)
    payload = name_index_payload(index)

    assert payload["indexVersion"] == KRX_NAME_INDEX_VERSION
    restored = load_name_index(json.loads(json.dumps(payload)))
    assert restored == index


def test_unknown_index_version_is_refused() -> None:
    with pytest.raises(ValueError):
        load_name_index({"indexVersion": "krx-name-windows/0.0.1", "windows": []})
