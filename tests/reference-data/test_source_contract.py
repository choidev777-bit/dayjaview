from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import FIXTURE_ROOT, REPOSITORY_ROOT, aware


@pytest.mark.parametrize(
    "fixture_name",
    (
        "krx-stock-daily.json",
        "opendart-stock-total.json",
        "opendart-largest-shareholder.json",
        "opendart-treasury.json",
    ),
)
def test_fixture_contract_preserves_source_metadata(
    load_fixture, fixture_name: str
) -> None:
    snapshot = load_fixture(fixture_name)

    assert snapshot.metadata.as_of.isoformat()
    assert snapshot.metadata.collected_at.isoformat()
    assert snapshot.metadata.lineage
    assert snapshot.metadata.revision == 1
    assert snapshot.metadata.parser_version == "reference-source-2026.08.1"
    assert snapshot.metadata.live_validation_status.value == "UNVERIFIED"
    assert len(snapshot.raw_hash) == 64


def test_krx_parser_normalizes_official_daily_fields(
    modules: dict[str, Any], load_fixture
) -> None:
    snapshot = load_fixture("krx-stock-daily.json")
    observations = modules["parsers"].parse_krx_stock_daily(snapshot)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.stock_code == "A00001"
    assert str(observation.close) == "51000"
    assert observation.listed_shares == 100_000_000
    assert str(observation.implied_previous_adjusted_close) == "50000"
    assert observation.metadata is snapshot.metadata


def test_opendart_stock_total_normalizes_receipt_and_treasury(
    modules: dict[str, Any], load_fixture
) -> None:
    snapshot = load_fixture("opendart-stock-total.json")
    normalized = modules["parsers"].parse_open_dart(snapshot, stock_code="A00001")

    assert normalized.issued_share_observations[0].value == 100_000_000
    assert normalized.non_float_holdings[0].holder_id == "ISSUER_TREASURY"
    assert normalized.non_float_holdings[0].shares == 10_000_000
    assert normalized.issued_share_observations[0].metadata.source_document_ids == (
        "20260814000001",
    )


def test_opendart_holders_keep_categories_and_stable_identity(
    modules: dict[str, Any], load_fixture
) -> None:
    snapshot = load_fixture("opendart-largest-shareholder.json")
    first = modules["parsers"].parse_open_dart(snapshot, stock_code="A00001")
    second = modules["parsers"].parse_open_dart(snapshot, stock_code="A00001")

    assert [item.holder_id for item in first.non_float_holdings] == [
        item.holder_id for item in second.non_float_holdings
    ]
    assert {item.category.value for item in first.non_float_holdings} == {
        "CONTROLLING_HOLDER",
        "STRATEGIC_LOCKUP",
    }


def test_opendart_holders_skip_share_class_total_row(
    modules: dict[str, Any], load_fixture
) -> None:
    """실제 hyslrSttus는 주식 종류별 '계' row를 붙여 보낸다. 이중 차감되면 안 된다."""

    snapshot = load_fixture("opendart-largest-shareholder.json")
    normalized = modules["parsers"].parse_open_dart(snapshot, stock_code="A00001")

    assert [item.holder_name for item in normalized.non_float_holdings] == [
        "예시홀딩스",
        "예시인",
        "전략투자자",
    ]
    assert sum(item.shares for item in normalized.non_float_holdings) == 35_000_000


def test_opendart_stock_total_reads_dash_treasury_as_zero(
    modules: dict[str, Any],
) -> None:
    """자사주가 없는 회사는 tesstk_co가 숫자가 아니라 '-'로 온다."""

    models = modules["models"]
    hashing = modules["hashing"]
    timestamp = aware("2026-06-30T00:00:00+09:00")
    payload = {
        "status": "000",
        "message": "정상",
        "list": [
            {
                "rcept_no": "20260814000001",
                "corp_code": "00000001",
                "se": "보통주",
                "istc_totqy": "62,000,000",
                "tesstk_co": "-",
                "stlm_dt": "2026-06-30",
            }
        ],
    }
    raw_text = hashing.canonical_json(payload)
    snapshot = models.SourceSnapshot(
        metadata=models.SourceMetadata(
            provider=models.SourceProvider.OPENDART,
            dataset=models.SourceDataset.OPENDART_STOCK_TOTAL,
            endpoint="https://opendart.fss.or.kr/api/stockTotqySttus.json",
            source_key="00000001:2026:11012",
            as_of=timestamp,
            collected_at=aware("2026-08-15T09:00:00+09:00"),
            parser_version="reference-source-2026.08.1",
            revision=1,
            lineage=("opendart:stock-total:00000001:2026:11012",),
            source_document_ids=("20260814000001",),
        ),
        raw_payload_text=raw_text,
        raw_hash=hashing.sha256_text(raw_text),
    )

    normalized = modules["parsers"].parse_open_dart(snapshot, stock_code="A00001")

    assert normalized.issued_share_observations[0].value == 62_000_000
    assert normalized.non_float_holdings[0].holder_id == "ISSUER_TREASURY"
    assert normalized.non_float_holdings[0].shares == 0


def test_opendart_treasury_total_has_same_economic_holder_key(
    modules: dict[str, Any], load_fixture
) -> None:
    stock_total = modules["parsers"].parse_open_dart(
        load_fixture("opendart-stock-total.json"), stock_code="A00001"
    )
    treasury = modules["parsers"].parse_open_dart(
        load_fixture("opendart-treasury.json"), stock_code="A00001"
    )

    assert (
        stock_total.non_float_holdings[0].economic_key
        == treasury.non_float_holdings[0].economic_key
    )
    assert stock_total.non_float_holdings[0].shares == treasury.non_float_holdings[0].shares


def _payload(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def test_hash_mutation_is_rejected(modules: dict[str, Any]) -> None:
    payload = _payload("krx-stock-daily.json")
    payload["rawResponse"]["OutBlock_1"][0]["LIST_SHRS"] = "1"

    with pytest.raises(modules["errors"].SourceContractError) as caught:
        modules["parsers"].parse_fixture_payload(payload)

    assert caught.value.code == "HASH_MISMATCH"


def test_fixture_cannot_claim_live_verified(modules: dict[str, Any]) -> None:
    payload = _payload("krx-stock-daily.json")
    payload["liveValidationStatus"] = "VERIFIED"

    with pytest.raises(modules["errors"].SourceContractError) as caught:
        modules["parsers"].parse_fixture_payload(payload)

    assert caught.value.code == "LIVE_STATUS_MISMATCH"


def test_endpoint_mismatch_is_rejected(modules: dict[str, Any]) -> None:
    payload = _payload("opendart-stock-total.json")
    payload["endpoint"] = "https://example.com/not-official"

    with pytest.raises(modules["errors"].SourceContractError) as caught:
        modules["parsers"].parse_fixture_payload(payload)

    assert caught.value.code == "ENDPOINT_MISMATCH"


def test_fixture_path_is_confined(modules: dict[str, Any], tmp_path: Path) -> None:
    outside = tmp_path / "source.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(modules["errors"].SourceContractError) as caught:
        modules["parsers"].load_source_fixture(
            outside,
            repository_root=REPOSITORY_ROOT,
        )

    assert caught.value.code == "UNAPPROVED_FIXTURE_PATH"
