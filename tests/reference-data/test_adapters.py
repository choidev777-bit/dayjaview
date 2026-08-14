from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
from conftest import aware


def test_live_readiness_keeps_fixture_and_live_status_separate(
    modules: dict[str, Any],
) -> None:
    readiness = modules["adapters"].assess_live_readiness({})

    assert readiness.fixture_contract_status == "VERIFIED"
    assert readiness.live_validation_status.value == "UNVERIFIED"
    assert readiness.blocker == "B-REFDATA-KEYS"
    assert readiness.missing_credentials == ("KRX_API_KEY", "OPENDART_API_KEY")
    assert readiness.live_request_attempted is False


def test_credentials_present_still_do_not_claim_live_verification(
    modules: dict[str, Any],
) -> None:
    readiness = modules["adapters"].assess_live_readiness(
        {"KRX_API_KEY": "krx-secret", "OPENDART_API_KEY": "dart-secret"}
    )

    assert readiness.live_validation_status.value == "UNVERIFIED"
    assert readiness.blocker == "LIVE_FIELD_COVERAGE_UNVERIFIED"
    assert readiness.missing_credentials == ()
    assert readiness.live_request_attempted is False


def test_krx_adapter_uses_auth_header_and_redacts_snapshot(
    modules: dict[str, Any],
) -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["auth"] = request.headers["AUTH_KEY"]
        observed["date"] = request.url.params["basDd"]
        return httpx.Response(200, json={"OutBlock_1": []}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = modules["adapters"].KrxOpenApiAdapter(
        api_key="krx-secret",
        client=client,
    )
    snapshot = adapter.fetch_stock_daily(
        market="KOSPI",
        market_date=date(2026, 8, 13),
        as_of=aware("2026-08-13T15:30:00+09:00"),
        collected_at=aware("2026-08-13T18:00:00+09:00"),
    )

    assert observed == {
        "method": "GET",
        "auth": "krx-secret",
        "date": "20260813",
    }
    assert "krx-secret" not in snapshot.metadata.endpoint
    assert "krx-secret" not in snapshot.metadata.source_key
    assert "krx-secret" not in snapshot.raw_payload_text
    assert snapshot.metadata.live_validation_status.value == "UNVERIFIED"


def test_opendart_adapter_preserves_receipt_without_leaking_key(
    modules: dict[str, Any],
) -> None:
    observed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["key"] = request.url.params["crtfc_key"]
        observed["report"] = request.url.params["reprt_code"]
        return httpx.Response(
            200,
            json={
                "status": "000",
                "message": "정상",
                "list": [{"rcept_no": "20260814000001"}],
            },
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = modules["adapters"].OpenDartAdapter(
        api_key="dart-secret",
        client=client,
    )
    dataset = modules["models"].SourceDataset.OPENDART_STOCK_TOTAL
    snapshot = adapter.fetch_periodic_report(
        dataset=dataset,
        corp_code="00000001",
        business_year=2026,
        report_code="11012",
        as_of=aware("2026-06-30T23:59:59+09:00"),
        collected_at=aware("2026-08-14T08:00:00+09:00"),
    )

    assert observed == {"method": "GET", "key": "dart-secret", "report": "11012"}
    assert snapshot.metadata.source_document_ids == ("20260814000001",)
    assert "dart-secret" not in snapshot.metadata.endpoint
    assert "dart-secret" not in snapshot.metadata.source_key
    assert "dart-secret" not in snapshot.raw_payload_text


@pytest.mark.parametrize(
    ("adapter_name", "keyword"),
    (("KrxOpenApiAdapter", "KRX_API_KEY"), ("OpenDartAdapter", "OPENDART_API_KEY")),
)
def test_missing_key_fails_before_transport(
    modules: dict[str, Any], adapter_name: str, keyword: str
) -> None:
    with pytest.raises(modules["errors"].MissingCredentialError) as caught:
        getattr(modules["adapters"], adapter_name)(api_key="")

    assert caught.value.blocker == "B-REFDATA-KEYS"
    assert caught.value.missing_names == (keyword,)


def test_transport_error_does_not_expose_secret(modules: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret-bearing transport detail", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = modules["adapters"].KrxOpenApiAdapter(
        api_key="do-not-print-this",
        client=client,
    )
    with pytest.raises(modules["errors"].SourceTransportError) as caught:
        adapter.fetch_stock_daily(
            market="KOSPI",
            market_date=date(2026, 8, 13),
            as_of=aware("2026-08-13T15:30:00+09:00"),
            collected_at=aware("2026-08-13T18:00:00+09:00"),
        )

    assert "do-not-print-this" not in str(caught.value)
    assert "secret-bearing" not in str(caught.value)
