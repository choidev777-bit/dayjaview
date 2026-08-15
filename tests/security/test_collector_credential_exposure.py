"""F-23 수리: OpenDART API 키가 예외 체인으로 새지 않는다.

OpenDART는 헤더 인증을 지원하지 않아 키가 URL 쿼리에 실린다. 그래서 httpx가
만드는 상태 예외(메시지에 요청 URL 전체가 들어간다)를 그대로 물고 가지 않고,
어댑터가 상태코드만 담은 예외를 새로 만든다. 같은 파일의 KRX 어댑터는 애초에
헤더로 보내므로 대조군이 된다.
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
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def _fetch(adapter: object) -> None:
    adapter.fetch_periodic_report(  # type: ignore[attr-defined]
        dataset=ADAPTERS.SourceDataset.OPENDART_STOCK_TOTAL,
        corp_code="00126380",
        business_year=2026,
        report_code="11012",
        as_of=NOW,
        collected_at=NOW,
    )


def test_opendart_key_still_travels_in_the_query_string() -> None:
    """공급원 제약이라 바꿀 수 없다 — 그래서 예외 쪽을 막는다."""

    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"status": "000", "list": []})

    _fetch(
        ADAPTERS.OpenDartAdapter(
            api_key=API_KEY,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
    )

    assert len(seen) == 1
    assert API_KEY in str(seen[0])


@pytest.mark.parametrize("status_code", [400, 500])
def test_opendart_http_failure_never_exposes_the_key(status_code: int) -> None:
    """메시지·체인 어디에도 키가 없고, 원본 예외를 물고 가지도 않는다."""

    adapter = ADAPTERS.OpenDartAdapter(api_key=API_KEY, client=_client(status_code))
    with pytest.raises(ERRORS.SourceTransportError) as caught:
        _fetch(adapter)

    assert API_KEY not in str(caught.value)
    assert API_KEY not in _causes(caught.value)
    assert caught.value.__cause__ is None
    # 상태코드는 남는다 — 디버깅에 필요한 정보까지 지우지는 않는다.
    assert str(status_code) in str(caught.value)


def test_opendart_corp_code_failure_never_exposes_the_key() -> None:
    """ZIP을 받는 경로도 같은 방식으로 막힌다."""

    adapter = ADAPTERS.OpenDartAdapter(api_key=API_KEY, client=_client(400))
    with pytest.raises(ERRORS.SourceTransportError) as caught:
        adapter.fetch_corp_code_index(as_of=NOW, collected_at=NOW)

    assert API_KEY not in _causes(caught.value)
    assert caught.value.__cause__ is None


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
