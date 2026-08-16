"""사건·소재 온톨로지의 도메인 모델 (E-17).

인포스탁 테마 history 원인문 하나를 분류한 결과를 표현한다.
분류 축은 넷이다 — 소재 유형(복수), 방향, 확실성(확정/기대·전망), 지속 여부.
모든 결과는 어휘·변환 버전을 지니므로 같은 입력·같은 버전이면 같은 출력이 재현된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Direction = Literal["UP", "DOWN", "MIXED", "UNKNOWN"]
Certainty = Literal["CONFIRMED", "ANTICIPATION", "UNSPECIFIED"]
EvidenceField = Literal["catalyst_type", "direction", "certainty", "continuation"]

TYPE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")


@dataclass(frozen=True, slots=True)
class CatalystTypeDefinition:
    """통제어휘의 소재 유형 하나. keywords는 원문 부분 문자열 매칭에 쓰인다."""

    type_id: str
    name_ko: str
    description_ko: str
    keywords: tuple[str, ...]

    def __post_init__(self) -> None:
        if not TYPE_ID_RE.fullmatch(self.type_id):
            raise ValueError("type_id는 대문자·숫자·밑줄 형식이어야 합니다.")
        if not self.name_ko or not self.description_ko:
            raise ValueError("name_ko·description_ko는 비울 수 없습니다.")
        if not self.keywords:
            raise ValueError("keywords는 1개 이상이어야 합니다.")
        if any(not keyword.strip() for keyword in self.keywords):
            raise ValueError("빈 keyword는 허용되지 않습니다.")
        if len(set(self.keywords)) != len(self.keywords):
            raise ValueError("같은 유형 안에서 keyword가 중복됩니다.")


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """분류 근거가 된 원문 조각. start·end는 원문(raw_text) 문자 오프셋(end 제외)."""

    field: EvidenceField
    value: str
    keyword: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("span 오프셋이 올바르지 않습니다.")


@dataclass(frozen=True, slots=True)
class ParsedCauseSentence:
    """원인문 구조 해부 결과.

    core_end는 원문에서 사유 본문이 끝나는 오프셋이라 원문 슬라이스로
    키워드를 찾으면 span이 곧 원문 오프셋이 된다. direction_verb는 문장
    꼬리에서 찾은 방향 동사(없으면 None)다.
    """

    raw_text: str
    core_text: str
    core_end: int
    tail_text: str | None
    trailing_reference_text: str | None
    direction_verb: str | None
    direction_verb_start: int | None

    def __post_init__(self) -> None:
        if self.core_end < 0 or self.core_end > len(self.raw_text):
            raise ValueError("core_end가 원문 범위를 벗어납니다.")
        if (self.direction_verb is None) != (self.direction_verb_start is None):
            raise ValueError("direction_verb와 시작 오프셋은 함께 있어야 합니다.")


@dataclass(frozen=True, slots=True)
class CatalystClassification:
    """원인문 하나의 분류 결과. type_ids는 원문 등장 순서를 유지한다."""

    vocabulary_version: str
    transform_version: str
    type_ids: tuple[str, ...]
    primary_type_id: str | None
    direction: Direction
    certainty: Certainty
    continuation: bool
    evidence_spans: tuple[EvidenceSpan, ...]

    def __post_init__(self) -> None:
        if len(set(self.type_ids)) != len(self.type_ids):
            raise ValueError("type_ids가 중복됩니다.")
        if self.type_ids:
            if self.primary_type_id != self.type_ids[0]:
                raise ValueError("primary_type_id는 첫 등장 유형이어야 합니다.")
        elif self.primary_type_id is not None:
            raise ValueError("유형이 없으면 primary_type_id도 없어야 합니다.")

    @property
    def is_unclassified(self) -> bool:
        return not self.type_ids
