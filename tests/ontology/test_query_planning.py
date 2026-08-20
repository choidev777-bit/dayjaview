from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from packages.ontology import (
    CompanyAliasDraft,
    CompanyDraft,
    CompanyInstrumentDraft,
    CompanyMaster,
    CountUnit,
    FailureReason,
    PublicFailure,
    QueryType,
    QuestionCatalog,
    RelativeExpression,
    ThemeEntry,
    plan_question,
)

TODAY = date(2026, 8, 17)


def _alias(
    name: str,
    *,
    alias_type: str = "CURRENT_NAME",
    valid_from: date | None = None,
    valid_to: date | None = None,
) -> CompanyAliasDraft:
    return CompanyAliasDraft(
        alias=name,
        normalized_alias=name.replace(" ", "").casefold(),
        alias_type=alias_type,  # type: ignore[arg-type]
        validity_basis="KRX_LISTING",
        source_authority="KRX_LISTING",
        valid_from=valid_from,
        valid_to=valid_to,
        mention_count=1,
    )


def _company(
    code: str,
    name: str,
    *,
    aliases: tuple[CompanyAliasDraft, ...] = (),
) -> CompanyDraft:
    return CompanyDraft(
        seed_stock_code=code,
        canonical_name=name,
        name_basis="KRX_LISTING",
        dart_corp_code=None,
        aliases=(_alias(name), *aliases),
        instruments=(
            CompanyInstrumentDraft(
                stock_code=code,
                share_class="COMMON",
                link_basis="STOCK_CODE",
                valid_from=None,
                valid_to=None,
            ),
        ),
        revisions=(),
    )


def _catalog() -> QuestionCatalog:
    master = CompanyMaster(
        master_version="company-master/1.0.0",
        companies=(
            _company("005930", "삼성전자"),
            _company(
                "012450",
                "한화에어로스페이스",
                aliases=(
                    _alias(
                        "한화테크윈",
                        alias_type="PAST_NAME",
                        valid_from=date(2015, 1, 1),
                        valid_to=date(2018, 3, 31),
                    ),
                ),
            ),
            _company("065350", "신성델타테크"),
            # 같은 이름을 쓰는 두 회사. 임의로 고르면 안 된다.
            _company("111111", "대한제강"),
            _company("222222", "대한제강홀딩스", aliases=(_alias("대한제강"),)),
        ),
        unresolved=(),
    )
    return QuestionCatalog(
        company_master=master,
        themes=(
            ThemeEntry("101", "2차전지"),
            ThemeEntry("102", "반도체 대표주(생산)"),
        ),
    )


def _plan(question: str):
    result = plan_question(question, catalog=_catalog(), today=TODAY)
    assert result.failure is None, result.failure
    assert result.plan is not None
    return result.plan


def _failure(question: str):
    result = plan_question(question, catalog=_catalog(), today=TODAY)
    assert result.plan is None
    assert result.failure is not None
    return result.failure


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("2026-06-29에 뭐가 올랐어?", QueryType.DAY_MOVERS),
        ("6/29 뭐 떨어졌어", QueryType.DAY_MOVERS),
        ("이번 주 시장 어땠어?", QueryType.PERIOD_SUMMARY),
        ("삼성전자 2026-06-29에 왜 올랐어?", QueryType.STOCK_DAY_REASON),
        ("삼성전자 올해 급락일 정리해줘", QueryType.STOCK_TOP_MOVES),
        ("삼성전자 어떤 테마에 속해?", QueryType.STOCK_THEME_MEMBERSHIP),
        ("삼성전자랑 같이 움직이는 종목은?", QueryType.STOCK_COOCCURRENCE),
        ("2차전지 테마에 어떤 종목이 있어?", QueryType.THEME_MEMBERS),
        ("2차전지 테마 과거에 뭘로 움직였어?", QueryType.THEME_HISTORY),
        ("2차전지랑 반도체 대표주(생산) 중에 올해 어디가 더 셌어?", QueryType.THEME_COMPARISON),
        ("최근 3개월 자주 나온 테마", QueryType.THEME_FREQUENCY),
        ("정책 소재에 과거 어떤 테마가 반응했어?", QueryType.CATALYST_THEME_REACTION),
        ("올해 정책 소재 몇 번 나왔어?", QueryType.CATALYST_FREQUENCY),
        ("정책 소재는 기대감이었어 확정이었어?", QueryType.CATALYST_CERTAINTY),
        ("정책 재부각인지 알려줘", QueryType.CATALYST_CONTINUATION),
        ("한화에어로스페이스가 직접 한 일만 알려줘", QueryType.COMPANY_DIRECT_EVENT),
        ("한화에어로스페이스 1조 넘는 수주 몇 건이야?", QueryType.COMPANY_VALUE_SUMMARY),
        (
            "한화에어로스페이스 수주 발표 뒤에 주가 어떻게 됐어?",
            QueryType.COMPANY_HISTORICAL_OUTCOME,
        ),
    ],
)
def test_seventeen_query_types_are_reachable(question: str, expected: QueryType) -> None:
    plan = _plan(question)
    assert plan.query_type is expected
    assert plan.count_unit is CountUnit(plan.count_unit)


def test_direction_is_symmetric_between_up_and_down() -> None:
    assert _plan("2026-06-29에 뭐가 올랐어?").direction == "UP"
    assert _plan("2026-06-29에 뭐가 빠졌어?").direction == "DOWN"
    assert _plan("2026-06-29 하락한 테마 알려줘").direction == "DOWN"
    assert _plan("삼성전자 많이 오른 날").direction == "UP"
    assert _plan("삼성전자 많이 떨어진 날").direction == "DOWN"


def test_relative_dates_resolve_against_today() -> None:
    yesterday = _plan("어제 뭐가 올랐어?")
    assert yesterday.date is not None
    assert yesterday.date.value == date(2026, 8, 16)
    assert yesterday.date.expression is RelativeExpression.YESTERDAY

    friday = _plan("지난 금요일 상승 테마")
    assert friday.date is not None
    # 2026-08-17은 월요일이므로 직전 금요일은 8월 14일이다.
    assert friday.date.value == date(2026, 8, 14)

    week = _plan("지난주 시장 어땠어?")
    assert week.period is not None
    assert (week.period.start, week.period.end) == (date(2026, 8, 10), date(2026, 8, 16))

    quarter = _plan("최근 3개월 자주 나온 테마")
    assert quarter.period is not None
    assert quarter.period.expression is RelativeExpression.LAST_3_MONTHS
    assert quarter.period.end == TODAY


def test_today_question_keeps_today_and_does_not_shift_to_realtime() -> None:
    plan = _plan("오늘 뭐가 올랐어?")
    assert plan.query_type is QueryType.DAY_MOVERS
    assert plan.date is not None
    assert plan.date.value == TODAY
    assert plan.date.expression is RelativeExpression.TODAY


def test_explicit_range_becomes_one_period_not_two_dates() -> None:
    plan = _plan("2026-06-01부터 2026-06-30까지 뭐가 올랐어?")
    assert plan.query_type is QueryType.PERIOD_SUMMARY
    assert plan.date is None
    assert plan.period is not None
    assert (plan.period.start, plan.period.end) == (date(2026, 6, 1), date(2026, 6, 30))


def test_past_alias_resolves_only_inside_its_validity_window() -> None:
    inside = _plan("한화테크윈 2016-05-02에 왜 올랐어?")
    assert inside.company is not None
    assert inside.company.seed_stock_code == "012450"
    assert inside.company.basis == "PAST_ALIAS"

    outside = _failure("한화테크윈 2024-05-02에 왜 올랐어?")
    assert outside.reason is FailureReason.UNKNOWN_COMPANY
    assert outside.public_reason is PublicFailure.QUESTION_NOT_UNDERSTOOD


def test_ambiguous_company_returns_candidates_instead_of_guessing() -> None:
    failure = _failure("대한제강 어떤 테마에 속해?")
    assert failure.reason is FailureReason.AMBIGUOUS_ALIAS
    assert failure.public_reason is PublicFailure.QUESTION_NOT_UNDERSTOOD
    assert {candidate.seed_stock_code for candidate in failure.candidates} == {
        "111111",
        "222222",
    }


def test_stock_code_wins_over_name_matching() -> None:
    plan = _plan("005930 어떤 테마에 속해?")
    assert plan.company is not None
    assert plan.company.seed_stock_code == "005930"
    assert plan.company.basis == "STOCK_CODE"


def test_amount_condition_is_normalized_to_won() -> None:
    plan = _plan("한화에어로스페이스 1조 넘는 수주 몇 건이야?")
    assert plan.amount_condition is not None
    assert plan.amount_condition.comparator == "GT"
    assert plan.amount_condition.normalized_value == Decimal("1000000000000")
    assert plan.amount_condition.matches(Decimal("1000000000001"))
    assert not plan.amount_condition.matches(Decimal("1000000000000"))

    at_least = _plan("한화에어로스페이스 1000억 이상 계약")
    assert at_least.amount_condition is not None
    assert at_least.amount_condition.comparator == "GTE"
    assert at_least.amount_condition.normalized_value == Decimal("100000000000")


def test_trade_and_forecast_questions_stay_out_of_scope() -> None:
    for question in (
        "삼성전자 지금 사야 돼?",
        "삼성전자 내일 오를까?",
        "2차전지 목표가 얼마야?",
        "2차전지 앞으로 전망 어때?",
    ):
        failure = _failure(question)
        assert failure.reason is FailureReason.OUT_OF_SCOPE
        assert failure.public_reason is PublicFailure.OUT_OF_SCOPE


def test_unsupported_questions_end_as_interpretation_failure() -> None:
    for question in (
        "삼성전자 배당 얼마 줘?",
        "삼성전자 부채비율 알려줘",
        "오늘 날씨 어때?",
        "삼성전자 공매도 잔고",
    ):
        failure = _failure(question)
        assert failure.reason is FailureReason.NOT_INTERPRETABLE
        assert failure.public_reason is PublicFailure.QUESTION_NOT_UNDERSTOOD


def test_same_question_produces_the_same_plan_and_cache_key() -> None:
    first = _plan("한화에어로스페이스가 직접 한 일만 알려줘")
    second = _plan("한화에어로스페이스가 직접 한 일만 알려줘")
    versions = {"datasetHash": "abc", "companyMaster": "company-master/1.0.0"}
    assert first.as_dict() == second.as_dict()
    assert first.cache_key(versions) == second.cache_key(versions)
    assert first.cache_key(versions) != first.cache_key({**versions, "datasetHash": "x"})


def test_plan_failure_never_carries_the_question_text() -> None:
    failure = _failure("삼성전자 지금 사야 돼?")
    payload = failure.as_dict()
    assert "삼성전자" not in str(payload)
    assert "publicReason" in payload and "reason" not in payload


def test_theme_alias_resolves_fragments_but_never_guesses_ambiguous_ones() -> None:
    """"지능형로봇"처럼 테마명 일부만 쳐도 유일하면 알아듣는다."""

    catalog = QuestionCatalog(
        company_master=_catalog().company_master,
        themes=(
            ThemeEntry("101", "2차전지"),
            ThemeEntry("103", "지능형로봇/인공지능(AI)"),
            ThemeEntry("104", "반도체 재료/부품"),
            ThemeEntry("105", "자동차(부품)"),
        ),
    )
    result = plan_question(
        "지능형로봇 테마에 어떤 종목이 있어?", catalog=catalog, today=TODAY
    )
    assert result.failure is None
    assert result.plan is not None
    assert result.plan.themes[0].theme_name == "지능형로봇/인공지능(AI)"

    # "부품"은 두 테마에 걸린다 — 지어내지 않고 실패한다.
    ambiguous = plan_question(
        "부품 테마에 어떤 종목이 있어?", catalog=catalog, today=TODAY
    )
    assert ambiguous.failure is not None


def test_asked_horizon_survives_and_never_eats_dates_or_periods() -> None:
    """"3거래일 뒤"의 3이 plan에 남는다. 날짜·기간 자리는 건드리지 않는다."""

    catalog = _catalog()
    asked = plan_question(
        "과거 로봇 정책 소재 발표 뒤 당시 주도주 3거래일 뒤 주가 어떻게 됐어?",
        catalog=catalog,
        today=TODAY,
    )
    assert asked.plan is not None
    assert asked.plan.outcome_horizon == 3

    for question, expected in (
        ("과거 로봇 정책 소재 발표 뒤 당시 주도주 17일 후 주가", 17),
        ("과거 로봇 정책 소재 발표 후 T+3 주가", 3),
        # 안 물으면 None이다 — 화면이 기본 5거래일로 답한다.
        ("과거 로봇 정책 소재 발표 뒤 당시 주도주 주가 어떻게 됐어?", None),
        # 기간과 날짜는 기준일이 아니다.
        ("최근 7일 뭐가 올랐어?", None),
        ("8월 5일 뭐가 올랐어?", None),
        # 가격 자료가 세어 줄 수 없는 수는 안 받는다.
        ("과거 로봇 정책 소재 발표 뒤 999거래일 뒤 주가", None),
    ):
        result = plan_question(question, catalog=catalog, today=TODAY)
        assert result.plan is not None, question
        assert result.plan.outcome_horizon == expected, question
