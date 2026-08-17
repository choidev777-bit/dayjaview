"""현실 사건 절·단계·참여자·금액 구조화 회귀 검증."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from packages.infostock.hashing import sha256_text
from packages.ontology.company_roles import (
    CompanyRole,
    CompanyRoleEvidence,
    HistoryCompanyMention,
    HistoryCompanyRoleLabel,
)
from packages.ontology.event_structure import (
    ValueFactType,
    extract_catalyst_values,
    split_event_clauses,
    structure_history_catalysts,
)
from packages.ontology.participants import extract_actor_mentions
from packages.ontology.projects import (
    EventStage,
    detect_event_stage,
    extract_project_reference,
)

_COMPANY = "한화에어로스페이스"
_CODE = "012450"


def _label(
    raw_text: str,
    *,
    key: str,
    theme_id: str = "379",
    when: date = date(2024, 5, 2),
    role: CompanyRole = "ACTOR",
) -> HistoryCompanyRoleLabel:
    mention_start = raw_text.index(_COMPANY)
    evidence_end = raw_text.find(" 등에")
    if evidence_end < 0:
        evidence_end = len(raw_text)
    mention = HistoryCompanyMention(
        source_order=0,
        mention_kind="BODY",
        source_reference_order=None,
        mention_text=_COMPANY,
        start=mention_start,
        end=mention_start + len(_COMPANY),
        resolution_status="RESOLVED",
        resolution_basis="EXACT_ALIAS",
        seed_stock_code=_CODE,
        suggested_role=None,
        roles=(
            CompanyRoleEvidence(
                source_order=0,
                role=role,
                extraction_basis="BODY_RULE",
                start=mention_start,
                end=evidence_end,
            ),
        ),
    )
    return HistoryCompanyRoleLabel(
        company_master_version="company-master/test",
        role_transform_version="company-role-transform/test",
        source_theme_id=theme_id,
        theme_name=f"테마-{theme_id}",
        source_history_key=key,
        event_date=when,
        history_content_hash=sha256_text(raw_text),
        raw_text=raw_text,
        mentions=(mention,),
    )


def test_compound_sentence_splits_only_between_explicit_actions() -> None:
    raw_text = "삼성전자 신제품 출시, 카카오 서비스 공개 등에 상승"

    clauses = split_event_clauses(raw_text)

    assert [item.text for item in clauses] == [
        "삼성전자 신제품 출시",
        "카카오 서비스 공개",
    ]
    assert [raw_text[item.start : item.end] for item in clauses] == [
        item.text for item in clauses
    ]


def test_unspecified_stage_has_no_invented_evidence() -> None:
    evidence = detect_event_stage("새 사업 관련 소식")

    assert evidence.stage is EventStage.UNSPECIFIED
    assert evidence.keyword is None
    assert evidence.start is None
    assert evidence.end is None


def test_generic_business_topic_is_not_misread_as_a_project() -> None:
    assert extract_project_reference("로봇 사업 본격화 기대감") is None
    assert extract_project_reference("원전 사업 진출 기대감") is None
    assert extract_project_reference("폴란드 원전 프로젝트 수주") is None
    assert extract_project_reference("프로젝트 폴란드원전 수주") == "폴란드원전"
    assert extract_project_reference("대형 프로젝트 추진 기대감") is None
    assert extract_project_reference("해외 프로젝트 참여 발표") is None
    assert extract_project_reference("신한울 3·4호기 건설사업 수주") == "신한울 3·4호기"
    assert extract_project_reference("CES 2026 개최 소식") is None
    assert extract_project_reference("UAE 350억달러 발주 기대") is None
    assert extract_project_reference("폴란드 K9 수주 기대감") == "K9"


def test_company_names_and_delivery_use_do_not_create_false_geography() -> None:
    assert extract_actor_mentions("한국전력, 원전 수주") == ()
    assert extract_actor_mentions("인도네시아 공장 투자") == ()
    assert extract_actor_mentions("차량 인도 완료") == ()
    india = extract_actor_mentions("인도 정부와 공급계약 체결")
    assert len(india) == 1
    assert india[0].geography_code == "IN"
    assert india[0].actor_kind.value == "GOVERNMENT"


def test_percent_requires_stake_context_and_estimated_share_is_not_summed() -> None:
    assert extract_catalyst_values("매출 20% 증가") == ()
    stake = extract_catalyst_values("지분 20% 인수")
    assert [item.fact_type for item in stake] == [ValueFactType.STAKE_PERCENT]

    exact = extract_catalyst_values("회사 몫 3억원 공급계약 체결")[0]
    estimated = extract_catalyst_values("약 회사 몫 3억원 공급계약 체결")[0]
    assert exact.eligible_for_sum is True
    assert estimated.eligible_for_sum is False


def test_contract_clause_extracts_stage_project_counterparty_and_value() -> None:
    raw_text = (
        "한화에어로스페이스, 폴란드와 K9 3조 2,000억원 "
        "공급계약 체결 등에 상승"
    )

    draft = structure_history_catalysts(
        _label(raw_text, key="signed"),
        dataset_hash="d" * 64,
    )[0]

    assert draft.event_stage is EventStage.SIGNED
    assert draft.stage_evidence.keyword == "공급계약 체결"
    assert raw_text[
        draft.stage_evidence.start : draft.stage_evidence.end
    ] == "공급계약 체결"
    assert draft.project_reference == "K9"
    assert draft.project_id is not None
    assert draft.geography_codes == ("PL",)
    assert draft.participants[0].participant_role == "COUNTERPARTY"
    assert {(item.seed_stock_code, item.role) for item in draft.company_roles} == {
        (_CODE, "ACTOR")
    }
    contract_value = next(
        item for item in draft.values if item.fact_type is ValueFactType.CONTRACT_VALUE
    )
    assert contract_value.normalized_value == Decimal("3200000000000")
    assert raw_text[
        contract_value.evidence_start : contract_value.evidence_end
    ] == "3조 2,000억원"
    assert raw_text[
        draft.source_mention.start : draft.source_mention.end
    ] == draft.raw_text


def test_contract_expectation_is_rumor_not_signed() -> None:
    raw_text = "한화에어로스페이스, 폴란드 K9 계약 체결 기대감 등에 상승"

    draft = structure_history_catalysts(
        _label(raw_text, key="expected"),
        dataset_hash="d" * 64,
    )[0]

    assert draft.event_stage is EventStage.RUMOR
    assert draft.stage_evidence.keyword == "기대"


def test_structured_leader_alone_never_becomes_direct_company_role() -> None:
    raw_text = "폴란드 K9 수주 기대감 등에 상승"
    leader = HistoryCompanyMention(
        source_order=0,
        mention_kind="LEADER_LIST",
        source_reference_order=0,
        mention_text=_COMPANY,
        start=0,
        end=len(_COMPANY),
        resolution_status="RESOLVED",
        resolution_basis="STOCK_CODE",
        seed_stock_code=_CODE,
        suggested_role="LEADER",
        roles=(
            CompanyRoleEvidence(
                source_order=0,
                role="LEADER",
                extraction_basis="STRUCTURED_LEADER",
                start=0,
                end=len(_COMPANY),
            ),
        ),
    )
    label = HistoryCompanyRoleLabel(
        company_master_version="company-master/test",
        role_transform_version="company-role-transform/test",
        source_theme_id="379",
        theme_name="방산",
        source_history_key="leader-only",
        event_date=date(2024, 5, 2),
        history_content_hash=sha256_text(raw_text),
        raw_text=raw_text,
        mentions=(leader,),
    )

    draft = structure_history_catalysts(label, dataset_hash="d" * 64)[0]

    assert draft.company_roles == ()
    assert draft.reaction.leader_stock_codes == (_CODE,)
