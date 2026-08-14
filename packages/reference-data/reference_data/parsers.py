"""공식 필드명을 보존하는 strict fixture/source parser와 normalizer."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Never, cast

from .adapters import KRX_BASE_URL, KRX_MARKET_PATHS, OPENDART_BASE_URL, OPENDART_PATHS
from .errors import SourceContractError
from .hashing import canonical_json, parse_json_object, sha256_text
from .models import (
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
    treasury = _integer(row.get("tesstk_co"), f"{path}.tesstk_co", allow_zero=True)
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
        shares=_integer(row.get("trmend_qy"), f"{path}.trmend_qy", allow_zero=True),
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
