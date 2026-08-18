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
    project_fingerprint,
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


def test_stage_detection_uses_current_semantic_state_not_bare_substrings() -> None:
    cases = {
        # 낙찰은 수주 확정 결과다 — 2026-08-19 사람 검수(S-008)가 재정의했다.
        "中 VBP 입찰 결과 오스템임플란트 최다 수량 낙찰 소식": EventStage.SIGNED,
        "NCC 연쇄 가동 중단 우려": EventStage.RUMOR,
        "환경영향평가 협의 완료 및 본 공사 착수 소식": EventStage.EXECUTING,
        "오는 3월 데이터센터 착공 계획": EventStage.RUMOR,
        # 핵협상 타결은 외교 사건의 종결 — 2026-08-19 사람 검수(S-134)가 재정의했다.
        "이란 핵협상 타결 소식": EventStage.COMPLETED,
        # 협상 결렬은 협상 국면 사건 — 2026-08-19 사람 검수(S-137)가 재정의했다.
        "가격 협상이 결렬됐다는 소식": EventStage.DISCUSSION,
        # 계획 차질은 계획 단계 사건 — 2026-08-19 사람 검수(S-307)가 재정의했다.
        "정부, 희토류 비축 계획 차질 소식": EventStage.UNSPECIFIED,
        "규제 개선 정책 연구 착수 소식": EventStage.EXECUTING,
        "미래 교통수단 지원 방안 모색 소식": EventStage.REVIEW,
    }

    for raw_text, expected in cases.items():
        assert detect_event_stage(raw_text).stage is expected


def test_stage_detection_rejects_non_event_compounds_and_denied_reports() -> None:
    samples = (
        "검찰, 'LH공사 보험 입찰 담합' 관련 손보사 7곳 압수수색",
        "브리지론 부도 등 건설사 책임 준공 리스크 부각",
        "한미 FTA 이행법안 美 의회 최종 통과 소식",
        "이재명 대선 예비후보, 퓨리오사AI 방문",
        "정부의 브라질 고속철도사업 수주 지원 소식",
        "청와대, 시진핑 방한 연기설 부인",
        "친중단체 관련 소식",
        "퀀텀에너지연구소 공동특허 출원 소식",
    )

    expected = (
        EventStage.UNSPECIFIED,
        EventStage.UNSPECIFIED,
        EventStage.COMPLETED,
        EventStage.UNSPECIFIED,
        EventStage.UNSPECIFIED,
        EventStage.UNSPECIFIED,
        EventStage.UNSPECIFIED,
        EventStage.UNSPECIFIED,
    )
    assert tuple(detect_event_stage(text).stage for text in samples) == expected


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
    assert extract_project_reference("폴란드 K9 수주 기대감") == "폴란드 K9"
    assert (
        extract_project_reference("루마니아 4조원대 K9 입찰")
        == "루마니아 K9"
    )
    assert extract_project_reference("K9 수주 기대감") is None


def test_project_identity_normalizes_aliases_without_event_participants() -> None:
    first = project_fingerprint(
        reference="스타게이트",
        company_codes=(),
        participant_keys=(),
    )
    alias = project_fingerprint(
        reference="스타게이트 프로젝트",
        company_codes=("034730",),
        participant_keys=("actor_a",),
    )
    domestic = project_fingerprint(
        reference="대한민국 3대 메가프로젝트",
        company_codes=(),
        participant_keys=("actor_kr",),
    )
    short = project_fingerprint(
        reference="3대 메가프로젝트",
        company_codes=(),
        participant_keys=(),
    )

    assert first == alias
    assert domestic == short


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


def test_value_extraction_rejects_number_substrings_and_price_returns() -> None:
    for raw_text in (
        "CES 2026 기대감 지속",
        "판매량 15개월 만에 최저치",
        "주가 3개월 새 20% 상승",
        "10대 공약 발표",
        "코로나19 대응체계 개편",
        "4680 원통형 배터리 공급 무산설",
        "40톤급 대형트럭 공개",
        "총 107GWh 규모 배터리 공급계약 체결",
        "캐시 우드, 서클(-15.49%) 주식 처분",
        "리튬아메리카스 지분 투자 추진 속 주가(+95.77%) 급등",
    ):
        assert extract_catalyst_values(raw_text) == ()


def test_value_extraction_handles_compound_units_and_local_money_context() -> None:
    quantity = extract_catalyst_values("대형 원전 8기 신규 건설 추진")[0]
    assert quantity.fact_type is ValueFactType.QUANTITY
    assert quantity.normalized_value == Decimal("8")

    capacity = extract_catalyst_values("요소 1만8,700톤 국내 반입 전망")[0]
    assert capacity.fact_type is ValueFactType.CAPACITY
    assert capacity.reported_value == "1만8,700톤"
    assert capacity.normalized_value == Decimal("18700")

    foreign = extract_catalyst_values(
        "GE와 약 3억2,000만 달러 규모 항공기 엔진 부품 계약 체결"
    )[0]
    assert foreign.fact_type is ValueFactType.CONTRACT_VALUE
    assert foreign.reported_value == "3억2,000만 달러"
    assert foreign.normalized_value == Decimal("320000000")
    assert foreign.currency == "USD"
    assert foreign.eligible_for_sum is False

    mixed = extract_catalyst_values(
        "엔비디아 시총 2조달러 재돌파 및 삼성전자 4조원대 HBM 공급계약"
    )
    assert len(mixed) == 1
    assert mixed[0].reported_value == "4조원대"
    assert mixed[0].fact_type is ValueFactType.CONTRACT_VALUE
    assert mixed[0].eligible_for_sum is False

    investment = extract_catalyst_values("이차전지 공급망 강화에 13조원 이상 투자")
    assert len(investment) == 1
    assert investment[0].fact_type is ValueFactType.INVESTMENT_VALUE
    assert investment[0].eligible_for_sum is False

    budget_and_contract = extract_catalyst_values(
        "AI 예산 10조원 편성 및 AWS와 380억달러 규모 계약 체결"
    )
    assert len(budget_and_contract) == 1
    assert budget_and_contract[0].reported_value == "380억달러"
    assert budget_and_contract[0].fact_type is ValueFactType.CONTRACT_VALUE


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
    assert draft.project_reference == "폴란드 K9"
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


def test_value_extraction_blocks_unrealized_and_context_mismatched_amounts() -> None:
    """2026-08-19 사람 검수가 잡은 금액 오류 유형을 고정한다."""

    # 돌파는 최소치다 — 합산하면 안 된다(M-027).
    breakthrough = extract_catalyst_values("방산 기업 수주 잔액 100조원 돌파 소식")
    assert [item.eligible_for_sum for item in breakthrough] == [False]

    # 해지·무산·사실무근 절의 금액은 성사액이 아니다(M-035·M-057).
    cancelled = extract_catalyst_values("테슬라와 3.8조원 규모 양극재 공급 계약 사실상 해지 소식")
    assert [item.eligible_for_sum for item in cancelled] == [False]

    # 유상증자 조달액은 시설투자가 아니다(M-056).
    raised = extract_catalyst_values("확보 목적 2조원 규모 유상증자 결정")
    assert raised == ()

    # 전력 이탈 규모는 생산·설비 용량이 아니다(M-002).
    assert extract_catalyst_values("데이터센터 1.5GW 전력 이탈 쇼크") == ()

    # 조·억이 쉼표로 이어진 외화는 전체 자릿수로 읽는다(M-065).
    compound = extract_catalyst_values("전기차 생산 위해 1조,2000억 달러 투자 소식")
    assert [item.normalized_value for item in compound] == [Decimal("1200000000000")]

    # 숫자 안 쉼표는 절 경계가 아니고, 괄호 삽입구가 표지 거리를 막지 않는다(M-033).
    clauses = split_event_clauses(
        "대한조선 2,760억원(최근 매출액대비 25.67%) 규모 원유운반석 2척 수주 소식 등에 상승"
    )
    values = [
        value
        for clause in clauses
        for value in extract_catalyst_values(clause.text, offset=clause.start)
    ]
    money = [value for value in values if value.unit == "KRW"]
    assert [item.normalized_value for item in money] == [Decimal("276000000000")]


def test_stage_detection_follows_2026_08_19_human_review_rulings() -> None:
    """사람 검수가 재정의한 단계 판정을 고정한다."""

    cases = {
        # 낙찰 = 수주 확정(S-008)
        "中 입찰 결과 최다 수량 낙찰 소식": EventStage.SIGNED,
        # 지연 우려·기대도 지연 사건이다(S-094·S-096)
        "애플 MR 헤드셋 출시 지연 우려 지속": EventStage.DELAYED,
        "비과세 한도 축소 연기 기대감": EventStage.DELAYED,
        # 검토는 기대·우려가 붙어도 검토다(S-182·S-200)
        "증권거래세 개편 검토 기대감 지속": EventStage.REVIEW,
        "美 반도체법 보조금 재검토 우려": EventStage.REVIEW,
        # 논의 예정은 아직 열리지 않았다(S-120)
        "국방장관과 방위산업 협력 논의 예정 소식": EventStage.RUMOR,
        # 협상 진전 기대는 진행 중 협상이다(S-127)
        "종전 협상 진전 기대감": EventStage.DISCUSSION,
        # 협상 결렬은 협상 국면 사건이다(S-137)
        "가격에 대한 2차 협상 결렬 소식": EventStage.DISCUSSION,
        # 국채 경매·입찰 불참은 사업 입찰이 아니다(S-014·S-016)
        "미국 20년물 입찰 부진 속 지수 하락": EventStage.UNSPECIFIED,
        # 이행 가속·원년·방안 발언은 실행 개시가 아니다(S-142·S-169)
        "합의 이행 가속화에 합의": EventStage.UNSPECIFIED,
    }
    for raw_text, expected in cases.items():
        assert detect_event_stage(raw_text).stage is expected, raw_text
