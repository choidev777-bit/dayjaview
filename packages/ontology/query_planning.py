"""자연어 질문 → 닫힌 QueryPlan (E-22 단계 5).

LLM을 쓰지 않는다. 같은 문장·같은 catalog·같은 버전이면 항상 같은 plan이
나온다. 해석하지 못한 문장은 추측하지 않고 실패 사유를 남긴다.

해석 순서는 계약서 8절 그대로다 — 질문 분류 → 슬롯 해석 → QueryPlan. 회사
슬롯은 8.1절 우선순위(종목코드 → 현재 사명 → 질문 시점에 유효한 과거 사명 →
복수 후보 → 실패)를 그대로 따르며 애매한 이름을 임의로 고르지 않는다.

질의 원문은 이 모듈 밖으로 나가지 않는다. 실패 사유만 집계 대상이다(12절).
"""

from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import AbstractSet, Any, Literal, Mapping, Sequence

from packages.infostock.hashing import sha256_json

from .company_entities import CompanyMaster, resolve_company
from .models import CatalystTypeDefinition
from .query_contracts import (
    QUERY_CONTRACT_BY_TYPE,
    QUERY_CONTRACT_VERSION,
    CountUnit,
    QuerySlot,
    QueryType,
    query_contract_content_hash,
    validate_query_slots,
)
from .vocabulary import VOCABULARY

QUERY_PLANNER_VERSION = "query-planner/1.4.0"
# 인포스탁 수집본이 시작하는 해. "과거·역대"는 여기부터 오늘까지다.
COLLECTION_FROM = date(2007, 1, 1)

QueryDirection = Literal["UP", "DOWN"]


class RelativeExpression(StrEnum):
    """지금 시각을 알아야 풀리는 날짜·기간 표현."""

    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    DAY_BEFORE_YESTERDAY = "DAY_BEFORE_YESTERDAY"
    PREVIOUS_TRADING_DAY = "PREVIOUS_TRADING_DAY"
    LAST_MONDAY = "LAST_MONDAY"
    LAST_TUESDAY = "LAST_TUESDAY"
    LAST_WEDNESDAY = "LAST_WEDNESDAY"
    LAST_THURSDAY = "LAST_THURSDAY"
    LAST_FRIDAY = "LAST_FRIDAY"
    THIS_WEEK = "THIS_WEEK"
    LAST_WEEK = "LAST_WEEK"
    THIS_MONTH = "THIS_MONTH"
    LAST_MONTH = "LAST_MONTH"
    THIS_YEAR = "THIS_YEAR"
    LAST_YEAR = "LAST_YEAR"
    LAST_7_DAYS = "LAST_7_DAYS"
    LAST_3_MONTHS = "LAST_3_MONTHS"
    LAST_12_MONTHS = "LAST_12_MONTHS"
    RECENT = "RECENT"
    ALL_TIME = "ALL_TIME"


class FailureReason(StrEnum):
    """내부 실패 사유. 사용자에게는 PUBLIC_FAILURE_BY_REASON만 나간다."""

    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    NOT_INTERPRETABLE = "NOT_INTERPRETABLE"
    UNKNOWN_COMPANY = "UNKNOWN_COMPANY"
    AMBIGUOUS_ALIAS = "AMBIGUOUS_ALIAS"
    UNKNOWN_THEME = "UNKNOWN_THEME"
    UNKNOWN_CATALYST_TYPE = "UNKNOWN_CATALYST_TYPE"
    MISSING_SLOT = "MISSING_SLOT"
    NO_MATCHING_EVENT = "NO_MATCHING_EVENT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_PUBLISHED = "NOT_PUBLISHED"
    UNSUPPORTED_QUERY_TYPE = "UNSUPPORTED_QUERY_TYPE"
    OUTCOME_GATE_CLOSED = "OUTCOME_GATE_CLOSED"
    SIMILARITY_GATE_CLOSED = "SIMILARITY_GATE_CLOSED"
    QUALITY_NOT_VERIFIED = "QUALITY_NOT_VERIFIED"
    PREREQUISITE_NOT_READY = "PREREQUISITE_NOT_READY"


class PublicFailure(StrEnum):
    """사용자가 보는 네 가지 실패 상태."""

    QUESTION_NOT_UNDERSTOOD = "QUESTION_NOT_UNDERSTOOD"
    NO_RECORD = "NO_RECORD"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    QUALITY_NOT_VERIFIED = "QUALITY_NOT_VERIFIED"


PUBLIC_FAILURE_BY_REASON: Mapping[FailureReason, PublicFailure] = {
    FailureReason.OUT_OF_SCOPE: PublicFailure.OUT_OF_SCOPE,
    FailureReason.NOT_INTERPRETABLE: PublicFailure.QUESTION_NOT_UNDERSTOOD,
    FailureReason.UNKNOWN_COMPANY: PublicFailure.QUESTION_NOT_UNDERSTOOD,
    FailureReason.AMBIGUOUS_ALIAS: PublicFailure.QUESTION_NOT_UNDERSTOOD,
    FailureReason.UNKNOWN_THEME: PublicFailure.QUESTION_NOT_UNDERSTOOD,
    FailureReason.UNKNOWN_CATALYST_TYPE: PublicFailure.QUESTION_NOT_UNDERSTOOD,
    FailureReason.MISSING_SLOT: PublicFailure.QUESTION_NOT_UNDERSTOOD,
    FailureReason.NO_MATCHING_EVENT: PublicFailure.NO_RECORD,
    FailureReason.INSUFFICIENT_EVIDENCE: PublicFailure.NO_RECORD,
    FailureReason.NOT_PUBLISHED: PublicFailure.NO_RECORD,
    FailureReason.UNSUPPORTED_QUERY_TYPE: PublicFailure.OUT_OF_SCOPE,
    FailureReason.OUTCOME_GATE_CLOSED: PublicFailure.OUT_OF_SCOPE,
    FailureReason.SIMILARITY_GATE_CLOSED: PublicFailure.OUT_OF_SCOPE,
    FailureReason.QUALITY_NOT_VERIFIED: PublicFailure.QUALITY_NOT_VERIFIED,
    FailureReason.PREREQUISITE_NOT_READY: PublicFailure.QUALITY_NOT_VERIFIED,
}

PUBLIC_FAILURE_LABEL_KO: Mapping[PublicFailure, str] = {
    PublicFailure.QUESTION_NOT_UNDERSTOOD: "질문 해석 실패",
    PublicFailure.NO_RECORD: "기록 없음",
    PublicFailure.OUT_OF_SCOPE: "제품 범위 밖",
    PublicFailure.QUALITY_NOT_VERIFIED: "품질 미검증",
}


# ---------------------------------------------------------------- 슬롯 값


@dataclass(frozen=True, slots=True)
class DateSlot:
    value: date
    literal: str
    expression: RelativeExpression | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value.isoformat(),
            "literal": self.literal,
            "expression": None if self.expression is None else self.expression.value,
        }


@dataclass(frozen=True, slots=True)
class PeriodSlot:
    start: date
    end: date
    literal: str
    expression: RelativeExpression | None = None

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("기간의 끝은 시작보다 앞설 수 없습니다.")

    def covers(self, value: date) -> bool:
        return self.start <= value <= self.end

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": self.start.isoformat(),
            "to": self.end.isoformat(),
            "literal": self.literal,
            "expression": None if self.expression is None else self.expression.value,
        }


@dataclass(frozen=True, slots=True)
class CompanyRef:
    """해석된 회사 슬롯. seed_stock_code가 회사 온톨로지의 안정 식별자다."""

    seed_stock_code: str
    canonical_name: str
    matched_text: str
    basis: Literal["STOCK_CODE", "CURRENT_NAME", "PAST_ALIAS"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "seedStockCode": self.seed_stock_code,
            "canonicalName": self.canonical_name,
            "matchedText": self.matched_text,
            "basis": self.basis,
        }


@dataclass(frozen=True, slots=True)
class CompanyCandidateRef:
    seed_stock_code: str
    canonical_name: str
    matched_text: str
    valid_from: date | None
    valid_to: date | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seedStockCode": self.seed_stock_code,
            "canonicalName": self.canonical_name,
            "matchedText": self.matched_text,
            "validFrom": None if self.valid_from is None else self.valid_from.isoformat(),
            "validTo": None if self.valid_to is None else self.valid_to.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ThemeRef:
    source_theme_id: str
    theme_name: str
    matched_text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceThemeId": self.source_theme_id,
            "themeName": self.theme_name,
            "matchedText": self.matched_text,
        }


@dataclass(frozen=True, slots=True)
class CatalystTypeRef:
    type_id: str
    name_ko: str
    matched_text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "typeId": self.type_id,
            "nameKo": self.name_ko,
            "matchedText": self.matched_text,
        }


@dataclass(frozen=True, slots=True)
class AmountCondition:
    """금액 조건. 합계는 원 단위 Decimal로만 비교한다."""

    comparator: Literal["GTE", "GT", "LTE", "LT"]
    normalized_value: Decimal
    currency: str
    literal: str

    def matches(self, value: Decimal) -> bool:
        if self.comparator == "GTE":
            return value >= self.normalized_value
        if self.comparator == "GT":
            return value > self.normalized_value
        if self.comparator == "LTE":
            return value <= self.normalized_value
        return value < self.normalized_value

    def as_dict(self) -> dict[str, Any]:
        return {
            "comparator": self.comparator,
            "normalizedValue": format(self.normalized_value, "f"),
            "currency": self.currency,
            "literal": self.literal,
        }


# ---------------------------------------------------------------- QueryPlan


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """닫힌 질의 하나. LLM은 여기에 없는 조건을 더할 수 없다."""

    query_type: QueryType
    count_unit: CountUnit
    direction: QueryDirection | None = None
    date: DateSlot | None = None
    period: PeriodSlot | None = None
    company: CompanyRef | None = None
    themes: tuple[ThemeRef, ...] = ()
    catalyst_type: CatalystTypeRef | None = None
    amount_condition: AmountCondition | None = None
    # 소재를 좁히는 주제어("로봇 정책"의 로봇). 원문에 있던 낱말 하나만 담는다.
    topic: str | None = None
    # 사건일 기준 며칠 뒤 주가를 물었는지("3거래일 뒤"의 3). 안 물었으면 None.
    outcome_horizon: int | None = None
    planner_version: str = QUERY_PLANNER_VERSION
    contract_version: str = QUERY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        contract = QUERY_CONTRACT_BY_TYPE[self.query_type]
        if self.count_unit != contract.count_unit:
            raise ValueError("QueryPlan의 집계 단위가 계약과 다릅니다.")
        validate_query_slots(self.query_type, self.slot_mapping())

    @property
    def period_slot_key(self) -> QuerySlot:
        """같은 기간 값이라도 계약이 쓰는 슬롯 이름이 다르다."""

        return (
            QuerySlot.DATE_RANGE
            if self.query_type is QueryType.PERIOD_SUMMARY
            else QuerySlot.PERIOD
        )

    def slot_mapping(self) -> dict[str, object]:
        slots: dict[str, object] = {}
        if self.date is not None:
            slots[QuerySlot.DATE.value] = self.date.value.isoformat()
        if self.period is not None:
            slots[self.period_slot_key.value] = (
                f"{self.period.start.isoformat()}~{self.period.end.isoformat()}"
            )
        if self.company is not None:
            slots[QuerySlot.COMPANY.value] = self.company.seed_stock_code
            slots[QuerySlot.STOCK.value] = self.company.seed_stock_code
        if self.themes:
            slots[QuerySlot.THEME.value] = self.themes[0].source_theme_id
            if len(self.themes) >= 2:
                slots[QuerySlot.THEMES.value] = tuple(
                    theme.source_theme_id for theme in self.themes
                )
        if self.catalyst_type is not None:
            slots[QuerySlot.CATALYST_TYPE.value] = self.catalyst_type.type_id
            slots[QuerySlot.EVENT.value] = self.catalyst_type.type_id
        if self.amount_condition is not None:
            slots[QuerySlot.AMOUNT_CONDITION.value] = (
                self.amount_condition.as_dict()["literal"]
            )
        if self.topic is not None:
            slots[QuerySlot.TOPIC.value] = self.topic
        return slots

    def as_dict(self) -> dict[str, Any]:
        return {
            "queryType": self.query_type.value,
            "countUnit": self.count_unit.value,
            "direction": self.direction,
            "date": None if self.date is None else self.date.as_dict(),
            "period": None if self.period is None else self.period.as_dict(),
            "company": None if self.company is None else self.company.as_dict(),
            "themes": [theme.as_dict() for theme in self.themes],
            "catalystType": (
                None if self.catalyst_type is None else self.catalyst_type.as_dict()
            ),
            "amountCondition": (
                None
                if self.amount_condition is None
                else self.amount_condition.as_dict()
            ),
            "outcomeHorizon": self.outcome_horizon,
            "plannerVersion": self.planner_version,
            "contractVersion": self.contract_version,
            "contractHash": query_contract_content_hash(),
        }

    def cache_key(self, dataset_versions: Mapping[str, str]) -> str:
        """cache key는 질문 문자열이 아니라 해석된 plan과 데이터 버전이다(8.4절)."""

        return sha256_json(
            {
                "plan": self.as_dict(),
                "datasetVersions": dict(sorted(dataset_versions.items())),
            }
        )


@dataclass(frozen=True, slots=True)
class PlanFailure:
    """해석 실패. 원문을 담지 않는다."""

    reason: FailureReason
    message_ko: str
    query_type: QueryType | None = None
    candidates: tuple[CompanyCandidateRef, ...] = ()
    missing_slots: tuple[QuerySlot, ...] = ()

    @property
    def public_reason(self) -> PublicFailure:
        return PUBLIC_FAILURE_BY_REASON[self.reason]

    def as_dict(self) -> dict[str, Any]:
        return {
            "publicReason": self.public_reason.value,
            "publicLabelKo": PUBLIC_FAILURE_LABEL_KO[self.public_reason],
            "messageKo": self.message_ko,
            "queryType": None if self.query_type is None else self.query_type.value,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "missingSlots": [slot.value for slot in self.missing_slots],
        }


@dataclass(frozen=True, slots=True)
class PlanResult:
    plan: QueryPlan | None
    failure: PlanFailure | None

    def __post_init__(self) -> None:
        if (self.plan is None) == (self.failure is None):
            raise ValueError("plan과 failure 중 정확히 하나만 있어야 합니다.")

    @property
    def ok(self) -> bool:
        return self.plan is not None


# ---------------------------------------------------------------- catalog


@dataclass(frozen=True, slots=True)
class ThemeEntry:
    source_theme_id: str
    theme_name: str


@dataclass(frozen=True, slots=True)
class _NameMatch:
    kind: Literal["COMPANY", "THEME", "CATALYST"]
    text: str
    start: int
    end: int
    payload: Any


_PARTICLE_SUFFIXES = frozenset("은는이가을를와과의도에서로랑만")


def _has_boundary(text: str, start: int, end: int) -> bool:
    if start > 0 and (text[start - 1].isalnum() or text[start - 1] in "·"):
        return False
    if end >= len(text):
        return True
    following = text[end]
    if not following.isalnum():
        return True
    return following in _PARTICLE_SUFFIXES


# 질문 문형의 상용어. 테마명 조각이 이것과 겹치면 별칭으로 삼지 않는다 —
# "정책 소재에"의 소재가 "2차전지(소재/부품)"로 붙는 오인을 막는다.
_THEME_ALIAS_STOPWORDS = frozenset(
    "소재 테마 종목 관련주 대표주 산업 그룹 기타 장비 재료 생산".split()
)


def _theme_alias_parts(theme_name: str) -> tuple[str, ...]:
    """테마명에서 사용자가 칠 법한 조각을 뽑는다.

    "지능형로봇/인공지능(AI)" → 지능형로봇, 인공지능, AI.
    괄호 밖 본체, 본체의 /·, 조각, 괄호 안 조각(" 등" 제거)이다.
    """

    base = re.sub(r"\([^)]*\)", "", theme_name).strip()
    inner = re.findall(r"\(([^)]*)\)", theme_name)
    parts: set[str] = set()
    if base and base != theme_name:
        parts.add(base)
    for source in (base, *inner):
        for piece in re.split(r"[/·,]", source):
            piece = re.sub(r"\s*등\s*$", "", piece).strip()
            if (
                len(piece) >= 2
                and piece != theme_name
                and piece not in _THEME_ALIAS_STOPWORDS
            ):
                parts.add(piece)
    return tuple(sorted(parts))


class QuestionCatalog:
    """질문에서 알아볼 수 있는 이름 목록. 여기에 없는 이름은 지어내지 않는다."""

    def __init__(
        self,
        *,
        company_master: CompanyMaster,
        themes: Sequence[ThemeEntry] = (),
        catalyst_types: Sequence[CatalystTypeDefinition] = VOCABULARY,
    ) -> None:
        self.company_master = company_master
        self.themes = tuple(themes)
        self.catalyst_types = tuple(catalyst_types)
        self._theme_by_name = {theme.theme_name: theme for theme in self.themes}
        self._catalyst_by_name: dict[str, CatalystTypeDefinition] = {}
        for definition in self.catalyst_types:
            self._catalyst_by_name.setdefault(definition.name_ko, definition)
            # 실제 사용자는 "정책·제도"가 아니라 "정책"이라고 친다.
            for part in re.split(r"[·/]", definition.name_ko):
                if len(part) >= 2:
                    self._catalyst_by_name.setdefault(part, definition)
        company_names = {
            alias.alias
            for company in company_master.companies
            for alias in company.aliases
            if alias.alias
        } | {company.canonical_name for company in company_master.companies}
        # 테마 별칭 — "지능형로봇/인공지능(AI)"을 "지능형로봇"으로도 알아듣는다.
        # 조각이 정확히 한 테마만 가리킬 때만 별칭이 된다. 여러 테마에 걸치는
        # 조각("부품")이나 회사·소재 이름과 겹치는 조각은 지어내지 않고 버린다.
        reserved = set(self._theme_by_name) | set(self._catalyst_by_name) | company_names
        candidates: dict[str, set[str]] = {}
        for theme in self.themes:
            for part in _theme_alias_parts(theme.theme_name):
                candidates.setdefault(part, set()).add(theme.theme_name)
        self._theme_alias = {
            alias: names.pop()
            for alias, names in candidates.items()
            if len(names) == 1 and alias not in reserved
        }
        self._pattern = self._build_pattern(
            company_names,
            set(self._theme_by_name) | set(self._theme_alias),
            set(self._catalyst_by_name),
        )
        self._company_names = company_names

    @staticmethod
    def _build_pattern(
        company_names: set[str], theme_names: set[str], catalyst_names: set[str]
    ) -> re.Pattern[str] | None:
        names = sorted(
            company_names | theme_names | catalyst_names,
            key=lambda value: (-len(value), value),
        )
        if not names:
            return None
        return re.compile("|".join(re.escape(name) for name in names))

    def scan(self, text: str, *, masked: Sequence[tuple[int, int]] = ()) -> tuple[
        _NameMatch, ...
    ]:
        """겹치지 않는 최장 이름 매칭을 왼쪽부터 반환한다."""

        if self._pattern is None:
            return ()
        matches: list[_NameMatch] = []
        used: list[tuple[int, int]] = list(masked)
        for match in self._pattern.finditer(text):
            start, end = match.span()
            value = match.group(0)
            if not _has_boundary(text, start, end):
                continue
            if any(start < stop and begin < end for begin, stop in used):
                continue
            kind = self._kind(value)
            if kind is None:
                continue
            used.append((start, end))
            matches.append(
                _NameMatch(
                    kind=kind,
                    text=value,
                    start=start,
                    end=end,
                    payload=self._payload(kind, value),
                )
            )
        return tuple(sorted(matches, key=lambda item: item.start))

    def _kind(self, value: str) -> Literal["COMPANY", "THEME", "CATALYST"] | None:
        # 테마·소재 어휘가 회사명보다 앞선다. 회사명은 질문 안에서 더 자주
        # 부분 문자열로 걸리는데, 테마·소재는 통제된 목록이라 오탐이 적다.
        if value in self._theme_by_name or value in self._theme_alias:
            return "THEME"
        if value in self._catalyst_by_name:
            return "CATALYST"
        if value in self._company_names:
            return "COMPANY"
        return None

    def _payload(self, kind: str, value: str) -> Any:
        if kind == "THEME":
            found = self._theme_by_name.get(value)
            if found is not None:
                return found
            return self._theme_by_name[self._theme_alias[value]]
        if kind == "CATALYST":
            return self._catalyst_by_name[value]
        return value


# ---------------------------------------------------------------- 날짜 해석

_ISO_DATE_RE = re.compile(r"(?<![0-9])(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?![0-9])")
_KOREAN_DATE_RE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_KOREAN_MONTH_DAY_RE = re.compile(r"(?<![0-9])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_SHORT_DATE_RE = re.compile(r"(?<![0-9./-])(\d{1,2})[/.](\d{1,2})(?![0-9./-])")
_KOREAN_MONTH_RE = re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월")
_KOREAN_YEAR_RE = re.compile(r"(?<![0-9])(\d{4})\s*년")
_BARE_YEAR_RE = re.compile(r"(?<![0-9])(19[5-9]\d|20\d{2})(?![0-9])")
_RECENT_DAYS_RE = re.compile(r"최근\s*(\d{1,3})\s*일")
_RECENT_WEEKS_RE = re.compile(r"최근\s*(\d{1,2})\s*주")
_RECENT_MONTHS_RE = re.compile(r"최근\s*(\d{1,2})\s*(?:개월|달)")
_RECENT_YEARS_RE = re.compile(r"최근\s*(\d{1,2})\s*년")

_TODAY_MARKERS = ("오늘", "금일", "지금", "현재")
_YESTERDAY_MARKERS = ("어제", "어저께", "전일")
_DAY_BEFORE_MARKERS = ("그저께", "그제", "그저깨")
_WEEKDAY_EXPRESSIONS: tuple[tuple[str, RelativeExpression, int], ...] = (
    ("지난 월요일", RelativeExpression.LAST_MONDAY, 0),
    ("지난 화요일", RelativeExpression.LAST_TUESDAY, 1),
    ("지난 수요일", RelativeExpression.LAST_WEDNESDAY, 2),
    ("지난 목요일", RelativeExpression.LAST_THURSDAY, 3),
    ("지난 금요일", RelativeExpression.LAST_FRIDAY, 4),
)
_PREVIOUS_TRADING_MARKERS = ("전 거래일", "직전 거래일", "지난 거래일")

# "3거래일 뒤", "17일 후", "5일 수익률", "T+3" — 사건 뒤 며칠째 주가를 묻는지.
# 기간(최근 3개월)이나 날짜(8월 5일)와 겹치는 자리는 앞 단계가 이미 가려 둔다.
_HORIZON_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![0-9])(\d{1,3})\s*(?:거래일|영업일|일)\s*(?:뒤|후|이후|만에|째|차)"),
    re.compile(
        r"(?<![0-9])(\d{1,3})\s*(?:거래일|영업일|일)\s*(?:주가|수익률|등락률|성적|결과)"
    ),
    re.compile(r"(?<![0-9])[Tt]\s*\+\s*(\d{1,3})"),
)
# 가격 corpus가 한 사건 뒤로 세어 줄 수 있는 거래일 상한. 1년치보다 넉넉하다.
MAX_OUTCOME_HORIZON = 250


def _clamp_day(year: int, month: int, day: int) -> date | None:
    if not 1 <= month <= 12:
        return None
    last = monthrange(year, month)[1]
    if not 1 <= day <= last:
        return None
    return date(year, month, day)


def _month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _add_months(anchor: date, months: int) -> date:
    total = (anchor.year * 12 + anchor.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(anchor.day, monthrange(year, month + 1)[1]))


@dataclass(frozen=True, slots=True)
class _Literal:
    start: int
    end: int
    text: str


def _find_dates(text: str, today: date) -> tuple[list[tuple[DateSlot, _Literal]], list[tuple[int, int]]]:
    found: list[tuple[DateSlot, _Literal]] = []
    masked: list[tuple[int, int]] = []

    def _take(start: int, end: int, value: date, expression: RelativeExpression | None) -> None:
        if any(start < stop and begin < end for begin, stop in masked):
            return
        masked.append((start, end))
        found.append(
            (
                DateSlot(value=value, literal=text[start:end], expression=expression),
                _Literal(start, end, text[start:end]),
            )
        )

    for match in _KOREAN_DATE_RE.finditer(text):
        value = _clamp_day(*(int(group) for group in match.groups()))
        if value is not None:
            _take(match.start(), match.end(), value, None)
    for match in _ISO_DATE_RE.finditer(text):
        value = _clamp_day(*(int(group) for group in match.groups()))
        if value is not None:
            _take(match.start(), match.end(), value, None)
    for marker, expression, weekday in _WEEKDAY_EXPRESSIONS:
        position = text.find(marker)
        if position >= 0:
            delta = (today.weekday() - weekday) % 7 or 7
            _take(position, position + len(marker), today - timedelta(days=delta), expression)
    for marker in _PREVIOUS_TRADING_MARKERS:
        position = text.find(marker)
        if position >= 0:
            _take(
                position,
                position + len(marker),
                today - timedelta(days=1),
                RelativeExpression.PREVIOUS_TRADING_DAY,
            )
    for markers, expression, offset in (
        (_DAY_BEFORE_MARKERS, RelativeExpression.DAY_BEFORE_YESTERDAY, 2),
        (_YESTERDAY_MARKERS, RelativeExpression.YESTERDAY, 1),
        (_TODAY_MARKERS, RelativeExpression.TODAY, 0),
    ):
        for marker in markers:
            position = text.find(marker)
            if position >= 0:
                _take(
                    position,
                    position + len(marker),
                    today - timedelta(days=offset),
                    expression,
                )
    for match in _KOREAN_MONTH_DAY_RE.finditer(text):
        month, day = (int(group) for group in match.groups())
        value = _clamp_day(today.year, month, day)
        if value is None:
            continue
        if value > today:
            value = _clamp_day(today.year - 1, month, day) or value
        _take(match.start(), match.end(), value, None)
    for match in _SHORT_DATE_RE.finditer(text):
        month, day = (int(group) for group in match.groups())
        value = _clamp_day(today.year, month, day)
        if value is None:
            continue
        if value > today:
            value = _clamp_day(today.year - 1, month, day) or value
        _take(match.start(), match.end(), value, None)
    found.sort(key=lambda item: item[1].start)
    return found, masked


def _find_outcome_horizon(text: str, masked: Sequence[tuple[int, int]]) -> int | None:
    """사건일 기준 며칠 뒤 주가를 물었는지. 안 물었으면 None이다."""

    for pattern in _HORIZON_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.start(), match.end()
            if any(start < stop and begin < end for begin, stop in masked):
                continue
            value = int(match.group(1))
            if 1 <= value <= MAX_OUTCOME_HORIZON:
                return value
    return None


def _find_periods(
    text: str, today: date, masked: list[tuple[int, int]]
) -> list[tuple[PeriodSlot, _Literal]]:
    found: list[tuple[PeriodSlot, _Literal]] = []

    def _take(
        start: int,
        end: int,
        period_start: date,
        period_end: date,
        expression: RelativeExpression | None,
    ) -> None:
        if any(start < stop and begin < end for begin, stop in masked):
            return
        masked.append((start, end))
        found.append(
            (
                PeriodSlot(
                    start=period_start,
                    end=period_end,
                    literal=text[start:end],
                    expression=expression,
                ),
                _Literal(start, end, text[start:end]),
            )
        )

    for match in _KOREAN_MONTH_RE.finditer(text):
        year, month = (int(group) for group in match.groups())
        if 1 <= month <= 12:
            start, end = _month_range(year, month)
            _take(match.start(), match.end(), start, end, None)
    for pattern, unit in (
        (_RECENT_DAYS_RE, "day"),
        (_RECENT_WEEKS_RE, "week"),
        (_RECENT_MONTHS_RE, "month"),
        (_RECENT_YEARS_RE, "year"),
    ):
        for match in pattern.finditer(text):
            amount = int(match.group(1))
            if amount <= 0:
                continue
            if unit == "day":
                start = today - timedelta(days=amount - 1)
                expression = (
                    RelativeExpression.LAST_7_DAYS if amount == 7 else RelativeExpression.RECENT
                )
            elif unit == "week":
                start = today - timedelta(days=amount * 7 - 1)
                expression = (
                    RelativeExpression.LAST_7_DAYS if amount == 1 else RelativeExpression.RECENT
                )
            elif unit == "month":
                start = _add_months(today, -amount) + timedelta(days=1)
                expression = {
                    1: RelativeExpression.LAST_MONTH,
                    3: RelativeExpression.LAST_3_MONTHS,
                    12: RelativeExpression.LAST_12_MONTHS,
                }.get(amount, RelativeExpression.RECENT)
            else:
                start = _add_months(today, -amount * 12) + timedelta(days=1)
                expression = (
                    RelativeExpression.LAST_12_MONTHS
                    if amount == 1
                    else RelativeExpression.RECENT
                )
            _take(match.start(), match.end(), start, today, expression)

    relative_periods: tuple[tuple[str, RelativeExpression], ...] = (
        ("최근 일주일", RelativeExpression.LAST_7_DAYS),
        ("최근 한 달", RelativeExpression.LAST_MONTH),
        ("최근 한달", RelativeExpression.LAST_MONTH),
        ("이번 주", RelativeExpression.THIS_WEEK),
        ("이번주", RelativeExpression.THIS_WEEK),
        ("금주", RelativeExpression.THIS_WEEK),
        ("지난 주", RelativeExpression.LAST_WEEK),
        ("지난주", RelativeExpression.LAST_WEEK),
        ("저번 주", RelativeExpression.LAST_WEEK),
        ("이번 달", RelativeExpression.THIS_MONTH),
        ("이번달", RelativeExpression.THIS_MONTH),
        ("이달", RelativeExpression.THIS_MONTH),
        ("지난 달", RelativeExpression.LAST_MONTH),
        ("지난달", RelativeExpression.LAST_MONTH),
        ("올해", RelativeExpression.THIS_YEAR),
        ("금년", RelativeExpression.THIS_YEAR),
        ("작년", RelativeExpression.LAST_YEAR),
        ("지난해", RelativeExpression.LAST_YEAR),
        ("최근", RelativeExpression.RECENT),
        # "과거·역대·지금까지"는 최근 몇 달이 아니라 가진 기록 전부다.
        # 전에는 이 말이 무시되고 최근 3개월만 봐서 사건 대부분을 놓쳤다.
        ("과거", RelativeExpression.ALL_TIME),
        ("역대", RelativeExpression.ALL_TIME),
        ("지금까지", RelativeExpression.ALL_TIME),
        ("여태", RelativeExpression.ALL_TIME),
        ("이제까지", RelativeExpression.ALL_TIME),
    )
    for marker, expression in relative_periods:
        position = text.find(marker)
        if position < 0:
            continue
        start, end = _relative_period_bounds(expression, today)
        _take(position, position + len(marker), start, end, expression)

    for match in _KOREAN_YEAR_RE.finditer(text):
        year = int(match.group(1))
        _take(match.start(), match.end(), date(year, 1, 1), date(year, 12, 31), None)
    for match in _BARE_YEAR_RE.finditer(text):
        year = int(match.group(1))
        _take(match.start(), match.end(), date(year, 1, 1), date(year, 12, 31), None)
    found.sort(key=lambda item: item[1].start)
    return found


def _relative_period_bounds(
    expression: RelativeExpression, today: date
) -> tuple[date, date]:
    if expression is RelativeExpression.ALL_TIME:
        return COLLECTION_FROM, today
    if expression is RelativeExpression.THIS_WEEK:
        start = today - timedelta(days=today.weekday())
        return start, today
    if expression is RelativeExpression.LAST_WEEK:
        this_monday = today - timedelta(days=today.weekday())
        return this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    if expression is RelativeExpression.THIS_MONTH:
        return date(today.year, today.month, 1), today
    if expression is RelativeExpression.LAST_MONTH:
        anchor = date(today.year, today.month, 1) - timedelta(days=1)
        return _month_range(anchor.year, anchor.month)
    if expression is RelativeExpression.THIS_YEAR:
        return date(today.year, 1, 1), today
    if expression is RelativeExpression.LAST_YEAR:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if expression is RelativeExpression.LAST_7_DAYS:
        return today - timedelta(days=6), today
    if expression is RelativeExpression.LAST_3_MONTHS:
        return _add_months(today, -3) + timedelta(days=1), today
    if expression is RelativeExpression.LAST_12_MONTHS:
        return _add_months(today, -12) + timedelta(days=1), today
    # RECENT: 기간을 지정하지 않은 "최근"은 90일로 고정한다. 답변 블록이 이
    # 해석을 그대로 표시하므로 사용자가 다르게 물어볼 수 있다.
    return today - timedelta(days=89), today


# ---------------------------------------------------------------- 금액 조건

_AMOUNT_UNITS: tuple[tuple[str, Decimal], ...] = (
    ("조원", Decimal("1000000000000")),
    ("조", Decimal("1000000000000")),
    ("억원", Decimal("100000000")),
    ("억", Decimal("100000000")),
    ("만원", Decimal("10000")),
    ("천만원", Decimal("10000000")),
    ("백만원", Decimal("1000000")),
    ("원", Decimal("1")),
)
_AMOUNT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(조원|조|억원|억|천만원|백만원|만원|원)"
    r"\s*(이상|초과|넘는|넘은|넘게|이하|미만)?"
)
_COMPARATOR_BY_MARKER: Mapping[str, Literal["GTE", "GT", "LTE", "LT"]] = {
    "이상": "GTE",
    "초과": "GT",
    "넘는": "GT",
    "넘은": "GT",
    "넘게": "GT",
    "이하": "LTE",
    "미만": "LT",
}


def _find_amount(text: str) -> tuple[AmountCondition | None, list[tuple[int, int]]]:
    masked: list[tuple[int, int]] = []
    condition: AmountCondition | None = None
    for match in _AMOUNT_RE.finditer(text):
        masked.append(match.span())
        if condition is not None:
            continue
        raw, unit, marker = match.groups()
        multiplier = next(value for name, value in _AMOUNT_UNITS if name == unit)
        amount = Decimal(raw.replace(",", "")) * multiplier
        condition = AmountCondition(
            comparator=_COMPARATOR_BY_MARKER.get(marker or "", "GTE"),
            normalized_value=amount,
            currency="KRW",
            literal=match.group(0).strip(),
        )
    return condition, masked


# ---------------------------------------------------------------- 방향·분류

# `올랏노`처럼 받침을 ㅅ으로 적는 사투리·오타 표기가 실제 질문에 들어온다.
# 이걸 못 잡으면 방향이 없는 질문으로 답해 반대 방향까지 섞어 보여준다.
_UP_MARKERS = (
    "올랐", "올랏", "올라", "오른", "오르", "오름", "상승", "강세", "급등",
    "뛴", "뛰었", "뛰엇", "셌", "셋노", "강했", "강햇", "상한가", "폭등", "반등",
)
_DOWN_MARKERS = (
    "빠졌", "빠젓", "빠졋", "빠져", "빠진", "빠짐", "떨어", "하락", "약세",
    "급락", "내린", "내려", "내렸", "내렷", "내림", "약했", "약햇", "하한가",
    "폭락",
)
# `빠짐없이`는 방향이 아니라 "전부"라는 뜻이다. 표식으로 세면 하락으로 뒤집힌다.
_DIRECTION_NOISE = ("빠짐없",)

# 매매·미래 판단 요청. `공매도 잔고`는 매매 요청이 아니므로 `매도`·`매수`는
# 앞에 `공`이 붙지 않을 때만 센다.
_OUT_OF_SCOPE_RE = re.compile(
    "|".join(
        (
            "사야", "살까", r"(?<!공)매수", r"(?<!공)매도", "손절", "익절",
            "목표가", "적정주가", "추천", "전망", "오를까", "내릴까",
            "떨어질까", "내일", "다음 주 오", "사도 돼", "팔까", "들어가도",
            "얼마까지", "유망", "수익 날",
        )
    )
)

_OUTCOME_MARKERS = (
    "이후 흐름", "이후 반응", "이후 주가", "이후에 주가", "뒤 흐름", "뒤에 주가",
    "발표 뒤", "발표 후", "수익률", "그 뒤 어떻게", "어떻게 됐", "뒤 어떻게",
)
_VALUE_MARKERS = ("수주액", "계약 금액", "금액 합계", "합계", "총액")
_DIRECT_EVENT_MARKERS = ("직접", "본인 사건", "주체인 사건", "자체 사건", "스스로")
_CONTINUATION_MARKERS = ("처음이야", "처음 나온", "처음인", "재부각", "반복된", "다시 나온")
_CERTAINTY_MARKERS = ("기대감", "확정", "기대야", "기대 건")
_FREQUENCY_MARKERS = ("몇 번", "몇 건", "건수", "빈도")
_REACTION_MARKERS = ("반응", "움직였", "움직인 테마")
_THEME_FREQUENCY_MARKERS = (
    "자주 나온", "많이 등장", "빈도 높은", "자주 부각", "자주 등장", "많이 나온",
)
_THEME_HISTORY_MARKERS = ("과거", "옛날", "예전", "그동안")
_THEME_MEMBER_MARKERS = (
    "어떤 종목", "관련주", "구성 종목", "구성종목", "뭐가 들어", "들어가 있",
    "종목 알려", "편입 이유", "무슨 종목",
)
_COOCCURRENCE_MARKERS = (
    "같이 움직", "같이 오른", "같이 올라", "같이 상승", "동반", "붙어 다니",
    "함께 오른", "같이 빠진", "같이 하락",
)
_TOP_MOVE_MARKERS = (
    "오른 날", "올라간 날", "뛴 날", "내린 날", "빠진 날", "떨어진 날",
    "급등일", "급락일", "상승 폭", "하락 폭", "크게 움직", "많이 움직",
    "움직인 날", "센 날",
)
_REASON_MARKERS = ("왜", "이유", "사유", "까닭", "때문")
_MOVERS_MARKERS = (
    "테마", "시장", "특징테마", "어땠", "뭐가", "뭐 ", "상한가", "요약",
    "정리", "순위", "강세", "약세",
)
_COMPARISON_MARKERS = ("중에", "비교", "vs", "VS", "어디가", "쪽", "중 ")


def _direction(text: str) -> QueryDirection | None:
    # 아래에서 표식의 위치로 앞뒤를 가리므로 글자 수를 유지한 채 지운다.
    for noise in _DIRECTION_NOISE:
        text = text.replace(noise, " " * len(noise))
    up = min((text.find(marker) for marker in _UP_MARKERS if marker in text), default=-1)
    down = min(
        (text.find(marker) for marker in _DOWN_MARKERS if marker in text), default=-1
    )
    if up < 0 and down < 0:
        return None
    if up < 0:
        return "DOWN"
    if down < 0:
        return "UP"
    # 둘 다 있으면 뒤에 나온 표현이 질문의 방향이다("올랐어 빠졌어" 형태).
    return "DOWN" if down > up else "UP"


def _any(text: str, markers: Sequence[str]) -> bool:
    return any(marker in text for marker in markers)


# 주제어가 아니라 소재 표현의 일부인 낱말들. 주제어로 지어내지 않는다.
_TOPIC_STOPWORDS = frozenset(
    (
        "과거 관련 산업 육성 지원 분야 국내 정부 올해 작년 최근 이번 무슨 어떤"
        " 그때 당시 테마 테마에 소재 소재에 종목 발표 소식 이후"
    ).split()
)
_TOPIC_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")


def _topic_before(text: str, start: int, taken: AbstractSet[str]) -> str | None:
    """소재 낱말 바로 앞의 주제어 하나를 뽑는다("로봇 산업 육성 정책"의 로봇).

    이미 회사·테마·소재로 해석된 낱말과 상투어는 빼고, 남는 낱말 중 소재에
    가장 가까운 것 하나만 쓴다. 없으면 좁히지 않는다 — 지어내지 않는다.
    """

    segment = text[max(0, start - 20) : start]
    candidates = [
        token
        for token in _TOPIC_TOKEN_RE.findall(segment)
        if len(token) >= 2 and token not in _TOPIC_STOPWORDS and token not in taken
    ]
    return candidates[-1] if candidates else None


def _classify(
    text: str,
    *,
    has_company: bool,
    theme_count: int,
    has_catalyst: bool,
    has_date: bool,
    has_period: bool,
    has_amount: bool,
    direction: QueryDirection | None,
) -> QueryType | None:
    """17종 중 하나로 분류한다. 어디에도 걸리지 않으면 None이다."""

    has_theme = theme_count >= 1
    # 회사 없이 소재·테마로 물으면 사건 당시 주도주의 실제 결과 축이다.
    if (has_company or has_catalyst or has_theme) and _any(text, _OUTCOME_MARKERS):
        return QueryType.COMPANY_HISTORICAL_OUTCOME
    if has_company and (has_amount or _any(text, _VALUE_MARKERS)):
        return QueryType.COMPANY_VALUE_SUMMARY
    if has_company and _any(text, _DIRECT_EVENT_MARKERS):
        return QueryType.COMPANY_DIRECT_EVENT
    if has_catalyst and _any(text, _CONTINUATION_MARKERS):
        return QueryType.CATALYST_CONTINUATION
    if has_catalyst and _any(text, _CERTAINTY_MARKERS):
        return QueryType.CATALYST_CERTAINTY
    if has_catalyst and _any(text, _FREQUENCY_MARKERS):
        return QueryType.CATALYST_FREQUENCY
    if has_catalyst and _any(text, _REACTION_MARKERS):
        return QueryType.CATALYST_THEME_REACTION
    if "테마" in text and _any(text, _THEME_FREQUENCY_MARKERS):
        return QueryType.THEME_FREQUENCY
    if theme_count >= 2 and _any(text, _COMPARISON_MARKERS):
        return QueryType.THEME_COMPARISON
    if has_theme and _any(text, _THEME_MEMBER_MARKERS):
        return QueryType.THEME_MEMBERS
    if has_theme and (
        _any(text, _THEME_HISTORY_MARKERS)
        or (has_period and _any(text, _REASON_MARKERS))
        or "뭘로 움직" in text
    ):
        return QueryType.THEME_HISTORY
    if has_company and _any(text, _COOCCURRENCE_MARKERS):
        return QueryType.STOCK_COOCCURRENCE
    if has_company and "테마" in text:
        return QueryType.STOCK_THEME_MEMBERSHIP
    if has_company and _any(text, _TOP_MOVE_MARKERS):
        return QueryType.STOCK_TOP_MOVES
    if has_company and has_date and _any(text, _REASON_MARKERS):
        return QueryType.STOCK_DAY_REASON
    # "6/11 상승"처럼 날짜와 방향만 있는 짧은 문장도 시장 조회 질문이다.
    if has_period and (_any(text, _MOVERS_MARKERS) or direction is not None):
        return QueryType.PERIOD_SUMMARY
    if has_date and (_any(text, _MOVERS_MARKERS) or direction is not None):
        return QueryType.DAY_MOVERS
    return None


# ---------------------------------------------------------------- 진입점


def normalize_question(text: str) -> str:
    """비교와 매칭 전 표준화. 원문은 저장하지 않으므로 이 값도 보관하지 않는다."""

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def plan_question(
    question: str,
    *,
    catalog: QuestionCatalog,
    today: date,
) -> PlanResult:
    """자연어 한 문장을 QueryPlan 하나로 옮긴다."""

    text = normalize_question(question)
    if not text:
        return PlanResult(
            None,
            PlanFailure(
                FailureReason.NOT_INTERPRETABLE,
                "질문이 비어 있습니다.",
            ),
        )
    if _OUT_OF_SCOPE_RE.search(text) is not None:
        return PlanResult(
            None,
            PlanFailure(
                FailureReason.OUT_OF_SCOPE,
                "매수·매도 판단과 미래 예측은 답하지 않습니다.",
            ),
        )

    amount, amount_spans = _find_amount(text)
    dates, masked = _find_dates(text, today)
    masked.extend(amount_spans)
    periods = _find_periods(text, today, masked)
    outcome_horizon = _find_outcome_horizon(text, masked)

    # 명시적 날짜가 둘 이상이면 기간 질문이다("A부터 B까지", "A~B", "A B 사이").
    if len(dates) >= 2 and not periods:
        first, last = dates[0][0], dates[-1][0]
        periods = [
            (
                PeriodSlot(
                    start=min(first.value, last.value),
                    end=max(first.value, last.value),
                    literal=f"{dates[0][1].text}~{dates[-1][1].text}",
                ),
                _Literal(dates[0][1].start, dates[-1][1].end, ""),
            )
        ]
        dates = []

    names = catalog.scan(text, masked=masked)
    company_matches = [item for item in names if item.kind == "COMPANY"]
    theme_matches = [item for item in names if item.kind == "THEME"]
    catalyst_matches = [item for item in names if item.kind == "CATALYST"]
    stock_codes = [
        match.group(0)
        for match in re.finditer(r"(?<![0-9A-Za-z])[0-9]{6}(?![0-9A-Za-z])", text)
    ]

    date_slot = dates[0][0] if dates else None
    period_slot = periods[0][0] if periods else None
    as_of = date_slot.value if date_slot is not None else (
        period_slot.end if period_slot is not None else None
    )

    direction = _direction(text)
    query_type = _classify(
        text,
        has_company=bool(company_matches or stock_codes),
        theme_count=len(theme_matches),
        has_catalyst=bool(catalyst_matches),
        has_date=date_slot is not None,
        has_period=period_slot is not None,
        has_amount=amount is not None,
        direction=direction,
    )
    if query_type is None:
        return PlanResult(
            None,
            PlanFailure(
                FailureReason.NOT_INTERPRETABLE,
                "지원하는 17종 질문 중 어디에도 맞지 않습니다.",
            ),
        )

    company: CompanyRef | None = None
    if company_matches or stock_codes:
        resolved = _resolve_company_slot(
            catalog,
            stock_codes=stock_codes,
            matches=company_matches,
            as_of=as_of,
        )
        if isinstance(resolved, PlanFailure):
            return PlanResult(None, PlanFailure(
                resolved.reason,
                resolved.message_ko,
                query_type=query_type,
                candidates=resolved.candidates,
            ))
        company = resolved

    themes = tuple(
        ThemeRef(
            source_theme_id=item.payload.source_theme_id,
            theme_name=item.payload.theme_name,
            matched_text=item.text,
        )
        for item in theme_matches
    )
    catalyst = (
        CatalystTypeRef(
            type_id=catalyst_matches[0].payload.type_id,
            name_ko=catalyst_matches[0].payload.name_ko,
            matched_text=catalyst_matches[0].text,
        )
        if catalyst_matches
        else None
    )

    topic: str | None = None
    if catalyst_matches:
        matched_texts = frozenset(
            item.text
            for group in (company_matches, theme_matches, catalyst_matches)
            for item in group
        )
        topic = _topic_before(text, catalyst_matches[0].start, matched_texts)
        # "로봇 산업 육성 정책"의 로봇이 테마 별칭으로 먼저 잡히면 주제어가
        # 비어 소재 필터가 통째로 풀린다(2026-08-20 운영 실측: 무관 사건
        # 29건 통과). 소재 낱말 바로 앞의 테마 글자는 주제어로도 쓴다.
        if topic is None:
            catalyst_start = catalyst_matches[0].start
            for item in theme_matches:
                if item.end <= catalyst_start and catalyst_start - item.end <= 20:
                    topic = item.text
                    break

    plan_kwargs: dict[str, Any] = {
        "query_type": query_type,
        "count_unit": QUERY_CONTRACT_BY_TYPE[query_type].count_unit,
        "direction": direction,
        "company": company,
        "themes": themes,
        "catalyst_type": catalyst,
        "amount_condition": amount,
        "topic": topic,
        "outcome_horizon": outcome_horizon,
    }
    if query_type is QueryType.STOCK_DAY_REASON or query_type is QueryType.DAY_MOVERS:
        plan_kwargs["date"] = date_slot
        plan_kwargs["period"] = None
    else:
        plan_kwargs["date"] = date_slot
        plan_kwargs["period"] = period_slot

    # 기간이 필수인데 문장이 말하지 않았으면 "최근"으로 채우지 않고 기본
    # 기간을 명시한다. 답변 블록이 이 해석을 그대로 보여준다.
    if plan_kwargs["period"] is None and _requires_period(query_type):
        start, end = _relative_period_bounds(RelativeExpression.RECENT, today)
        plan_kwargs["period"] = PeriodSlot(
            start=start, end=end, literal="최근", expression=RelativeExpression.RECENT
        )
    if query_type is QueryType.COMPANY_VALUE_SUMMARY and amount is None:
        plan_kwargs["amount_condition"] = AmountCondition(
            comparator="GTE",
            normalized_value=Decimal(0),
            currency="KRW",
            literal="금액 조건 없음",
        )

    try:
        plan = QueryPlan(**plan_kwargs)
    except ValueError:
        return PlanResult(
            None,
            PlanFailure(
                FailureReason.MISSING_SLOT,
                "질문에서 필요한 조건을 찾지 못했습니다.",
                query_type=query_type,
                missing_slots=_missing_slots(query_type, plan_kwargs),
            ),
        )
    return PlanResult(plan, None)


def _requires_period(query_type: QueryType) -> bool:
    contract = QUERY_CONTRACT_BY_TYPE[query_type]
    return any(
        QuerySlot.PERIOD in alternative or QuerySlot.DATE_RANGE in alternative
        for alternative in contract.required_alternatives
    )


def _missing_slots(query_type: QueryType, plan_kwargs: Mapping[str, Any]) -> tuple[QuerySlot, ...]:
    contract = QUERY_CONTRACT_BY_TYPE[query_type]
    supplied: set[QuerySlot] = set()
    if plan_kwargs.get("date") is not None:
        supplied.add(QuerySlot.DATE)
    if plan_kwargs.get("period") is not None:
        supplied.add(QuerySlot.PERIOD)
        supplied.add(QuerySlot.DATE_RANGE)
    if plan_kwargs.get("company") is not None:
        supplied.add(QuerySlot.COMPANY)
        supplied.add(QuerySlot.STOCK)
    themes = plan_kwargs.get("themes") or ()
    if themes:
        supplied.add(QuerySlot.THEME)
    if len(themes) >= 2:
        supplied.add(QuerySlot.THEMES)
    if plan_kwargs.get("catalyst_type") is not None:
        supplied.add(QuerySlot.CATALYST_TYPE)
        supplied.add(QuerySlot.EVENT)
    if plan_kwargs.get("amount_condition") is not None:
        supplied.add(QuerySlot.AMOUNT_CONDITION)
    best = min(
        contract.required_alternatives,
        key=lambda alternative: len(set(alternative) - supplied),
    )
    return tuple(sorted(set(best) - supplied, key=lambda slot: slot.value))


def _resolve_company_slot(
    catalog: QuestionCatalog,
    *,
    stock_codes: Sequence[str],
    matches: Sequence[_NameMatch],
    as_of: date | None,
) -> CompanyRef | PlanFailure:
    """8.1절 순서를 그대로 따른다. 복수 후보를 임의로 고르지 않는다."""

    for code in stock_codes:
        resolution = resolve_company(catalog.company_master, code)
        if resolution.status == "RESOLVED":
            candidate = resolution.candidates[0]
            return CompanyRef(
                seed_stock_code=candidate.seed_stock_code,
                canonical_name=candidate.canonical_name,
                matched_text=code,
                basis="STOCK_CODE",
            )
    for match in matches:
        resolution = resolve_company(catalog.company_master, match.text, as_of=as_of)
        if resolution.status == "RESOLVED":
            candidate = resolution.candidates[0]
            return CompanyRef(
                seed_stock_code=candidate.seed_stock_code,
                canonical_name=candidate.canonical_name,
                matched_text=match.text,
                basis=(
                    "CURRENT_NAME"
                    if candidate.canonical_name == match.text
                    else "PAST_ALIAS"
                ),
            )
        if resolution.status in {"AMBIGUOUS", "OUT_OF_VALIDITY"}:
            return PlanFailure(
                (
                    FailureReason.AMBIGUOUS_ALIAS
                    if resolution.status == "AMBIGUOUS"
                    else FailureReason.UNKNOWN_COMPANY
                ),
                (
                    "같은 이름의 회사가 여럿입니다. 종목코드나 정확한 사명을 알려주세요."
                    if resolution.status == "AMBIGUOUS"
                    else "그 시점에 유효하지 않은 사명입니다. 종목코드로 다시 물어봐 주세요."
                ),
                candidates=tuple(
                    CompanyCandidateRef(
                        seed_stock_code=candidate.seed_stock_code,
                        canonical_name=candidate.canonical_name,
                        matched_text=candidate.matched_alias,
                        valid_from=candidate.valid_from,
                        valid_to=candidate.valid_to,
                    )
                    for candidate in resolution.candidates
                ),
            )
    return PlanFailure(
        FailureReason.UNKNOWN_COMPANY,
        "회사를 찾지 못했습니다. 종목코드나 정확한 사명을 알려주세요.",
    )
