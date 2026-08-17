"""테마 history의 회사 mention·역할 연결 (회사 온톨로지 단계 3).

원인문 본문과 문장 끝 주도주 목록은 의미가 다르다. 본문에서는 유효기간이
맞는 exact alias만 찾고 명시된 행동 표지로 역할을 붙인다. 기존 leader와
membership은 각각 ``LEADER``와 ``RELATED``로만 옮긴다. 이름이 목록에 있다는
이유로 수혜주나 사건 주체로 승격하지 않는다.

역할 규칙이나 evidence 범위가 바뀌면 COMPANY_ROLE_TRANSFORM_VERSION을 올린다.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, AbstractSet, Any, Literal

from packages.infostock.hashing import sha256_json

from .company_entities import (
    STOCK_CODE_RE,
    CompanyMaster,
    CompanyResolution,
    resolve_company,
)
from .transform import parse_cause_sentence

if TYPE_CHECKING:
    from packages.infostock.models import ImportBundle, StockReference, ThemeHistory

COMPANY_ROLE_TRANSFORM_VERSION = "company-role-transform/1.0.3"

CompanyRole = Literal[
    "ACTOR",
    "ISSUER",
    "CONTRACTOR",
    "COUNTERPARTY",
    "TARGET",
    "BENEFICIARY",
    "ADVERSELY_AFFECTED",
    "LEADER",
    "RELATED",
]
MentionKind = Literal["BODY", "LEADER_LIST", "MEMBERSHIP"]
RoleResolutionStatus = Literal[
    "RESOLVED",
    "SOURCE_CODE_MISSING",
    "CODE_INVALID",
    "UNKNOWN_STOCK_CODE",
    "AMBIGUOUS_ALIAS",
    "OUT_OF_VALIDITY",
]
RoleResolutionBasis = Literal["STOCK_CODE", "EXACT_ALIAS", "NONE"]
RoleExtractionBasis = Literal[
    "BODY_RULE",
    "STRUCTURED_LEADER",
    "STRUCTURED_MEMBERSHIP",
]

ALL_COMPANY_ROLES: tuple[CompanyRole, ...] = (
    "ACTOR",
    "ISSUER",
    "CONTRACTOR",
    "COUNTERPARTY",
    "TARGET",
    "BENEFICIARY",
    "ADVERSELY_AFFECTED",
    "LEADER",
    "RELATED",
)
DIRECT_EVENT_ROLES: frozenset[CompanyRole] = frozenset(
    {"ACTOR", "ISSUER", "CONTRACTOR", "TARGET"}
)
_ROLE_RANK = {role: rank for rank, role in enumerate(ALL_COMPANY_ROLES)}

# 긴 표지를 먼저 둔다. 같은 위치에서는 더 구체적인 표지가 evidence가 된다.
_CONTRACT_MARKERS = (
    "공급계약 체결",
    "납품계약 체결",
    "수주계약 체결",
    "양해각서(MOU) 체결",
    "양해각서 체결",
    "업무협약 체결",
    "MOU 체결",
    "계약을 체결",
    "계약 체결",
    "공급 계약",
    "공급계약",
    "납품 계약",
    "납품계약",
    "수주 계약",
    "수주계약",
    "수주",
)
_COLLABORATION_MARKERS = (
    "공동 개발",
    "공동개발",
    "합작법인 설립",
    "사업 협력",
    "전략적 협력",
    "업무협약",
    "양해각서",
    "협력",
    "제휴",
)
_ISSUER_MARKERS = (
    "신주인수권부사채",
    "전환사채",
    "유상증자",
    "무상증자",
    "주식분할",
    "액면분할",
    "상장폐지",
    "실적 발표",
    "잠정 실적",
    "영업이익",
    "흑자전환",
    "적자전환",
    "매출액",
    "자사주",
    "회사채 발행",
    "배당",
    "감자",
    "IPO",
    "상장",
    "호실적",
)
_GENERIC_ACTION_MARKERS = (
    *_CONTRACT_MARKERS,
    "합작법인 설립",
    "공동 개발",
    "공동개발",
    "투자 결정",
    "투자 발표",
    "개발 성공",
    "임상 성공",
    "승인 신청",
    "허가 신청",
    "인수 추진",
    "합병 결정",
    "분할 결정",
    "매각 결정",
    "생산 개시",
    "공급 개시",
    "서비스 출시",
    "제품 출시",
    "사업 진출",
    "업무협약",
    "양해각서",
    "발표",
    "공개",
    "개발",
    "투자",
    "설립",
    "출시",
    "생산",
    "공급",
    "체결",
    "인수",
    "합병",
    "분할",
    "매각",
    "취득",
    "처분",
    "신청",
    "제출",
    "진출",
    "착공",
    "준공",
    "공시",
    "추진",
    "결정",
    "선정",
    "참여",
    "협력",
    "제휴",
    "성공",
    "완료",
    "중단",
    "취소",
)
_PASSIVE_TARGET_MARKERS = (
    "인수 대상",
    "매각 대상",
    "제재 대상",
    "규제 대상",
    "소송 대상",
    "상장폐지",
    "거래정지",
    "압수수색",
    "피인수",
    "피소",
)
_REGULATORY_TARGET_MARKERS = ("제재", "규제")
_ACQUISITION_TARGET_MARKERS = ("인수", "매각")
_BENEFIT_MARKERS = ("반사 수혜", "반사수혜", "수혜")
_ADVERSE_MARKERS = (
    "피해 우려",
    "피해",
    "비용 부담",
    "부담",
    "악영향",
    "타격",
    "손실",
)

_HARD_BOUNDARIES = (".", "!", "?", ";", "。", "\n")
_COUNTERPARTY_PARTICLE_RE = re.compile(
    r"^\s*(?:(?:측\s*)?(?:와|과)(?:의)?|로부터|상대로|에게|에)"
)
_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:(?:측)?(?:은|는|이|가|도))?\s*[,：:]")
_DIRECT_SUBJECT_PARTICLE_RE = re.compile(r"^\s*(?:(?:측)?(?:은|는|이|가|도))\s*")


@dataclass(frozen=True, slots=True)
class CompanyRoleEvidence:
    """역할 하나의 근거. 오프셋은 부모 mention과 같은 원천 문자열 기준이다."""

    source_order: int
    role: CompanyRole
    extraction_basis: RoleExtractionBasis
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.source_order < 0:
            raise ValueError("role evidence source_order는 0 이상이어야 합니다.")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("role evidence span이 올바르지 않습니다.")


@dataclass(frozen=True, slots=True)
class HistoryCompanyMention:
    """history 원천에서 발견한 회사 이름 하나와 해석 결과."""

    source_order: int
    mention_kind: MentionKind
    source_reference_order: int | None
    mention_text: str
    start: int
    end: int
    resolution_status: RoleResolutionStatus
    resolution_basis: RoleResolutionBasis
    seed_stock_code: str | None
    suggested_role: CompanyRole | None
    roles: tuple[CompanyRoleEvidence, ...]

    def __post_init__(self) -> None:
        if self.source_order < 0:
            raise ValueError("mention source_order는 0 이상이어야 합니다.")
        if not self.mention_text:
            raise ValueError("mention_text는 비울 수 없습니다.")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("mention span이 올바르지 않습니다.")
        expected_role: CompanyRole | None = None
        if self.mention_kind == "BODY":
            if self.source_reference_order is not None:
                raise ValueError("본문 mention에는 구조화 원천 순서가 없어야 합니다.")
        elif self.mention_kind == "LEADER_LIST":
            expected_role = "LEADER"
        else:
            expected_role = "RELATED"
        if self.mention_kind != "BODY" and self.source_reference_order is None:
            raise ValueError("구조화 mention에는 원천 순서가 필요합니다.")
        if self.suggested_role != expected_role:
            raise ValueError("mention 종류와 suggested_role이 맞지 않습니다.")
        if self.resolution_status == "RESOLVED":
            if self.seed_stock_code is None or self.resolution_basis == "NONE":
                raise ValueError("해결된 mention에는 회사와 해결 근거가 필요합니다.")
        elif self.seed_stock_code is not None:
            raise ValueError("미해결 mention에는 회사를 붙일 수 없습니다.")
        if self.roles and self.resolution_status != "RESOLVED":
            raise ValueError("미해결 mention에는 role fact를 만들 수 없습니다.")
        if self.mention_kind == "BODY" and any(
            role.role in {"LEADER", "RELATED"} for role in self.roles
        ):
            raise ValueError("본문 mention을 LEADER·RELATED로 만들 수 없습니다.")
        if (
            self.mention_kind != "BODY"
            and self.roles
            and (len(self.roles) != 1 or self.roles[0].role != expected_role)
        ):
            raise ValueError("구조화 mention은 원천이 정한 역할 하나만 가집니다.")

    @property
    def role_names(self) -> tuple[CompanyRole, ...]:
        return tuple(role.role for role in self.roles)


@dataclass(frozen=True, slots=True)
class HistoryCompanyRoleLabel:
    """한 테마 history revision의 회사 mention·역할 전체."""

    company_master_version: str
    role_transform_version: str
    source_theme_id: str
    theme_name: str
    source_history_key: str
    event_date: date | None
    history_content_hash: str
    raw_text: str
    mentions: tuple[HistoryCompanyMention, ...]

    def __post_init__(self) -> None:
        if not self.company_master_version or not self.role_transform_version:
            raise ValueError("회사 master·역할 변환 버전은 비울 수 없습니다.")
        if not self.source_theme_id or not self.source_history_key:
            raise ValueError("테마·history 원천 키는 비울 수 없습니다.")
        if not re.fullmatch(r"[0-9a-f]{64}", self.history_content_hash):
            raise ValueError("history_content_hash는 SHA-256이어야 합니다.")
        if tuple(mention.source_order for mention in self.mentions) != tuple(
            range(len(self.mentions))
        ):
            raise ValueError("mention source_order는 입력 순서대로 연속이어야 합니다.")
        for mention in self.mentions:
            source_text = (
                self.raw_text
                if mention.mention_kind == "BODY"
                else mention.mention_text
            )
            if mention.end > len(source_text):
                raise ValueError("mention span이 원천 문자열 범위를 벗어납니다.")
            if source_text[mention.start : mention.end] != mention.mention_text:
                raise ValueError("mention span과 mention_text가 다릅니다.")
            if tuple(role.source_order for role in mention.roles) != tuple(
                range(len(mention.roles))
            ):
                raise ValueError(
                    "role source_order는 mention 안에서 연속이어야 합니다."
                )
            for role in mention.roles:
                if role.end > len(source_text):
                    raise ValueError("role evidence span이 원천 범위를 벗어납니다.")
                if role.start > mention.start or role.end < mention.end:
                    raise ValueError(
                        "role evidence에는 회사 mention이 포함돼야 합니다."
                    )

    @property
    def output_hash(self) -> str:
        return sha256_json(self.as_dict(include_raw_text=False))

    def as_dict(self, *, include_raw_text: bool = True) -> dict[str, Any]:
        row: dict[str, Any] = {
            "companyMasterVersion": self.company_master_version,
            "roleTransformVersion": self.role_transform_version,
            "sourceThemeId": self.source_theme_id,
            "themeName": self.theme_name,
            "sourceHistoryKey": self.source_history_key,
            "eventDate": self.event_date.isoformat() if self.event_date else None,
            "historyContentHash": self.history_content_hash,
            "mentions": [],
        }
        if include_raw_text:
            row["rawText"] = self.raw_text
        serialized: list[dict[str, Any]] = []
        for mention in self.mentions:
            source_text = (
                self.raw_text
                if mention.mention_kind == "BODY"
                else mention.mention_text
            )
            serialized.append(
                {
                    "sourceOrder": mention.source_order,
                    "mentionKind": mention.mention_kind,
                    "sourceReferenceOrder": mention.source_reference_order,
                    "mentionText": mention.mention_text,
                    "start": mention.start,
                    "end": mention.end,
                    "resolutionStatus": mention.resolution_status,
                    "resolutionBasis": mention.resolution_basis,
                    "seedStockCode": mention.seed_stock_code,
                    "suggestedRole": mention.suggested_role,
                    "roles": [
                        {
                            "sourceOrder": evidence.source_order,
                            "role": evidence.role,
                            "extractionBasis": evidence.extraction_basis,
                            "start": evidence.start,
                            "end": evidence.end,
                            "evidenceText": source_text[evidence.start : evidence.end],
                        }
                        for evidence in mention.roles
                    ],
                }
            )
        row["mentions"] = serialized
        return row


@dataclass(frozen=True, slots=True)
class CompanyHistoryObservation:
    """회사 질의에서 한 번만 세는 history 원천 관측."""

    source_theme_id: str
    theme_name: str
    source_history_key: str
    event_date: date | None
    roles: tuple[CompanyRole, ...]
    mentions: tuple[HistoryCompanyMention, ...]


@dataclass(frozen=True, slots=True)
class CompanyThemeAssociation:
    """한 회사와 테마의 결정론적 history 반응 집계."""

    source_theme_id: str
    theme_name: str
    observation_count: int
    first_seen_on: date | None
    last_seen_on: date | None
    roles: tuple[CompanyRole, ...]


@dataclass(frozen=True, slots=True)
class _BodyOccurrence:
    text: str
    start: int
    end: int
    resolution: CompanyResolution


@dataclass(frozen=True, slots=True)
class _AliasMatcher:
    pattern: re.Pattern[str]


def _theme_sort_key(source_theme_id: str) -> tuple[int, int | str]:
    return (
        (0, int(source_theme_id)) if source_theme_id.isdigit() else (1, source_theme_id)
    )


def _marker_after(
    text: str,
    start: int,
    markers: tuple[str, ...],
    *,
    stop: int,
    max_distance: int,
) -> tuple[int, int] | None:
    limit = min(stop, start + max_distance)
    found: list[tuple[int, int]] = []
    for marker in markers:
        position = text.find(marker, start, limit)
        if position >= 0:
            found.append((position, position + len(marker)))
    if not found:
        return None
    return min(found, key=lambda item: (item[0], -(item[1] - item[0])))


def _right_boundary(text: str, start: int) -> int:
    positions = [
        position
        for boundary in _HARD_BOUNDARIES
        if (position := text.find(boundary, start)) >= 0
    ]
    return min(positions) if positions else len(text)


def _has_name_boundary(text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1].isalnum():
        return False
    if end >= len(text) or not text[end].isalnum():
        return True
    # 조사가 바로 붙는 한국어 표기는 허용하되 더 긴 이름 속 부분 문자열은 막는다.
    return text[end] in frozenset("은는이가을를와과의도에로")


def _alias_matcher(master: CompanyMaster) -> _AliasMatcher:
    aliases = sorted(
        {
            alias.alias
            for company in master.companies
            for alias in company.aliases
            if alias.alias
        }
        | {company.canonical_name for company in master.companies},
        key=lambda value: (-len(value), value.casefold(), value),
    )
    if not aliases:
        return _AliasMatcher(pattern=re.compile(r"(?!x)x"))
    return _AliasMatcher(
        pattern=re.compile(
            "|".join(re.escape(alias) for alias in aliases), re.IGNORECASE
        )
    )


def _body_occurrences(
    master: CompanyMaster,
    matcher: _AliasMatcher,
    text: str,
    *,
    as_of: date | None,
) -> tuple[_BodyOccurrence, ...]:
    candidates: list[tuple[int, int, str]] = []
    for match in matcher.pattern.finditer(text):
        start, end = match.span()
        if not _has_name_boundary(text, start, end):
            continue
        candidates.append((start, end, match.group(0)))

    occurrences: list[_BodyOccurrence] = []
    for start, end, value in candidates:
        resolution = resolve_company(master, value, as_of=as_of)
        if (
            resolution.status == "UNKNOWN"
        ):  # exact master alias에서 나온 값이라 방어용이다.
            continue
        occurrences.append(
            _BodyOccurrence(text=value, start=start, end=end, resolution=resolution)
        )
    return tuple(occurrences)


def _resolution_fields(
    resolution: CompanyResolution,
    *,
    resolvable_seed_codes: AbstractSet[str] | None,
) -> tuple[RoleResolutionStatus, RoleResolutionBasis, str | None]:
    if resolution.status == "RESOLVED":
        if (
            resolvable_seed_codes is not None
            and resolution.seed_stock_code not in resolvable_seed_codes
        ):
            return "UNKNOWN_STOCK_CODE", "EXACT_ALIAS", None
        return "RESOLVED", "EXACT_ALIAS", resolution.seed_stock_code
    if resolution.status == "AMBIGUOUS":
        return "AMBIGUOUS_ALIAS", "EXACT_ALIAS", None
    return "OUT_OF_VALIDITY", "EXACT_ALIAS", None


def _structured_resolution(
    master: CompanyMaster,
    reference: StockReference,
    *,
    resolvable_seed_codes: AbstractSet[str] | None,
) -> tuple[RoleResolutionStatus, RoleResolutionBasis, str | None]:
    code = reference.stock_code
    if code is None:
        return "SOURCE_CODE_MISSING", "NONE", None
    if not STOCK_CODE_RE.fullmatch(code) or reference.quality_status == "CODE_INVALID":
        return "CODE_INVALID", "NONE", None
    resolution = resolve_company(master, code)
    if resolution.status != "RESOLVED" or (
        resolvable_seed_codes is not None
        and resolution.seed_stock_code not in resolvable_seed_codes
    ):
        return "UNKNOWN_STOCK_CODE", "STOCK_CODE", None
    return "RESOLVED", "STOCK_CODE", resolution.seed_stock_code


def _is_counterparty(
    text: str, occurrence: _BodyOccurrence, *, stop: int
) -> tuple[int, int] | None:
    event = _marker_after(
        text,
        occurrence.end,
        (*_CONTRACT_MARKERS, *_COLLABORATION_MARKERS),
        stop=stop,
        max_distance=96,
    )
    if event is None:
        return None
    between = text[occurrence.end : event[0]]
    return event if _COUNTERPARTY_PARTICLE_RE.match(between) else None


def _is_subject(
    text: str,
    occurrence: _BodyOccurrence,
    marker_start: int,
    occurrences: tuple[_BodyOccurrence, ...],
) -> bool:
    between = text[occurrence.end : marker_start]
    if _COUNTERPARTY_PARTICLE_RE.match(between):
        return False
    prefix_match = _SUBJECT_PREFIX_RE.match(between)
    direct_match = _DIRECT_SUBJECT_PARTICLE_RE.match(between)
    explicit = prefix_match is not None or direct_match is not None
    if not explicit and marker_start - occurrence.end > 12:
        return False
    for other in occurrences:
        if occurrence.end <= other.start < marker_start:
            other_gap = text[other.end : marker_start]
            # ``A, B 인수 추진``에서 B는 목적어이고 A가 행동 주체다.
            if (
                prefix_match is not None
                and marker_start - other.end <= 12
                and text.startswith(_ACQUISITION_TARGET_MARKERS, marker_start)
            ):
                continue
            if _SUBJECT_PREFIX_RE.match(other_gap) or (
                not _COUNTERPARTY_PARTICLE_RE.match(other_gap)
                and marker_start - other.end <= 12
            ):
                return False
    return True


def _preceding_subject_exists(
    text: str,
    occurrence: _BodyOccurrence,
    marker_start: int,
    occurrences: tuple[_BodyOccurrence, ...],
) -> bool:
    for other in occurrences:
        if other.end > occurrence.start:
            continue
        # 목적어 직전 회사까지의 구간에서 쉼표나 주격 표지가 있으면 앞 회사가
        # 명시된 주체다. marker까지 보게 하면 목적어 회사를 새 주체로 오인한다.
        gap = text[other.end : occurrence.start]
        if _SUBJECT_PREFIX_RE.match(gap) or _DIRECT_SUBJECT_PARTICLE_RE.match(gap):
            return True
    return False


def _no_company_between(
    occurrence: _BodyOccurrence,
    marker_start: int,
    occurrences: tuple[_BodyOccurrence, ...],
) -> bool:
    return not any(
        occurrence.end <= other.start < marker_start for other in occurrences
    )


def _role_evidence(
    text: str,
    occurrence: _BodyOccurrence,
    occurrences: tuple[_BodyOccurrence, ...],
) -> tuple[CompanyRoleEvidence, ...]:
    stop = _right_boundary(text, occurrence.end)
    found: dict[CompanyRole, tuple[int, int]] = {}

    counterparty_event = _is_counterparty(text, occurrence, stop=stop)
    if counterparty_event is not None:
        found["COUNTERPARTY"] = counterparty_event

    passive_target = _marker_after(
        text,
        occurrence.end,
        _PASSIVE_TARGET_MARKERS,
        stop=stop,
        max_distance=48,
    )
    regulatory_target = _marker_after(
        text,
        occurrence.end,
        _REGULATORY_TARGET_MARKERS,
        stop=stop,
        max_distance=48,
    )
    acquisition_target = _marker_after(
        text,
        occurrence.end,
        _ACQUISITION_TARGET_MARKERS,
        stop=stop,
        max_distance=32,
    )
    target_event = passive_target or regulatory_target
    if acquisition_target is not None and _preceding_subject_exists(
        text, occurrence, acquisition_target[0], occurrences
    ):
        target_event = acquisition_target
    if target_event is not None and _no_company_between(
        occurrence, target_event[0], occurrences
    ):
        found["TARGET"] = target_event

    benefit = _marker_after(
        text,
        occurrence.end,
        _BENEFIT_MARKERS,
        stop=stop,
        max_distance=48,
    )
    if benefit is not None and _no_company_between(occurrence, benefit[0], occurrences):
        found["BENEFICIARY"] = benefit

    adverse = _marker_after(
        text,
        occurrence.end,
        _ADVERSE_MARKERS,
        stop=stop,
        max_distance=48,
    )
    if adverse is not None and _no_company_between(occurrence, adverse[0], occurrences):
        found["ADVERSELY_AFFECTED"] = adverse

    issuer = _marker_after(
        text,
        occurrence.end,
        _ISSUER_MARKERS,
        stop=stop,
        max_distance=80,
    )
    if issuer is not None and _is_subject(text, occurrence, issuer[0], occurrences):
        found["ISSUER"] = issuer

    contract = _marker_after(
        text,
        occurrence.end,
        _CONTRACT_MARKERS,
        stop=stop,
        max_distance=112,
    )
    if (
        contract is not None
        and counterparty_event is None
        and "TARGET" not in found
        and _is_subject(text, occurrence, contract[0], occurrences)
    ):
        found["CONTRACTOR"] = contract

    action = _marker_after(
        text,
        occurrence.end,
        _GENERIC_ACTION_MARKERS,
        stop=stop,
        max_distance=96,
    )
    if (
        action is not None
        and counterparty_event is None
        and "TARGET" not in found
        and _is_subject(text, occurrence, action[0], occurrences)
    ):
        found["ACTOR"] = action

    return tuple(
        CompanyRoleEvidence(
            source_order=source_order,
            role=role,
            extraction_basis="BODY_RULE",
            start=min(occurrence.start, span[0]),
            end=max(occurrence.end, span[1]),
        )
        for source_order, (role, span) in enumerate(
            sorted(found.items(), key=lambda item: _ROLE_RANK[item[0]])
        )
    )


def classify_history_company_roles(
    *,
    source_theme_id: str,
    theme_name: str,
    history: ThemeHistory,
    master: CompanyMaster,
    resolvable_seed_codes: AbstractSet[str] | None = None,
    _matcher: _AliasMatcher | None = None,
) -> HistoryCompanyRoleLabel:
    """history 한 건에서 본문 mention과 구조화 주도주·관련주를 분리한다."""

    parsed = parse_cause_sentence(history.raw_text)
    body_text = history.raw_text[: parsed.core_end]
    occurrences = _body_occurrences(
        master,
        _matcher or _alias_matcher(master),
        body_text,
        as_of=history.event_date,
    )
    mentions: list[HistoryCompanyMention] = []
    for occurrence in occurrences:
        status, basis, seed = _resolution_fields(
            occurrence.resolution,
            resolvable_seed_codes=resolvable_seed_codes,
        )
        roles = (
            _role_evidence(body_text, occurrence, occurrences)
            if status == "RESOLVED"
            else ()
        )
        mentions.append(
            HistoryCompanyMention(
                source_order=len(mentions),
                mention_kind="BODY",
                source_reference_order=None,
                mention_text=occurrence.text,
                start=occurrence.start,
                end=occurrence.end,
                resolution_status=status,
                resolution_basis=basis,
                seed_stock_code=seed,
                suggested_role=None,
                roles=roles,
            )
        )

    def add_structured(
        reference: StockReference,
        *,
        kind: Literal["LEADER_LIST", "MEMBERSHIP"],
        role: Literal["LEADER", "RELATED"],
        extraction_basis: Literal["STRUCTURED_LEADER", "STRUCTURED_MEMBERSHIP"],
    ) -> None:
        status, basis, seed = _structured_resolution(
            master,
            reference,
            resolvable_seed_codes=resolvable_seed_codes,
        )
        role_facts = (
            (
                CompanyRoleEvidence(
                    source_order=0,
                    role=role,
                    extraction_basis=extraction_basis,
                    start=0,
                    end=len(reference.name),
                ),
            )
            if status == "RESOLVED"
            else ()
        )
        mentions.append(
            HistoryCompanyMention(
                source_order=len(mentions),
                mention_kind=kind,
                source_reference_order=reference.source_order,
                mention_text=reference.name,
                start=0,
                end=len(reference.name),
                resolution_status=status,
                resolution_basis=basis,
                seed_stock_code=seed,
                suggested_role=role,
                roles=role_facts,
            )
        )

    for reference in sorted(history.leaders, key=lambda item: item.source_order):
        add_structured(
            reference,
            kind="LEADER_LIST",
            role="LEADER",
            extraction_basis="STRUCTURED_LEADER",
        )
    for reference in sorted(history.member_stocks, key=lambda item: item.source_order):
        add_structured(
            reference,
            kind="MEMBERSHIP",
            role="RELATED",
            extraction_basis="STRUCTURED_MEMBERSHIP",
        )
    return HistoryCompanyRoleLabel(
        company_master_version=master.master_version,
        role_transform_version=COMPANY_ROLE_TRANSFORM_VERSION,
        source_theme_id=source_theme_id,
        theme_name=theme_name,
        source_history_key=history.source_history_key,
        event_date=history.event_date,
        history_content_hash=history.content_hash,
        raw_text=history.raw_text,
        mentions=tuple(mentions),
    )


def label_company_history(
    bundle: ImportBundle,
    master: CompanyMaster,
    *,
    resolvable_seed_codes: AbstractSet[str] | None = None,
) -> tuple[tuple[HistoryCompanyRoleLabel, ...], dict[str, Any]]:
    """수집본 전체를 라벨링하고 역할·해결 상태 coverage를 계산한다."""

    matcher = _alias_matcher(master)
    labels = tuple(
        classify_history_company_roles(
            source_theme_id=detail.source_theme_id,
            theme_name=detail.theme_name,
            history=history,
            master=master,
            resolvable_seed_codes=resolvable_seed_codes,
            _matcher=matcher,
        )
        for detail in sorted(
            bundle.details, key=lambda item: _theme_sort_key(item.source_theme_id)
        )
        for history in sorted(
            detail.history,
            key=lambda item: (item.source_order, item.source_history_key),
        )
    )
    report: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "datasetHash": bundle.dataset_hash,
        "companyMasterVersion": master.master_version,
        "roleTransformVersion": COMPANY_ROLE_TRANSFORM_VERSION,
        "reviewStatus": "AI_DRAFT",
        **summarize_company_role_labels(labels),
    }
    return labels, report


def summarize_company_role_labels(
    labels: Iterable[HistoryCompanyRoleLabel],
) -> dict[str, Any]:
    """라벨 튜플의 mention·resolution·role 분포를 다시 계산한다."""

    materialized = tuple(labels)
    mentions = [mention for label in materialized for mention in label.mentions]
    roles = [role.role for mention in mentions for role in mention.roles]
    direct_histories = sum(
        bool(
            DIRECT_EVENT_ROLES
            & {role.role for mention in label.mentions for role in mention.roles}
        )
        for label in materialized
    )
    return {
        "totalHistories": len(materialized),
        "historiesWithCompanyMention": sum(
            bool(label.mentions) for label in materialized
        ),
        "directEventHistories": direct_histories,
        "mentionCounts": dict(
            sorted(Counter(mention.mention_kind for mention in mentions).items())
        ),
        "resolutionCounts": dict(
            sorted(Counter(mention.resolution_status for mention in mentions).items())
        ),
        "roleCounts": {role: Counter(roles).get(role, 0) for role in ALL_COMPANY_ROLES},
    }


def _labels_by_observation(
    labels: Iterable[HistoryCompanyRoleLabel],
) -> tuple[HistoryCompanyRoleLabel, ...]:
    unique: dict[tuple[str, str], HistoryCompanyRoleLabel] = {}
    for label in labels:
        key = (label.source_theme_id, label.source_history_key)
        previous = unique.get(key)
        if previous is not None and previous.output_hash != label.output_hash:
            raise ValueError(
                "같은 history에 서로 다른 역할 revision을 섞을 수 없습니다."
            )
        unique[key] = label
    return tuple(
        sorted(
            unique.values(),
            key=lambda label: (
                label.event_date is None,
                label.event_date or date.max,
                _theme_sort_key(label.source_theme_id),
                label.source_history_key,
            ),
        )
    )


def query_company_appearance(
    labels: Iterable[HistoryCompanyRoleLabel],
    seed_stock_code: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[CompanyHistoryObservation, ...]:
    """회사 mention이 있는 history 원천 관측을 한 history당 한 번 반환한다."""

    observations: list[CompanyHistoryObservation] = []
    for label in _labels_by_observation(labels):
        if date_from is not None and (
            label.event_date is None or label.event_date < date_from
        ):
            continue
        if date_to is not None and (
            label.event_date is None or label.event_date > date_to
        ):
            continue
        mentions = tuple(
            mention
            for mention in label.mentions
            if mention.resolution_status == "RESOLVED"
            and mention.seed_stock_code == seed_stock_code
        )
        if not mentions:
            continue
        roles = tuple(
            sorted(
                {role.role for mention in mentions for role in mention.roles},
                key=_ROLE_RANK.__getitem__,
            )
        )
        observations.append(
            CompanyHistoryObservation(
                source_theme_id=label.source_theme_id,
                theme_name=label.theme_name,
                source_history_key=label.source_history_key,
                event_date=label.event_date,
                roles=roles,
                mentions=mentions,
            )
        )
    return tuple(observations)


def query_company_theme_association(
    labels: Iterable[HistoryCompanyRoleLabel],
    seed_stock_code: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[CompanyThemeAssociation, ...]:
    """역할이 확인된 회사·테마 history 반응을 테마별로 집계한다."""

    grouped: dict[str, list[CompanyHistoryObservation]] = {}
    for observation in query_company_appearance(
        labels, seed_stock_code, date_from=date_from, date_to=date_to
    ):
        if not observation.roles:
            continue
        grouped.setdefault(observation.source_theme_id, []).append(observation)
    associations: list[CompanyThemeAssociation] = []
    for source_theme_id, observations in grouped.items():
        dated = [
            item.event_date for item in observations if item.event_date is not None
        ]
        roles = tuple(
            sorted(
                {role for item in observations for role in item.roles},
                key=_ROLE_RANK.__getitem__,
            )
        )
        associations.append(
            CompanyThemeAssociation(
                source_theme_id=source_theme_id,
                theme_name=min(item.theme_name for item in observations),
                observation_count=len(observations),
                first_seen_on=min(dated) if dated else None,
                last_seen_on=max(dated) if dated else None,
                roles=roles,
            )
        )
    return tuple(
        sorted(
            associations,
            key=lambda item: (
                -item.observation_count,
                _theme_sort_key(item.source_theme_id),
                item.theme_name,
            ),
        )
    )


def query_company_direct_events(
    labels: Iterable[HistoryCompanyRoleLabel],
    seed_stock_code: str,
    *,
    roles: Iterable[CompanyRole] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[CompanyHistoryObservation, ...]:
    """직접 역할이 있는 history만 반환한다. LEADER 전용 기록은 제외된다.

    단계 4의 catalyst 중복 제거 전이므로 반환 단위는 고유 사건이 아니라 history
    원천 관측이다. 같은 history 안의 복수 role·mention은 한 번만 센다.
    """

    selected = DIRECT_EVENT_ROLES if roles is None else frozenset(roles)
    unknown = selected - frozenset(ALL_COMPANY_ROLES)
    if unknown:
        raise ValueError(f"지원하지 않는 회사 역할입니다: {sorted(unknown)}")
    return tuple(
        observation
        for observation in query_company_appearance(
            labels,
            seed_stock_code,
            date_from=date_from,
            date_to=date_to,
        )
        if selected & frozenset(observation.roles)
    )
