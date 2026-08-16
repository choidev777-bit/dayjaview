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
    assert result.vocabulary_version == "1.1.0"
    assert result.transform_version == "catalyst-transform/1.0.0"


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
