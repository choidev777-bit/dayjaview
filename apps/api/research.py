"""자연어 리서치 답변의 공개 경계 (E-22 단계 5).

질문 원문은 이 경계 안에서만 살아 있고 저장되지 않는다. 밖으로 나가는 것은
해석된 슬롯, 계산된 수치, 근거, 버전, 그리고 네 가지 공개 실패 상태뿐이다.
내부 실패 사유는 응답에 넣지 않고 집계에만 쓴다(계획서 8.3절·12절).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import date
from threading import Lock
from typing import Protocol

from packages.ontology.query_answers import (
    QueryAvailability,
    ResearchRepository,
    answer_plan,
)
from packages.ontology.query_compose import (
    ComposedStep,
    ComposeLlm,
    compose_answer,
    looks_compound,
    pick_conclusion,
)
from packages.ontology.query_contracts import QUERY_CONTRACTS, QueryType
from packages.ontology.query_planning import (
    PUBLIC_FAILURE_LABEL_KO,
    QuestionCatalog,
    plan_question,
)

from .app_types import JsonObject

MAX_QUESTION_LENGTH = 300


class ResearchService(Protocol):
    def answer(self, question: str, *, today: date) -> JsonObject: ...

    def failure_reason_counts(self) -> dict[str, int]: ...


class ResearchBoundary:
    """질문 하나를 해석·계산해 공개 응답 하나로 만든다."""

    def __init__(
        self,
        *,
        catalog: QuestionCatalog | Callable[[], QuestionCatalog],
        repository: ResearchRepository,
        availability: QueryAvailability,
        limit: int = 20,
        llm: ComposeLlm | None = None,
    ) -> None:
        # 이름 목록은 전 회사·alias를 읽으므로 조립 시점이 아니라 첫 질문에서
        # 만든다. 기동만 하고 질문이 없으면 읽지 않는다.
        self._catalog_source = catalog
        self._catalog: QuestionCatalog | None = (
            catalog if isinstance(catalog, QuestionCatalog) else None
        )
        self._repository = repository
        self._availability = availability
        self._limit = limit
        self._llm = llm
        # 유형 확대 판단에 쓰는 집계다. 질의 원문은 세지 않는다.
        self._failure_counts: Counter[str] = Counter()
        self._lock = Lock()

    def _resolved_catalog(self) -> QuestionCatalog:
        if self._catalog is None:
            assert not isinstance(self._catalog_source, QuestionCatalog)
            self._catalog = self._catalog_source()
        return self._catalog

    def answer(self, question: str, *, today: date) -> JsonObject:
        result = plan_question(question, catalog=self._resolved_catalog(), today=today)
        single_failure: object | None = result.failure
        single_block = None
        if result.failure is None:
            assert result.plan is not None
            answered = answer_plan(
                result.plan,
                self._repository,
                availability=self._availability,
                today=today,
                limit=self._limit,
            )
            single_failure = answered.failure
            single_block = answered.answer

        # 복합 질문이거나 단일 해석이 실패하면 LLM 분해를 시도한다. LLM은
        # 다음 질문 문장만 만들고 수치는 전부 기존 엔진이 낸다. 실패하면
        # 아래 단일 경로 결과가 그대로 나간다.
        if self._llm is not None and (single_block is None or looks_compound(question)):
            # 손님이 끝내 묻는 것(목표 유형)은 해석기가 정한다. LLM이 아니다.
            goal_type = None if result.plan is None else result.plan.query_type
            steps = self._composed_steps(question, today, goal_type)
            successes = [step for step in steps if step.result.answer is not None]
            # 단일 답이 이미 있으면 두 단계 이상 성공했을 때만 분해가 낫다.
            if len(successes) >= (1 if single_block is None else 2):
                conclusion = pick_conclusion(steps, goal_type=goal_type)
                assert conclusion is not None and conclusion.result.answer is not None
                return {
                    "status": "ANSWERED",
                    "answer": conclusion.result.answer.as_dict(),
                    "steps": [
                        self._step_dict(step, step is conclusion) for step in steps
                    ],
                }

        if single_block is not None:
            return {"status": "ANSWERED", "answer": single_block.as_dict()}
        assert single_failure is not None
        return self._failed(single_failure)

    def _composed_steps(
        self, question: str, today: date, goal_type: object | None = None
    ) -> tuple[ComposedStep, ...]:
        assert self._llm is not None
        try:
            return compose_answer(
                question,
                llm=self._llm,
                catalog=self._resolved_catalog(),
                repository=self._repository,
                availability=self._availability,
                today=today,
                limit=self._limit,
                goal_type=goal_type,  # type: ignore[arg-type]
            )
        except Exception:  # noqa: BLE001 - LLM 장애는 단일 경로로 조용히 물러난다
            return ()

    @staticmethod
    def _step_dict(step: ComposedStep, conclusion: bool = False) -> JsonObject:
        if step.result.answer is not None:
            return {
                "question": step.question,
                "status": "ANSWERED",
                "conclusion": conclusion,
                "answer": step.result.answer.as_dict(),
            }
        assert step.result.failure is not None
        return {
            "question": step.question,
            "status": "FAILED",
            "conclusion": False,
            "failure": step.result.failure.as_dict(),  # type: ignore[attr-defined]
        }

    def failure_reason_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self._failure_counts.items()))

    def _failed(self, failure: object) -> JsonObject:
        reason = getattr(failure, "reason")
        query_type = getattr(failure, "query_type", None)
        key = f"{reason.value}:{query_type.value if query_type else 'UNCLASSIFIED'}"
        with self._lock:
            self._failure_counts[key] += 1
        payload = failure.as_dict()  # type: ignore[attr-defined]
        return {"status": "FAILED", "failure": payload}


def supported_query_types(availability: QueryAvailability) -> list[JsonObject]:
    """지금 열려 있는 유형 목록. 화면의 예시 질문이 이 값을 따라간다."""

    return [
        {
            "queryType": contract.query_type.value,
            "countUnit": contract.count_unit.value,
            "open": availability.is_open(contract.query_type),
            "humanVerified": contract.query_type in availability.human_verified,
        }
        for contract in QUERY_CONTRACTS
    ]


EXAMPLE_QUESTIONS: dict[QueryType, str] = {
    QueryType.DAY_MOVERS: "어제 뭐가 올랐어?",
    QueryType.PERIOD_SUMMARY: "이번 주 시장 어땠어?",
    QueryType.STOCK_DAY_REASON: "삼성전자 어제 왜 올랐어?",
    QueryType.STOCK_TOP_MOVES: "삼성전자 올해 크게 오른 날 알려줘",
    QueryType.STOCK_THEME_MEMBERSHIP: "삼성전자 어떤 테마에 속해?",
    QueryType.STOCK_COOCCURRENCE: "삼성전자랑 같이 움직이는 종목은?",
    QueryType.THEME_MEMBERS: "2차전지 테마에 어떤 종목이 있어?",
    QueryType.THEME_HISTORY: "2차전지 테마 과거에 뭘로 움직였어?",
    QueryType.THEME_COMPARISON: "2차전지랑 반도체 중에 올해 어디가 더 셌어?",
    QueryType.THEME_FREQUENCY: "올해 자주 나온 테마는?",
    QueryType.CATALYST_THEME_REACTION: "정책 소재에 어떤 테마가 반응했어?",
    QueryType.CATALYST_FREQUENCY: "올해 정책 소재 몇 번 나왔어?",
    QueryType.CATALYST_CERTAINTY: "정책 소재는 기대감이었어 확정이었어?",
    QueryType.CATALYST_CONTINUATION: "정책 소재 처음 나온 소재야?",
    QueryType.COMPANY_DIRECT_EVENT: "한화에어로스페이스가 직접 한 일만 알려줘",
    QueryType.COMPANY_VALUE_SUMMARY: "한화에어로스페이스 1조 넘는 수주 몇 건이야?",
    QueryType.COMPANY_HISTORICAL_OUTCOME: "한화에어로스페이스 수주 발표 뒤에 주가 어떻게 됐어?",
}


def example_questions(availability: QueryAvailability) -> list[JsonObject]:
    """열린 유형의 예시만 보여준다. 잠긴 유형을 물어보게 유도하지 않는다."""

    return [
        {"queryType": query_type.value, "question": question}
        for query_type, question in EXAMPLE_QUESTIONS.items()
        if availability.is_open(query_type)
    ]


__all__ = [
    "EXAMPLE_QUESTIONS",
    "MAX_QUESTION_LENGTH",
    "PUBLIC_FAILURE_LABEL_KO",
    "ResearchBoundary",
    "ResearchService",
    "example_questions",
    "supported_query_types",
]
