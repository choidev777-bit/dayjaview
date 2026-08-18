"""사건 단계와 장기 프로젝트 식별 규칙 (E-22 단계 4)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import IntEnum, StrEnum

from packages.infostock.hashing import sha256_json
from packages.ontology.participants import extract_actor_mentions


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
    (
        EventStage.CANCELLED,
        ("계약 해지", "수주 취소", "취소", "철회", "중단", "결렬"),
    ),
    (EventStage.DELAYED, ("납기 연기", "일정 연기", "지연", "연기", "차질")),
    (
        EventStage.COMPLETED,
        # 핵협상 타결은 외교 사건의 종결이다 — 사업 계약 SIGNED가 아니다(검수 S-134).
        ("납품 완료", "사업 완료", "최종 통과", "완공", "준공", "완료", "핵협상 타결"),
    ),
    (
        EventStage.EXECUTING,
        (
            "납품 개시",
            "공급 개시",
            "생산 개시",
            "진행 중",
            "본격화",
            "실증",
            "착공",
            "착수",
            "이행",
        ),
    ),
    (
        EventStage.SIGNED,
        (
            "본계약 체결",
            "공급계약 체결",
            "수주계약 체결",
            "계약을 체결",
            "계약 체결",
            "합의서 체결",
            "MOU 체결",
            "양해각서 체결",
            "협상 타결",
            "협의 타결",
            "타결",
            "수주",
        ),
    ),
    (
        EventStage.SIGNED,
        # 낙찰·사업자 선정은 수주가 확정된 결과다
        # (검수 S-008, P-012, 확증 S2-007·S2-019).
        ("낙찰", "사업자 선정"),
    ),
    (
        EventStage.PREFERRED_BIDDER,
        (
            "우선협상대상자",
            "우선협상자로",
            "우선협상자 선정",
        ),
    ),
    (
        EventStage.SHORTLIST,
        # "2파전"은 입찰 경쟁 서술에 흔해 SHORTLIST 근거로 약하다(검수 S-026).
        ("숏리스트", "적격예비후보", "예비후보"),
    ),
    (EventStage.BID, ("입찰 참여", "제안서 제출", "입찰", "응찰", "발주")),
    (EventStage.DISCUSSION, ("협상", "논의", "협의", "회담")),
    (EventStage.REVIEW, ("검토", "타당성 조사", "모색")),
    (
        EventStage.RUMOR,
        (
            "무산 위기",
            "가능성",
            "가능",
            "추진설",
            "연기설",
            "기대",
            "전망",
            "관측",
            "루머",
            "우려",
        ),
    ),
)
_FUTURE_STAGE_MODIFIERS = (
    "발표 전",
    "하기로",
    "발표",
    "예정",
    "계획",
    "목표",
    "추진",
    "언급",
)
_FUTURE_STAGE_PREFIX_RE = re.compile(r"(?:오는|향후|\d{4}년까지).{0,16}$")
_RESOLUTION_MARKERS = ("부인", "해소", "완화")
_PROJECT_QUOTED_RE = re.compile(r"[‘'\"“]([^’'\"”]{2,60})[’'\"”]")
_PROJECT_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,6}-?\d{1,4})(?![A-Za-z0-9])")
_PROJECT_ONLY_COUNTRIES = ("루마니아", "핀란드", "체코")
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

    ``계약 체결 기대감``처럼 아직 발생하지 않은 표현은 RUMOR로 남긴다.
    한 절에 여러 단계가 있으면 텍스트상 마지막 유효 표지를 현재 단계로 본다.
    이는 ``협의 완료 후 공사 착수``를 COMPLETED가 아니라 EXECUTING으로 읽고,
    ``협상 타결``을 DISCUSSION보다 SIGNED로 읽기 위한 규칙이다.
    """

    candidates: list[tuple[int, int, EventStage, str]] = []
    for stage, markers in _STAGE_MARKERS:
        for marker in markers:
            for match in re.finditer(re.escape(marker), text):
                position = match.start()
                if _invalid_stage_marker(text, marker, position):
                    continue
                candidates.append((position, len(marker), stage, marker))
    if not candidates:
        return EventStageEvidence(EventStage.UNSPECIFIED, None, None, None)

    concrete = [item for item in candidates if item[2] is not EventStage.RUMOR]
    if not concrete and "예비후보" in text and _is_political_candidate_context(text):
        return EventStageEvidence(EventStage.UNSPECIFIED, None, None, None)
    position, _, stage, marker = max(
        concrete or candidates,
        key=lambda item: (
            item[0] + item[1],
            item[1],
            event_stage_rank(item[2]),
        ),
    )
    if concrete:
        future = [
            item
            for item in candidates
            if item[2] is EventStage.RUMOR
            and position + len(marker) <= item[0] <= position + len(marker) + 14
            and not _is_reaction_tail(
                text[position + len(marker) : item[0]]
            )
            and marker != "차질"
            and not (
                stage is EventStage.DELAYED
                and re.search(r"(?:지연|연기)(?:\s+소식|\s+및|에\s+따른)", text)
            )
            # 지연·연기 우려·가능성·기대는 지연 사건으로 답한다
            # (검수 S-080·S-094·S-096·S-104).
            and not (
                stage is EventStage.DELAYED
                and item[3] in ("우려", "가능성", "가능", "기대")
            )
            # 검토는 그 자체가 선행 단계라 기대·우려가 붙어도 검토다
            # (검수 S-182·S-200).
            and not (
                stage is EventStage.REVIEW and item[3] in ("기대", "우려")
            )
            # 협상 '진전' 기대는 진행 중인 협상이다(검수 S-127).
            and not re.search(
                r"진전|순항", text[position + len(marker) : item[0]]
            )
        ]
        # 논의 '예정'은 아직 열리지 않은 논의다. 앞뒤 어느 쪽에 붙어도
        # 같다 — "논의 예정"도 "출장 예정 속 … 논의"도 미래다
        # (검수 S-120·S-126).
        if stage is EventStage.DISCUSSION:
            future.extend(
                (position, len("예정"), EventStage.RUMOR, "예정")
                for match in re.finditer("예정", text)
                if (
                    position + len(marker)
                    <= match.start()
                    <= position + len(marker) + 14
                    and not _is_reaction_tail(
                        text[position + len(marker) : match.start()]
                    )
                )
                or position - 18 <= match.start() < position
            )
        if stage not in {
            EventStage.BID,
            EventStage.SHORTLIST,
            EventStage.DISCUSSION,
            EventStage.REVIEW,
        } or marker == "발주":
            future.extend(
                (match.start(), len(future_marker), EventStage.RUMOR, future_marker)
                for future_marker in _FUTURE_STAGE_MODIFIERS
                for match in re.finditer(re.escape(future_marker), text)
                if position + len(marker)
                <= match.start()
                <= position + len(marker) + 14
                and not _is_reaction_tail(
                    text[position + len(marker) : match.start()]
                )
                and not (
                    future_marker == "추진"
                    and marker not in {"착공", "수주", "계약 체결"}
                )
                and not (
                    future_marker == "발표"
                    and marker not in {"착공", "발주"}
                )
                and not (
                    future_marker == "언급" and marker != "착공"
                )
            )
        if (
            stage
            in {
                EventStage.COMPLETED,
                EventStage.EXECUTING,
                EventStage.SIGNED,
                EventStage.PREFERRED_BIDDER,
            }
            and _FUTURE_STAGE_PREFIX_RE.search(text[:position])
        ):
            future.append((position, len(marker), EventStage.RUMOR, marker))
        if future:
            position, _, stage, marker = min(
                future,
                key=lambda item: (item[0], -item[1]),
            )
    completed_agreement = re.search(r"(?:협상|타결)\s*완료", text)
    if stage is EventStage.RUMOR and completed_agreement is not None:
        position = completed_agreement.end() - len("완료")
        stage = EventStage.COMPLETED
        marker = "완료"
    if any(
        position < resolution_position <= position + len(marker) + 24
        for resolution_marker in _RESOLUTION_MARKERS
        for resolution_position in (text.find(resolution_marker, position),)
        if resolution_position >= 0
    ):
        return EventStageEvidence(EventStage.UNSPECIFIED, None, None, None)
    return EventStageEvidence(
        stage,
        marker,
        offset + position,
        offset + position + len(marker),
    )


def _is_reaction_tail(between: str) -> bool:
    return any(
        separator in between
        for separator in (
            "에 따른",
            "에 따라",
            "소식 속",
            "소식이",
            "여파 속",
            "등에 따른",
            "로 ",
        )
    )


def _invalid_stage_marker(text: str, marker: str, position: int) -> bool:
    """단계 표지와 철자가 겹칠 뿐인 합성어·주변 문맥을 제외한다."""

    end = position + len(marker)
    before = text[max(0, position - 12) : position]
    after = text[end : min(len(text), end + 14)]
    around = text[max(0, position - 12) : min(len(text), end + 14)]
    if marker == "중단" and after.startswith("체"):
        return True
    if marker == "지연" and before.endswith("에너"):
        return True
    if marker == "완료" and after.startswith("자"):
        return True
    if marker in {"우려", "전망", "가능성", "가능", "기대"} and after.startswith(
        "에 대한"
    ):
        return True
    if marker == "준공" and "책임 준공" in around:
        return True
    if marker == "이행" and (
        after.startswith("법")
        # 이행 '가속'·'원년'·'방안'은 발언이지 실행 개시 확인이 아니다
        # (검수 S-142·S-154·S-167·S-169).
        or any(word in after for word in ("가속", "원년", "방안"))
    ):
        return True
    # 계획 차질은 계획 단계 사건이다 — 실제 지연이 아니다(검수 S-307).
    if marker == "차질" and before.endswith(("계획", "계획 ")):
        return True
    # 협상 결렬은 협상 국면의 사건으로 남긴다(검수 S-137).
    if marker == "결렬" and "협상" in before:
        return True
    # '타결 불발'은 타결이 일어나지 않은 것이다(확증 검수 S2-015).
    if "타결" in marker and text[end : end + 4].lstrip().startswith(("불발", "무산")):
        return True
    if marker == "예비후보" and _is_political_candidate_context(text):
        return True
    if marker == "입찰" and any(
        phrase in around
        for phrase in (
            "입찰 담합",
            "입찰담합",
            "입찰 제한",
            "입찰제한",
            "경쟁입찰제",
            # 국채·채권 경매와 불참·자격 문맥은 사업 입찰 단계가 아니다
            # (검수 S-014·S-016·S-023).
            "년물",
            "국채",
            "경매",
            "채권",
            "불참",
            "자격",
        )
    ):
        return True
    if marker == "수주" and any(
        phrase in around for phrase in ("수주 지원", "수주 경쟁")
    ):
        return True
    if marker == "협의" and "담합" in around:
        return True
    if marker == "착공" and after.startswith("식") and "제재 면제" in around:
        return True
    if marker == "착수" and before.endswith("검토 "):
        return True
    if marker == "이행" and after.startswith("계약"):
        return True
    if marker in {"계약 체결", "본계약 체결", "수주계약 체결"} and after.lstrip(
    ).startswith("가속화"):
        return True
    return False


def _is_political_candidate_context(text: str) -> bool:
    return any(
        word in text
        for word in (
            "대선",
            "경선",
            "정치",
            "대통령",
            "민주당",
            "민주통합당",
            "국민의힘",
            "보수통합",
            "공약",
            "예비후보자",
        )
    )


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
        countries: list[tuple[int, str]] = [
            (mention.end, mention.canonical_name)
            for mention in extract_actor_mentions(text)
            if mention.geography_code is not None
            and mention.end <= match.start()
            and match.start() - mention.end <= 28
        ]
        countries.extend(
            (country_match.end(), country)
            for country in _PROJECT_ONLY_COUNTRIES
            for country_match in re.finditer(re.escape(country), text)
            if country_match.end() <= match.start()
            and match.start() - country_match.end() <= 28
        )
        if not countries:
            continue
        _, country = max(countries, key=lambda item: item[0])
        code = " ".join(match.group(1).strip().split()).upper()
        return f"{country} {code}"
    return None


def project_fingerprint(
    *,
    reference: str | None,
    company_codes: tuple[str, ...],
    participant_keys: tuple[str, ...],
) -> str | None:
    """명시 프로젝트의 여러 진행 단계를 같은 장기 대상으로 묶는다.

    단계·소재 유형과 사건마다 달라질 수 있는 참여자는 프로젝트 식별자가
    아니다. 명시 프로젝트명을 정규화한 값만 안정 키로 쓴다. 단순 제품 코드는
    ``extract_project_reference``에서 국가 문맥이 있을 때만 허용한다.
    """

    if reference is None:
        return None
    del company_codes, participant_keys
    normalized_reference = unicodedata.normalize("NFKC", reference).casefold()
    normalized_reference = re.sub(r"^(?:대한민국|한국)\s+", "", normalized_reference)
    normalized_reference = re.sub(r"\s+프로젝트$", "", normalized_reference)
    normalized_reference = re.sub(r"[\s\-_]+", "", normalized_reference)
    return sha256_json({"reference": normalized_reference})


def project_id_from_fingerprint(fingerprint: str | None) -> str | None:
    return None if fingerprint is None else f"project_{fingerprint[:24]}"
