"""QueryPlan → 근거 있는 답변 블록 (E-22 단계 5·6).

수치는 전부 저장된 값을 세거나 더한 것이다. 이 모듈은 확률·순위·수익률을
만들지 않고, 근거가 없는 행은 답에 넣지 않는다. 같은 plan과 같은 저장소
버전은 같은 정렬·수치·근거 목록을 만든다(계약서 8.4절).

답변 블록은 FR-11 구조 그대로다 — 해석된 슬롯, 한 문장 요약, 집계 단위가
붙은 수치, 근거 사건, 미제시 사유, 데이터 버전.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Mapping, Protocol, Sequence

from .query_contracts import (
    QUERY_CONTRACT_BY_TYPE,
    CountUnit,
    QueryPrerequisite,
    QueryType,
)
from .query_planning import (
    FailureReason,
    PlanFailure,
    QueryPlan,
)

ANSWER_CONTRACT_VERSION = "answer-contract/1.0.0"

# E-16 가격 corpus가 실제로 덮는 첫 거래일. 그 이전 사건의 실제 결과는
# 0으로 채우지 않고 범위 밖으로 답한다(마스터 플랜 9절).
OUTCOME_RANGE_FROM = date(2010, 1, 1)
DEFAULT_OUTCOME_HORIZONS: tuple[int, ...] = (1, 5, 20)

COUNT_UNIT_LABEL_KO: Mapping[CountUnit, str] = {
    CountUnit.DAILY_SECTION: "Daily 섹션",
    CountUnit.DAILY_STOCK_ROW: "Daily 종목 행",
    CountUnit.CURRENT_THEME_MEMBERSHIP: "현재 테마 구성",
    CountUnit.SOURCE_RECORD: "원천 기록",
    CountUnit.THEME_REACTION: "테마 반응",
    CountUnit.CATALYST: "고유 사건",
    CountUnit.VALUE_FACT: "금액 fact",
    CountUnit.OUTCOME_OBSERVATION: "outcome 관측",
}


# ---------------------------------------------------------------- 저장소 행


@dataclass(frozen=True, slots=True)
class DailyStock:
    stock_name: str
    stock_code: str | None
    close_price: int | None
    change_rate: Decimal | None


@dataclass(frozen=True, slots=True)
class DailyTheme:
    theme_name: str
    change_rate: Decimal | None
    stocks: tuple[DailyStock, ...]


@dataclass(frozen=True, slots=True)
class DailySection:
    section_name: str
    headline: str
    details: tuple[str, ...]
    themes: tuple[DailyTheme, ...]

    @property
    def direction(self) -> Literal["UP", "DOWN", "MIXED", "UNKNOWN"]:
        rates = [
            theme.change_rate for theme in self.themes if theme.change_rate is not None
        ]
        if not rates:
            return "UNKNOWN"
        if all(rate < 0 for rate in rates):
            return "DOWN"
        if all(rate >= 0 for rate in rates):
            return "UP"
        return "MIXED"


@dataclass(frozen=True, slots=True)
class DailyDay:
    """하루치 특징테마. trading_date는 발행일이 아니라 거래일이다."""

    trading_date: date
    published_date: date | None
    status: Literal["PUBLISHED", "NOT_PUBLISHED", "NO_RECORD"]
    sections: tuple[DailySection, ...]
    unsplit_post_count: int = 0


@dataclass(frozen=True, slots=True)
class DailyStockRow:
    trading_date: date
    theme_name: str
    section_headline: str
    stock_name: str
    stock_code: str | None
    close_price: int | None
    change_rate: Decimal | None


@dataclass(frozen=True, slots=True)
class ThemeDailyChange:
    trading_date: date
    theme_name: str
    change_rate: Decimal | None
    section_headline: str


@dataclass(frozen=True, slots=True)
class ThemeMembership:
    source_theme_id: str
    theme_name: str
    stock_code: str | None
    stock_name: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class ThemeHistoryRecord:
    source_theme_id: str
    theme_name: str
    source_history_key: str
    event_date: date | None
    raw_text: str
    primary_catalyst_type: str | None
    primary_catalyst_name_ko: str | None
    catalyst_types: tuple[str, ...]
    direction: str
    certainty: str
    continuation: bool


@dataclass(frozen=True, slots=True)
class CatalystCompanyRoleRow:
    seed_stock_code: str
    company_name: str
    role: str
    impact: str


@dataclass(frozen=True, slots=True)
class CatalystSummary:
    """중복이 제거된 현실 사건 하나. source_record_count가 원천 기록 수다."""

    catalyst_id: str
    occurred_on: date | None
    primary_catalyst_type: str | None
    primary_catalyst_name_ko: str | None
    catalyst_types: tuple[str, ...]
    event_stage: str
    certainty: str
    novelty_type: str
    action: str | None
    object_text: str | None
    project_id: str | None
    geography_codes: tuple[str, ...]
    theme_names: tuple[str, ...]
    company_roles: tuple[CatalystCompanyRoleRow, ...]
    source_record_count: int
    theme_reaction_count: int
    evidence_text: str
    evidence_start: int
    evidence_end: int
    review_state: str = "AI_DRAFT"


@dataclass(frozen=True, slots=True)
class ValueFact:
    catalyst_id: str
    occurred_on: date | None
    fact_type: str
    reported_value: str
    normalized_value: Decimal
    unit: str
    currency: str | None
    value_basis: str
    eligible_for_sum: bool
    theme_name: str
    evidence_text: str


@dataclass(frozen=True, slots=True)
class OutcomeObservation:
    """사건 이후 실제 주가. 없는 값은 0이 아니라 None이다."""

    catalyst_id: str
    occurred_on: date
    seed_stock_code: str
    company_name: str
    base_trading_date: date | None
    base_close: Decimal | None
    returns: Mapping[int, Decimal | None]
    missing_reason: str | None
    evidence_text: str


@dataclass(frozen=True, slots=True)
class CatalystFilter:
    date_from: date | None = None
    date_to: date | None = None
    catalyst_type: str | None = None
    seed_stock_code: str | None = None
    roles: tuple[str, ...] = ()
    source_theme_id: str | None = None
    limit: int | None = None
    # 사건 원문에 이 낱말이 있는 것만("로봇 정책"의 로봇). 대소문자 무시.
    topic_text: str | None = None


class ResearchRepository(Protocol):
    """결정론적 읽기 표면. 여기에 쓰기는 없다."""

    def versions(self) -> Mapping[str, str]: ...

    def ready_prerequisites(self) -> frozenset[QueryPrerequisite]: ...

    def daily_day(self, trading_date: date) -> DailyDay: ...

    def daily_days(self, start: date, end: date) -> tuple[DailyDay, ...]: ...

    def stock_daily_rows(
        self, seed_stock_code: str, start: date, end: date
    ) -> tuple[DailyStockRow, ...]: ...

    def theme_daily_changes(
        self, theme_names: Sequence[str], start: date, end: date
    ) -> tuple[ThemeDailyChange, ...]: ...

    def theme_members(self, source_theme_id: str) -> tuple[ThemeMembership, ...]: ...

    def stock_theme_memberships(
        self, seed_stock_code: str
    ) -> tuple[ThemeMembership, ...]: ...

    def theme_history(
        self,
        source_theme_id: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[ThemeHistoryRecord, ...]: ...

    def catalysts(self, catalyst_filter: CatalystFilter) -> tuple[CatalystSummary, ...]: ...

    def value_facts(self, catalyst_filter: CatalystFilter) -> tuple[ValueFact, ...]: ...

    def outcomes(
        self, catalyst_filter: CatalystFilter, *, horizons: Sequence[int]
    ) -> tuple[OutcomeObservation, ...]: ...

    def leader_outcomes(
        self, catalyst_filter: CatalystFilter, *, horizons: Sequence[int]
    ) -> tuple[OutcomeObservation, ...]: ...


# ---------------------------------------------------------------- 답변 블록


@dataclass(frozen=True, slots=True)
class AnswerEvidence:
    """근거 하나. 원문 전체가 아니라 인용 구간만 담는다."""

    source_kind: str
    label_ko: str
    occurred_on: date | None
    excerpt: str
    start: int | None = None
    end: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sourceKind": self.source_kind,
            "labelKo": self.label_ko,
            "occurredOn": None if self.occurred_on is None else self.occurred_on.isoformat(),
            "excerpt": self.excerpt,
            "start": self.start,
            "end": self.end,
        }


@dataclass(frozen=True, slots=True)
class AnswerRow:
    """답변 표의 한 행. 근거 없는 행은 만들지 않는다."""

    label: str
    values: Mapping[str, Any]
    evidence: tuple[AnswerEvidence, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "values": dict(self.values),
            "evidence": [item.as_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class AnswerMetric:
    label_ko: str
    value: str
    count_unit: CountUnit | None = None
    sample_size: int | None = None
    note_ko: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "labelKo": self.label_ko,
            "value": self.value,
            "countUnit": None if self.count_unit is None else self.count_unit.value,
            "countUnitLabelKo": (
                None if self.count_unit is None else COUNT_UNIT_LABEL_KO[self.count_unit]
            ),
            "sampleSize": self.sample_size,
            "noteKo": self.note_ko,
        }


@dataclass(frozen=True, slots=True)
class AnswerExclusion:
    """미제시 사유. 왜 답에서 뺐는지를 숨기지 않는다."""

    code: str
    label_ko: str
    count: int

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "labelKo": self.label_ko, "count": self.count}


@dataclass(frozen=True, slots=True)
class AnswerBlock:
    query_type: QueryType
    count_unit: CountUnit
    interpretation: Mapping[str, Any]
    summary_ko: str
    metrics: tuple[AnswerMetric, ...]
    rows: tuple[AnswerRow, ...]
    exclusions: tuple[AnswerExclusion, ...]
    versions: Mapping[str, str]
    sample_size: int
    human_verified: bool
    notes_ko: tuple[str, ...] = ()
    contract_version: str = ANSWER_CONTRACT_VERSION

    @property
    def evidence_coverage(self) -> float:
        if not self.rows:
            return 1.0
        covered = sum(1 for row in self.rows if row.evidence)
        return covered / len(self.rows)

    def as_dict(self) -> dict[str, Any]:
        return {
            "queryType": self.query_type.value,
            "countUnit": self.count_unit.value,
            "countUnitLabelKo": COUNT_UNIT_LABEL_KO[self.count_unit],
            "interpretation": dict(self.interpretation),
            "summaryKo": self.summary_ko,
            "metrics": [metric.as_dict() for metric in self.metrics],
            "rows": [row.as_dict() for row in self.rows],
            "exclusions": [item.as_dict() for item in self.exclusions],
            "sampleSize": self.sample_size,
            "evidenceCoverage": self.evidence_coverage,
            "humanVerified": self.human_verified,
            "notesKo": list(self.notes_ko),
            "versions": dict(sorted(self.versions.items())),
            "contractVersion": self.contract_version,
        }


@dataclass(frozen=True, slots=True)
class AnswerResult:
    answer: AnswerBlock | None
    failure: PlanFailure | None

    def __post_init__(self) -> None:
        if (self.answer is None) == (self.failure is None):
            raise ValueError("answer와 failure 중 정확히 하나만 있어야 합니다.")

    @property
    def ok(self) -> bool:
        return self.answer is not None

    def as_dict(self) -> dict[str, Any]:
        if self.answer is not None:
            return {"status": "ANSWERED", "answer": self.answer.as_dict()}
        assert self.failure is not None
        return {"status": "FAILED", "failure": self.failure.as_dict()}


@dataclass(frozen=True, slots=True)
class QueryAvailability:
    """어떤 유형을 열어 둘지. 사람 검수 비율이 오르면 잠긴 유형이 열린다."""

    human_verified: frozenset[QueryType] = frozenset()
    outcome_gate_open: bool = False
    similarity_gate_open: bool = False
    outcome_range_from: date = OUTCOME_RANGE_FROM
    serve_unverified: bool = False

    def is_open(self, query_type: QueryType) -> bool:
        return self.serve_unverified or query_type in self.human_verified


# ---------------------------------------------------------------- 진입점


def answer_plan(
    plan: QueryPlan,
    repository: ResearchRepository,
    *,
    availability: QueryAvailability,
    today: date,
    limit: int = 20,
) -> AnswerResult:
    """QueryPlan 하나를 결정론적으로 계산해 답변 블록으로 만든다."""

    contract = QUERY_CONTRACT_BY_TYPE[plan.query_type]
    ready = repository.ready_prerequisites()
    missing = tuple(
        item
        for item in contract.prerequisites
        if item is not QueryPrerequisite.NONE and item not in ready
    )
    if missing:
        return _fail(
            FailureReason.PREREQUISITE_NOT_READY,
            "이 질문이 쓰는 데이터가 아직 준비되지 않았습니다: "
            + ", ".join(item.value for item in missing),
            plan,
        )
    if plan.query_type is QueryType.COMPANY_HISTORICAL_OUTCOME and not (
        availability.outcome_gate_open
    ):
        return _fail(
            FailureReason.OUTCOME_GATE_CLOSED,
            "사건 이후 실제 주가는 가격 자료 검증을 통과한 뒤에 엽니다. "
            "지금은 과거 사건 목록만 답할 수 있습니다.",
            plan,
        )
    if not availability.is_open(plan.query_type):
        return _fail(
            FailureReason.QUALITY_NOT_VERIFIED,
            "이 질문 유형은 아직 사람 검수를 통과하지 않아 수치를 내보내지 않습니다.",
            plan,
        )

    handler = _HANDLERS[plan.query_type]
    block = handler(plan, repository, availability, today, limit)
    if isinstance(block, PlanFailure):
        return AnswerResult(None, block)
    if block.evidence_coverage < 1.0:
        return _fail(
            FailureReason.INSUFFICIENT_EVIDENCE,
            "근거를 붙이지 못한 행이 있어 답변을 내보내지 않았습니다.",
            plan,
        )
    return AnswerResult(block, None)


def _fail(reason: FailureReason, message: str, plan: QueryPlan) -> AnswerResult:
    return AnswerResult(
        None, PlanFailure(reason, message, query_type=plan.query_type)
    )


def _no_record(plan: QueryPlan, message: str) -> PlanFailure:
    return PlanFailure(
        FailureReason.NO_MATCHING_EVENT, message, query_type=plan.query_type
    )


# ---------------------------------------------------------------- 공통 도우미


def _rate(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{'+' if value >= 0 else ''}{format(value, 'f')}%"


def _display_limit_exclusions(total: int, limit: int) -> tuple[AnswerExclusion, ...]:
    """행이 표시 한도를 넘어 잘렸으면 몇 개를 생략했는지 답에 남긴다."""
    if total <= limit:
        return ()
    return (AnswerExclusion("DISPLAY_LIMIT", "화면 표시 한도로 생략", total - limit),)


def _matches_direction(
    plan: QueryPlan, rate: Decimal | None
) -> bool:
    if plan.direction is None:
        return True
    if rate is None:
        return False
    return rate >= 0 if plan.direction == "UP" else rate < 0


def _direction_label(plan: QueryPlan) -> str:
    if plan.direction == "UP":
        return "오른"
    if plan.direction == "DOWN":
        return "빠진"
    return "움직인"


def _interpretation(plan: QueryPlan, *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        "queryType": plan.query_type.value,
        "countUnit": plan.count_unit.value,
        "countUnitLabelKo": COUNT_UNIT_LABEL_KO[plan.count_unit],
        "direction": plan.direction,
        "date": None if plan.date is None else plan.date.as_dict(),
        "period": None if plan.period is None else plan.period.as_dict(),
        "company": None if plan.company is None else plan.company.as_dict(),
        "themes": [theme.as_dict() for theme in plan.themes],
        "catalystType": (
            None if plan.catalyst_type is None else plan.catalyst_type.as_dict()
        ),
        "amountCondition": (
            None if plan.amount_condition is None else plan.amount_condition.as_dict()
        ),
        "topic": plan.topic,
    }
    if extra:
        resolved.update(extra)
    return resolved


def _versions(
    repository: ResearchRepository, plan: QueryPlan
) -> dict[str, str]:
    versions = dict(repository.versions())
    versions.update(
        {
            "queryContract": plan.contract_version,
            "queryPlanner": plan.planner_version,
            "answerContract": ANSWER_CONTRACT_VERSION,
        }
    )
    return versions


def _period_label(plan: QueryPlan) -> str:
    if plan.period is None:
        return ""
    return f"{plan.period.start.isoformat()}~{plan.period.end.isoformat()}"


def _daily_notes(day: DailyDay) -> tuple[str, ...]:
    notes: list[str] = []
    if day.status == "NOT_PUBLISHED":
        notes.append(
            "아직 그날 특징테마가 발행되지 않아 직전 거래일"
            f"({day.trading_date.isoformat()}) 결과를 보여드립니다. "
            "장중 실시간 값과 섞지 않았습니다."
        )
    if day.unsplit_post_count > 1:
        notes.append(
            f"이 발행일에 게시물이 {day.unsplit_post_count}건인데 거래일로 가르지 "
            "못했습니다. 여러 거래일 내용이 섞였을 수 있습니다."
        )
    return tuple(notes)


def _section_evidence(day: DailyDay, section: DailySection) -> tuple[AnswerEvidence, ...]:
    excerpt = section.headline or section.section_name
    items = [
        AnswerEvidence(
            source_kind="INFOSTOCK_DAILY_DESCRIPTION",
            label_ko=f"{day.trading_date.isoformat()} 특징테마 · {section.section_name}",
            occurred_on=day.trading_date,
            excerpt=excerpt,
        )
    ]
    items.extend(
        AnswerEvidence(
            source_kind="INFOSTOCK_DAILY_DESCRIPTION",
            label_ko=f"{day.trading_date.isoformat()} 상세 문단",
            occurred_on=day.trading_date,
            excerpt=detail,
        )
        for detail in section.details[:2]
    )
    return tuple(items)


# ---------------------------------------------------------------- 유형별 계산


def _answer_day_movers(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.date is not None
    day = repository.daily_day(plan.date.value)
    if day.status == "NO_RECORD" or not day.sections:
        return _no_record(
            plan, f"{plan.date.value.isoformat()} 특징테마 기록이 없습니다."
        )
    wanted = "DOWN" if plan.direction == "DOWN" else "UP"
    rows: list[AnswerRow] = []
    excluded = 0
    for section in day.sections:
        direction = section.direction
        if plan.direction is not None and direction not in {wanted, "MIXED"}:
            excluded += 1
            continue
        if plan.direction is None and direction == "UNKNOWN":
            excluded += 1
            continue
        rows.append(
            AnswerRow(
                label=section.headline or section.section_name,
                values={
                    "sectionName": section.section_name,
                    "direction": direction,
                    "themes": [
                        {
                            "themeName": theme.theme_name,
                            "changeRate": _rate(theme.change_rate),
                            "stocks": [
                                {
                                    "stockName": stock.stock_name,
                                    "stockCode": stock.stock_code,
                                    "closePrice": stock.close_price,
                                    "changeRate": _rate(stock.change_rate),
                                }
                                for stock in theme.stocks[:8]
                            ],
                            "stockTotal": len(theme.stocks),
                        }
                        for theme in section.themes[:6]
                    ],
                    "themeTotal": len(section.themes),
                },
                evidence=_section_evidence(day, section),
            )
        )
    if not rows:
        return _no_record(
            plan,
            f"{day.trading_date.isoformat()}에 {_direction_label(plan)} 섹션이 없습니다.",
        )
    exclusions = (
        (AnswerExclusion("DIRECTION_MISMATCH", "질문한 방향과 반대인 섹션", excluded),)
        if excluded
        else ()
    )
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(
            plan, extra={"tradingDate": day.trading_date.isoformat()}
        ),
        summary_ko=(
            f"{day.trading_date.isoformat()}에 {_direction_label(plan)} 특징테마 "
            f"섹션은 {len(rows)}개입니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko=f"{_direction_label(plan)} 섹션",
                value=str(len(rows)),
                count_unit=plan.count_unit,
                sample_size=len(day.sections),
            ),
        ),
        rows=tuple(rows[:limit]),
        exclusions=exclusions + _display_limit_exclusions(len(rows), limit),
        versions=_versions(repository, plan),
        sample_size=len(day.sections),
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=_daily_notes(day),
    )


def _answer_period_summary(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.period is not None
    days = repository.daily_days(plan.period.start, plan.period.end)
    published = [day for day in days if day.status == "PUBLISHED" and day.sections]
    if not published:
        return _no_record(
            plan, f"{_period_label(plan)} 구간에 발행된 특징테마가 없습니다."
        )
    wanted = "DOWN" if plan.direction == "DOWN" else "UP"
    counter: Counter[str] = Counter()
    evidence_by_theme: dict[str, list[AnswerEvidence]] = {}
    section_total = 0
    excluded = 0
    for day in published:
        for section in day.sections:
            section_total += 1
            direction = section.direction
            if plan.direction is not None and direction not in {wanted, "MIXED"}:
                excluded += 1
                continue
            for theme in section.themes:
                if plan.direction is not None and not _matches_direction(
                    plan, theme.change_rate
                ):
                    continue
                counter[theme.theme_name] += 1
                bucket = evidence_by_theme.setdefault(theme.theme_name, [])
                if len(bucket) < 3:
                    bucket.append(
                        AnswerEvidence(
                            source_kind="INFOSTOCK_DAILY_DESCRIPTION",
                            label_ko=(
                                f"{day.trading_date.isoformat()} · {section.section_name}"
                            ),
                            occurred_on=day.trading_date,
                            excerpt=section.headline or section.section_name,
                        )
                    )
    if not counter:
        return _no_record(
            plan,
            f"{_period_label(plan)} 구간에 {_direction_label(plan)} 테마 기록이 없습니다.",
        )
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    rows = tuple(
        AnswerRow(
            label=theme_name,
            values={"appearanceCount": count, "countUnit": plan.count_unit.value},
            evidence=tuple(evidence_by_theme[theme_name]),
        )
        for theme_name, count in ordered[:limit]
    )
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(
            plan,
            extra={
                "tradingDays": [day.trading_date.isoformat() for day in published],
            },
        ),
        summary_ko=(
            f"{_period_label(plan)} 거래일 {len(published)}일 동안 "
            f"{_direction_label(plan)} 테마는 {len(counter)}개입니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="거래일 수", value=str(len(published)), sample_size=len(days)
            ),
            AnswerMetric(
                label_ko=f"{_direction_label(plan)} 테마",
                value=str(len(counter)),
                count_unit=plan.count_unit,
                sample_size=section_total,
            ),
        ),
        rows=rows,
        exclusions=(
            (AnswerExclusion("DIRECTION_MISMATCH", "질문한 방향과 반대인 섹션", excluded),)
            if excluded
            else ()
        )
        + _display_limit_exclusions(len(ordered), limit),
        versions=_versions(repository, plan),
        sample_size=section_total,
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=tuple(
            note for day in published for note in _daily_notes(day)
        )[:3],
    )


def _answer_stock_day_reason(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.date is not None and plan.company is not None
    day = repository.daily_day(plan.date.value)
    if day.status == "NO_RECORD" or not day.sections:
        return _no_record(
            plan, f"{plan.date.value.isoformat()} 특징테마 기록이 없습니다."
        )
    code = plan.company.seed_stock_code
    name = plan.company.canonical_name
    rows: list[AnswerRow] = []
    for section in day.sections:
        for theme in section.themes:
            for stock in theme.stocks:
                if stock.stock_code != code and stock.stock_name != name:
                    continue
                rows.append(
                    AnswerRow(
                        label=f"{section.section_name} · {theme.theme_name}",
                        values={
                            "themeName": theme.theme_name,
                            "sectionHeadline": section.headline,
                            "closePrice": stock.close_price,
                            "changeRate": _rate(stock.change_rate),
                            "themeChangeRate": _rate(theme.change_rate),
                            "details": list(section.details[:3]),
                            "detailTotal": len(section.details),
                        },
                        evidence=_section_evidence(day, section),
                    )
                )
    if not rows:
        return _no_record(
            plan,
            f"{day.trading_date.isoformat()} 특징테마에 {name} 기록이 없습니다.",
        )
    observed = [
        row.values["changeRate"] for row in rows if row.values["changeRate"] != "—"
    ]
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(
            plan, extra={"tradingDate": day.trading_date.isoformat()}
        ),
        summary_ko=(
            f"{name}은 {day.trading_date.isoformat()} 특징테마 {len(rows)}개 섹션에 "
            f"등장했고 그날 등락률은 {observed[0] if observed else '기록 없음'}입니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="등장 섹션",
                value=str(len(rows)),
                count_unit=plan.count_unit,
                sample_size=len(day.sections),
            ),
        ),
        rows=tuple(rows[:limit]),
        exclusions=_display_limit_exclusions(len(rows), limit),
        versions=_versions(repository, plan),
        sample_size=len(day.sections),
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=_daily_notes(day),
    )


def _answer_stock_top_moves(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.company is not None and plan.period is not None
    rows_raw = repository.stock_daily_rows(
        plan.company.seed_stock_code, plan.period.start, plan.period.end
    )
    usable = [row for row in rows_raw if row.change_rate is not None]
    missing = len(rows_raw) - len(usable)
    selected = [row for row in usable if _matches_direction(plan, row.change_rate)]
    if not selected:
        return _no_record(
            plan,
            f"{plan.company.canonical_name}의 {_period_label(plan)} 구간에 "
            f"{_direction_label(plan)} 기록이 없습니다.",
        )
    reverse = plan.direction != "DOWN"
    ordered = sorted(
        selected,
        key=lambda row: (row.change_rate or Decimal(0), row.trading_date),
        reverse=reverse,
    )
    rows = tuple(
        AnswerRow(
            label=row.trading_date.isoformat(),
            values={
                "themeName": row.theme_name,
                "changeRate": _rate(row.change_rate),
                "closePrice": row.close_price,
                "sectionHeadline": row.section_headline,
            },
            evidence=(
                AnswerEvidence(
                    source_kind="INFOSTOCK_DAILY_THEME_STOCK",
                    label_ko=f"{row.trading_date.isoformat()} · {row.theme_name}",
                    occurred_on=row.trading_date,
                    excerpt=row.section_headline or row.theme_name,
                ),
            ),
        )
        for row in ordered[:limit]
    )
    exclusions: list[AnswerExclusion] = []
    if missing:
        exclusions.append(
            AnswerExclusion("RATE_MISSING", "등락률이 원문에 없는 행", missing)
        )
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"{plan.company.canonical_name}은 {_period_label(plan)} 구간에 "
            f"{len(selected)}일 {_direction_label(plan)} 기록이 있습니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko=f"{_direction_label(plan)} 행",
                value=str(len(selected)),
                count_unit=plan.count_unit,
                sample_size=len(rows_raw),
            ),
        ),
        rows=rows,
        exclusions=tuple(exclusions) + _display_limit_exclusions(len(ordered), limit),
        versions=_versions(repository, plan),
        sample_size=len(rows_raw),
        human_verified=plan.query_type in availability.human_verified,
    )


def _answer_stock_theme_membership(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.company is not None
    memberships = repository.stock_theme_memberships(plan.company.seed_stock_code)
    described = [item for item in memberships if item.reason]
    if not memberships:
        return _no_record(
            plan, f"{plan.company.canonical_name}이 속한 테마 기록이 없습니다."
        )
    rows = tuple(
        AnswerRow(
            label=item.theme_name,
            values={"sourceThemeId": item.source_theme_id, "reason": item.reason},
            evidence=(
                AnswerEvidence(
                    source_kind="INFOSTOCK_THEME_MEMBERSHIP",
                    label_ko=f"{item.theme_name} 구성 종목",
                    occurred_on=None,
                    excerpt=item.reason or item.theme_name,
                ),
            ),
        )
        for item in memberships[:limit]
    )
    exclusions = (
        (
            AnswerExclusion(
                "REASON_MISSING",
                "편입 사유가 원문에 없는 테마",
                len(memberships) - len(described),
            ),
        )
        if len(described) != len(memberships)
        else ()
    )
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"{plan.company.canonical_name}은 현재 테마 {len(memberships)}개에 "
            "구성 종목으로 들어 있습니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="편입 테마",
                value=str(len(memberships)),
                count_unit=plan.count_unit,
                sample_size=len(memberships),
            ),
        ),
        rows=rows,
        exclusions=exclusions + _display_limit_exclusions(len(memberships), limit),
        versions=_versions(repository, plan),
        sample_size=len(memberships),
        human_verified=plan.query_type in availability.human_verified,
    )


def _answer_theme_members(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    theme = plan.themes[0]
    members = repository.theme_members(theme.source_theme_id)
    if not members:
        return _no_record(plan, f"{theme.theme_name} 테마의 구성 종목 기록이 없습니다.")
    described = [item for item in members if item.reason]
    rows = tuple(
        AnswerRow(
            label=item.stock_name,
            values={"stockCode": item.stock_code, "reason": item.reason},
            evidence=(
                AnswerEvidence(
                    source_kind="INFOSTOCK_THEME_MEMBERSHIP",
                    label_ko=f"{theme.theme_name} 구성 종목",
                    occurred_on=None,
                    excerpt=item.reason or item.stock_name,
                ),
            ),
        )
        for item in members[:limit]
    )
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=f"{theme.theme_name} 테마의 구성 종목은 {len(members)}개입니다.",
        metrics=(
            AnswerMetric(
                label_ko="구성 종목",
                value=str(len(members)),
                count_unit=plan.count_unit,
                sample_size=len(members),
            ),
        ),
        rows=rows,
        exclusions=(
            (
                AnswerExclusion(
                    "REASON_MISSING",
                    "편입 사유가 원문에 없는 종목",
                    len(members) - len(described),
                ),
            )
            if len(described) != len(members)
            else ()
        )
        + _display_limit_exclusions(len(members), limit),
        versions=_versions(repository, plan),
        sample_size=len(members),
        human_verified=plan.query_type in availability.human_verified,
    )


def _answer_theme_history(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    theme = plan.themes[0]
    records = repository.theme_history(
        theme.source_theme_id,
        date_from=None if plan.period is None else plan.period.start,
        date_to=None if plan.period is None else plan.period.end,
    )
    if plan.direction is not None:
        records = tuple(item for item in records if item.direction == plan.direction)
    if not records:
        return _no_record(
            plan, f"{theme.theme_name} 테마의 과거 사유 기록이 없습니다."
        )
    counter = Counter(
        item.primary_catalyst_name_ko or item.primary_catalyst_type or "미분류"
        for item in records
    )
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    evidence_by_type: dict[str, list[AnswerEvidence]] = {}
    for item in records:
        key = item.primary_catalyst_name_ko or item.primary_catalyst_type or "미분류"
        bucket = evidence_by_type.setdefault(key, [])
        if len(bucket) < 3:
            bucket.append(
                AnswerEvidence(
                    source_kind="INFOSTOCK_THEME_HISTORY",
                    label_ko=(
                        f"{item.event_date.isoformat() if item.event_date else '날짜 없음'}"
                        f" · {item.theme_name}"
                    ),
                    occurred_on=item.event_date,
                    excerpt=item.raw_text[:160],
                )
            )
    rows = tuple(
        AnswerRow(
            label=name,
            values={"recordCount": count, "countUnit": plan.count_unit.value},
            evidence=tuple(evidence_by_type[name]),
        )
        for name, count in ordered[:limit]
    )
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"{theme.theme_name} 테마는 원천 기록 {len(records)}건에서 "
            f"소재 {len(counter)}종으로 움직였습니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="원천 기록",
                value=str(len(records)),
                count_unit=plan.count_unit,
                sample_size=len(records),
            ),
        ),
        rows=rows,
        exclusions=_display_limit_exclusions(len(ordered), limit),
        versions=_versions(repository, plan),
        sample_size=len(records),
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=(
            "소재 유형은 자동 분류 결과이며 test split 기준 primary 정확도가 "
            "77.8%입니다.",
        ),
    )


def _answer_theme_comparison(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.period is not None
    names = [theme.theme_name for theme in plan.themes]
    changes = repository.theme_daily_changes(names, plan.period.start, plan.period.end)
    usable = [item for item in changes if item.change_rate is not None]
    if not usable:
        return _no_record(
            plan, f"{_period_label(plan)} 구간에 두 테마의 등락률 기록이 없습니다."
        )
    grouped: dict[str, list[ThemeDailyChange]] = {name: [] for name in names}
    for item in usable:
        grouped.setdefault(item.theme_name, []).append(item)
    rows: list[AnswerRow] = []
    for name in names:
        items = grouped.get(name) or []
        if not items:
            continue
        rates = sorted(item.change_rate for item in items if item.change_rate is not None)
        total = sum(rates, Decimal(0))
        median = rates[len(rates) // 2]
        best = max(items, key=lambda item: item.change_rate or Decimal(0))
        worst = min(items, key=lambda item: item.change_rate or Decimal(0))
        pick = worst if plan.direction == "DOWN" else best
        rows.append(
            AnswerRow(
                label=name,
                values={
                    "observedDays": len(items),
                    "sumChangeRate": format(total, "f"),
                    "medianChangeRate": format(median, "f"),
                    "bestDate": best.trading_date.isoformat(),
                    "bestChangeRate": _rate(best.change_rate),
                    "worstDate": worst.trading_date.isoformat(),
                    "worstChangeRate": _rate(worst.change_rate),
                },
                evidence=(
                    AnswerEvidence(
                        source_kind="INFOSTOCK_DAILY_DESCRIPTION",
                        label_ko=f"{pick.trading_date.isoformat()} · {name}",
                        occurred_on=pick.trading_date,
                        excerpt=pick.section_headline or name,
                    ),
                ),
            )
        )
    if len(rows) < 2:
        return _no_record(
            plan, f"{_period_label(plan)} 구간에 비교할 두 테마의 기록이 모두 있지는 않습니다."
        )
    key = "sumChangeRate"
    winner = (
        min(rows, key=lambda row: Decimal(str(row.values[key])))
        if plan.direction == "DOWN"
        else max(rows, key=lambda row: Decimal(str(row.values[key])))
    )
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"{_period_label(plan)} 구간 관측일 기준으로 "
            f"{'더 빠진' if plan.direction == 'DOWN' else '더 오른'} 쪽은 "
            f"{winner.label}입니다."
        ),
        metrics=tuple(
            AnswerMetric(
                label_ko=f"{row.label} 등락률 합",
                value=str(row.values["sumChangeRate"]),
                count_unit=plan.count_unit,
                sample_size=int(row.values["observedDays"]),
            )
            for row in rows
        ),
        rows=tuple(rows),
        exclusions=(
            (
                AnswerExclusion(
                    "RATE_MISSING", "등락률이 원문에 없는 행", len(changes) - len(usable)
                ),
            )
            if len(changes) != len(usable)
            else ()
        ),
        versions=_versions(repository, plan),
        sample_size=len(usable),
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=(
            "두 테마가 같은 날 모두 등장하지 않을 수 있어 관측일 수를 함께 표시합니다.",
        ),
    )


def _catalyst_evidence(item: CatalystSummary) -> tuple[AnswerEvidence, ...]:
    if not item.evidence_text:
        return ()
    return (
        AnswerEvidence(
            source_kind="ONTOLOGY_CATALYST",
            label_ko=(
                f"{item.occurred_on.isoformat() if item.occurred_on else '날짜 없음'}"
                f" · {item.theme_names[0] if item.theme_names else '테마 미상'}"
            ),
            occurred_on=item.occurred_on,
            excerpt=item.evidence_text[:200],
            start=item.evidence_start,
            end=item.evidence_end,
        ),
    )


def _catalyst_counts(items: Sequence[CatalystSummary]) -> tuple[int, int, int]:
    return (
        sum(item.source_record_count for item in items),
        sum(item.theme_reaction_count for item in items),
        len(items),
    )


def _answer_theme_frequency(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.period is not None
    items = repository.catalysts(
        CatalystFilter(date_from=plan.period.start, date_to=plan.period.end)
    )
    if not items:
        return _no_record(plan, f"{_period_label(plan)} 구간에 고유 사건 기록이 없습니다.")
    counter: Counter[str] = Counter()
    evidence_by_theme: dict[str, list[AnswerEvidence]] = {}
    for item in items:
        for theme_name in dict.fromkeys(item.theme_names):
            counter[theme_name] += 1
            bucket = evidence_by_theme.setdefault(theme_name, [])
            if len(bucket) < 3:
                bucket.extend(_catalyst_evidence(item))
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    rows = tuple(
        AnswerRow(
            label=name,
            values={"catalystCount": count, "countUnit": plan.count_unit.value},
            evidence=tuple(evidence_by_theme.get(name, ())),
        )
        for name, count in ordered[:limit]
        if evidence_by_theme.get(name)
    )
    if not rows:
        return _no_record(plan, "근거를 붙일 수 있는 테마 사건이 없습니다.")
    source_records, reactions, unique = _catalyst_counts(items)
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"{_period_label(plan)} 구간에서 가장 자주 나온 테마는 "
            f"{ordered[0][0]}({ordered[0][1]}건)입니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="고유 사건",
                value=str(unique),
                count_unit=CountUnit.CATALYST,
                sample_size=unique,
            ),
            AnswerMetric(
                label_ko="원천 기록", value=str(source_records), count_unit=CountUnit.SOURCE_RECORD
            ),
            AnswerMetric(
                label_ko="테마 반응", value=str(reactions), count_unit=CountUnit.THEME_REACTION
            ),
        ),
        rows=rows,
        exclusions=_display_limit_exclusions(len(ordered), limit),
        versions=_versions(repository, plan),
        sample_size=unique,
        human_verified=plan.query_type in availability.human_verified,
    )


def _answer_catalyst_theme_reaction(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.catalyst_type is not None
    items = repository.catalysts(
        CatalystFilter(
            catalyst_type=plan.catalyst_type.type_id,
            date_from=None if plan.period is None else plan.period.start,
            date_to=None if plan.period is None else plan.period.end,
            source_theme_id=(
                plan.themes[0].source_theme_id if plan.themes else None
            ),
            topic_text=plan.topic,
        )
    )
    if not items:
        scope = f"{plan.topic} " if plan.topic else ""
        return _no_record(
            plan,
            f"{scope}{plan.catalyst_type.name_ko} 소재로 분류된 사건이 없습니다.",
        )
    counter: Counter[str] = Counter()
    evidence_by_theme: dict[str, list[AnswerEvidence]] = {}
    for item in items:
        for theme_name in dict.fromkeys(item.theme_names):
            counter[theme_name] += item.theme_reaction_count or 1
            bucket = evidence_by_theme.setdefault(theme_name, [])
            if len(bucket) < 3:
                bucket.extend(_catalyst_evidence(item))
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    rows = tuple(
        AnswerRow(
            label=name,
            values={"reactionCount": count, "countUnit": plan.count_unit.value},
            evidence=tuple(evidence_by_theme.get(name, ())),
        )
        for name, count in ordered[:limit]
        if evidence_by_theme.get(name)
    )
    if not rows:
        return _no_record(plan, "근거를 붙일 수 있는 테마 반응이 없습니다.")
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"{plan.catalyst_type.name_ko} 소재에 반응한 테마는 {len(counter)}개이고 "
            f"가장 많이 반응한 곳은 {ordered[0][0]}입니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="테마 반응",
                value=str(sum(counter.values())),
                count_unit=plan.count_unit,
                sample_size=len(items),
            ),
            AnswerMetric(
                label_ko="고유 사건", value=str(len(items)), count_unit=CountUnit.CATALYST
            ),
        ),
        rows=rows,
        exclusions=_display_limit_exclusions(len(ordered), limit),
        versions=_versions(repository, plan),
        sample_size=len(items),
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=(
            "소재 유형은 자동 분류 결과이며 test split 기준 primary 정확도가 "
            "77.8%입니다.",
        ),
    )


def _answer_catalyst_frequency(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.catalyst_type is not None and plan.period is not None
    items = repository.catalysts(
        CatalystFilter(
            catalyst_type=plan.catalyst_type.type_id,
            date_from=plan.period.start,
            date_to=plan.period.end,
            source_theme_id=(
                plan.themes[0].source_theme_id if plan.themes else None
            ),
            topic_text=plan.topic,
        )
    )
    if not items:
        return _no_record(
            plan,
            f"{_period_label(plan)} 구간에 {plan.catalyst_type.name_ko} 소재 사건이 "
            "없습니다.",
        )
    source_records, reactions, unique = _catalyst_counts(items)
    by_year: Counter[str] = Counter()
    evidence_by_year: dict[str, list[AnswerEvidence]] = {}
    for item in items:
        key = str(item.occurred_on.year) if item.occurred_on else "날짜 없음"
        by_year[key] += 1
        bucket = evidence_by_year.setdefault(key, [])
        if len(bucket) < 3:
            bucket.extend(_catalyst_evidence(item))
    rows = tuple(
        AnswerRow(
            label=year,
            values={"catalystCount": count, "countUnit": plan.count_unit.value},
            evidence=tuple(evidence_by_year.get(year, ())),
        )
        for year, count in sorted(by_year.items())
        if evidence_by_year.get(year)
    )
    if not rows:
        return _no_record(plan, "근거를 붙일 수 있는 사건이 없습니다.")
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"{_period_label(plan)} 구간에 {plan.catalyst_type.name_ko} 소재는 "
            f"고유 사건 {unique}건입니다(원천 기록 {source_records}건)."
        ),
        metrics=(
            AnswerMetric(
                label_ko="고유 사건",
                value=str(unique),
                count_unit=CountUnit.CATALYST,
                sample_size=unique,
            ),
            AnswerMetric(
                label_ko="원천 기록", value=str(source_records), count_unit=CountUnit.SOURCE_RECORD
            ),
            AnswerMetric(
                label_ko="테마 반응", value=str(reactions), count_unit=CountUnit.THEME_REACTION
            ),
        ),
        rows=rows,
        exclusions=(),
        versions=_versions(repository, plan),
        sample_size=unique,
        human_verified=plan.query_type in availability.human_verified,
    )


def _answer_catalyst_certainty(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.catalyst_type is not None
    items = repository.catalysts(
        CatalystFilter(
            catalyst_type=plan.catalyst_type.type_id,
            date_from=None if plan.period is None else plan.period.start,
            date_to=None if plan.period is None else plan.period.end,
            source_theme_id=(
                plan.themes[0].source_theme_id if plan.themes else None
            ),
            topic_text=plan.topic,
        )
    )
    if not items:
        scope = f"{plan.topic} " if plan.topic else ""
        return _no_record(
            plan,
            f"{scope}{plan.catalyst_type.name_ko} 소재로 분류된 사건이 없습니다.",
        )
    counter = Counter(item.certainty for item in items)
    evidence_by_certainty: dict[str, list[AnswerEvidence]] = {}
    for item in items:
        bucket = evidence_by_certainty.setdefault(item.certainty, [])
        if len(bucket) < 3:
            bucket.extend(_catalyst_evidence(item))
    labels = {
        "CONFIRMED": "확정",
        "ANTICIPATION": "기대·전망",
        "UNSPECIFIED": "표지 없음",
    }
    rows = tuple(
        AnswerRow(
            label=labels.get(certainty, certainty),
            values={
                "catalystCount": count,
                "share": f"{count / len(items) * 100:.1f}%",
                "countUnit": plan.count_unit.value,
            },
            evidence=tuple(evidence_by_certainty.get(certainty, ())),
        )
        for certainty, count in sorted(counter.items(), key=lambda item: -item[1])
        if evidence_by_certainty.get(certainty)
    )
    if not rows:
        return _no_record(plan, "근거를 붙일 수 있는 사건이 없습니다.")
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"{plan.catalyst_type.name_ko} 소재 고유 사건 {len(items)}건 중 확정은 "
            f"{counter.get('CONFIRMED', 0)}건, 기대·전망은 "
            f"{counter.get('ANTICIPATION', 0)}건입니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="고유 사건",
                value=str(len(items)),
                count_unit=plan.count_unit,
                sample_size=len(items),
            ),
        ),
        rows=rows,
        exclusions=(),
        versions=_versions(repository, plan),
        sample_size=len(items),
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=(
            "확실성은 자동 분류 결과이며 test split 기준 정확도가 90.0%입니다.",
        ),
    )


def _answer_catalyst_continuation(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    items = repository.catalysts(
        CatalystFilter(
            catalyst_type=(
                None if plan.catalyst_type is None else plan.catalyst_type.type_id
            ),
            source_theme_id=plan.themes[0].source_theme_id if plan.themes else None,
            date_from=None if plan.period is None else plan.period.start,
            date_to=None if plan.period is None else plan.period.end,
            topic_text=plan.topic,
        )
    )
    if not items:
        return _no_record(plan, "해당 소재·테마의 사건 기록이 없습니다.")
    counter = Counter(item.novelty_type for item in items)
    evidence_by_novelty: dict[str, list[AnswerEvidence]] = {}
    for item in items:
        bucket = evidence_by_novelty.setdefault(item.novelty_type, [])
        if len(bucket) < 3:
            bucket.extend(_catalyst_evidence(item))
    labels = {
        "NEW": "처음 나온 소재",
        "RECURRING": "다시 나온 소재",
        "CONTINUATION": "이어지는 소재",
        "UNSPECIFIED": "표지 없음",
    }
    rows = tuple(
        AnswerRow(
            label=labels.get(novelty, novelty),
            values={"catalystCount": count, "countUnit": plan.count_unit.value},
            evidence=tuple(evidence_by_novelty.get(novelty, ())),
        )
        for novelty, count in sorted(counter.items(), key=lambda item: -item[1])
        if evidence_by_novelty.get(novelty)
    )
    if not rows:
        return _no_record(plan, "근거를 붙일 수 있는 사건이 없습니다.")
    dated = [item.occurred_on for item in items if item.occurred_on is not None]
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"고유 사건 {len(items)}건 중 재부각·반복은 "
            f"{sum(count for key, count in counter.items() if key != 'NEW')}건입니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="고유 사건",
                value=str(len(items)),
                count_unit=plan.count_unit,
                sample_size=len(items),
            ),
            AnswerMetric(
                label_ko="첫 기록",
                value=min(dated).isoformat() if dated else "날짜 없음",
            ),
            AnswerMetric(
                label_ko="마지막 기록",
                value=max(dated).isoformat() if dated else "날짜 없음",
            ),
        ),
        rows=rows,
        exclusions=(),
        versions=_versions(repository, plan),
        sample_size=len(items),
        human_verified=plan.query_type in availability.human_verified,
    )


def _answer_stock_cooccurrence(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.company is not None and plan.period is not None
    items = repository.catalysts(
        CatalystFilter(
            seed_stock_code=plan.company.seed_stock_code,
            date_from=plan.period.start,
            date_to=plan.period.end,
        )
    )
    if not items:
        return _no_record(
            plan,
            f"{plan.company.canonical_name}의 {_period_label(plan)} 구간 고유 사건이 "
            "없습니다.",
        )
    counter: Counter[tuple[str, str]] = Counter()
    evidence_by_peer: dict[tuple[str, str], list[AnswerEvidence]] = {}
    for item in items:
        peers = {
            (role.seed_stock_code, role.company_name)
            for role in item.company_roles
            if role.seed_stock_code != plan.company.seed_stock_code
        }
        for peer in peers:
            counter[peer] += 1
            bucket = evidence_by_peer.setdefault(peer, [])
            if len(bucket) < 3:
                bucket.extend(_catalyst_evidence(item))
    if not counter:
        return _no_record(
            plan, f"{plan.company.canonical_name}과 같은 사건에 등장한 회사가 없습니다."
        )
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0][0]))
    rows = tuple(
        AnswerRow(
            label=name,
            values={
                "stockCode": code,
                "sharedCatalystCount": count,
                "countUnit": plan.count_unit.value,
            },
            evidence=tuple(evidence_by_peer.get((code, name), ())),
        )
        for (code, name), count in ordered[:limit]
        if evidence_by_peer.get((code, name))
    )
    if not rows:
        return _no_record(plan, "근거를 붙일 수 있는 동반 종목이 없습니다.")
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"{plan.company.canonical_name}과 같은 고유 사건에 함께 등장한 회사는 "
            f"{len(counter)}곳입니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="공동 등장 사건",
                value=str(len(items)),
                count_unit=plan.count_unit,
                sample_size=len(items),
            ),
        ),
        rows=rows,
        exclusions=_display_limit_exclusions(len(ordered), limit),
        versions=_versions(repository, plan),
        sample_size=len(items),
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=(
            "같은 사건에 함께 나왔다는 뜻이며 주가가 같이 움직였다는 뜻이 아닙니다.",
        ),
    )


def _answer_company_direct_event(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.company is not None
    direct_roles = ("ACTOR", "ISSUER", "CONTRACTOR", "TARGET")
    base = CatalystFilter(
        seed_stock_code=plan.company.seed_stock_code,
        date_from=None if plan.period is None else plan.period.start,
        date_to=None if plan.period is None else plan.period.end,
    )
    every = repository.catalysts(base)
    direct = repository.catalysts(
        CatalystFilter(
            seed_stock_code=base.seed_stock_code,
            roles=direct_roles,
            date_from=base.date_from,
            date_to=base.date_to,
        )
    )
    if not direct:
        return _no_record(
            plan,
            f"{plan.company.canonical_name}이 직접 주체인 사건 기록이 없습니다. "
            f"이름이 등장한 고유 사건은 {len(every)}건입니다.",
        )
    rows = tuple(
        AnswerRow(
            label=(
                f"{item.occurred_on.isoformat() if item.occurred_on else '날짜 없음'}"
                f" · {item.action or item.primary_catalyst_name_ko or '사건'}"
            ),
            values={
                "catalystId": item.catalyst_id,
                "eventStage": item.event_stage,
                "certainty": item.certainty,
                "roles": sorted(
                    {
                        role.role
                        for role in item.company_roles
                        if role.seed_stock_code == plan.company.seed_stock_code
                        and role.role in direct_roles
                    }
                ),
                "themeNames": list(item.theme_names[:4]),
                "themeNameTotal": len(item.theme_names),
                "geographyCodes": list(item.geography_codes),
                "projectId": item.project_id,
                "sourceRecordCount": item.source_record_count,
            },
            evidence=_catalyst_evidence(item),
        )
        for item in direct[:limit]
    )
    leader_only = len(every) - len(direct)
    source_records, reactions, unique = _catalyst_counts(direct)
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan, extra={"roles": list(direct_roles)}),
        summary_ko=(
            f"{plan.company.canonical_name}이 직접 주체인 고유 사건은 {unique}건입니다"
            f"(이름이 등장한 고유 사건 {len(every)}건 중)."
        ),
        metrics=(
            AnswerMetric(
                label_ko="직접 사건",
                value=str(unique),
                count_unit=CountUnit.CATALYST,
                sample_size=len(every),
            ),
            AnswerMetric(
                label_ko="원천 기록", value=str(source_records), count_unit=CountUnit.SOURCE_RECORD
            ),
            AnswerMetric(
                label_ko="테마 반응", value=str(reactions), count_unit=CountUnit.THEME_REACTION
            ),
        ),
        rows=rows,
        exclusions=(
            (
                AnswerExclusion(
                    "LEADER_OR_RELATED_ONLY",
                    "주도주·관련주로만 등장해 직접 사건이 아닌 기록",
                    leader_only,
                ),
            )
            if leader_only > 0
            else ()
        )
        + _display_limit_exclusions(len(direct), limit),
        versions=_versions(repository, plan),
        sample_size=len(every),
        human_verified=plan.query_type in availability.human_verified,
    )


def _answer_company_value_summary(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    condition = plan.amount_condition
    facts = repository.value_facts(
        CatalystFilter(
            seed_stock_code=(
                None if plan.company is None else plan.company.seed_stock_code
            ),
            catalyst_type=(
                None if plan.catalyst_type is None else plan.catalyst_type.type_id
            ),
            date_from=None if plan.period is None else plan.period.start,
            date_to=None if plan.period is None else plan.period.end,
        )
    )
    if not facts:
        return _no_record(plan, "금액이 원문에 적힌 사건 기록이 없습니다.")
    eligible = [fact for fact in facts if fact.eligible_for_sum]
    dropped = len(facts) - len(eligible)
    if condition is not None and condition.normalized_value > 0:
        selected = [
            fact for fact in eligible if condition.matches(fact.normalized_value)
        ]
    else:
        selected = list(eligible)
    if not selected:
        return _no_record(plan, "금액 조건에 맞는 사건이 없습니다.")
    # 같은 고유 사건의 여러 금액 fact를 두 번 더하지 않는다.
    by_catalyst: dict[str, ValueFact] = {}
    for fact in sorted(
        selected, key=lambda item: (item.catalyst_id, -item.normalized_value)
    ):
        by_catalyst.setdefault(fact.catalyst_id, fact)
    unique_facts = sorted(
        by_catalyst.values(),
        key=lambda item: (-item.normalized_value, item.catalyst_id),
    )
    total = sum((fact.normalized_value for fact in unique_facts), Decimal(0))
    rows = tuple(
        AnswerRow(
            label=(
                f"{fact.occurred_on.isoformat() if fact.occurred_on else '날짜 없음'}"
                f" · {fact.theme_name}"
            ),
            values={
                "catalystId": fact.catalyst_id,
                "factType": fact.fact_type,
                "reportedValue": fact.reported_value,
                "normalizedValue": format(fact.normalized_value, "f"),
                "unit": fact.unit,
                "currency": fact.currency,
                "valueBasis": fact.value_basis,
            },
            evidence=(
                AnswerEvidence(
                    source_kind="ONTOLOGY_CATALYST_VALUE",
                    label_ko=(
                        f"{fact.occurred_on.isoformat() if fact.occurred_on else '날짜 없음'}"
                        f" · {fact.theme_name}"
                    ),
                    occurred_on=fact.occurred_on,
                    excerpt=fact.evidence_text[:200],
                ),
            ),
        )
        for fact in unique_facts[:limit]
    )
    exclusions: list[AnswerExclusion] = []
    if dropped:
        exclusions.append(
            AnswerExclusion(
                "VALUE_NOT_SUMMABLE",
                "범위·최대값·총사업비라 합계에서 뺀 금액",
                dropped,
            )
        )
    if len(selected) != len(unique_facts):
        exclusions.append(
            AnswerExclusion(
                "DUPLICATE_VALUE_FACT",
                "같은 고유 사건의 중복 금액",
                len(selected) - len(unique_facts),
            )
        )
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(plan),
        summary_ko=(
            f"조건에 맞는 고유 사건은 {len(unique_facts)}건이고 합계는 "
            f"{format(total, 'f')}원입니다."
        ),
        metrics=(
            AnswerMetric(
                label_ko="해당 사건",
                value=str(len(unique_facts)),
                count_unit=plan.count_unit,
                sample_size=len(facts),
            ),
            AnswerMetric(
                label_ko="금액 합계",
                value=format(total, "f"),
                count_unit=CountUnit.VALUE_FACT,
                note_ko="원 단위. 합산 가능한 fact만 더했습니다.",
            ),
        ),
        rows=rows,
        exclusions=tuple(exclusions) + _display_limit_exclusions(len(unique_facts), limit),
        versions=_versions(repository, plan),
        sample_size=len(facts),
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=(
            "회사 몫이 명시되지 않은 총사업비는 합계에 넣지 않았습니다.",
        ),
    )


def _answer_company_historical_outcome(
    plan: QueryPlan,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int,
) -> AnswerBlock | PlanFailure:
    assert plan.period is not None
    start = plan.period.start
    out_of_range = start < availability.outcome_range_from
    effective_start = max(start, availability.outcome_range_from)
    if effective_start > plan.period.end:
        return _fail_block(
            plan,
            FailureReason.OUTCOME_GATE_CLOSED,
            f"{availability.outcome_range_from.isoformat()} 이전 사건의 실제 결과는 "
            "가격 자료가 없어 답할 수 없습니다.",
        )
    outcome_filter = CatalystFilter(
        seed_stock_code=(
            None if plan.company is None else plan.company.seed_stock_code
        ),
        catalyst_type=(
            None if plan.catalyst_type is None else plan.catalyst_type.type_id
        ),
        source_theme_id=plan.themes[0].source_theme_id if plan.themes else None,
        topic_text=plan.topic,
        date_from=effective_start,
        date_to=plan.period.end,
    )
    # 회사를 물으면 그 회사의 직접 사건 축, 소재·테마로 물으면 사건 당시
    # 주도주 축이다(계획서 4.1 "회사 또는 당시 주도주 outcome").
    leader_axis = plan.company is None
    if leader_axis:
        observations = repository.leader_outcomes(
            outcome_filter, horizons=DEFAULT_OUTCOME_HORIZONS
        )
    else:
        observations = repository.outcomes(
            outcome_filter, horizons=DEFAULT_OUTCOME_HORIZONS
        )
    if not observations:
        return _no_record(plan, "그 구간에 실제 결과를 붙일 사건이 없습니다.")
    observed = [item for item in observations if item.base_close is not None]
    rows = tuple(
        AnswerRow(
            label=(
                f"{item.occurred_on.isoformat()} · {item.company_name}"
            ),
            values={
                "catalystId": item.catalyst_id,
                "baseTradingDate": (
                    None
                    if item.base_trading_date is None
                    else item.base_trading_date.isoformat()
                ),
                "baseClose": (
                    None if item.base_close is None else format(item.base_close, "f")
                ),
                "returns": {
                    f"T+{horizon}": (
                        None
                        if item.returns.get(horizon) is None
                        else _rate(item.returns[horizon])
                    )
                    for horizon in DEFAULT_OUTCOME_HORIZONS
                },
                "missingReason": item.missing_reason,
            },
            evidence=(
                AnswerEvidence(
                    source_kind="ONTOLOGY_CATALYST",
                    label_ko=f"{item.occurred_on.isoformat()} · {item.company_name}",
                    occurred_on=item.occurred_on,
                    excerpt=item.evidence_text[:200],
                ),
            ),
        )
        for item in observations[:limit]
    )
    exclusions: list[AnswerExclusion] = []
    if out_of_range:
        exclusions.append(
            AnswerExclusion(
                "OUT_OF_PRICE_RANGE",
                f"{availability.outcome_range_from.isoformat()} 이전이라 가격 자료가 없는 구간",
                1,
            )
        )
    missing = len(observations) - len(observed)
    if missing:
        exclusions.append(
            AnswerExclusion("PRICE_MISSING", "가격을 찾지 못한 사건", missing)
        )
    metrics: list[AnswerMetric] = [
        AnswerMetric(
            label_ko="대상 사건",
            value=str(len(observations)),
            count_unit=plan.count_unit,
            sample_size=len(observations),
        ),
        AnswerMetric(
            label_ko="가격 관측",
            value=str(len(observed)),
            count_unit=CountUnit.OUTCOME_OBSERVATION,
            note_ko="가격이 없는 사건은 0으로 바꾸지 않고 제외했습니다.",
        ),
    ]
    for horizon in DEFAULT_OUTCOME_HORIZONS:
        observed_returns: list[Decimal] = [
            value
            for item in observed
            if (value := item.returns.get(horizon)) is not None
        ]
        values = sorted(observed_returns)
        metrics.append(
            AnswerMetric(
                label_ko=f"T+{horizon} 중앙값",
                value=(
                    _rate(values[len(values) // 2]) if values else "관측 없음"
                ),
                count_unit=CountUnit.OUTCOME_OBSERVATION,
                sample_size=len(values),
            )
        )
    return AnswerBlock(
        query_type=plan.query_type,
        count_unit=plan.count_unit,
        interpretation=_interpretation(
            plan,
            extra={
                "outcomeRangeFrom": availability.outcome_range_from.isoformat(),
                "horizons": list(DEFAULT_OUTCOME_HORIZONS),
            },
        ),
        summary_ko=(
            f"대상 {'사건 당시 주도주' if leader_axis else '사건'} "
            f"{len(observations)}건 중 {len(observed)}건에 실제 주가를 연결했습니다."
        ),
        metrics=tuple(metrics),
        rows=rows,
        exclusions=tuple(exclusions) + _display_limit_exclusions(len(observations), limit),
        versions=_versions(repository, plan),
        sample_size=len(observations),
        human_verified=plan.query_type in availability.human_verified,
        notes_ko=(
            ("과거 실제 결과이며 앞으로의 수익률이 아닙니다.",)
            + (
                ("사건 당시 주도주 목록 기준입니다 — 현재 테마 구성과 다를 수 있습니다.",)
                if leader_axis
                else ()
            )
        ),
    )


def _fail_block(plan: QueryPlan, reason: FailureReason, message: str) -> PlanFailure:
    return PlanFailure(reason, message, query_type=plan.query_type)


_HANDLERS: Mapping[QueryType, Any] = {
    QueryType.DAY_MOVERS: _answer_day_movers,
    QueryType.PERIOD_SUMMARY: _answer_period_summary,
    QueryType.STOCK_DAY_REASON: _answer_stock_day_reason,
    QueryType.STOCK_TOP_MOVES: _answer_stock_top_moves,
    QueryType.STOCK_THEME_MEMBERSHIP: _answer_stock_theme_membership,
    QueryType.STOCK_COOCCURRENCE: _answer_stock_cooccurrence,
    QueryType.THEME_MEMBERS: _answer_theme_members,
    QueryType.THEME_HISTORY: _answer_theme_history,
    QueryType.THEME_COMPARISON: _answer_theme_comparison,
    QueryType.THEME_FREQUENCY: _answer_theme_frequency,
    QueryType.CATALYST_THEME_REACTION: _answer_catalyst_theme_reaction,
    QueryType.CATALYST_FREQUENCY: _answer_catalyst_frequency,
    QueryType.CATALYST_CERTAINTY: _answer_catalyst_certainty,
    QueryType.CATALYST_CONTINUATION: _answer_catalyst_continuation,
    QueryType.COMPANY_DIRECT_EVENT: _answer_company_direct_event,
    QueryType.COMPANY_VALUE_SUMMARY: _answer_company_value_summary,
    QueryType.COMPANY_HISTORICAL_OUTCOME: _answer_company_historical_outcome,
}


def answer_question(
    question: str,
    *,
    catalog: Any,
    repository: ResearchRepository,
    availability: QueryAvailability,
    today: date,
    limit: int = 20,
) -> AnswerResult:
    """자연어 한 문장을 해석하고 계산해 답변 블록 하나를 만든다."""

    from .query_planning import plan_question

    result = plan_question(question, catalog=catalog, today=today)
    if result.failure is not None:
        return AnswerResult(None, result.failure)
    assert result.plan is not None
    return answer_plan(
        result.plan,
        repository,
        availability=availability,
        today=today,
        limit=limit,
    )


__all__ = [
    "ANSWER_CONTRACT_VERSION",
    "COUNT_UNIT_LABEL_KO",
    "DEFAULT_OUTCOME_HORIZONS",
    "OUTCOME_RANGE_FROM",
    "AnswerBlock",
    "AnswerEvidence",
    "AnswerExclusion",
    "AnswerMetric",
    "AnswerResult",
    "AnswerRow",
    "CatalystCompanyRoleRow",
    "CatalystFilter",
    "CatalystSummary",
    "DailyDay",
    "DailySection",
    "DailyStock",
    "DailyStockRow",
    "DailyTheme",
    "OutcomeObservation",
    "QueryAvailability",
    "ResearchRepository",
    "ThemeDailyChange",
    "ThemeHistoryRecord",
    "ThemeMembership",
    "ValueFact",
    "answer_plan",
    "answer_question",
]
