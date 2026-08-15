"""F-23 finding: OpenDART API 키가 URL 쿼리로 나가고 예외 체인에 남는다.

같은 파일의 KRX adapter는 키를 header로 보내 이 문제가 없다. OpenDART만
`params`로 보내므로 httpx가 만드는 `HTTPStatusError` 메시지에 키가 박힌
전체 URL이 들어가고, `raise ... from exc`로 `__cause__`에 보존된다.
traceback을 통째로 로깅하면 키가 그대로 찍힌다.

두 테스트 모두 현재 동작을 고정한다. 수리하면 실패한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module

import httpx
import pytest

ADAPTERS = import_module("packages." + "reference-data.reference_data.adapters")
ERRORS = import_module("packages." + "reference-data.reference_data.errors")

API_KEY = "opendart-secret-key-0123456789abcdef"
NOW = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)


def _client(status_code: int) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"status": "020", "message": "거부"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _causes(error: BaseException) -> str:
    """예외 체인 전체를 traceback 로깅과 같은 방식으로 펼친다."""

    parts: list[str] = []
    current: BaseException | None = error
    while current is not None:
        parts.append(str(current))
        current = current.__cause__
    return "\n".join(parts)


def test_opendart_key_travels_in_the_query_string() -> None:
    """요청 URL 자체에 키가 실린다 — 중간 프록시 access log에 남는 형태."""

    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"status": "000", "list": []})

    adapter = ADAPTERS.OpenDartAdapter(
        api_key=API_KEY,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    adapter.fetch_periodic_report(
        dataset=ADAPTERS.SourceDataset.OPENDART_STOCK_TOTAL,
        corp_code="00126380",
        business_year=2026,
        report_code="11012",
        as_of=NOW,
        collected_at=NOW,
    )

    assert len(seen) == 1
    assert API_KEY in str(seen[0])


def test_opendart_http_failure_keeps_the_key_in_the_exception_chain() -> None:
    """최상위 메시지는 깨끗하지만 `__cause__`에 키가 남는다."""

    adapter = ADAPTERS.OpenDartAdapter(api_key=API_KEY, client=_client(400))
    with pytest.raises(ERRORS.SourceTransportError) as caught:
        adapter.fetch_periodic_report(
            dataset=ADAPTERS.SourceDataset.OPENDART_STOCK_TOTAL,
            corp_code="00126380",
            business_year=2026,
            report_code="11012",
            as_of=NOW,
            collected_at=NOW,
        )

    # 이것이 지금 안전을 만들어 주고 있는 유일한 이유 — 상위 메시지는 키가 없다.
    assert API_KEY not in str(caught.value)
    # 하지만 체인을 펼치면 키가 나온다.
    assert API_KEY in _causes(caught.value)


def test_krx_adapter_sends_its_key_in_a_header() -> None:
    """대조군 — 이 방식이면 URL·예외 어느 쪽에도 키가 남지 않는다."""

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"message": "거부"})

    adapter = ADAPTERS.KrxOpenApiAdapter(
        api_key=API_KEY,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ERRORS.SourceTransportError) as caught:
        adapter.fetch_stock_daily(
            market="KOSPI",
            market_date=NOW.date(),
            as_of=NOW,
            collected_at=NOW,
        )

    assert seen[0].headers["AUTH_KEY"] == API_KEY
    assert API_KEY not in str(seen[0].url)
    assert API_KEY not in _causes(caught.value)
