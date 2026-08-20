"""복합 질문 분해 — LLM은 질문만 만들고 수치는 엔진이 낸다."""

from __future__ import annotations

from datetime import date

from apps.api.research import ResearchBoundary
from packages.ontology.query_compose import compose_answer, looks_compound
from tests.ontology.test_company_queries import OPEN, FakeRepository, _day
from tests.ontology.test_query_planning import TODAY, _catalog


class ScriptedLlm:
    """대본대로 다음 질문을 내는 가짜 LLM. 받은 payload를 기록한다."""

    def __init__(self, script: list[str | None]) -> None:
        self._script = list(script)
        self.payloads: list[dict] = []

    def structure(self, *, prompt_version: str, payload: dict) -> dict:
        self.payloads.append(payload)
        return {"next": self._script.pop(0) if self._script else None}


def test_compose_chains_engine_answers_and_feeds_digests_back() -> None:
    repository = FakeRepository(days=(_day(date(2026, 6, 29)),))
    llm = ScriptedLlm(
        [
            "2026-06-29에 뭐가 올랐어?",
            "신성델타테크 어떤 테마에 속해?",
            None,
        ]
    )
    steps = compose_answer(
        "2026-06-29에 뭐가 올랐고, 당시 주도주는 어떤 테마 소속이야?",
        llm=llm,
        catalog=_catalog(),
        repository=repository,
        availability=OPEN,
        today=TODAY,
    )
    assert [step.question for step in steps] == [
        "2026-06-29에 뭐가 올랐어?",
        "신성델타테크 어떤 테마에 속해?",
    ]
    assert steps[0].result.answer is not None
    # 두 번째 LLM 호출은 첫 답의 요약을 받았다 — 참조 치환의 재료다.
    assert llm.payloads[1]["steps"][0]["result"].startswith("질의 유형 DAY_MOVERS")


def test_boundary_returns_steps_for_compound_questions() -> None:
    repository = FakeRepository(days=(_day(date(2026, 6, 29)),))
    llm = ScriptedLlm(["2026-06-29에 뭐가 올랐어?", "2026-06-29에 뭐가 빠졌어?", None])
    boundary = ResearchBoundary(
        catalog=_catalog(), repository=repository, availability=OPEN, llm=llm
    )
    payload = boundary.answer(
        "2026-06-29에 뭐가 올랐고, 뭐가 빠졌어?", today=TODAY
    )
    assert payload["status"] == "ANSWERED"
    assert [step["question"] for step in payload["steps"]] == [
        "2026-06-29에 뭐가 올랐어?",
        "2026-06-29에 뭐가 빠졌어?",
    ]
    assert all(step["status"] == "ANSWERED" for step in payload["steps"])


def test_single_questions_never_touch_the_llm() -> None:
    repository = FakeRepository(days=(_day(date(2026, 6, 29)),))
    llm = ScriptedLlm([])
    boundary = ResearchBoundary(
        catalog=_catalog(), repository=repository, availability=OPEN, llm=llm
    )
    assert not looks_compound("2026-06-29에 뭐가 올랐어?")
    payload = boundary.answer("2026-06-29에 뭐가 올랐어?", today=TODAY)
    assert payload["status"] == "ANSWERED"
    assert "steps" not in payload
    assert llm.payloads == []


def test_topic_narrows_catalyst_questions_and_leader_axis_answers_outcome() -> None:
    """"로봇 정책"의 로봇이 살아남고, 회사 없이도 당시 주도주 축이 답한다."""

    from packages.ontology import plan_question

    result = plan_question(
        "과거 로봇 산업 육성 정책 발표 뒤 당시 주도주 주가 어떻게 됐어?",
        catalog=_catalog(),
        today=TODAY,
    )
    assert result.failure is None
    assert result.plan is not None
    assert result.plan.query_type.value == "COMPANY_HISTORICAL_OUTCOME"
    assert result.plan.company is None
    assert result.plan.topic == "로봇"

    from datetime import date as date_type
    from decimal import Decimal

    from packages.ontology import OutcomeObservation, QueryAvailability, QueryType
    from packages.ontology.query_answers import answer_plan

    repository = FakeRepository(
        outcomes=(
            OutcomeObservation(
                catalyst_id="catalyst_" + "1" * 24,
                occurred_on=date_type(2026, 7, 1),
                seed_stock_code="065350",
                company_name="신성델타테크",
                base_trading_date=date_type(2026, 7, 1),
                base_close=Decimal("34000"),
                returns={1: Decimal("2.0"), 5: Decimal("5.5"), 20: None},
                missing_reason=None,
                evidence_text="정부, 로봇 산업 육성 정책 발표",
            ),
        ),
    )
    availability = QueryAvailability(
        human_verified=frozenset(QueryType),
        outcome_gate_open=True,
        outcome_range_from=date_type(2010, 1, 1),
    )
    answered = answer_plan(
        result.plan, repository, availability=availability, today=TODAY
    )
    assert answered.answer is not None
    assert "주도주" in answered.answer.summary_ko
    row = answered.answer.rows[0]
    assert row.values["leaders"][0]["returns"]["T+5"] == "+5.50%"
    assert row.values["medianReturn"] == "+5.50%"
