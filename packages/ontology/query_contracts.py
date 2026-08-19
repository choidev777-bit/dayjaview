"""자연어 리서치 질의 17종의 기계 계약 (E-22 단계 0).

이 모듈은 자연어 문장을 해석하지 않는다. 해석기가 만들 수 있는 질의 종류,
필수 슬롯 조합, 집계 단위, 선행 단계를 버전과 함께 고정한다. 같은 계약 문서는
항상 같은 hash를 만들며 단계 5의 QueryPlan 검증과 cache key가 이를 재사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any, Mapping

from packages.infostock.hashing import sha256_json

QUERY_CONTRACT_VERSION = "query-contract/1.1.0"


class QueryType(StrEnum):
    DAY_MOVERS = "DAY_MOVERS"
    PERIOD_SUMMARY = "PERIOD_SUMMARY"
    STOCK_DAY_REASON = "STOCK_DAY_REASON"
    STOCK_TOP_MOVES = "STOCK_TOP_MOVES"
    STOCK_THEME_MEMBERSHIP = "STOCK_THEME_MEMBERSHIP"
    STOCK_COOCCURRENCE = "STOCK_COOCCURRENCE"
    THEME_MEMBERS = "THEME_MEMBERS"
    THEME_HISTORY = "THEME_HISTORY"
    THEME_COMPARISON = "THEME_COMPARISON"
    THEME_FREQUENCY = "THEME_FREQUENCY"
    CATALYST_THEME_REACTION = "CATALYST_THEME_REACTION"
    CATALYST_FREQUENCY = "CATALYST_FREQUENCY"
    CATALYST_CERTAINTY = "CATALYST_CERTAINTY"
    CATALYST_CONTINUATION = "CATALYST_CONTINUATION"
    COMPANY_DIRECT_EVENT = "COMPANY_DIRECT_EVENT"
    COMPANY_VALUE_SUMMARY = "COMPANY_VALUE_SUMMARY"
    COMPANY_HISTORICAL_OUTCOME = "COMPANY_HISTORICAL_OUTCOME"


class QuerySlot(StrEnum):
    DATE = "date"
    DATE_RANGE = "dateRange"
    STOCK = "stock"
    THEME = "theme"
    THEMES = "themes"
    PERIOD = "period"
    CATALYST_TYPE = "catalystType"
    EVENT = "event"
    COMPANY = "company"
    AMOUNT_CONDITION = "amountCondition"
    # 소재 안을 좁히는 주제어("로봇 정책"의 로봇). 필수 조합에는 안 들어간다.
    TOPIC = "topic"


class CountUnit(StrEnum):
    DAILY_SECTION = "DAILY_SECTION"
    DAILY_STOCK_ROW = "DAILY_STOCK_ROW"
    CURRENT_THEME_MEMBERSHIP = "CURRENT_THEME_MEMBERSHIP"
    SOURCE_RECORD = "SOURCE_RECORD"
    THEME_REACTION = "THEME_REACTION"
    CATALYST = "CATALYST"
    VALUE_FACT = "VALUE_FACT"
    OUTCOME_OBSERVATION = "OUTCOME_OBSERVATION"


class QueryPrerequisite(StrEnum):
    NONE = "NONE"
    E17_LABELS_DB = "E17_LABELS_DB"
    E22_STAGE_1 = "E22_STAGE_1"
    E22_STAGE_2 = "E22_STAGE_2"
    E22_STAGE_3 = "E22_STAGE_3"
    E22_STAGE_4 = "E22_STAGE_4"
    E22_STAGE_6 = "E22_STAGE_6"


class QueryBundle(IntEnum):
    FIRST = 1
    SECOND = 2
    THIRD = 3


@dataclass(frozen=True, slots=True)
class QueryContract:
    """한 질의 유형의 필수 입력과 집계 경계를 고정한다.

    ``required_alternatives``의 각 튜플은 하나의 유효한 필수 슬롯 조합이다.
    예를 들어 회사 또는 소재를 받을 수 있는 질의는 두 조합을 가진다.
    """

    query_type: QueryType
    required_alternatives: tuple[tuple[QuerySlot, ...], ...]
    count_unit: CountUnit
    prerequisites: tuple[QueryPrerequisite, ...]
    bundle: QueryBundle
    supports_direction: bool = True

    def __post_init__(self) -> None:
        if not self.required_alternatives:
            raise ValueError("필수 슬롯 조합은 하나 이상이어야 합니다.")
        for alternative in self.required_alternatives:
            if not alternative:
                raise ValueError("빈 필수 슬롯 조합은 허용하지 않습니다.")
            if len(set(alternative)) != len(alternative):
                raise ValueError("한 필수 슬롯 조합에 중복 슬롯이 있습니다.")
        if not self.prerequisites:
            raise ValueError("선행 단계는 NONE이라도 명시해야 합니다.")
        if QueryPrerequisite.NONE in self.prerequisites and len(self.prerequisites) > 1:
            raise ValueError("NONE과 실제 선행 단계를 함께 둘 수 없습니다.")

    def accepts(self, slots: Mapping[str | QuerySlot, object]) -> bool:
        """필수 슬롯 조합 중 하나가 비어 있지 않은 값으로 채워졌는지 확인한다."""

        supplied = {
            str(key): value
            for key, value in slots.items()
            if value is not None and value != "" and value != () and value != []
        }
        return any(
            all(slot.value in supplied for slot in alternative)
            for alternative in self.required_alternatives
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "queryType": self.query_type.value,
            "requiredAlternatives": [
                [slot.value for slot in alternative]
                for alternative in self.required_alternatives
            ],
            "countUnit": self.count_unit.value,
            "prerequisites": [item.value for item in self.prerequisites],
            "bundle": int(self.bundle),
            "supportsDirection": self.supports_direction,
        }


def _one(*slots: QuerySlot) -> tuple[tuple[QuerySlot, ...], ...]:
    return (slots,)


QUERY_CONTRACTS: tuple[QueryContract, ...] = (
    QueryContract(
        QueryType.DAY_MOVERS,
        _one(QuerySlot.DATE),
        CountUnit.DAILY_SECTION,
        (QueryPrerequisite.E22_STAGE_1,),
        QueryBundle.FIRST,
    ),
    QueryContract(
        QueryType.PERIOD_SUMMARY,
        _one(QuerySlot.DATE_RANGE),
        CountUnit.DAILY_SECTION,
        (QueryPrerequisite.E22_STAGE_1,),
        QueryBundle.SECOND,
    ),
    QueryContract(
        QueryType.STOCK_DAY_REASON,
        _one(QuerySlot.STOCK, QuerySlot.DATE),
        CountUnit.DAILY_SECTION,
        (QueryPrerequisite.E22_STAGE_1, QueryPrerequisite.E22_STAGE_2),
        QueryBundle.FIRST,
    ),
    QueryContract(
        QueryType.STOCK_TOP_MOVES,
        _one(QuerySlot.STOCK, QuerySlot.PERIOD),
        CountUnit.DAILY_STOCK_ROW,
        (QueryPrerequisite.E22_STAGE_1, QueryPrerequisite.E22_STAGE_2),
        QueryBundle.SECOND,
    ),
    QueryContract(
        QueryType.STOCK_THEME_MEMBERSHIP,
        _one(QuerySlot.STOCK),
        CountUnit.CURRENT_THEME_MEMBERSHIP,
        (QueryPrerequisite.E22_STAGE_2,),
        QueryBundle.FIRST,
    ),
    QueryContract(
        QueryType.STOCK_COOCCURRENCE,
        _one(QuerySlot.STOCK, QuerySlot.PERIOD),
        CountUnit.CATALYST,
        (QueryPrerequisite.E22_STAGE_4,),
        QueryBundle.THIRD,
    ),
    QueryContract(
        QueryType.THEME_MEMBERS,
        _one(QuerySlot.THEME),
        CountUnit.CURRENT_THEME_MEMBERSHIP,
        (QueryPrerequisite.NONE,),
        QueryBundle.SECOND,
    ),
    QueryContract(
        QueryType.THEME_HISTORY,
        _one(QuerySlot.THEME),
        CountUnit.SOURCE_RECORD,
        (QueryPrerequisite.E17_LABELS_DB,),
        QueryBundle.SECOND,
    ),
    QueryContract(
        QueryType.THEME_COMPARISON,
        _one(QuerySlot.THEMES, QuerySlot.PERIOD),
        CountUnit.DAILY_SECTION,
        (QueryPrerequisite.E22_STAGE_1,),
        QueryBundle.THIRD,
    ),
    QueryContract(
        QueryType.THEME_FREQUENCY,
        _one(QuerySlot.PERIOD),
        CountUnit.CATALYST,
        (QueryPrerequisite.E22_STAGE_4,),
        QueryBundle.THIRD,
    ),
    QueryContract(
        QueryType.CATALYST_THEME_REACTION,
        _one(QuerySlot.CATALYST_TYPE),
        CountUnit.THEME_REACTION,
        (QueryPrerequisite.E17_LABELS_DB,),
        QueryBundle.THIRD,
    ),
    QueryContract(
        QueryType.CATALYST_FREQUENCY,
        _one(QuerySlot.CATALYST_TYPE, QuerySlot.PERIOD),
        CountUnit.CATALYST,
        (QueryPrerequisite.E22_STAGE_4,),
        QueryBundle.THIRD,
    ),
    QueryContract(
        QueryType.CATALYST_CERTAINTY,
        ((QuerySlot.CATALYST_TYPE,), (QuerySlot.EVENT,)),
        CountUnit.CATALYST,
        (QueryPrerequisite.E22_STAGE_4,),
        QueryBundle.THIRD,
    ),
    QueryContract(
        QueryType.CATALYST_CONTINUATION,
        ((QuerySlot.CATALYST_TYPE,), (QuerySlot.THEME,)),
        CountUnit.CATALYST,
        (QueryPrerequisite.E22_STAGE_4,),
        QueryBundle.THIRD,
    ),
    QueryContract(
        QueryType.COMPANY_DIRECT_EVENT,
        _one(QuerySlot.COMPANY),
        CountUnit.CATALYST,
        (QueryPrerequisite.E22_STAGE_3,),
        QueryBundle.THIRD,
    ),
    QueryContract(
        QueryType.COMPANY_VALUE_SUMMARY,
        (
            (QuerySlot.COMPANY, QuerySlot.AMOUNT_CONDITION),
            (QuerySlot.CATALYST_TYPE, QuerySlot.AMOUNT_CONDITION),
        ),
        CountUnit.VALUE_FACT,
        (QueryPrerequisite.E22_STAGE_4,),
        QueryBundle.THIRD,
    ),
    QueryContract(
        QueryType.COMPANY_HISTORICAL_OUTCOME,
        (
            (QuerySlot.COMPANY, QuerySlot.PERIOD),
            (QuerySlot.EVENT, QuerySlot.PERIOD),
            # 회사 없이 소재·테마로 물으면 사건 당시 주도주의 실제 결과를
            # 계산한다(계획서 4.1 "회사 또는 당시 주도주 outcome").
            (QuerySlot.CATALYST_TYPE, QuerySlot.PERIOD),
            (QuerySlot.THEME, QuerySlot.PERIOD),
        ),
        CountUnit.OUTCOME_OBSERVATION,
        (QueryPrerequisite.E22_STAGE_6,),
        QueryBundle.THIRD,
    ),
)

QUERY_CONTRACT_BY_TYPE: Mapping[QueryType, QueryContract] = {
    contract.query_type: contract for contract in QUERY_CONTRACTS
}


def query_contract_document() -> dict[str, Any]:
    """버전과 순서를 포함한 직렬화 가능한 정본 문서를 반환한다."""

    return {
        "version": QUERY_CONTRACT_VERSION,
        "queries": [contract.as_dict() for contract in QUERY_CONTRACTS],
    }


def query_contract_content_hash() -> str:
    return sha256_json(query_contract_document())


def validate_query_slots(
    query_type: QueryType | str,
    slots: Mapping[str | QuerySlot, object],
) -> QueryContract:
    """필수 슬롯을 검증하고 대응 계약을 돌려준다."""

    try:
        normalized = QueryType(query_type)
    except ValueError as exc:
        raise ValueError(f"지원하지 않는 질의 유형입니다: {query_type}") from exc
    contract = QUERY_CONTRACT_BY_TYPE[normalized]
    if not contract.accepts(slots):
        alternatives = [
            "+".join(slot.value for slot in alternative)
            for alternative in contract.required_alternatives
        ]
        raise ValueError(
            f"{normalized.value} 필수 슬롯이 없습니다: {' 또는 '.join(alternatives)}"
        )
    return contract
