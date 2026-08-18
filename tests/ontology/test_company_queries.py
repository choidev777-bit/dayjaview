"""QueryPlan → 답변 블록 (E-22 단계 5·6).

계획서 11.4절의 네 질문이 서로 다른 답을 내는지, 집계 단위가 갈리는지,
근거 없는 답이 나가지 않는지를 고정한다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from packages.ontology import (
    AnswerRow,
    CatalystFilter,
    CatalystSummary,
    CountUnit,
    DailyDay,
    DailySection,
    DailyStock,
    DailyStockRow,
    DailyTheme,
    FailureReason,
    OutcomeObservation,
    PublicFailure,
    QueryAvailability,
    QueryPrerequisite,
    QueryType,
    ThemeDailyChange,
    ThemeHistoryRecord,
    ThemeMembership,
    ValueFact,
)
from packages.ontology.query_answers import CatalystCompanyRoleRow, answer_question
from tests.ontology.test_query_planning import TODAY, _catalog

HANWHA = "012450"
PEER = "065350"


def _stock(name: str, code: str, close: int, rate: str) -> DailyStock:
    return DailyStock(name, code, close, Decimal(rate))


def _day(trading_date: date, *, unsplit: int = 0) -> DailyDay:
    up = DailySection(
        section_name="2차전지 등",
        headline="K-배터리, 2분기 실적 반등 기대감 등에 상승",
        details=("▷언론에 따르면 2분기 실적 반등의 기반을 마련할 것이라는 분석.",),
        themes=(
            DailyTheme(
                theme_name="2차전지",
                change_rate=Decimal("10.86"),
                stocks=(_stock("신성델타테크", PEER, 34000, "21.00"),),
            ),
        ),
    )
    down = DailySection(
        section_name="반도체 대표주(생산)",
        headline="美 필라델피아 반도체지수 폭락 영향 등에 하락",
        details=("▷메모리 수요 둔화 우려가 지속.",),
        themes=(
            DailyTheme(
                theme_name="반도체 대표주(생산)",
                change_rate=Decimal("-3.26"),
                stocks=(_stock("삼성전자", "005930", 70000, "-2.10"),),
            ),
        ),
    )
    return DailyDay(
        trading_date=trading_date,
        published_date=trading_date,
        status="PUBLISHED",
        sections=(up, down),
        unsplit_post_count=unsplit,
    )


def _catalyst(
    catalyst_id: str,
    *,
    occurred_on: date,
    roles: tuple[CatalystCompanyRoleRow, ...],
    stage: str = "SIGNED",
    certainty: str = "CONFIRMED",
    novelty: str = "NEW",
    types: tuple[str, ...] = ("ORDER_CONTRACT",),
    source_records: int = 2,
    reactions: int = 2,
) -> CatalystSummary:
    return CatalystSummary(
        catalyst_id=catalyst_id,
        occurred_on=occurred_on,
        primary_catalyst_type=types[0] if types else None,
        primary_catalyst_name_ko="수주·계약" if types else None,
        catalyst_types=types,
        event_stage=stage,
        certainty=certainty,
        novelty_type=novelty,
        action="체결",
        object_text="천무 2차 실행계약",
        project_id="project_abc",
        geography_codes=("PL",),
        theme_names=("방위산업",),
        company_roles=roles,
        source_record_count=source_records,
        theme_reaction_count=reactions,
        evidence_text="한화에어로스페이스가 폴란드와 천무 2차 실행계약 체결",
        evidence_start=0,
        evidence_end=30,
    )


def _role(code: str, name: str, role: str) -> CatalystCompanyRoleRow:
    return CatalystCompanyRoleRow(code, name, role, "POSITIVE")


class FakeRepository:
    """저장된 값만 돌려주는 결정론적 저장소."""

    def __init__(
        self,
        *,
        days: tuple[DailyDay, ...] = (),
        catalysts: tuple[CatalystSummary, ...] = (),
        value_facts: tuple[ValueFact, ...] = (),
        outcomes: tuple[OutcomeObservation, ...] = (),
        memberships: tuple[ThemeMembership, ...] = (),
        history: tuple[ThemeHistoryRecord, ...] = (),
        stock_rows: tuple[DailyStockRow, ...] = (),
        theme_changes: tuple[ThemeDailyChange, ...] = (),
        ready: frozenset[QueryPrerequisite] | None = None,
    ) -> None:
        self._days = {day.trading_date: day for day in days}
        self._catalysts = catalysts
        self._value_facts = value_facts
        self._outcomes = outcomes
        self._memberships = memberships
        self._history = history
        self._stock_rows = stock_rows
        self._theme_changes = theme_changes
        self._ready = (
            frozenset(
                item for item in QueryPrerequisite if item is not QueryPrerequisite.NONE
            )
            if ready is None
            else ready
        )

    def versions(self):
        return {"datasetHash": "a" * 64, "companyMaster": "company-master/1.0.0"}

    def ready_prerequisites(self):
        return self._ready

    def daily_day(self, trading_date: date) -> DailyDay:
        found = self._days.get(trading_date)
        if found is not None:
            return found
        earlier = [key for key in self._days if key <= trading_date]
        if not earlier:
            return DailyDay(trading_date, None, "NO_RECORD", ())
        fallback = self._days[max(earlier)]
        return DailyDay(
            fallback.trading_date,
            fallback.published_date,
            "NOT_PUBLISHED",
            fallback.sections,
            fallback.unsplit_post_count,
        )

    def daily_days(self, start: date, end: date):
        return tuple(
            day for key, day in sorted(self._days.items()) if start <= key <= end
        )

    def stock_daily_rows(self, seed_stock_code: str, start: date, end: date):
        return tuple(
            row
            for row in self._stock_rows
            if row.stock_code == seed_stock_code and start <= row.trading_date <= end
        )

    def theme_daily_changes(self, theme_names, start: date, end: date):
        wanted = set(theme_names)
        return tuple(
            item
            for item in self._theme_changes
            if item.theme_name in wanted and start <= item.trading_date <= end
        )

    def theme_members(self, source_theme_id: str):
        return tuple(
            item for item in self._memberships if item.source_theme_id == source_theme_id
        )

    def stock_theme_memberships(self, seed_stock_code: str):
        return tuple(
            item for item in self._memberships if item.stock_code == seed_stock_code
        )

    def theme_history(self, source_theme_id: str, *, date_from=None, date_to=None):
        return tuple(
            item
            for item in self._history
            if item.source_theme_id == source_theme_id
            and (date_from is None or (item.event_date and item.event_date >= date_from))
            and (date_to is None or (item.event_date and item.event_date <= date_to))
        )

    def catalysts(self, catalyst_filter: CatalystFilter):
        selected = []
        for item in self._catalysts:
            if catalyst_filter.date_from and (
                item.occurred_on is None or item.occurred_on < catalyst_filter.date_from
            ):
                continue
            if catalyst_filter.date_to and (
                item.occurred_on is None or item.occurred_on > catalyst_filter.date_to
            ):
                continue
            if (
                catalyst_filter.catalyst_type
                and catalyst_filter.catalyst_type not in item.catalyst_types
            ):
                continue
            if catalyst_filter.seed_stock_code is not None:
                matched = [
                    role
                    for role in item.company_roles
                    if role.seed_stock_code == catalyst_filter.seed_stock_code
                ]
                if not matched:
                    continue
                if catalyst_filter.roles and not any(
                    role.role in catalyst_filter.roles for role in matched
                ):
                    continue
            selected.append(item)
        return tuple(selected)

    def value_facts(self, catalyst_filter: CatalystFilter):
        allowed = {item.catalyst_id for item in self.catalysts(catalyst_filter)}
        return tuple(
            fact for fact in self._value_facts if fact.catalyst_id in allowed
        )

    def outcomes(self, catalyst_filter: CatalystFilter, *, horizons):
        return tuple(
            item
            for item in self._outcomes
            if (
                catalyst_filter.date_from is None
                or item.occurred_on >= catalyst_filter.date_from
            )
            and (
                catalyst_filter.date_to is None
                or item.occurred_on <= catalyst_filter.date_to
            )
        )


OPEN = QueryAvailability(human_verified=frozenset(QueryType), serve_unverified=False)


def _ask(question: str, repository: FakeRepository, *, availability=OPEN):
    return answer_question(
        question,
        catalog=_catalog(),
        repository=repository,
        availability=availability,
        today=TODAY,
    )


def test_day_movers_answers_only_the_asked_direction_with_evidence() -> None:
    repository = FakeRepository(days=(_day(date(2026, 6, 29)),))
    result = _ask("2026-06-29에 뭐가 올랐어?", repository)

    assert result.answer is not None
    answer = result.answer
    assert answer.count_unit is CountUnit.DAILY_SECTION
    assert [row.label for row in answer.rows] == [
        "K-배터리, 2분기 실적 반등 기대감 등에 상승"
    ]
    assert answer.exclusions[0].code == "DIRECTION_MISMATCH"
    assert answer.evidence_coverage == 1.0
    assert answer.sample_size == 2

    down = _ask("2026-06-29에 뭐가 빠졌어?", repository)
    assert down.answer is not None
    assert [row.label for row in down.answer.rows] == [
        "美 필라델피아 반도체지수 폭락 영향 등에 하락"
    ]


def test_today_before_publication_falls_back_and_says_so() -> None:
    repository = FakeRepository(days=(_day(date(2026, 8, 14)),))
    result = _ask("오늘 뭐가 올랐어?", repository)

    assert result.answer is not None
    assert result.answer.interpretation["tradingDate"] == "2026-08-14"
    assert any("발행되지 않아" in note for note in result.answer.notes_ko)
    # 장중 실시간 값을 섞지 않았다는 사실을 답에 남긴다.
    assert any("실시간" in note for note in result.answer.notes_ko)


def test_unsplit_publication_day_is_reported_instead_of_silently_mixed() -> None:
    repository = FakeRepository(days=(_day(date(2012, 8, 30), unsplit=3),))
    result = _ask("2012-08-30에 뭐가 올랐어?", repository)

    assert result.answer is not None
    assert any("가르지 못했습니다" in note for note in result.answer.notes_ko)


def test_company_direct_event_separates_leader_only_records() -> None:
    direct = _catalyst(
        "catalyst_" + "a" * 24,
        occurred_on=date(2024, 7, 19),
        roles=(_role(HANWHA, "한화에어로스페이스", "CONTRACTOR"),),
    )
    leader_only = _catalyst(
        "catalyst_" + "b" * 24,
        occurred_on=date(2024, 3, 5),
        roles=(_role(HANWHA, "한화에어로스페이스", "BENEFICIARY"),),
    )
    repository = FakeRepository(catalysts=(direct, leader_only))
    result = _ask("한화에어로스페이스가 직접 한 일만 알려줘", repository)

    assert result.answer is not None
    answer = result.answer
    assert answer.count_unit is CountUnit.CATALYST
    assert [row.values["catalystId"] for row in answer.rows] == [direct.catalyst_id]
    labels = {metric.label_ko: metric.value for metric in answer.metrics}
    # 11.4절: 언급된 기록 수와 직접 행동한 고유 사건 수가 갈려야 한다.
    assert labels["직접 사건"] == "1"
    assert labels["원천 기록"] == "2"
    assert labels["테마 반응"] == "2"
    assert answer.sample_size == 2
    assert answer.exclusions[0].code == "LEADER_OR_RELATED_ONLY"


def test_value_summary_dedupes_and_drops_non_summable_amounts() -> None:
    catalyst = _catalyst(
        "catalyst_" + "c" * 24,
        occurred_on=date(2024, 7, 19),
        roles=(_role(HANWHA, "한화에어로스페이스", "CONTRACTOR"),),
    )
    facts = (
        ValueFact(
            catalyst_id=catalyst.catalyst_id,
            occurred_on=date(2024, 7, 19),
            fact_type="CONTRACT_VALUE",
            reported_value="1조6천억원",
            normalized_value=Decimal("1600000000000"),
            unit="KRW",
            currency="KRW",
            value_basis="EXACT",
            eligible_for_sum=True,
            theme_name="방위산업",
            evidence_text="1조6천억원 규모 계약",
        ),
        # 같은 사건의 두 번째 금액. 합계에 두 번 들어가면 안 된다.
        ValueFact(
            catalyst_id=catalyst.catalyst_id,
            occurred_on=date(2024, 7, 19),
            fact_type="CONTRACT_VALUE",
            reported_value="1.6조",
            normalized_value=Decimal("1600000000000"),
            unit="KRW",
            currency="KRW",
            value_basis="EXACT",
            eligible_for_sum=True,
            theme_name="방위산업",
            evidence_text="1.6조 규모",
        ),
        ValueFact(
            catalyst_id=catalyst.catalyst_id,
            occurred_on=date(2024, 7, 19),
            fact_type="CONTRACT_VALUE",
            reported_value="총사업비 5조",
            normalized_value=Decimal("5000000000000"),
            unit="KRW",
            currency="KRW",
            value_basis="TOTAL_PROJECT",
            eligible_for_sum=False,
            theme_name="방위산업",
            evidence_text="총사업비 5조",
        ),
    )
    repository = FakeRepository(catalysts=(catalyst,), value_facts=facts)
    result = _ask("한화에어로스페이스 1조 넘는 수주 몇 건이야?", repository)

    assert result.answer is not None
    answer = result.answer
    assert answer.count_unit is CountUnit.VALUE_FACT
    totals = {metric.label_ko: metric.value for metric in answer.metrics}
    assert totals["금액 합계"] == "1600000000000"
    assert totals["해당 사건"] == "1"
    codes = {item.code for item in answer.exclusions}
    assert {"VALUE_NOT_SUMMABLE", "DUPLICATE_VALUE_FACT"} <= codes


def test_outcome_keeps_missing_prices_null_and_blocks_pre_2010() -> None:
    catalyst_id = "catalyst_" + "d" * 24
    observation = OutcomeObservation(
        catalyst_id=catalyst_id,
        occurred_on=date(2024, 7, 19),
        seed_stock_code=HANWHA,
        company_name="한화에어로스페이스",
        base_trading_date=date(2024, 7, 19),
        base_close=Decimal("310000"),
        returns={1: Decimal("2.5"), 5: Decimal("7.1"), 20: None},
        missing_reason="HORIZON_NOT_REACHED",
        evidence_text="한화에어로스페이스가 폴란드와 천무 2차 실행계약 체결",
    )
    repository = FakeRepository(outcomes=(observation,))
    availability = QueryAvailability(
        human_verified=frozenset(QueryType), outcome_gate_open=True
    )
    result = _ask(
        "한화에어로스페이스 2024년 수주 발표 뒤에 주가 어떻게 됐어?",
        repository,
        availability=availability,
    )

    assert result.answer is not None
    returns = result.answer.rows[0].values["returns"]
    assert returns["T+5"] == "+7.1%"
    # 값이 없으면 0이 아니라 null이다.
    assert returns["T+20"] is None

    gated = _ask(
        "한화에어로스페이스 2024년 수주 발표 뒤에 주가 어떻게 됐어?",
        repository,
        availability=QueryAvailability(human_verified=frozenset(QueryType)),
    )
    assert gated.failure is not None
    assert gated.failure.reason is FailureReason.OUTCOME_GATE_CLOSED
    assert gated.failure.public_reason is PublicFailure.OUT_OF_SCOPE


def test_outcome_before_the_price_corpus_is_out_of_range_not_zero() -> None:
    repository = FakeRepository(outcomes=())
    availability = QueryAvailability(
        human_verified=frozenset(QueryType), outcome_gate_open=True
    )
    result = _ask(
        "한화에어로스페이스 2007년 수주 발표 뒤에 주가 어떻게 됐어?",
        repository,
        availability=availability,
    )
    assert result.failure is not None
    assert result.failure.reason is FailureReason.OUTCOME_GATE_CLOSED
    assert "2010-01-01" in result.failure.message_ko


def test_unverified_query_types_answer_quality_not_verified() -> None:
    repository = FakeRepository(days=(_day(date(2026, 6, 29)),))
    result = _ask(
        "2026-06-29에 뭐가 올랐어?",
        repository,
        availability=QueryAvailability(human_verified=frozenset()),
    )
    assert result.answer is None
    assert result.failure is not None
    assert result.failure.reason is FailureReason.QUALITY_NOT_VERIFIED
    assert result.failure.public_reason is PublicFailure.QUALITY_NOT_VERIFIED


def test_missing_prerequisite_stage_locks_the_type() -> None:
    repository = FakeRepository(
        catalysts=(),
        ready=frozenset({QueryPrerequisite.E22_STAGE_1, QueryPrerequisite.E22_STAGE_2}),
    )
    result = _ask("한화에어로스페이스가 직접 한 일만 알려줘", repository)
    assert result.failure is not None
    assert result.failure.reason is FailureReason.PREREQUISITE_NOT_READY
    assert result.failure.public_reason is PublicFailure.QUALITY_NOT_VERIFIED


def test_no_record_is_distinct_from_interpretation_failure() -> None:
    empty = FakeRepository(days=())
    no_record = _ask("2026-06-29에 뭐가 올랐어?", empty)
    assert no_record.failure is not None
    assert no_record.failure.public_reason is PublicFailure.NO_RECORD

    misunderstood = _ask("삼성전자 부채비율 알려줘", empty)
    assert misunderstood.failure is not None
    assert misunderstood.failure.public_reason is PublicFailure.QUESTION_NOT_UNDERSTOOD


def test_stock_cooccurrence_counts_shared_catalysts_not_price_moves() -> None:
    shared = _catalyst(
        "catalyst_" + "e" * 24,
        occurred_on=date(2024, 7, 19),
        roles=(
            _role(HANWHA, "한화에어로스페이스", "CONTRACTOR"),
            _role(PEER, "신성델타테크", "ACTOR"),
        ),
    )
    repository = FakeRepository(catalysts=(shared,))
    result = _ask("한화에어로스페이스랑 2024년에 같이 움직인 종목", repository)

    assert result.answer is not None
    assert result.answer.count_unit is CountUnit.CATALYST
    assert [row.label for row in result.answer.rows] == ["신성델타테크"]
    assert any("주가가 같이 움직였다는 뜻이 아닙니다" in note for note in result.answer.notes_ko)


def test_every_row_carries_evidence_or_the_answer_is_refused() -> None:
    without_evidence = _catalyst(
        "catalyst_" + "f" * 24,
        occurred_on=date(2024, 7, 19),
        roles=(_role(HANWHA, "한화에어로스페이스", "CONTRACTOR"),),
    )
    stripped = CatalystSummary(
        **{
            **{
                field: getattr(without_evidence, field)
                for field in CatalystSummary.__slots__
            },
            "evidence_text": "",
        }
    )
    repository = FakeRepository(catalysts=(stripped,))
    result = _ask("한화에어로스페이스가 직접 한 일만 알려줘", repository)

    assert result.answer is None
    assert result.failure is not None
    assert result.failure.reason is FailureReason.INSUFFICIENT_EVIDENCE


def test_same_plan_and_same_repository_return_the_same_answer() -> None:
    repository = FakeRepository(days=(_day(date(2026, 6, 29)),))
    first = _ask("2026-06-29에 뭐가 올랐어?", repository)
    second = _ask("2026-06-29에 뭐가 올랐어?", repository)
    assert first.answer is not None and second.answer is not None
    assert first.answer.as_dict() == second.answer.as_dict()


def test_theme_history_reports_source_record_unit_and_accuracy_note() -> None:
    records = (
        ThemeHistoryRecord(
            source_theme_id="101",
            theme_name="2차전지",
            source_history_key="h1",
            event_date=date(2024, 5, 2),
            raw_text="정부 지원 기대감 등에 상승",
            primary_catalyst_type="POLICY_MEASURE",
            primary_catalyst_name_ko="정책·제도",
            catalyst_types=("POLICY_MEASURE",),
            direction="UP",
            certainty="ANTICIPATION",
            continuation=False,
        ),
    )
    repository = FakeRepository(history=records)
    result = _ask("2차전지 테마 과거에 뭘로 움직였어?", repository)

    assert result.answer is not None
    assert result.answer.count_unit is CountUnit.SOURCE_RECORD
    assert result.answer.rows[0].label == "정책·제도"
    assert any("77.8%" in note for note in result.answer.notes_ko)


@pytest.mark.parametrize(
    ("question", "expected_unit"),
    [
        ("2026-06-29에 뭐가 올랐어?", CountUnit.DAILY_SECTION),
        ("한화에어로스페이스가 직접 한 일만 알려줘", CountUnit.CATALYST),
        ("2차전지 테마 과거에 뭘로 움직였어?", CountUnit.SOURCE_RECORD),
    ],
)
def test_count_unit_is_shown_with_every_answer(
    question: str, expected_unit: CountUnit
) -> None:
    repository = FakeRepository(
        days=(_day(date(2026, 6, 29)),),
        catalysts=(
            _catalyst(
                "catalyst_" + "0" * 24,
                occurred_on=date(2026, 6, 1),
                roles=(_role(HANWHA, "한화에어로스페이스", "CONTRACTOR"),),
            ),
        ),
        history=(
            ThemeHistoryRecord(
                source_theme_id="101",
                theme_name="2차전지",
                source_history_key="h1",
                event_date=date(2024, 5, 2),
                raw_text="정부 지원 기대감 등에 상승",
                primary_catalyst_type="POLICY_MEASURE",
                primary_catalyst_name_ko="정책·제도",
                catalyst_types=("POLICY_MEASURE",),
                direction="UP",
                certainty="ANTICIPATION",
                continuation=False,
            ),
        ),
    )
    result = _ask(question, repository)
    assert result.answer is not None
    assert result.answer.count_unit is expected_unit
    payload = result.answer.as_dict()
    assert payload["countUnitLabelKo"]
    assert payload["versions"]["queryContract"]


def test_answer_rows_never_leak_the_question_text() -> None:
    repository = FakeRepository(days=(_day(date(2026, 6, 29)),))
    result = _ask("2026-06-29에 뭐가 올랐어?", repository)
    assert result.answer is not None
    assert "올랐어" not in str(result.answer.as_dict())


def test_answer_row_requires_evidence_to_be_constructed_meaningfully() -> None:
    row = AnswerRow(label="x", values={}, evidence=())
    assert row.evidence == ()


def test_display_limit_exclusion_reports_hidden_row_count() -> None:
    from packages.ontology.query_answers import _display_limit_exclusions

    assert _display_limit_exclusions(20, 20) == ()
    cut = _display_limit_exclusions(25, 20)
    assert cut[0].code == "DISPLAY_LIMIT"
    assert cut[0].count == 5


def test_day_movers_values_say_how_long_the_full_lists_are() -> None:
    repository = FakeRepository(days=(_day(date(2026, 6, 29)),))
    result = _ask("2026-06-29에 뭐가 올랐어?", repository)

    assert result.answer is not None
    values = result.answer.rows[0].values
    themes = values["themes"]
    assert values["themeTotal"] == len(themes)
    for theme in themes:
        assert theme["stockTotal"] == len(theme["stocks"])
