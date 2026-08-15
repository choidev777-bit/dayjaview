"""KRX Open API와 OpenDART의 read-only JSON transport 경계."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Any, Final, cast
from zipfile import BadZipFile, ZipFile

import httpx

from .errors import MissingCredentialError, SourceContractError, SourceTransportError
from .hashing import canonical_json, sha256_text
from .models import (
    LiveValidationStatus,
    SourceDataset,
    SourceMetadata,
    SourceProvider,
    SourceSnapshot,
)

KRX_BASE_URL: Final = "https://data-dbg.krx.co.kr"
OPENDART_BASE_URL: Final = "https://opendart.fss.or.kr"
PARSER_VERSION: Final = "reference-source-2026.08.1"

KRX_MARKET_PATHS: Final[dict[str, str]] = {
    "KOSPI": "/svc/apis/sto/stk_bydd_trd",
    "KOSDAQ": "/svc/apis/sto/ksq_bydd_trd",
    "KONEX": "/svc/apis/sto/knx_bydd_trd",
}

OPENDART_PATHS: Final[dict[SourceDataset, str]] = {
    SourceDataset.OPENDART_STOCK_TOTAL: "/api/stockTotqySttus.json",
    SourceDataset.OPENDART_LARGEST_SHAREHOLDER: "/api/hyslrSttus.json",
    SourceDataset.OPENDART_TREASURY_STATUS: "/api/tesstkAcqsDspsSttus.json",
}

# 정기보고서 endpoint와 달리 회사 고유번호 대조표는 ZIP 안의 XML로 온다.
OPENDART_CORP_CODE_PATH: Final = "/api/corpCode.xml"
CORP_CODE_ENTRY_NAME: Final = "CORPCODE.xml"


@dataclass(frozen=True, slots=True)
class LiveReadiness:
    fixture_contract_status: str
    live_validation_status: LiveValidationStatus
    blocker: str | None
    missing_credentials: tuple[str, ...]
    live_request_attempted: bool = False


def assess_live_readiness(config: Mapping[str, str]) -> LiveReadiness:
    """자격정보 원문을 반환하지 않고 live/fixture 상태를 분리한다."""

    required = ("KRX_API_KEY", "OPENDART_API_KEY")
    missing = tuple(name for name in required if not config.get(name, "").strip())
    if missing:
        return LiveReadiness(
            fixture_contract_status="VERIFIED",
            live_validation_status=LiveValidationStatus.UNVERIFIED,
            blocker="B-REFDATA-KEYS",
            missing_credentials=missing,
        )
    return LiveReadiness(
        fixture_contract_status="VERIFIED",
        live_validation_status=LiveValidationStatus.UNVERIFIED,
        blocker="LIVE_FIELD_COVERAGE_UNVERIFIED",
        missing_credentials=(),
    )


def _required_key(value: str, name: str) -> str:
    if not value.strip():
        raise MissingCredentialError((name,))
    return value


def _json_object(response: httpx.Response, *, provider: str, endpoint: str) -> dict[str, Any]:
    try:
        response.raise_for_status()
        value = response.json()
    except httpx.HTTPError as exc:
        raise SourceTransportError(provider, endpoint, "HTTP 응답이 정상적이지 않습니다.") from exc
    except ValueError as exc:
        raise SourceContractError(
            "MALFORMED_SOURCE_RESPONSE",
            endpoint,
            "JSON object 응답이 필요합니다.",
        ) from exc
    if not isinstance(value, dict):
        raise SourceContractError(
            "MALFORMED_SOURCE_RESPONSE",
            endpoint,
            "최상위 JSON object 응답이 필요합니다.",
        )
    return cast(dict[str, Any], value)


def _snapshot(
    *,
    provider: SourceProvider,
    dataset: SourceDataset,
    endpoint: str,
    source_key: str,
    as_of: datetime,
    collected_at: datetime,
    revision: int,
    lineage: tuple[str, ...],
    source_document_ids: tuple[str, ...],
    payload: Mapping[str, Any] | None = None,
    raw_text: str | None = None,
) -> SourceSnapshot:
    if (payload is None) == (raw_text is None):
        raise ValueError("payload와 raw_text 중 정확히 하나가 필요합니다.")
    if raw_text is None:
        assert payload is not None
        raw_text = canonical_json(payload)
    return SourceSnapshot(
        metadata=SourceMetadata(
            provider=provider,
            dataset=dataset,
            endpoint=endpoint,
            source_key=source_key,
            as_of=as_of,
            collected_at=collected_at,
            parser_version=PARSER_VERSION,
            revision=revision,
            lineage=lineage,
            source_document_ids=source_document_ids,
            live_validation_status=LiveValidationStatus.UNVERIFIED,
        ),
        raw_payload_text=raw_text,
        raw_hash=sha256_text(raw_text),
    )


class KrxOpenApiAdapter:
    """인증키를 header에만 싣는 KRX GET adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = _required_key(api_key, "KRX_API_KEY")
        self._client = client or httpx.Client()
        self._timeout = timeout_seconds

    def fetch_stock_daily(
        self,
        *,
        market: str,
        market_date: date,
        as_of: datetime,
        collected_at: datetime,
        revision: int = 1,
    ) -> SourceSnapshot:
        try:
            path = KRX_MARKET_PATHS[market]
        except KeyError as exc:
            raise ValueError("market은 KOSPI, KOSDAQ, KONEX 중 하나여야 합니다.") from exc
        endpoint = f"{KRX_BASE_URL}{path}"
        try:
            response = self._client.get(
                endpoint,
                headers={"AUTH_KEY": self._api_key},
                params={"basDd": market_date.strftime("%Y%m%d")},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise SourceTransportError(
                SourceProvider.KRX_OPEN_API.value,
                endpoint,
                "네트워크 조회에 실패했습니다.",
            ) from exc
        payload = _json_object(
            response,
            provider=SourceProvider.KRX_OPEN_API.value,
            endpoint=endpoint,
        )
        source_key = f"{market}:{market_date.isoformat()}"
        return _snapshot(
            provider=SourceProvider.KRX_OPEN_API,
            dataset=SourceDataset.KRX_STOCK_DAILY,
            endpoint=endpoint,
            source_key=source_key,
            as_of=as_of,
            collected_at=collected_at,
            revision=revision,
            lineage=(f"krx-open-api:{source_key}",),
            source_document_ids=(),
            payload=payload,
        )


class OpenDartAdapter:
    """정기보고서 주요정보를 읽기만 하는 OpenDART GET adapter."""

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key = _required_key(api_key, "OPENDART_API_KEY")
        self._client = client or httpx.Client()
        self._timeout = timeout_seconds

    def fetch_corp_code_index(
        self,
        *,
        as_of: datetime,
        collected_at: datetime,
        revision: int = 1,
    ) -> SourceSnapshot:
        """종목코드 → 고유번호 대조표. 정기보고서 endpoint는 6자리를 받지 않는다."""

        endpoint = f"{OPENDART_BASE_URL}{OPENDART_CORP_CODE_PATH}"
        try:
            response = self._client.get(
                endpoint,
                params={"crtfc_key": self._api_key},
                timeout=self._timeout,
            )
            response.raise_for_status()
            archive = ZipFile(BytesIO(response.content))
            raw_text = archive.read(CORP_CODE_ENTRY_NAME).decode("utf-8")
        except httpx.HTTPError as exc:
            raise SourceTransportError(
                SourceProvider.OPENDART.value,
                endpoint,
                "네트워크 조회에 실패했습니다.",
            ) from exc
        except (BadZipFile, KeyError, UnicodeDecodeError) as exc:
            raise SourceContractError(
                "MALFORMED_SOURCE_RESPONSE",
                "$corpCode",
                f"{CORP_CODE_ENTRY_NAME}을 담은 ZIP 응답이 필요합니다.",
            ) from exc
        source_key = f"corp-code:{as_of.date().isoformat()}"
        return _snapshot(
            provider=SourceProvider.OPENDART,
            dataset=SourceDataset.OPENDART_CORP_CODE,
            endpoint=endpoint,
            source_key=source_key,
            as_of=as_of,
            collected_at=collected_at,
            revision=revision,
            lineage=(f"opendart:{source_key}",),
            source_document_ids=(),
            raw_text=raw_text,
        )

    def fetch_periodic_report(
        self,
        *,
        dataset: SourceDataset,
        corp_code: str,
        business_year: int,
        report_code: str,
        as_of: datetime,
        collected_at: datetime,
        revision: int = 1,
    ) -> SourceSnapshot:
        if dataset not in OPENDART_PATHS:
            raise ValueError("지원하지 않는 OpenDART dataset입니다.")
        if len(corp_code) != 8 or not corp_code.isdigit():
            raise ValueError("corp_code는 숫자 8자리여야 합니다.")
        if business_year < 2015:
            raise ValueError("OpenDART 정기보고서 API는 2015년 이후만 지원합니다.")
        if report_code not in {"11011", "11012", "11013", "11014"}:
            raise ValueError("지원하지 않는 정기보고서 코드입니다.")
        endpoint = f"{OPENDART_BASE_URL}{OPENDART_PATHS[dataset]}"
        params = {
            "crtfc_key": self._api_key,
            "corp_code": corp_code,
            "bsns_year": str(business_year),
            "reprt_code": report_code,
        }
        try:
            response = self._client.get(
                endpoint,
                params=params,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise SourceTransportError(
                SourceProvider.OPENDART.value,
                endpoint,
                "네트워크 조회에 실패했습니다.",
            ) from exc
        payload = _json_object(
            response,
            provider=SourceProvider.OPENDART.value,
            endpoint=endpoint,
        )
        source_key = f"{corp_code}:{business_year}:{report_code}"
        document_ids = tuple(
            sorted(
                {
                    str(row["rcept_no"])
                    for row in payload.get("list", [])
                    if isinstance(row, dict) and row.get("rcept_no")
                }
            )
        )
        return _snapshot(
            provider=SourceProvider.OPENDART,
            dataset=dataset,
            endpoint=endpoint,
            source_key=source_key,
            as_of=as_of,
            collected_at=collected_at,
            revision=revision,
            lineage=(f"opendart:{dataset.value}:{source_key}",),
            source_document_ids=document_ids,
            payload=payload,
        )
