"""KRX Open API와 OpenDART의 read-only JSON transport 경계."""

from __future__ import annotations

import json
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

# 상류가 침해되거나 오작동해도 메모리를 고갈시키지 못하게 하는 상한.
MAX_RESPONSE_BYTES: Final = 32 * 1024 * 1024
MAX_ARCHIVE_ENTRY_BYTES: Final = 64 * 1024 * 1024

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


def _fetch_bytes(
    client: httpx.Client,
    endpoint: str,
    *,
    provider: str,
    params: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float,
) -> bytes:
    """상한 안에서만 본문을 읽고, 예외에 요청 URL을 남기지 않는다.

    httpx가 만드는 상태 예외 메시지에는 요청 URL 전체가 들어간다. OpenDART는 API
    키를 쿼리로 보내야 하므로(헤더 인증을 지원하지 않는다) 메시지를 직접 만들고
    예외 체인을 잇지 않는다.
    """

    body = bytearray()
    try:
        with client.stream(
            "GET",
            endpoint,
            params=params,
            headers=headers,
            timeout=timeout,
        ) as response:
            if response.status_code >= 400:
                raise SourceTransportError(
                    provider,
                    endpoint,
                    f"HTTP {response.status_code} 응답입니다.",
                )
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise SourceContractError(
                        "OVERSIZED_SOURCE_RESPONSE",
                        endpoint,
                        f"응답 본문 상한 {MAX_RESPONSE_BYTES} bytes를 넘었습니다.",
                    )
    except httpx.HTTPError:
        raise SourceTransportError(
            provider,
            endpoint,
            "네트워크 조회에 실패했습니다.",
        ) from None
    return bytes(body)


def _read_archive_entry(archive: ZipFile, name: str, *, endpoint: str) -> bytes:
    """압축 해제 크기에 상한을 둔다. 선언값과 실제 읽은 양을 모두 본다."""

    info = archive.getinfo(name)
    if info.file_size > MAX_ARCHIVE_ENTRY_BYTES:
        raise SourceContractError(
            "OVERSIZED_SOURCE_RESPONSE",
            endpoint,
            f"압축 해제 상한 {MAX_ARCHIVE_ENTRY_BYTES} bytes를 넘었습니다.",
        )
    with archive.open(info) as entry:
        data = entry.read(MAX_ARCHIVE_ENTRY_BYTES + 1)
    if len(data) > MAX_ARCHIVE_ENTRY_BYTES:
        raise SourceContractError(
            "OVERSIZED_SOURCE_RESPONSE",
            endpoint,
            f"압축 해제 상한 {MAX_ARCHIVE_ENTRY_BYTES} bytes를 넘었습니다.",
        )
    return data


def _json_object(body: bytes, *, endpoint: str) -> dict[str, Any]:
    try:
        value = json.loads(body)
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
        payload = _json_object(
            _fetch_bytes(
                self._client,
                endpoint,
                provider=SourceProvider.KRX_OPEN_API.value,
                headers={"AUTH_KEY": self._api_key},
                params={"basDd": market_date.strftime("%Y%m%d")},
                timeout=self._timeout,
            ),
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
        body = _fetch_bytes(
            self._client,
            endpoint,
            provider=SourceProvider.OPENDART.value,
            params={"crtfc_key": self._api_key},
            timeout=self._timeout,
        )
        try:
            archive = ZipFile(BytesIO(body))
            raw_text = _read_archive_entry(
                archive,
                CORP_CODE_ENTRY_NAME,
                endpoint=endpoint,
            ).decode("utf-8")
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
        payload = _json_object(
            _fetch_bytes(
                self._client,
                endpoint,
                provider=SourceProvider.OPENDART.value,
                params=params,
                timeout=self._timeout,
            ),
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
