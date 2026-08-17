"""사건 단계와 장기 프로젝트 식별 규칙 (E-22 단계 4)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from packages.infostock.hashing import sha256_json


class EventStage(StrEnum):
    RUMOR = "RUMOR"
    REVIEW = "REVIEW"
    DISCUSSION = "DISCUSSION"
    BID = "BID"
    SHORTLIST = "SHORTLIST"
    PREFERRED_BIDDER = "PREFERRED_BIDDER"
    SIGNED = "SIGNED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"
    UNSPECIFIED = "UNSPECIFIED"


class EventStageRank(IntEnum):
    RUMOR = 10
    REVIEW = 20
    DISCUSSION = 30
    BID = 40
    SHORTLIST = 50
    PREFERRED_BIDDER = 60
    SIGNED = 70
    EXECUTING = 80
    COMPLETED = 90
    DELAYED = 95
    CANCELLED = 100
    UNSPECIFIED = 0


_STAGE_MARKERS: tuple[tuple[EventStage, tuple[str, ...]], ...] = (
    (EventStage.CANCELLED, ("계약 해지", "수주 취소", "취소", "철회", "중단")),
    (EventStage.DELAYED, ("납기 연기", "일정 연기", "지연", "연기")),
    (EventStage.COMPLETED, ("납품 완료", "사업 완료", "완공", "준공", "완료")),
    (EventStage.EXECUTING, ("납품 개시", "공급 개시", "착공", "이행", "생산 개시")),
    (
        EventStage.SIGNED,
        (
            "본계약 체결",
            "공급계약 체결",
            "수주계약 체결",
            "계약을 체결",
            "계약 체결",
            "MOU 체결",
            "양해각서 체결",
            "수주",
        ),
    ),
    (
        EventStage.PREFERRED_BIDDER,
        ("우선협상대상자", "우선협상자로", "우선협상자 선정"),
    ),
    (EventStage.SHORTLIST, ("숏리스트", "적격예비후보", "예비후보")),
    (EventStage.BID, ("입찰 참여", "입찰", "응찰", "제안서 제출")),
    (EventStage.DISCUSSION, ("협상", "논의", "협의")),
    (EventStage.REVIEW, ("검토", "타당성 조사")),
    (EventStage.RUMOR, ("기대감", "전망", "가능성", "추진설", "관측", "루머")),
)
_ANTICIPATION_MARKERS = ("기대", "전망", "가능성", "추진설", "관측", "루머")
_PROJECT_QUOTED_RE = re.compile(r"[‘'\"“]([^’'\"”]{2,60})[’'\"”]")
_PROJECT_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,6}-?\d{1,4})(?![A-Za-z0-9])")
_PROJECT_PREFIX_RE = re.compile(
    r"프로젝트\s+[‘'\"“]?([가-힣A-Za-z0-9·\-]{2,40})"
)
_PROJECT_NUMBERED_BUSINESS_RE = re.compile(
    r"([가-힣A-Za-z][가-힣A-Za-z0-9·\- ]{1,35}?"
    r"\d+(?:[·.\-]\d+)?(?:호기|단계))\s*(?:건설\s*)?사업"
)
_PROJECT_CONTEXT_MARKERS = (
    "수주",
    "계약",
    "입찰",
    "응찰",
    "협상",
    "우선협상",
    "납품",
    "착공",
    "완공",
    "준공",
)
_GENERIC_PROJECT_REFERENCES = frozenset(
    {
        "1차",
        "2차",
        "가동",
        "3각협력",
        "개발",
        "거론",
        "건설",
        "견제",
        "경제성",
        "기대",
        "검토",
        "계획",
        "공개",
        "공급",
        "구축",
        "관련",
        "국내",
        "글로벌",
        "금융",
        "기대감",
        "메가 프로젝트",
        "mou",
        "납품",
        "논의",
        "대응",
        "발주",
        "발주에",
        "발표",
        "발표에",
        "본격",
        "본격화",
        "부각",
        "부진",
        "불확실성",
        "사업",
        "숏리스트",
        "승인",
        "승인에",
        "수주",
        "수혜",
        "입찰",
        "입찰공고",
        "의제",
        "이행",
        "인프라",
        "예산",
        "원전",
        "유리 기판",
        "전격",
        "전면",
        "전환",
        "정상화",
        "중단",
        "진행",
        "추진",
        "재추진",
        "차질",
        "착수",
        "참여",
        "탐사",
        "투자",
        "협력",
        "협의",
        "확대",
        "현실화",
        "전남",
        "흑연",
    }
)
_GENERIC_PROJECT_SUFFIXES = (
    " 개발",
    " 검토",
    " 계획",
    " 공급",
    " 구축",
    " 방안",
    " 사업",
    " 수주",
    " 승인",
    " 입찰",
    " 참여",
    " 투자",
    " 추진",
    " 협력",
    " 협의",
    " 성장",
    " 지원",
)


@dataclass(frozen=True, slots=True)
class EventStageEvidence:
    stage: EventStage
    keyword: str | None
    start: int | None
    end: int | None

    def __post_init__(self) -> None:
        positions = (self.start, self.end)
        if self.stage is EventStage.UNSPECIFIED:
            if self.keyword is not None or any(item is not None for item in positions):
                raise ValueError("미지정 단계에는 가짜 evidence를 붙일 수 없습니다.")
            return
        if (
            not self.keyword
            or self.start is None
            or self.end is None
            or self.start < 0
            or self.end <= self.start
        ):
            raise ValueError("사건 단계 evidence span이 올바르지 않습니다.")


def event_stage_rank(stage: EventStage) -> int:
    return int(EventStageRank[stage.name])


def detect_event_stage(text: str, *, offset: int = 0) -> EventStageEvidence:
    """가장 구체적인 명시 단계 하나를 반환한다.

    ``계약 체결 기대감``처럼 아직 발생하지 않은 표현은 SIGNED보다 RUMOR가
    우선한다. 그 밖에는 취소·완료처럼 뒤 단계의 명시 표지가 앞선다.
    """

    anticipation = [
        (text.find(marker), marker)
        for marker in _ANTICIPATION_MARKERS
        if text.find(marker) >= 0
    ]
    for stage, markers in _STAGE_MARKERS:
        found = [
            (text.find(marker), marker)
            for marker in markers
            if text.find(marker) >= 0
        ]
        if not found:
            continue
        position, marker = min(found, key=lambda item: (item[0], -len(item[1])))
        if stage is EventStage.SIGNED and anticipation:
            anticipation_position, anticipation_marker = min(anticipation)
            if anticipation_position >= position:
                return EventStageEvidence(
                    EventStage.RUMOR,
                    anticipation_marker,
                    offset + anticipation_position,
                    offset + anticipation_position + len(anticipation_marker),
                )
        return EventStageEvidence(
            stage,
            marker,
            offset + position,
            offset + position + len(marker),
        )
    return EventStageEvidence(EventStage.UNSPECIFIED, None, None, None)


def extract_project_reference(text: str) -> str | None:
    """명시적 이름·코드가 있을 때만 프로젝트 연결 키를 만든다."""

    patterns = (
        _PROJECT_PREFIX_RE,
        _PROJECT_NUMBERED_BUSINESS_RE,
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        value = " ".join(match.group(1).strip().split())
        folded = value.casefold()
        if (
            value
            and folded not in _GENERIC_PROJECT_REFERENCES
            and not folded.endswith(_GENERIC_PROJECT_SUFFIXES)
        ):
            return value.upper()
    for match in _PROJECT_QUOTED_RE.finditer(text):
        window = text[max(0, match.start() - 12) : min(len(text), match.end() + 12)]
        if "사업" not in window and "프로젝트" not in window:
            continue
        value = " ".join(match.group(1).strip().split())
        folded = value.casefold()
        if (
            value
            and folded not in _GENERIC_PROJECT_REFERENCES
            and not folded.endswith(_GENERIC_PROJECT_SUFFIXES)
        ):
            return value.upper()
    for match in _PROJECT_CODE_RE.finditer(text):
        window = text[max(0, match.start() - 24) : min(len(text), match.end() + 24)]
        if not any(marker in window for marker in _PROJECT_CONTEXT_MARKERS):
            continue
        return " ".join(match.group(1).strip().split()).upper()
    return None


def project_fingerprint(
    *,
    reference: str | None,
    company_codes: tuple[str, ...],
    participant_keys: tuple[str, ...],
) -> str | None:
    """명시 프로젝트의 여러 진행 단계를 같은 장기 대상으로 묶는다.

    단계·소재 유형은 프로젝트가 아니라 개별 catalyst 속성이므로 fingerprint에
    넣지 않는다. 회사가 명시된 경우 회사가 가장 강한 anchor다. 회사가 없는
    기록에서만 통제된 비회사 참여자를 보조 anchor로 쓴다.
    """

    if reference is None:
        return None
    normalized_reference = unicodedata.normalize("NFKC", reference).casefold()
    normalized_reference = re.sub(r"[\s\-_]+", "", normalized_reference)
    companies = sorted(set(company_codes))
    return sha256_json(
        {
            "reference": normalized_reference,
            "companyCodes": companies,
            "participantKeys": (
                [] if companies else sorted(set(participant_keys))
            ),
        }
    )


def project_id_from_fingerprint(fingerprint: str | None) -> str | None:
    return None if fingerprint is None else f"project_{fingerprint[:24]}"
