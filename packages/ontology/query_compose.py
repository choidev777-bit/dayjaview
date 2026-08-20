"""복합 질문을 단일 질의 여러 개로 풀어 답한다 (E-21 열린 질문 1단계).

LLM은 다음에 던질 질문 문장만 만든다. 계산·근거·수치는 전부 기존 결정론
엔진(answer_question)이 낸다. LLM이 무엇을 물을지 정하고, 무엇이 사실인지는
정하지 못한다. LLM이 없거나 실패하면 호출자는 기존 단일 경로로 돌아간다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, Sequence

from .query_answers import (
    AnswerResult,
    QueryAvailability,
    ResearchRepository,
    answer_question,
)
from .query_contracts import QueryType
from .query_planning import PUBLIC_FAILURE_LABEL_KO, QuestionCatalog

COMPOSE_PROMPT_VERSION = "research-compose/1.1.0"
MAX_COMPOSE_STEPS = 3

# 절이 이어지는 복합 질문 표지(과거형 연결어미 포함). 단일 질문은 LLM 없이
# 기존 경로로 간다.
_COMPOUND_RE = re.compile(
    r"그리고|했고|됐고|였고|랐고|았고|었고|졌고|웠고|이고\s|,\s*그\s|당시"
)

# 다음 질문 재료로 쓸 값만 추린다. 내부 식별자·원문 전문은 넘기지 않는다.
_DIGEST_VALUE_KEYS = (
    "stockName",
    "stockCode",
    "themeName",
    "themeNames",
    "direction",
    "changeRate",
    "eventDate",
    "occurredOn",
    "reason",
    "sectionHeadline",
    "eventStage",
    "certainty",
)


class ComposeLlm(Protocol):
    def structure(
        self, *, prompt_version: str, payload: dict[str, object]
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class ComposedStep:
    """LLM이 만든 단일 질의 하나와 엔진이 낸 결과."""

    question: str
    result: AnswerResult


def looks_compound(question: str) -> bool:
    return question.count("?") >= 2 or _COMPOUND_RE.search(question) is not None


def answer_digest(result: AnswerResult, *, row_limit: int = 5) -> str:
    """엔진 결과를 LLM이 다음 질문 재료로 쓸 짧은 요약으로 만든다."""

    if result.failure is not None:
        label = PUBLIC_FAILURE_LABEL_KO[result.failure.public_reason]
        return f"해석 실패({label})"
    block = result.answer
    assert block is not None
    lines: list[str] = [f"질의 유형 {block.query_type.value}"]
    for metric in block.metrics[:3]:
        lines.append(f"{metric.label_ko}: {metric.value}")
    for row in block.rows[:row_limit]:
        kept: list[str] = []
        for key in _DIGEST_VALUE_KEYS:
            value = row.values.get(key)
            if value in (None, "", [], ()):
                continue
            if isinstance(value, (list, tuple)):
                value = ",".join(str(item) for item in value[:4])
            kept.append(f"{key}={value}")
        lines.append(f"- {row.label}" + (f" ({'; '.join(kept)})" if kept else ""))
        # 주도주·구성 종목은 근거 원문 안에 있다 — 종목명 참조를 위해 짧게 싣는다.
        for evidence in row.evidence[:1]:
            lines.append(f"  근거: {evidence.excerpt[:160]}")
    if len(block.rows) > row_limit:
        lines.append(f"…외 행 {len(block.rows) - row_limit}개")
    return "\n".join(lines)[:1400]


def compose_answer(
    question: str,
    *,
    llm: ComposeLlm,
    catalog: QuestionCatalog,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int = 20,
    goal_type: QueryType | None = None,
) -> tuple[ComposedStep, ...]:
    """복합 질문을 최대 3개 단일 질의로 풀어 순서대로 답한다.

    ``goal_type``은 원 질문이 끝내 묻는 것이다(해석기가 정한다). LLM에게
    알려 주기만 한다 — 어느 단계가 결론인지는 :func:`pick_conclusion`이
    코드로 고른다. 다른 요구가 남았을 수 있으므로 여기서 일찍 멈추지 않는다.

    LLM 호출이 예외를 내면 그대로 올린다 — 호출자가 기존 경로로 돌아간다.
    """

    steps: list[ComposedStep] = []
    for _ in range(MAX_COMPOSE_STEPS):
        payload: dict[str, object] = {
            "question": question,
            "finalQueryType": None if goal_type is None else goal_type.value,
            "steps": [
                {"question": step.question, "result": answer_digest(step.result)}
                for step in steps
            ],
        }
        parsed = llm.structure(
            prompt_version=COMPOSE_PROMPT_VERSION, payload=payload
        )
        next_question = parsed.get("next") if isinstance(parsed, dict) else None
        if not isinstance(next_question, str) or not next_question.strip():
            break
        next_question = next_question.strip()
        if any(step.question == next_question for step in steps):
            break  # 같은 질문 반복은 진전이 없다
        result = answer_question(
            next_question,
            catalog=catalog,
            repository=repository,
            availability=availability,
            today=today,
            limit=limit,
        )
        steps.append(ComposedStep(question=next_question, result=result))
    return tuple(steps)


def pick_conclusion(
    steps: Sequence[ComposedStep], *, goal_type: QueryType | None
) -> ComposedStep | None:
    """어느 단계가 손님이 물은 답인지 코드가 고른다.

    '마지막으로 성공한 단계'로 두면 LLM이 끝에 딴 질문을 던졌을 때 그게
    결론이 된다(2026-08-20 실측: 주가를 물었는데 구성 종목 목록이 결론으로
    올라왔다). 해석기가 정한 목표 유형의 답을 결론으로 삼는다.
    """

    answered = [step for step in steps if step.result.answer is not None]
    if not answered:
        return None
    if goal_type is not None:
        matched = [
            step
            for step in answered
            if step.result.answer is not None
            and step.result.answer.query_type is goal_type
        ]
        if matched:
            return matched[-1]
    return answered[-1]


__all__ = [
    "COMPOSE_PROMPT_VERSION",
    "MAX_COMPOSE_STEPS",
    "ComposedStep",
    "answer_digest",
    "compose_answer",
    "looks_compound",
    "pick_conclusion",
]
