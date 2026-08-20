"""원인문 분류 transform (E-17).

문장은 실제 인포스탁 원인문 규격을 본뜬 합성 예문이다.
"""

from __future__ import annotations

from packages.ontology import classify_catalyst, parse_cause_sentence


def test_parse_strips_leader_suffix_and_direction_tail() -> None:
    parsed = parse_cause_sentence(
        "정부 스마트홈 육성 방안 발표 소식 등에 상승(주도주 : 코콤, 코맥스)"
    )
    assert parsed.core_text == "정부 스마트홈 육성 방안 발표 소식"
    assert parsed.trailing_reference_text == "(주도주 : 코콤, 코맥스)"
    assert parsed.direction_verb == "상승"


def test_parse_handles_bare_stock_list_and_newline() -> None:
    parsed = parse_cause_sentence(
        "비철금속 가격 상승으로 일부 관련주 상승\n(주도주 : 이구산업, 대창 등...)"
    )
    assert parsed.trailing_reference_text is not None
    assert parsed.direction_verb == "상승"
    assert "이구산업" not in parsed.core_text


def test_multi_label_keeps_source_order_and_first_is_primary() -> None:
    result = classify_catalyst(
        "필라델피아 반도체지수 급등 및 삼성전자 반도체 240조 투자 계획 발표 등에 상승"
    )
    assert result.type_ids[0] == "MARKET_SYNC"
    assert "INVESTMENT_CAPACITY" in result.type_ids
    assert result.primary_type_id == "MARKET_SYNC"


def test_longer_keyword_claims_span_over_shorter() -> None:
    # "재건축"(투자·증설)이 "재건"(국제 분쟁)을 선점한다.
    rebuild = classify_catalyst("강남 재건축 활성화 기대감 등에 상승")
    assert "INVESTMENT_CAPACITY" in rebuild.type_ids
    assert "GEOPOLITICS_GLOBAL" not in rebuild.type_ids
    # 금리 문장이 가격·수급으로 새지 않는다.
    rate = classify_catalyst("美 기준금리 인상 결정 소식에 하락")
    assert "MACRO_RATES_FX" in rate.type_ids
    assert "PRICE_SUPPLY" not in rate.type_ids


def test_direction_prefers_tail_verb_over_cause_mention() -> None:
    # 사유에 급락이 있어도 꼬리 동사가 상승이면 UP이다.
    result = classify_catalyst("국제유가 급락에 따른 비용 절감 기대감에 관련주 상승")
    assert result.direction == "UP"


def test_direction_mixed_and_unknown() -> None:
    assert classify_catalyst("원/달러 환율 급변으로 관련주들의 희비가 엇갈림").direction == "MIXED"
    assert classify_catalyst("업계 전반의 사업 재편 움직임").direction == "UNKNOWN"


def test_certainty_uses_rightmost_marker() -> None:
    # "타결 기대감"은 기대, "협상 타결 소식"은 확정 — 오른쪽 표지가 이긴다.
    assert classify_catalyst("무역협상 타결 기대감 등에 상승").certainty == "ANTICIPATION"
    assert classify_catalyst("무역협상 타결 소식 등에 상승").certainty == "CONFIRMED"
    assert classify_catalyst("신제품 출시 예정 속 일부 관련주 상승").certainty == "ANTICIPATION"


def test_certainty_uses_rightmost_marker_across_compound_causes() -> None:
    confirmed_first = classify_catalyst(
        "LG엔솔 공급계약 체결 및 추가 협력 기대감 등에 상승"
    )
    assert confirmed_first.primary_type_id == "ORDER_CONTRACT"
    assert confirmed_first.certainty == "ANTICIPATION"

    anticipation_first = classify_catalyst(
        "신공장 착공 예정 및 장기 공급계약 체결 소식 등에 상승"
    )
    assert anticipation_first.primary_type_id == "INVESTMENT_CAPACITY"
    assert anticipation_first.certainty == "CONFIRMED"


def test_certainty_unspecified_without_markers() -> None:
    assert classify_catalyst("저가 매수세 유입 등으로 상승").certainty == "UNSPECIFIED"


def test_continuation_flag() -> None:
    result = classify_catalyst("반도체 업황 호조 지속 등으로 상승")
    assert result.continuation is True
    assert classify_catalyst("반도체 업황 호조 소식에 상승").continuation is False


def test_unclassified_when_no_type_keyword_matches() -> None:
    result = classify_catalyst("개별 이슈로 상승")
    assert result.is_unclassified
    assert result.primary_type_id is None
    assert result.direction == "UP"


def test_evidence_spans_point_into_raw_text() -> None:
    raw = "정부 예산 확대 소식 등에 상승(주도주 : 조비)"
    result = classify_catalyst(raw)
    for span in result.evidence_spans:
        assert raw[span.start : span.end] == span.keyword


def test_versions_are_stamped() -> None:
    result = classify_catalyst("정부 지원 소식에 상승")
    assert result.vocabulary_version == "1.2.0"
    assert result.transform_version == "catalyst-transform/1.2.0"


def test_substring_collisions_do_not_fire() -> None:
    # "중국산"의 국산, "방송사"의 송사, "10억달러"의 달러가 새지 않는다.
    assert "PRODUCT_TECH" not in classify_catalyst("중국산 타이어 반덤핑 관세 결정에 상승").type_ids
    assert "LEGAL_RISK" not in classify_catalyst("방송사 광고영업 기대감에 상승").type_ids
    assert "MACRO_RATES_FX" not in classify_catalyst("GE 10억달러 투자 소식에 급등").type_ids


def test_deterministic_output() -> None:
    raw = "北 미사일 발사 소식 및 대북 제재 우려 등에 하락"
    assert classify_catalyst(raw) == classify_catalyst(raw)


def test_nk_missile_launch_does_not_leak_product_type() -> None:
    result = classify_catalyst("北 미사일 발사 소식에 하락")
    assert "GEOPOLITICS_GLOBAL" in result.type_ids
    assert "PRODUCT_TECH" not in result.type_ids


def test_tail_connector_does_not_swallow_verb_ending_in_deung() -> None:
    # "급등에 상승"의 "등에"는 열거 조사가 아니다 — core가 "…급"으로 잘리면
    # 유형(시세)과 확실성(급등) 표지를 같이 잃는다.
    result = classify_catalyst("국제 리튬 시세 급등에 상승")
    assert "PRICE_SUPPLY" in result.type_ids
    assert result.certainty == "CONFIRMED"
    assert result.direction == "UP"


def test_certainty_compound_marker_shadows_inner_confirmed() -> None:
    # "검토 소식"은 예정된 사건의 보도 — 안쪽 "소식"(확정)을 가린다.
    assert classify_catalyst("배터리 자체 생산 검토 소식에 하락").certainty == "ANTICIPATION"
    assert classify_catalyst("신공장 착공 예정 소식에 상승").certainty == "ANTICIPATION"
    # 표지 없는 "소식"은 여전히 확정이다.
    assert classify_catalyst("신공장 착공 소식에 상승").certainty == "CONFIRMED"


def test_certainty_statement_and_rumor_markers() -> None:
    assert classify_catalyst("정부 고위 관계자 지원 발언 등에 상승").certainty == "CONFIRMED"
    assert classify_catalyst("대형 플랫폼 기업 피인수설에 급등").certainty == "ANTICIPATION"


def test_substring_shields_keep_specific_type_first() -> None:
    # 긴 특수 키워드가 안쪽 일반 키워드("임상", "후보", "공급", "수출")를 선점한다.
    asco = classify_catalyst("미국임상종양학회(ASCO) 참가 기대감 등에 상승")
    assert asco.primary_type_id == "EVENT_CONFERENCE"
    candidate = classify_catalyst("바이오 기업 후보물질 기술이전 소식에 상승")
    assert candidate.primary_type_id == "CLINICAL_REGULATORY"
    housing = classify_catalyst("주택공급 확대 방안 발표 소식에 상승")
    assert housing.primary_type_id == "POLICY_MEASURE"
    license_out = classify_catalyst("제약사 조 단위 기술수출 소식 등에 상승")
    assert license_out.primary_type_id == "ORDER_CONTRACT"
    ipo = classify_catalyst("기관 수요예측 흥행 소식에 상승")
    assert ipo.primary_type_id == "CAPITAL_MARKET_EVENT"


def test_sanction_is_trade_not_legal() -> None:
    result = classify_catalyst("美 중국 통신장비업체 제재에 따른 반사이익 기대감 등에 상승")
    assert result.primary_type_id == "TRADE_TARIFF"


def test_fallback_subject_noun_fires_only_without_action_keyword() -> None:
    # 행위어가 없으면 주제어(로봇)가 primary가 된다.
    bare = classify_catalyst("휴머노이드 로봇 테마 부상 등에 상승")
    assert bare.primary_type_id == "PRODUCT_TECH"
    # 행위어(수요)가 있으면 주제어는 primary를 빼앗지 않고 뒤에 붙는다.
    demand = classify_catalyst("데이터센터 서버용 메모리 수요 회복 전망 등에 상승")
    assert demand.primary_type_id == "DEMAND_INDUSTRY"
    assert "INVESTMENT_CAPACITY" in demand.type_ids


def test_market_sync_fallback_for_bare_price_move_cause() -> None:
    result = classify_catalyst("美 대형 기술기업 주가 급등 여파 등에 상승")
    assert "MARKET_SYNC" in result.type_ids
    bare = classify_catalyst("해외 동종업체 급락 속 하락")
    assert bare.primary_type_id == "MARKET_SYNC"


def test_concrete_policy_action_beats_actor_or_industry_context() -> None:
    politics = classify_catalyst(
        "더불어민주당, 드라마·영화·웹툰·게임 지원정책 추진 소식 등에 상승"
    )
    assert politics.primary_type_id == "POLICY_MEASURE"
    assert "POLITICS_ELECTION" in politics.type_ids

    clinical = classify_catalyst("식약처, K-화장품 미국 수출 지원 소식 등에 상승")
    assert clinical.primary_type_id == "POLICY_MEASURE"
    assert "CLINICAL_REGULATORY" in clinical.type_ids

    short_sale = classify_catalyst("내년 6월까지 공매도 전면 금지 속 상승")
    assert short_sale.primary_type_id == "POLICY_MEASURE"
    assert "FLOW_TECHNICAL" in short_sale.type_ids

    election = classify_catalyst(
        "美 대통령 선거 바이든 당선 소식에 따른 정책 기대감 등에 상승"
    )
    assert election.primary_type_id == "POLITICS_ELECTION"
    assert "POLICY_MEASURE" in election.type_ids

    legislation = classify_catalyst(
        "딥페이크 성범죄 처벌 강화 법사위 통과 소식 등에 상승"
    )
    assert legislation.primary_type_id == "POLICY_MEASURE"
    assert "LEGAL_RISK" in legislation.type_ids


def test_policy_action_does_not_override_a_separate_first_event() -> None:
    legal = classify_catalyst(
        "공정위, 카카오 제재절차 착수 소식 및 전기통신사업법 개정안 발의 소식에 하락"
    )
    assert legal.primary_type_id == "LEGAL_RISK"
    assert "POLICY_MEASURE" in legal.type_ids

    statement = classify_catalyst(
        "트럼프 대통령 반도체법 폐지 관련 발언, 보조금 지급 무산 우려에 하락"
    )
    assert statement.primary_type_id == "STATEMENT_REMARK"
    assert "POLICY_MEASURE" in statement.type_ids

    politics = classify_catalyst(
        "글로벌 우파 정치 세력 약진 속 전기차 지원 정책 후퇴 우려 등에 하락"
    )
    assert politics.primary_type_id == "POLITICS_ELECTION"
    assert "POLICY_MEASURE" in politics.type_ids


def test_market_index_event_beats_flow_context() -> None:
    result = classify_catalyst(
        "반도체 과열론 속 美 필라델피아 반도체지수 약세 영향 등에 하락"
    )
    assert result.primary_type_id == "MARKET_SYNC"
    assert "FLOW_TECHNICAL" in result.type_ids


def test_topic_clause_match_keeps_korean_robot_policy_and_drops_lookalikes() -> None:
    """"로봇+정책" 질문에서 진짜 국내 로봇 정책만 남는다(2026-08-20 실측 문장)."""

    from packages.ontology.transform import topic_clause_match

    kept = (
        "정부 로봇산업 육성 계획 발표 등에 일부 관련주 상승(주도주 : 져스텍, 클로봇)",
        "정부, 로봇 규제 정비 논의 소식에 상승",
        "산업부, 로봇테스트필드에 2,000억 투입 소식 및 엔젤로보틱스 상장 기대감 지속 등에 상승",
    )
    dropped = (
        # 정책은 노란봉투법, 로봇은 ETF 편입 — 서로 다른 소식 조각이다.
        "노란봉투법 시행 및 레인보우로보틱스 등 로봇주 코스닥 액티브 ETF 구성종목으로 편입 소식 등에 상승",
        # 금감원 '승인'은 개별 기업 심사지 정책 발표가 아니다.
        "두산로보틱스, 밥캣 편입안 금감원 승인 소식 및 트럼프 당선에 따른 로봇 시장 확대 기대감 지속 등에 상승",
        # 해외 정부·인사(미국·한미 포함)는 뺀다 — 사용자 결정.
        "머스크 美 정부효율부 수장 발탁에 따른 휴머노이드 로봇 산업 기대감 부각 등에 일부 관련주 상승",
        "한미 정부 로봇 산업 육성 정책 기대감 지속 등에 상승",
        # 정책 대상이 AI고 로봇은 여파면 뺀다 — 사용자 결정.
        "이재명 정부, AI 100조 펀드 조성에 따른 로보틱스 산업 수혜 전망 등에 상승",
        # 주체가 기업이면 정책이 아니다.
        "오픈AI, 자체 휴머노이드 로봇 개발 방안 논의 소식 및 조선업계, 로봇 도입 확대 기대감 지속 등에 상승",
    )
    for text in kept:
        assert topic_clause_match(text, "로봇", "POLICY_MEASURE"), text
    for text in dropped:
        assert not topic_clause_match(text, "로봇", "POLICY_MEASURE"), text
    # 정책이 아닌 유형은 주제어와 유형 낱말이 같은 조각에 있으면 된다.
    assert topic_clause_match(
        "한화에어로스페이스, 폴란드 로봇 무기체계 수주 소식 및 삼성전자 실적 발표 등에 상승",
        "로봇",
        "ORDER_CONTRACT",
    )
