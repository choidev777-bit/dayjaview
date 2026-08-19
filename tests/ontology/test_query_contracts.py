"""E-22 자연어 질의 17종 기계 계약."""

from __future__ import annotations

import re

import pytest

from packages.ontology.query_contracts import (
    QUERY_CONTRACT_BY_TYPE,
    QUERY_CONTRACT_VERSION,
    QUERY_CONTRACTS,
    CountUnit,
    QueryBundle,
    QueryPrerequisite,
    QuerySlot,
    QueryType,
    query_contract_content_hash,
    query_contract_document,
    validate_query_slots,
)


def test_query_contract_has_exactly_the_seventeen_product_types() -> None:
    assert len(QueryType) == 17
    assert tuple(contract.query_type for contract in QUERY_CONTRACTS) == tuple(QueryType)
    assert set(QUERY_CONTRACT_BY_TYPE) == set(QueryType)
    assert "COMPANY_APPEARANCE" not in {item.value for item in QueryType}
    assert "COMPANY_SIMILAR_CASE" not in {item.value for item in QueryType}


def test_query_contract_keeps_required_alternatives_and_count_units() -> None:
    company_value = QUERY_CONTRACT_BY_TYPE[QueryType.COMPANY_VALUE_SUMMARY]
    assert company_value.required_alternatives == (
        (QuerySlot.COMPANY, QuerySlot.AMOUNT_CONDITION),
        (QuerySlot.CATALYST_TYPE, QuerySlot.AMOUNT_CONDITION),
    )
    assert company_value.count_unit is CountUnit.VALUE_FACT
    assert company_value.prerequisites == (QueryPrerequisite.E22_STAGE_4,)

    outcome = QUERY_CONTRACT_BY_TYPE[QueryType.COMPANY_HISTORICAL_OUTCOME]
    # 소재·테마 축은 사건 당시 주도주 outcome이다(2026-08-19 개방).
    assert outcome.required_alternatives == (
        (QuerySlot.COMPANY, QuerySlot.PERIOD),
        (QuerySlot.EVENT, QuerySlot.PERIOD),
        (QuerySlot.CATALYST_TYPE, QuerySlot.PERIOD),
        (QuerySlot.THEME, QuerySlot.PERIOD),
    )
    assert outcome.bundle is QueryBundle.THIRD


def test_query_slot_validation_accepts_one_complete_alternative_only() -> None:
    assert validate_query_slots(
        QueryType.CATALYST_CERTAINTY, {"event": "cat-1"}
    ).query_type is QueryType.CATALYST_CERTAINTY
    assert validate_query_slots(
        "COMPANY_VALUE_SUMMARY",
        {"catalystType": "ORDER_CONTRACT", "amountCondition": ">=1000000000000"},
    ).query_type is QueryType.COMPANY_VALUE_SUMMARY

    with pytest.raises(ValueError, match="amountCondition"):
        validate_query_slots("COMPANY_VALUE_SUMMARY", {"company": "company-1"})
    with pytest.raises(ValueError, match="지원하지 않는 질의"):
        validate_query_slots("COMPANY_APPEARANCE", {"company": "company-1"})


def test_query_contract_document_and_hash_are_stable_and_machine_readable() -> None:
    document = query_contract_document()
    assert document["version"] == QUERY_CONTRACT_VERSION
    assert [row["queryType"] for row in document["queries"]] == [
        item.value for item in QueryType
    ]
    assert re.fullmatch(r"[0-9a-f]{64}", query_contract_content_hash())
    assert query_contract_content_hash() == query_contract_content_hash()
