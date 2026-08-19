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
