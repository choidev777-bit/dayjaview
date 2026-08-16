"""KRX 일별매매 봉투를 전 필드 일봉 row로 해석한다 (E-16).

`packages/reference-data`는 하이픈 디렉터리라 일반 import가 안 되므로 importlib로
불러온다. 봉투 형식(dump/load_collected_snapshot)은 그 패키지 것을 그대로 쓰고,
이 parser는 기존 parse_krx_stock_daily(종가·전일대비·상장주식수만)와 달리 E-16
corpus가 요구하는 시가·고가·저가·거래량·거래대금까지 전부 읽는다.
"""

from __future__ import annotations

import json
import re
from datetime import date
from importlib import import_module
from pathlib import Path
from typing import Any

from .models import KRX_MARKETS, HistoricalDataError, KrxDailyRow, ParsedKrxDaily

_REFERENCE_PACKAGE = "packages." + "reference-data.reference_data"
_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_DATE_RE = re.compile(r"^\d{8}$")


def _reference(name: str) -> Any:
    return import_module(f"{_REFERENCE_PACKAGE}.{name}")


def load_daily_envelope(path: Path) -> Any:
    """수집 봉투 파일을 hash 검증까지 포함해 SourceSnapshot으로 읽는다."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HistoricalDataError(str(path), f"봉투 파일을 읽지 못했습니다: {exc}") from exc
    return _reference("parsers").load_collected_snapshot(payload)


def _integer(value: object, path: str, *, minimum: int | None = 0) -> int:
    text = str(value).replace(",", "").strip()
    if not _INTEGER_RE.fullmatch(text):
        raise HistoricalDataError(path, "정수 형식이 필요합니다.")
    result = int(text)
    if minimum is not None and result < minimum:
        raise HistoricalDataError(path, f"{minimum} 이상 값이 필요합니다.")
    return result


def _price_or_none(value: object, path: str) -> int | None:
    """KRX는 무거래 종목의 시·고·저가를 0으로 보낸다. 0은 '가격 없음'이다."""

    text = str(value).replace(",", "").strip()
    if text in {"", "0"}:
        return None
    return _integer(text, path, minimum=1)


def parse_daily_envelope(snapshot: Any) -> ParsedKrxDaily:
    """봉투 metadata와 raw 응답의 일치까지 검증하는 strict parser.

    종가가 없는 row(0 이하)는 가격 사실이 없으므로 corpus에 넣지 않고 종목코드만
    남긴다. 그 외의 형식 위반은 조용히 넘어가지 않고 전체를 실패시킨다.
    """

    metadata = snapshot.metadata
    if (
        metadata.provider.value != "KRX_OPEN_API"
        or metadata.dataset.value != "KRX_STOCK_DAILY"
    ):
        raise HistoricalDataError("$snapshot", "KRX 주식 일별매매 봉투가 아닙니다.")
    market, separator, source_date = metadata.source_key.partition(":")
    if market not in KRX_MARKETS or not separator:
        raise HistoricalDataError("$metadata.sourceKey", "market:거래일 형식이 아닙니다.")
    try:
        expected_date = date.fromisoformat(source_date)
    except ValueError as exc:
        raise HistoricalDataError("$metadata.sourceKey", "거래일 형식이 아닙니다.") from exc
    if expected_date != metadata.as_of.date():
        raise HistoricalDataError("$metadata.asOf", "sourceKey 거래일과 as_of가 다릅니다.")
    adapters = _reference("adapters")
    expected_endpoint = f"{adapters.KRX_BASE_URL}{adapters.KRX_MARKET_PATHS[market]}"
    if metadata.endpoint != expected_endpoint:
        raise HistoricalDataError("$metadata.endpoint", "market과 KRX endpoint가 다릅니다.")

    payload = json.loads(snapshot.raw_payload_text)
    rows_raw = payload.get("OutBlock_1")
    if not isinstance(rows_raw, list):
        raise HistoricalDataError("$.OutBlock_1", "JSON array가 필요합니다.")

    rows: list[KrxDailyRow] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(rows_raw):
        path = f"$.OutBlock_1[{index}]"
        if not isinstance(value, dict):
            raise HistoricalDataError(path, "JSON object가 필요합니다.")
        row_date = str(value.get("BAS_DD", "")).strip()
        if _DATE_RE.fullmatch(row_date):
            row_date = f"{row_date[:4]}-{row_date[4:6]}-{row_date[6:8]}"
        if row_date != expected_date.isoformat():
            raise HistoricalDataError(f"{path}.BAS_DD", "raw 거래일과 봉투 metadata가 다릅니다.")
        if str(value.get("MKT_NM", "")).strip() != market:
            raise HistoricalDataError(f"{path}.MKT_NM", "raw 시장과 봉투 metadata가 다릅니다.")
        stock_code = str(value.get("ISU_CD", "")).strip()
        if stock_code in seen:
            raise HistoricalDataError(path, "같은 종목 row가 중복되었습니다.")
        seen.add(stock_code)
        close_text = str(value.get("TDD_CLSPRC", "")).replace(",", "").strip()
        if not _INTEGER_RE.fullmatch(close_text) or int(close_text) <= 0:
            skipped.append(stock_code)
            continue
        change_text = str(value.get("CMPPREVDD_PRC", "")).strip()
        change = (
            None
            if change_text == ""
            else _integer(change_text, f"{path}.CMPPREVDD_PRC", minimum=None)
        )
        try:
            rows.append(
                KrxDailyRow(
                    stock_code=stock_code,
                    market=market,
                    trade_date=expected_date,
                    open=_price_or_none(value.get("TDD_OPNPRC", ""), f"{path}.TDD_OPNPRC"),
                    high=_price_or_none(value.get("TDD_HGPRC", ""), f"{path}.TDD_HGPRC"),
                    low=_price_or_none(value.get("TDD_LWPRC", ""), f"{path}.TDD_LWPRC"),
                    close=int(close_text),
                    change_from_previous=change,
                    volume=_integer(value.get("ACC_TRDVOL", ""), f"{path}.ACC_TRDVOL"),
                    trading_value=_integer(value.get("ACC_TRDVAL", ""), f"{path}.ACC_TRDVAL"),
                )
            )
        except ValueError as exc:
            raise HistoricalDataError(path, str(exc)) from exc
    return ParsedKrxDaily(
        market=market,
        trade_date=expected_date,
        rows=tuple(rows),
        skipped_stock_codes=tuple(skipped),
    )
