from __future__ import annotations

import argparse
import io
import json
import zipfile
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import aware

CORP_CODE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list><corp_code>00000001</corp_code><corp_name>예시전자</corp_name><stock_code>A00001</stock_code><modify_date>20260801</modify_date></list>
  <list><corp_code>00000002</corp_code><corp_name>비상장사</corp_name><stock_code> </stock_code><modify_date>20260801</modify_date></list>
</result>
"""

KRX_ROW = {
    "BAS_DD": "20260813",
    "ISU_CD": "A00001",
    "ISU_NM": "예시전자",
    "MKT_NM": "KOSPI",
    "SECT_TP_NM": "주권",
    "TDD_CLSPRC": "51,000",
    "CMPPREVDD_PRC": "1,000",
    "FLUC_RT": "2.00",
    "TDD_OPNPRC": "50,100",
    "TDD_HGPRC": "52,000",
    "TDD_LWPRC": "49,900",
    "ACC_TRDVOL": "1,000,000",
    "ACC_TRDVAL": "51,000,000,000",
    "MKTCAP": "5,100,000,000,000",
    "LIST_SHRS": "100,000,000",
}


def _dart_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "rcept_no": "20260814000001",
        "corp_code": "00000001",
        "corp_name": "예시전자",
        "stlm_dt": "2026-06-30",
    }
    row.update(overrides)
    return row


# 실제 응답 모양. stlm_dt가 있어야 저장된 as_of가 결산기준일인지 검증할 수 있고,
# 자기주식 '-'와 최대주주 '계' 합계 row도 실제로 오는 값이다.
_DART_ROWS: dict[str, list[dict[str, Any]]] = {
    "stockTotqySttus.json": [
        _dart_row(se="보통주", istc_totqy="100,000,000", tesstk_co="-"),
        _dart_row(se="합계", istc_totqy="100,000,000", tesstk_co="-"),
    ],
    "hyslrSttus.json": [
        _dart_row(
            nm="예시홀딩스",
            relate="최대주주 본인",
            stock_knd="보통주",
            trmend_posesn_stock_co="25,000,000",
        ),
        _dart_row(
            nm="계",
            relate=None,
            stock_knd="보통주",
            trmend_posesn_stock_co="25,000,000",
        ),
    ],
    "tesstkAcqsDspsSttus.json": [
        _dart_row(stock_knd="보통주", acqs_mth3="총계", trmend_qy="-"),
    ],
}


def _corp_code_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", CORP_CODE_XML)
    return buffer.getvalue()


def _worker() -> Any:
    return import_module("apps.worker-batch.reference-data.collect_daily")


def _reference_modules() -> dict[str, Any]:
    prefix = "packages." + "reference-data.reference_data"
    return {
        name: import_module(f"{prefix}.{name}")
        for name in ("parsers", "models", "hashing")
    }


def _arguments(output_dir: Path, **overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "market_date": date(2026, 8, 13),
        "output_dir": output_dir,
        "stock_codes_file": None,
        "business_year": 2026,
        "report_code": "11012",
        "calendar_lookback_days": 1,
        "limit": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_corp_code_index_maps_listed_stocks_only(modules: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["crtfc_key"] == "dart-secret"
        return httpx.Response(200, content=_corp_code_zip(), request=request)

    adapter = modules["adapters"].OpenDartAdapter(
        api_key="dart-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    snapshot = adapter.fetch_corp_code_index(
        as_of=aware("2026-08-14T08:00:00+09:00"),
        collected_at=aware("2026-08-14T08:00:00+09:00"),
    )
    index = modules["parsers"].parse_corp_code_index(snapshot)

    assert index == {"A00001": "00000001"}
    assert "dart-secret" not in snapshot.raw_payload_text
    assert snapshot.metadata.dataset.value == "OPENDART_CORP_CODE"


def test_corp_code_response_without_expected_entry_fails_closed(
    modules: dict[str, Any],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-a-zip", request=request)

    adapter = modules["adapters"].OpenDartAdapter(
        api_key="dart-secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(modules["errors"].SourceContractError):
        adapter.fetch_corp_code_index(
            as_of=aware("2026-08-14T08:00:00+09:00"),
            collected_at=aware("2026-08-14T08:00:00+09:00"),
        )


def test_trading_calendar_is_derived_from_krx_response_presence(
    modules: dict[str, Any], load_fixture
) -> None:
    from dataclasses import replace

    traded = load_fixture("krx-stock-daily.json")
    closed = modules["models"].SourceSnapshot(
        metadata=replace(
            traded.metadata,
            source_key="KOSPI:2026-08-15",
            as_of=aware("2026-08-15T15:30:00+09:00"),
            collected_at=aware("2026-08-15T18:00:00+09:00"),
            lineage=("krx-open-api:KOSPI:2026-08-15",),
        ),
        raw_payload_text='{"OutBlock_1":[]}',
        raw_hash=modules["hashing"].sha256_text('{"OutBlock_1":[]}'),
    )

    calendar = modules["parsers"].derive_trading_calendar(
        (traded, closed),
        version="krx-calendar-test",
    )

    assert [(item.market_date, item.is_trading_day) for item in calendar] == [
        (date(2026, 8, 13), True),
        (date(2026, 8, 15), False),
    ]
    assert calendar[1].session_open is None
    assert {item.metadata.dataset.value for item in calendar} == {
        "KRX_CALENDAR_DERIVED"
    }


def test_settlement_stamp_follows_the_response_not_the_report_code() -> None:
    """3월·6월 결산 회사는 같은 보고서코드라도 stlm_dt가 다르다."""

    modules = _reference_modules()
    payload = {
        "status": "000",
        "list": [_dart_row(se="보통주", istc_totqy="1,000", tesstk_co="-", stlm_dt="2026-03-31")],
    }
    raw_text = modules["hashing"].canonical_json(payload)
    models = modules["models"]
    # 보고서코드 11012로 계산한 결산기준일(6/30)로 일단 저장된 snapshot.
    assumed = models.SourceSnapshot(
        metadata=models.SourceMetadata(
            provider=models.SourceProvider.OPENDART,
            dataset=models.SourceDataset.OPENDART_STOCK_TOTAL,
            endpoint="https://opendart.fss.or.kr/api/stockTotqySttus.json",
            source_key="00000001:2026:11012",
            as_of=aware("2026-06-30T00:00:00+00:00"),
            collected_at=aware("2026-08-15T09:00:00+09:00"),
            parser_version="reference-source-2026.08.1",
            revision=1,
            lineage=("opendart:stock-total:00000001:2026:11012",),
            source_document_ids=("20260814000001",),
        ),
        raw_payload_text=raw_text,
        raw_hash=modules["hashing"].sha256_text(raw_text),
    )

    stamped = _worker()._stamp_settlement(assumed)

    assert stamped.metadata.as_of.date() == date(2026, 3, 31)
    assert stamped.raw_hash == assumed.raw_hash
    normalized = modules["parsers"].parse_open_dart(stamped, stock_code="A00001")
    assert normalized.issued_share_observations[0].effective_on == date(2026, 3, 31)


def test_collect_daily_stops_before_transport_without_keys(tmp_path: Path) -> None:
    result = _worker().collect(_arguments(tmp_path), environment={})

    assert result["status"] == "BLOCKED"
    assert result["blocker"] == "B-REFDATA-KEYS"
    assert result["missingCredentials"] == ["KRX_API_KEY", "OPENDART_API_KEY"]
    assert result["liveRequestAttempted"] is False
    assert list(tmp_path.iterdir()) == []


def test_collect_daily_stores_raw_snapshots_and_resumes(tmp_path: Path) -> None:
    krx_calls: list[str] = []
    dart_calls: list[str] = []

    def krx_handler(request: httpx.Request) -> httpx.Response:
        krx_calls.append(str(request.url))
        traded = (
            request.url.params["basDd"] == "20260813"
            and request.url.path.endswith("stk_bydd_trd")
        )
        return httpx.Response(
            200,
            json={"OutBlock_1": [KRX_ROW] if traded else []},
            request=request,
        )

    def dart_handler(request: httpx.Request) -> httpx.Response:
        dart_calls.append(str(request.url.path))
        if request.url.path.endswith("corpCode.xml"):
            return httpx.Response(200, content=_corp_code_zip(), request=request)
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": _DART_ROWS[request.url.path.rsplit("/", 1)[-1]],
            },
            request=request,
        )

    environment = {"KRX_API_KEY": "krx-secret", "OPENDART_API_KEY": "dart-secret"}
    worker = _worker()
    first = worker.collect(
        _arguments(tmp_path),
        environment=environment,
        krx_client=httpx.Client(transport=httpx.MockTransport(krx_handler)),
        dart_client=httpx.Client(transport=httpx.MockTransport(dart_handler)),
        now=aware("2026-08-14T08:00:00+09:00"),
    )

    assert first["status"] == "COMPLETE"
    # 2일 × 3시장 KRX + 대조표 1 + 종목 1 × 보고서 3
    assert first["krxRequests"] == 6
    assert first["openDartRequests"] == 3
    assert first["tradingDays"] == 1
    assert first["collectedStocks"] == 1
    assert first["unmappedStockCodes"] == []
    stored = sorted(path.name for path in tmp_path.glob("*.json"))
    assert "KRX_STOCK_DAILY.KOSPI_2026-08-13.json" in stored
    assert "OPENDART_CORP_CODE.corp-code_2026-08-14.json" in stored
    body = json.loads(
        (tmp_path / "KRX_STOCK_DAILY.KOSPI_2026-08-13.json").read_text(encoding="utf-8")
    )
    assert "krx-secret" not in json.dumps(body, ensure_ascii=False)
    assert json.loads(body["rawPayloadText"])["OutBlock_1"][0]["ISU_CD"] == "A00001"

    # 정기보고서는 수집 시각이 아니라 결산기준일을 as_of로 저장해야 다시 읽힌다.
    modules = _reference_modules()
    stored_total = modules["parsers"].load_collected_snapshot(
        json.loads(
            (tmp_path / "OPENDART_STOCK_TOTAL.00000001_2026_11012.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert stored_total.metadata.as_of.date() == date(2026, 6, 30)
    normalized = modules["parsers"].parse_open_dart(stored_total, stock_code="A00001")
    assert normalized.issued_share_observations[0].value == 100_000_000

    krx_calls.clear()
    dart_calls.clear()
    second = worker.collect(
        _arguments(tmp_path),
        environment=environment,
        krx_client=httpx.Client(transport=httpx.MockTransport(krx_handler)),
        dart_client=httpx.Client(transport=httpx.MockTransport(dart_handler)),
        now=aware("2026-08-14T08:00:00+09:00"),
    )

    # 이미 적재된 원문은 다시 부르지 않는다.
    assert second["krxRequests"] == 0
    assert second["openDartRequests"] == 0
    assert krx_calls == [] and dart_calls == []


def test_collect_daily_runs_at_kst_midnight_rollover(tmp_path: Path) -> None:
    """거래일이 넘어가는 KST 00:00에 그날치를 수집해도 as_of가 미래가 아니다.

    TradingDayLoop은 KST 자정에 그날 세션을 세운다. 그 시각 UTC는 아직 전날
    15:00이라, 거래일(KST 날짜)의 자정을 UTC로 붙이면 as_of가 collected_at보다
    9시간 늦어 SourceMetadata 검증에 걸렸다.
    """

    def krx_handler(request: httpx.Request) -> httpx.Response:
        traded = (
            request.url.params["basDd"] == "20260813"
            and request.url.path.endswith("stk_bydd_trd")
        )
        return httpx.Response(
            200,
            json={"OutBlock_1": [KRX_ROW] if traded else []},
            request=request,
        )

    def dart_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("corpCode.xml"):
            return httpx.Response(200, content=_corp_code_zip(), request=request)
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": _DART_ROWS[request.url.path.rsplit("/", 1)[-1]],
            },
            request=request,
        )

    result = _worker().collect(
        _arguments(tmp_path, market_date=date(2026, 8, 14)),
        environment={"KRX_API_KEY": "krx-secret", "OPENDART_API_KEY": "dart-secret"},
        krx_client=httpx.Client(transport=httpx.MockTransport(krx_handler)),
        dart_client=httpx.Client(transport=httpx.MockTransport(dart_handler)),
        now=aware("2026-08-14T00:00:00+09:00"),
    )

    assert result["status"] == "COMPLETE"
    # 자정에는 그날·전날의 빈 응답이 휴장인지 미발행인지 알 수 없어 저장하지
    # 않는다. 저장하면 달력이 전일을 휴장으로 오판하고 잔해가 재수집을 막는다.
    assert not (tmp_path / "KRX_STOCK_DAILY.KOSPI_2026-08-14.json").is_file()
    stored = _reference_modules()["parsers"].load_collected_snapshot(
        json.loads(
            (tmp_path / "KRX_STOCK_DAILY.KOSPI_2026-08-13.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert stored.metadata.as_of.date() == date(2026, 8, 13)
    assert stored.metadata.as_of <= stored.metadata.collected_at


def test_collect_daily_replaces_a_stale_empty_previous_day(tmp_path: Path) -> None:
    """미발행 시점에 저장된 빈 전일 응답은 잔해로 보고 다시 받는다.

    2026-08-20 운영: 자정 잔해의 빈 전일 파일 때문에 재수집이 전일 종가를
    영영 확보하지 못했다.
    """

    published: list[str] = []

    def krx_handler(request: httpx.Request) -> httpx.Response:
        traded = (
            request.url.params["basDd"] == "20260813"
            and request.url.path.endswith("stk_bydd_trd")
        )
        if traded:
            published.append(request.url.params["basDd"])
        return httpx.Response(
            200,
            json={"OutBlock_1": [KRX_ROW] if traded else []},
            request=request,
        )

    def dart_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("corpCode.xml"):
            return httpx.Response(200, content=_corp_code_zip(), request=request)
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": _DART_ROWS[request.url.path.rsplit("/", 1)[-1]],
            },
            request=request,
        )

    environment = {"KRX_API_KEY": "krx-secret", "OPENDART_API_KEY": "dart-secret"}
    worker = _worker()
    # 자정 실행: 08-13 데이터가 아직 KRX에 없던 시각을 흉내 내 빈 응답만 온다.
    def empty_krx_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"OutBlock_1": []}, request=request)

    midnight = worker.collect(
        _arguments(tmp_path, market_date=date(2026, 8, 14)),
        environment=environment,
        krx_client=httpx.Client(transport=httpx.MockTransport(empty_krx_handler)),
        dart_client=httpx.Client(transport=httpx.MockTransport(dart_handler)),
        now=aware("2026-08-14T00:00:00+09:00"),
    )
    assert midnight["status"] == "COMPLETE"
    assert not (tmp_path / "KRX_STOCK_DAILY.KOSPI_2026-08-13.json").is_file()

    # 아침 재시도: 이제 08-13 데이터가 나왔고, 잔해 없이 새로 받아 저장한다.
    morning = worker.collect(
        _arguments(tmp_path, market_date=date(2026, 8, 14)),
        environment=environment,
        krx_client=httpx.Client(transport=httpx.MockTransport(krx_handler)),
        dart_client=httpx.Client(transport=httpx.MockTransport(dart_handler)),
        now=aware("2026-08-14T06:10:00+09:00"),
    )
    assert morning["status"] == "COMPLETE"
    assert published == ["20260813"]
    body = json.loads(
        (tmp_path / "KRX_STOCK_DAILY.KOSPI_2026-08-13.json").read_text(
            encoding="utf-8"
        )
    )
    assert json.loads(body["rawPayloadText"])["OutBlock_1"][0]["ISU_CD"] == "A00001"
