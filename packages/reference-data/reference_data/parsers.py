"""공식 필드명을 보존하는 strict fixture/source parser와 normalizer."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Never, cast
from xml.etree import ElementTree

from .adapters import (
    KRX_BASE_URL,
    KRX_MARKET_PATHS,
    OPENDART_BASE_URL,
    OPENDART_CORP_CODE_PATH,
    OPENDART_PATHS,
)
from .errors import SourceContractError
from .hashing import canonical_json, parse_json_object, sha256_text
from .models import (
    STOCK_CODE_RE,
    CoverageDeclarationStatus,
    DailyPriceObservation,
    EconomicField,
    FieldObservation,
    HoldingCoverageDeclaration,
    LiveValidationStatus,
    NonFloatCategory,
    NonFloatHolding,
    ShareClass,
    SourceDataset,
    SourceMetadata,
    SourceProvider,
    SourceSnapshot,
    TradingDayObservation,
)

INTEGER_RE = re.compile(r"^[+-]?\d+$")


@dataclass(frozen=True, slots=True)
class OpenDartNormalization:
    issued_share_observations: tuple[FieldObservation, ...] = ()
    non_float_holdings: tuple[NonFloatHolding, ...] = ()
    coverage_declarations: tuple[HoldingCoverageDeclaration, ...] = ()


def _fail(code: str, path: str, detail: str) -> Never:
    raise SourceContractError(code, path, detail)


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("MALFORMED_SOURCE_RESPONSE", path, "JSON object가 필요합니다.")
    return cast(dict[str, Any], value)


def _sequence(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail("MALFORMED_SOURCE_RESPONSE", path, "JSON array가 필요합니다.")
    return cast(list[Any], value)


def _text(value: object, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail("MALFORMED_SOURCE_RESPONSE", path, "문자열이 필요합니다.")
    result = value.strip()
    if not result and not allow_empty:
        _fail("MALFORMED_SOURCE_RESPONSE", path, "빈 문자열은 허용되지 않습니다.")
    return result


def _datetime(value: object, path: str) -> datetime:
    text = _text(value, path)
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        _fail("MALFORMED_SOURCE_RESPONSE", path, "ISO 8601 시각이 필요합니다.")
    if result.tzinfo is None or result.utcoffset() is None:
        _fail("MALFORMED_SOURCE_RESPONSE", path, "timezone이 있는 시각이 필요합니다.")
    return result


def _date(value: object, path: str) -> date:
    text = _text(value, path)
    normalized = (
        f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        if re.fullmatch(r"\d{8}", text)
        else text
    )
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        _fail("MALFORMED_SOURCE_RESPONSE", path, "YYYY-MM-DD 또는 YYYYMMDD 날짜가 필요합니다.")


def _integer(value: object, path: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        _fail("MALFORMED_SOURCE_RESPONSE", path, "정수 주식수가 필요합니다.")
    text = str(value).replace(",", "").strip()
    if not INTEGER_RE.fullmatch(text):
        _fail("MALFORMED_SOURCE_RESPONSE", path, "정수 형식이 필요합니다.")
    result = int(text)
    if result < 0 or (result == 0 and not allow_zero):
        requirement = "0 이상" if allow_zero else "0 초과"
        _fail("MALFORMED_SOURCE_RESPONSE", path, f"{requirement} 값이 필요합니다.")
    return result


def _treasury_shares(value: object, path: str) -> int:
    """OpenDART 정기보고서는 자기주식 0주를 숫자가 아니라 '-'로 렌더링해 보낸다."""

    return 0 if str(value).strip() == "-" else _integer(value, path, allow_zero=True)


def _decimal(value: object, path: str) -> Decimal:
    text = str(value).replace(",", "").strip()
    try:
        result = Decimal(text)
    except InvalidOperation:
        _fail("MALFORMED_SOURCE_RESPONSE", path, "유한 decimal 형식이 필요합니다.")
    if not result.is_finite():
        _fail("MALFORMED_SOURCE_RESPONSE", path, "유한 decimal 형식이 필요합니다.")
    return result


def _allowed_endpoint(provider: SourceProvider, dataset: SourceDataset) -> str:
    if provider is SourceProvider.KRX_OPEN_API and dataset is SourceDataset.KRX_STOCK_DAILY:
        return KRX_BASE_URL
    if provider is SourceProvider.OPENDART and dataset is SourceDataset.OPENDART_CORP_CODE:
        return f"{OPENDART_BASE_URL}{OPENDART_CORP_CODE_PATH}"
    if provider is SourceProvider.OPENDART and dataset in OPENDART_PATHS:
        return f"{OPENDART_BASE_URL}{OPENDART_PATHS[dataset]}"
    _fail(
        "SOURCE_DATASET_MISMATCH",
        "$.dataset",
        f"{provider.value}에서 {dataset.value} dataset을 사용할 수 없습니다.",
    )


def parse_fixture_payload(payload: Mapping[str, Any]) -> SourceSnapshot:
    """Tracked fixture envelope를 exact raw snapshot으로 바꾼다."""

    if _text(payload.get("fixtureVersion"), "$.fixtureVersion") != "2026-08-14.1":
        _fail("UNSUPPORTED_FIXTURE_VERSION", "$.fixtureVersion", "지원하지 않는 fixture입니다.")
    try:
        provider = SourceProvider(_text(payload.get("source"), "$.source"))
        dataset = SourceDataset(_text(payload.get("dataset"), "$.dataset"))
    except ValueError:
        _fail("MALFORMED_SOURCE_RESPONSE", "$.source", "알 수 없는 source 또는 dataset입니다.")
    endpoint = _text(payload.get("endpoint"), "$.endpoint")
    allowed = _allowed_endpoint(provider, dataset)
    if provider is SourceProvider.KRX_OPEN_API:
        allowed_endpoints = {f"{KRX_BASE_URL}{path}" for path in KRX_MARKET_PATHS.values()}
        if endpoint not in allowed_endpoints:
            _fail("ENDPOINT_MISMATCH", "$.endpoint", "허용된 KRX read endpoint가 아닙니다.")
    elif endpoint != allowed:
        _fail("ENDPOINT_MISMATCH", "$.endpoint", "dataset의 공식 OpenDART endpoint와 다릅니다.")

    raw_payload = _mapping(payload.get("rawResponse"), "$.rawResponse")
    raw_text = canonical_json(raw_payload)
    declared_hash = _text(payload.get("rawHash"), "$.rawHash")
    if sha256_text(raw_text) != declared_hash:
        _fail("HASH_MISMATCH", "$.rawHash", "rawResponse canonical SHA-256과 다릅니다.")
    lineage = tuple(
        _text(value, f"$.lineage[{index}]")
        for index, value in enumerate(_sequence(payload.get("lineage"), "$.lineage"))
    )
    document_ids = tuple(
        _text(value, f"$.sourceDocumentIds[{index}]")
        for index, value in enumerate(
            _sequence(payload.get("sourceDocumentIds", []), "$.sourceDocumentIds")
        )
    )
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        _fail("MALFORMED_SOURCE_RESPONSE", "$.revision", "1 이상의 정수가 필요합니다.")
    live_status = _text(payload.get("liveValidationStatus"), "$.liveValidationStatus")
    if live_status != LiveValidationStatus.UNVERIFIED.value:
        _fail(
            "LIVE_STATUS_MISMATCH",
            "$.liveValidationStatus",
            "committed fixture는 live 검증 완료를 주장할 수 없습니다.",
        )
    try:
        metadata = SourceMetadata(
            provider=provider,
            dataset=dataset,
            endpoint=endpoint,
            source_key=_text(payload.get("sourceKey"), "$.sourceKey"),
            as_of=_datetime(payload.get("asOf"), "$.asOf"),
            collected_at=_datetime(payload.get("collectedAt"), "$.collectedAt"),
            parser_version=_text(payload.get("parserVersion"), "$.parserVersion"),
            revision=revision,
            lineage=lineage,
            source_document_ids=document_ids,
            live_validation_status=LiveValidationStatus.UNVERIFIED,
        )
    except ValueError as exc:
        _fail("MALFORMED_SOURCE_RESPONSE", "$", str(exc))
    return SourceSnapshot(metadata=metadata, raw_payload_text=raw_text, raw_hash=declared_hash)


def load_source_fixture(path: Path, *, repository_root: Path) -> SourceSnapshot:
    fixture_root = (repository_root / "tests" / "reference-data" / "fixtures").resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(fixture_root) or resolved.suffix.lower() != ".json":
        _fail(
            "UNAPPROVED_FIXTURE_PATH",
            "$fixture",
            "tests/reference-data/fixtures 아래 JSON만 읽을 수 있습니다.",
        )
    try:
        text = resolved.read_text(encoding="utf-8")
        payload = parse_json_object(text)
    except (OSError, TypeError, ValueError) as exc:
        _fail("MALFORMED_FIXTURE", "$fixture", str(exc))
    return parse_fixture_payload(payload)


def parse_krx_stock_daily(snapshot: SourceSnapshot) -> tuple[DailyPriceObservation, ...]:
    metadata = snapshot.metadata
    if (
        metadata.provider is not SourceProvider.KRX_OPEN_API
        or metadata.dataset is not SourceDataset.KRX_STOCK_DAILY
    ):
        _fail("SOURCE_DATASET_MISMATCH", "$snapshot", "KRX 주식 일별매매 snapshot이 아닙니다.")
    payload = parse_json_object(snapshot.raw_payload_text)
    rows = _sequence(payload.get("OutBlock_1"), "$.OutBlock_1")
    market, separator, source_date = metadata.source_key.partition(":")
    if market not in KRX_MARKET_PATHS:
        _fail("MALFORMED_SOURCE_RESPONSE", "$metadata.sourceKey", "market 정보가 없습니다.")
    if not separator:
        _fail("MALFORMED_SOURCE_RESPONSE", "$metadata.sourceKey", "거래일 정보가 없습니다.")
    expected_date = _date(source_date, "$metadata.sourceKey")
    expected_endpoint = f"{KRX_BASE_URL}{KRX_MARKET_PATHS[market]}"
    if metadata.endpoint != expected_endpoint:
        _fail("ENDPOINT_MISMATCH", "$metadata.endpoint", "market과 KRX endpoint가 다릅니다.")
    observations: list[DailyPriceObservation] = []
    seen: set[tuple[str, date]] = set()
    for index, value in enumerate(rows):
        path = f"$.OutBlock_1[{index}]"
        row = _mapping(value, path)
        market_date = _date(row.get("BAS_DD"), f"{path}.BAS_DD")
        if market_date != expected_date or market_date != metadata.as_of.date():
            _fail(
                "SOURCE_REFERENCE_CONFLICT",
                f"{path}.BAS_DD",
                "raw 거래일과 source metadata가 다릅니다.",
            )
        source_market = _text(row.get("MKT_NM"), f"{path}.MKT_NM")
        if source_market != market:
            _fail(
                "SOURCE_REFERENCE_CONFLICT",
                f"{path}.MKT_NM",
                "raw 시장과 source metadata가 다릅니다.",
            )
        stock_code = _text(row.get("ISU_CD"), f"{path}.ISU_CD")
        key = stock_code, market_date
        if key in seen:
            _fail("DUPLICATE_ECONOMIC_FIELD", path, "같은 종목·거래일 row가 중복되었습니다.")
        seen.add(key)
        change_raw = row.get("CMPPREVDD_PRC")
        change = None if change_raw in {None, ""} else _decimal(change_raw, f"{path}.CMPPREVDD_PRC")
        try:
            observations.append(
                DailyPriceObservation(
                    stock_code=stock_code,
                    market=market,
                    market_date=market_date,
                    close=_decimal(row.get("TDD_CLSPRC"), f"{path}.TDD_CLSPRC"),
                    change_from_previous=change,
                    listed_shares=_integer(row.get("LIST_SHRS"), f"{path}.LIST_SHRS"),
                    metadata=metadata,
                )
            )
        except ValueError as exc:
            _fail("MALFORMED_SOURCE_RESPONSE", path, str(exc))
    return tuple(observations)


COLLECTION_ENVELOPE_VERSION = "reference-collection-2026.08.1"


def dump_collected_snapshot(snapshot: SourceSnapshot) -> dict[str, Any]:
    """수집 process와 서빙 process 사이에 원문 그대로 넘기는 봉투.

    JSON 응답도 XML 응답도 담을 수 있어야 하므로 raw는 항상 text로 보존한다.
    """

    metadata = snapshot.metadata
    return {
        "envelopeVersion": COLLECTION_ENVELOPE_VERSION,
        "provider": metadata.provider.value,
        "dataset": metadata.dataset.value,
        "endpoint": metadata.endpoint,
        "sourceKey": metadata.source_key,
        "asOf": metadata.as_of.isoformat(),
        "collectedAt": metadata.collected_at.isoformat(),
        "parserVersion": metadata.parser_version,
        "revision": metadata.revision,
        "lineage": list(metadata.lineage),
        "sourceDocumentIds": list(metadata.source_document_ids),
        "liveValidationStatus": metadata.live_validation_status.value,
        "rawPayloadText": snapshot.raw_payload_text,
        "rawHash": snapshot.raw_hash,
    }


def load_collected_snapshot(payload: Mapping[str, Any]) -> SourceSnapshot:
    if _text(payload.get("envelopeVersion"), "$.envelopeVersion") != (
        COLLECTION_ENVELOPE_VERSION
    ):
        _fail("UNSUPPORTED_FIXTURE_VERSION", "$.envelopeVersion", "지원하지 않는 봉투입니다.")
    try:
        provider = SourceProvider(_text(payload.get("provider"), "$.provider"))
        dataset = SourceDataset(_text(payload.get("dataset"), "$.dataset"))
    except ValueError:
        _fail("MALFORMED_SOURCE_RESPONSE", "$.provider", "알 수 없는 source 또는 dataset입니다.")
    # 원문은 공백 하나까지 그대로여야 hash가 맞으므로 strip하지 않는다.
    raw_text = payload.get("rawPayloadText")
    if not isinstance(raw_text, str) or not raw_text:
        _fail("MALFORMED_SOURCE_RESPONSE", "$.rawPayloadText", "원문 문자열이 필요합니다.")
    declared_hash = _text(payload.get("rawHash"), "$.rawHash")
    if sha256_text(raw_text) != declared_hash:
        _fail("HASH_MISMATCH", "$.rawHash", "rawPayloadText와 rawHash가 다릅니다.")
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        _fail("MALFORMED_SOURCE_RESPONSE", "$.revision", "1 이상의 정수가 필요합니다.")
    try:
        metadata = SourceMetadata(
            provider=provider,
            dataset=dataset,
            endpoint=_text(payload.get("endpoint"), "$.endpoint"),
            source_key=_text(payload.get("sourceKey"), "$.sourceKey"),
            as_of=_datetime(payload.get("asOf"), "$.asOf"),
            collected_at=_datetime(payload.get("collectedAt"), "$.collectedAt"),
            parser_version=_text(payload.get("parserVersion"), "$.parserVersion"),
            revision=revision,
            lineage=tuple(
                _text(value, f"$.lineage[{index}]")
                for index, value in enumerate(_sequence(payload.get("lineage"), "$.lineage"))
            ),
            source_document_ids=tuple(
                _text(value, f"$.sourceDocumentIds[{index}]")
                for index, value in enumerate(
                    _sequence(payload.get("sourceDocumentIds", []), "$.sourceDocumentIds")
                )
            ),
            live_validation_status=LiveValidationStatus.UNVERIFIED,
        )
    except ValueError as exc:
        _fail("MALFORMED_SOURCE_RESPONSE", "$", str(exc))
    return SourceSnapshot(metadata=metadata, raw_payload_text=raw_text, raw_hash=declared_hash)


def parse_corp_code_index(snapshot: SourceSnapshot) -> dict[str, str]:
    """상장 종목만 남긴 6자리 종목코드 → 8자리 고유번호 대조표."""

    metadata = snapshot.metadata
    if (
        metadata.provider is not SourceProvider.OPENDART
        or metadata.dataset is not SourceDataset.OPENDART_CORP_CODE
    ):
        _fail("SOURCE_DATASET_MISMATCH", "$snapshot", "OpenDART 고유번호 대조표가 아닙니다.")
    try:
        root = ElementTree.fromstring(snapshot.raw_payload_text)
    except ElementTree.ParseError as exc:
        _fail("MALFORMED_SOURCE_RESPONSE", "$corpCode", str(exc))
    index: dict[str, str] = {}
    for position, element in enumerate(root.findall("list")):
        path = f"$corpCode.list[{position}]"
        stock_code = (element.findtext("stock_code") or "").strip()
        if not stock_code:
            continue
        corp_code = _text(element.findtext("corp_code"), f"{path}.corp_code")
        if len(corp_code) != 8 or not corp_code.isdigit():
            _fail("MALFORMED_SOURCE_RESPONSE", f"{path}.corp_code", "숫자 8자리가 필요합니다.")
        if not STOCK_CODE_RE.fullmatch(stock_code):
            _fail("MALFORMED_SOURCE_RESPONSE", f"{path}.stock_code", "6자리 종목코드가 필요합니다.")
        existing = index.get(stock_code)
        if existing is not None and existing != corp_code:
            _fail(
                "SOURCE_REFERENCE_CONFLICT",
                f"{path}.stock_code",
                "같은 종목코드에 서로 다른 고유번호가 있습니다.",
            )
        index[stock_code] = corp_code
    return index


def derive_trading_calendar(
    snapshots: Iterable[SourceSnapshot],
    *,
    version: str,
    session_open: time = time(9, 0),
    session_close: time = time(15, 30),
) -> tuple[TradingDayObservation, ...]:
    """KRX 일별매매 응답 유무로 거래일 여부를 만든다. row가 있으면 거래일이다.

    KRX Open API에는 거래일 달력 endpoint가 없다. 조회한 날짜만 판정하며,
    조회하지 않은 날짜는 아예 만들지 않아 calendar가 fail-closed로 남는다.
    """

    observations: list[TradingDayObservation] = []
    for snapshot in snapshots:
        metadata = snapshot.metadata
        if metadata.dataset is not SourceDataset.KRX_STOCK_DAILY:
            _fail("SOURCE_DATASET_MISMATCH", "$snapshot", "KRX 일별매매 snapshot이 아닙니다.")
        market_date = _date(metadata.source_key.partition(":")[2], "$metadata.sourceKey")
        payload = parse_json_object(snapshot.raw_payload_text)
        is_trading_day = bool(_sequence(payload.get("OutBlock_1"), "$.OutBlock_1"))
        observations.append(
            TradingDayObservation(
                market_date=market_date,
                is_trading_day=is_trading_day,
                session_open=session_open if is_trading_day else None,
                session_close=session_close if is_trading_day else None,
                version=version,
                metadata=replace(
                    metadata,
                    dataset=SourceDataset.KRX_CALENDAR_DERIVED,
                    source_key=market_date.isoformat(),
                    lineage=tuple(
                        f"krx-calendar-derived:{item}" for item in metadata.lineage
                    ),
                ),
            )
        )
    return tuple(observations)


def _require_opendart_success(snapshot: SourceSnapshot) -> list[Any]:
    metadata = snapshot.metadata
    if metadata.provider is not SourceProvider.OPENDART:
        _fail("SOURCE_DATASET_MISMATCH", "$snapshot", "OpenDART snapshot이 아닙니다.")
    payload = parse_json_object(snapshot.raw_payload_text)
    status = _text(payload.get("status"), "$.status")
    if status != "000":
        _fail("SOURCE_NO_DATA" if status == "013" else "SOURCE_REJECTED", "$.status", status)
    rows = _sequence(payload.get("list"), "$.list")
    expected_corp_code = metadata.source_key.partition(":")[0]
    if len(expected_corp_code) != 8 or not expected_corp_code.isdigit():
        _fail("MALFORMED_SOURCE_RESPONSE", "$metadata.sourceKey", "corp_code 정보가 없습니다.")
    for index, value in enumerate(rows):
        row = _mapping(value, f"$.list[{index}]")
        corp_code = _text(row.get("corp_code"), f"$.list[{index}].corp_code")
        if corp_code != expected_corp_code:
            _fail(
                "SOURCE_REFERENCE_CONFLICT",
                f"$.list[{index}].corp_code",
                "raw corp_code와 source metadata가 다릅니다.",
            )
        receipt = _text(row.get("rcept_no"), f"$.list[{index}].rcept_no")
        if receipt not in metadata.source_document_ids:
            _fail(
                "LINEAGE_MISMATCH",
                f"$.list[{index}].rcept_no",
                "접수번호가 source metadata에 보존되지 않았습니다.",
            )
    return rows


def _is_common(value: object, path: str) -> bool:
    text = "".join(_text(value, path).split())
    return text in {"보통주", "보통주식", "의결권있는주식"}


def _is_total_row(name: str) -> bool:
    """최대주주 응답은 주식 종류별 합계 row를 붙여 보낸다. 개별 보유와 이중 차감된다."""

    return "".join(name.split()) in {"계", "합계", "소계", "총계"}


def _holder_id(corp_code: str, name: str, relationship: str) -> str:
    normalized = "|".join((corp_code, "".join(name.split()), "".join(relationship.split())))
    return f"DART_HOLDER:{sha256_text(normalized)[:24]}"


def _holding_category(name: str, relationship: str) -> tuple[str, NonFloatCategory]:
    combined = f"{name} {relationship}"
    if "자기주식" in combined or "자사주" in combined:
        return "ISSUER_TREASURY", NonFloatCategory.TREASURY
    if "전략" in combined or "보호예수" in combined:
        return "", NonFloatCategory.STRATEGIC_LOCKUP
    return "", NonFloatCategory.CONTROLLING_HOLDER


def parse_open_dart(snapshot: SourceSnapshot, *, stock_code: str) -> OpenDartNormalization:
    metadata = snapshot.metadata
    if metadata.dataset not in OPENDART_PATHS:
        _fail("SOURCE_DATASET_MISMATCH", "$snapshot", "지원하지 않는 OpenDART dataset입니다.")
    rows = _require_opendart_success(snapshot)
    if metadata.dataset is SourceDataset.OPENDART_STOCK_TOTAL:
        return _parse_stock_total(rows, stock_code=stock_code, metadata=metadata)
    if metadata.dataset is SourceDataset.OPENDART_LARGEST_SHAREHOLDER:
        return _parse_largest_shareholders(rows, stock_code=stock_code, metadata=metadata)
    return _parse_treasury_status(rows, stock_code=stock_code, metadata=metadata)


def _parse_stock_total(
    rows: list[Any], *, stock_code: str, metadata: SourceMetadata
) -> OpenDartNormalization:
    common = [
        (index, _mapping(value, f"$.list[{index}]"))
        for index, value in enumerate(rows)
        if _is_common(_mapping(value, f"$.list[{index}]").get("se"), f"$.list[{index}].se")
    ]
    if len(common) != 1:
        _fail("AMBIGUOUS_COMMON_SHARE_ROW", "$.list", "보통주 row가 정확히 하나여야 합니다.")
    index, row = common[0]
    path = f"$.list[{index}]"
    effective_on = _date(row.get("stlm_dt"), f"{path}.stlm_dt")
    if effective_on != metadata.as_of.date():
        _fail("SOURCE_REFERENCE_CONFLICT", f"{path}.stlm_dt", "결산기준일이 as_of와 다릅니다.")
    issued = _integer(row.get("istc_totqy"), f"{path}.istc_totqy")
    treasury = _treasury_shares(row.get("tesstk_co"), f"{path}.tesstk_co")
    return OpenDartNormalization(
        issued_share_observations=(
            FieldObservation(
                stock_code=stock_code,
                field=EconomicField.LISTED_COMMON_SHARES,
                value=issued,
                effective_on=effective_on,
                share_class=ShareClass.COMMON,
                metadata=metadata,
            ),
        ),
        non_float_holdings=(
            NonFloatHolding(
                stock_code=stock_code,
                holder_id="ISSUER_TREASURY",
                holder_name="자기주식",
                category=NonFloatCategory.TREASURY,
                share_class=ShareClass.COMMON,
                shares=treasury,
                effective_on=effective_on,
                metadata=metadata,
            ),
        ),
        coverage_declarations=(
            HoldingCoverageDeclaration(
                stock_code=stock_code,
                category=NonFloatCategory.TREASURY,
                status=CoverageDeclarationStatus.COMPLETE,
                effective_on=effective_on,
                metadata=metadata,
            ),
        ),
    )


def _parse_largest_shareholders(
    rows: list[Any], *, stock_code: str, metadata: SourceMetadata
) -> OpenDartNormalization:
    holdings: list[NonFloatHolding] = []
    effective_dates: set[date] = set()
    corp_codes: set[str] = set()
    for index, value in enumerate(rows):
        path = f"$.list[{index}]"
        row = _mapping(value, path)
        if not _is_common(row.get("stock_knd"), f"{path}.stock_knd"):
            continue
        shares = _integer(
            row.get("trmend_posesn_stock_co"),
            f"{path}.trmend_posesn_stock_co",
            allow_zero=True,
        )
        if shares == 0:
            continue
        name = _text(row.get("nm"), f"{path}.nm")
        if _is_total_row(name):
            continue
        relationship = _text(row.get("relate"), f"{path}.relate")
        corp_code = _text(row.get("corp_code"), f"{path}.corp_code")
        effective_on = _date(row.get("stlm_dt"), f"{path}.stlm_dt")
        effective_dates.add(effective_on)
        corp_codes.add(corp_code)
        special_id, category = _holding_category(name, relationship)
        holdings.append(
            NonFloatHolding(
                stock_code=stock_code,
                holder_id=special_id or _holder_id(corp_code, name, relationship),
                holder_name=name,
                category=category,
                share_class=ShareClass.COMMON,
                shares=shares,
                effective_on=effective_on,
                metadata=metadata,
            )
        )
    if len(effective_dates) != 1 or len(corp_codes) != 1:
        _fail("SOURCE_REFERENCE_CONFLICT", "$.list", "corp_code 또는 결산기준일이 일치하지 않습니다.")
    effective_on = next(iter(effective_dates))
    if effective_on != metadata.as_of.date():
        _fail("SOURCE_REFERENCE_CONFLICT", "$.list", "결산기준일이 as_of와 다릅니다.")
    declarations = [
        HoldingCoverageDeclaration(
            stock_code=stock_code,
            category=NonFloatCategory.CONTROLLING_HOLDER,
            status=CoverageDeclarationStatus.COMPLETE,
            effective_on=effective_on,
            metadata=metadata,
        )
    ]
    return OpenDartNormalization(
        non_float_holdings=tuple(holdings),
        coverage_declarations=tuple(declarations),
    )


def _parse_treasury_status(
    rows: list[Any], *, stock_code: str, metadata: SourceMetadata
) -> OpenDartNormalization:
    totals: list[tuple[int, dict[str, Any]]] = []
    for index, value in enumerate(rows):
        path = f"$.list[{index}]"
        row = _mapping(value, path)
        if not _is_common(row.get("stock_knd"), f"{path}.stock_knd"):
            continue
        if "".join(_text(row.get("acqs_mth3"), f"{path}.acqs_mth3").split()) in {
            "총계",
            "합계",
        }:
            totals.append((index, row))
    if len(totals) != 1:
        _fail("AMBIGUOUS_TREASURY_TOTAL", "$.list", "보통주 자기주식 총계 row가 하나여야 합니다.")
    index, row = totals[0]
    path = f"$.list[{index}]"
    effective_on = _date(row.get("stlm_dt"), f"{path}.stlm_dt")
    if effective_on != metadata.as_of.date():
        _fail("SOURCE_REFERENCE_CONFLICT", f"{path}.stlm_dt", "결산기준일이 as_of와 다릅니다.")
    holding = NonFloatHolding(
        stock_code=stock_code,
        holder_id="ISSUER_TREASURY",
        holder_name="자기주식",
        category=NonFloatCategory.TREASURY,
        share_class=ShareClass.COMMON,
        shares=_treasury_shares(row.get("trmend_qy"), f"{path}.trmend_qy"),
        effective_on=effective_on,
        metadata=metadata,
    )
    declaration = HoldingCoverageDeclaration(
        stock_code=stock_code,
        category=NonFloatCategory.TREASURY,
        status=CoverageDeclarationStatus.COMPLETE,
        effective_on=effective_on,
        metadata=metadata,
    )
    return OpenDartNormalization(
        non_float_holdings=(holding,),
        coverage_declarations=(declaration,),
    )
