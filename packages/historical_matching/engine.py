"""같은 테마 안에서 소재가 닮은 과거 사건을 고르고 실제 결과를 붙인다.

후보는 같은 테마의 과거 원인문 중 소재 유형(통제어휘 28종)이 하나라도 겹치는
것뿐이다. 점수는 겹친 유형·주 유형 일치·방향·확실성·당시 주도주 중첩으로만
만든다. 미래 수익률은 고르는 데 쓰지 않는다 — 사례를 먼저 고른 뒤 T+1·5·20
실제 등락을 붙인다.

과거 원인문은 인포스탁 수집본(theme detail history)이고 실제 등락은 E-16 일봉
corpus다. 둘 다 이미 확보된 자료라 새로 수집하지 않는다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from statistics import median
from typing import Protocol

from packages.infostock.models import ThemeDetail, ThemeHistory
from packages.ontology.transform import TRANSFORM_VERSION, classify_catalyst
from packages.ontology.vocabulary import VOCABULARY, VOCABULARY_VERSION

MATCH_MODEL_VERSION = "historical-match/1.0.0"
HORIZONS: tuple[int, ...] = (1, 5, 20)
# 한 페이지로 발행한다. nextCursor를 만들지 않으므로 여기서 잘린 나머지는
# hasMore로만 알린다.
MAX_ITEMS = 20

# 점수가 낮은 사건까지 20건을 채우면 요약 중앙값이 닮지도 않은 과거로 계산된다
# (문턱 없이 실측: 뽑힌 3,845건 중 주 소재까지 같은 것 73.5%, 하위 10% 테마는
# 6%). 0.35는 주 소재가 같거나(0.45+) 유형 교집합·방향·확실성이 함께 맞는
# 경우만 남긴다 — 실측 주 소재 일치율 95.6%, 사례가 나오는 테마 255→241.
MIN_SCORE = 0.35

PRIMARY_WEIGHT = 0.45
OVERLAP_WEIGHT = 0.25
DIRECTION_WEIGHT = 0.15
CERTAINTY_WEIGHT = 0.05
LEADER_WEIGHT = 0.10

_TYPE_NAMES = {definition.type_id: definition.name_ko for definition in VOCABULARY}


class OutcomeSource(Protocol):
    """일봉 corpus 읽기 표면. `packages.ontology.SqliteOutcomeReader`가 만족한다."""

    def returns(
        self, stock_code: str, occurred_on: date, horizons: Sequence[int]
    ) -> tuple[
        date | None, Decimal | None, Mapping[int, Decimal | None], str | None
    ]: ...

    def daily_return(self, stock_code: str, on: date) -> Decimal | None: ...


@dataclass(frozen=True, slots=True)
class TodayContext:
    """오늘 사건의 소재. 원인문 한 줄과 오늘 주도주에서 만든다."""

    ordered_type_ids: tuple[str, ...]
    direction: str
    certainty: str
    leader_codes: frozenset[str]

    @property
    def type_ids(self) -> frozenset[str]:
        return frozenset(self.ordered_type_ids)

    @property
    def primary_type_id(self) -> str:
        return self.ordered_type_ids[0]


@dataclass(frozen=True, slots=True)
class PastCase:
    """분류가 붙은 과거 사건 하나."""

    matched_event_id: str
    theme_id: str
    display_name_at_event: str
    market_date: date
    catalyst_summary: str
    ordered_type_ids: tuple[str, ...]
    direction: str
    certainty: str
    leaders: tuple[tuple[str, str], ...]
    members: tuple[tuple[str, str], ...]

    @property
    def type_ids(self) -> frozenset[str]:
        return frozenset(self.ordered_type_ids)

    @property
    def primary_type_id(self) -> str:
        return self.ordered_type_ids[0]

    @property
    def leader_codes(self) -> frozenset[str]:
        return frozenset(code for code, _ in self.leaders)

    @property
    def basket(self) -> tuple[tuple[str, str], ...]:
        """등락을 재는 바스켓. 그날 그 테마에 속했던 종목이다.

        주도주만 쓰면 2022년 이전 기록에는 주도주가 없어 대부분의 과거가
        `기록 없음`이 된다(수집본 실측: 39,696건 중 주도주 16,741건, 당시
        구성종목 39,346건). 구성종목은 그 기록 안에 함께 남아 있는 당시 명단
        이므로 오늘 명단을 과거에 덮어씌우는 것이 아니다.
        """

        return self.members or self.leaders


@dataclass(frozen=True, slots=True)
class ScoredCase:
    case: PastCase
    score: float
    reasons: tuple[str, ...]


def _catalyst_summary(raw_text: str) -> str:
    """원인문에서 문장 끝 종목 나열 괄호만 떼고 사유 문장은 그대로 둔다."""

    text = raw_text.strip()
    for marker in ("(주도주", "(관련주"):
        cut = text.rfind(marker)
        if cut > 0:
            text = text[:cut].strip(" .…")
            break
    return text or raw_text.strip()


def _matched_event_id(source_theme_id: str, source_history_key: str) -> str:
    digest = hashlib.sha256(
        f"{source_theme_id}|{source_history_key}".encode()
    ).hexdigest()
    return f"evt_hist_{source_theme_id}_{digest[:16]}"


def _theme_number(matched_event_id: str) -> str | None:
    parts = matched_event_id.split("_")
    if len(parts) != 4 or parts[0] != "evt" or parts[1] != "hist":
        return None
    return parts[2] or None


def _past_case(
    detail: ThemeDetail, history: ThemeHistory, theme_id: str
) -> PastCase | None:
    if history.event_date is None or not history.raw_text.strip():
        return None
    classification = classify_catalyst(history.raw_text)
    if not classification.type_ids:
        return None
    leaders = tuple(
        (reference.stock_code, reference.name)
        for reference in history.leaders
        if reference.stock_code and reference.name
    )
    members = tuple(
        (reference.stock_code, reference.name)
        for reference in history.member_stocks
        if reference.stock_code and reference.name
    )
    return PastCase(
        matched_event_id=_matched_event_id(
            detail.source_theme_id, history.source_history_key
        ),
        theme_id=theme_id,
        display_name_at_event=detail.theme_name,
        market_date=history.event_date,
        catalyst_summary=_catalyst_summary(history.raw_text),
        ordered_type_ids=classification.type_ids,
        direction=classification.direction,
        certainty=classification.certainty,
        leaders=leaders,
        members=members,
    )


class HistoricalCaseIndex:
    """테마별 과거 사건 색인. 원인문 분류는 테마를 처음 볼 때 한 번만 한다."""

    def __init__(self, details: Iterable[ThemeDetail]) -> None:
        self._details = {detail.source_theme_id: detail for detail in details}
        self._cases: dict[str, tuple[PastCase, ...]] = {}

    @property
    def theme_ids(self) -> frozenset[str]:
        return frozenset(f"thm_{number}" for number in self._details)

    def cases(self, theme_id: str) -> tuple[PastCase, ...]:
        cached = self._cases.get(theme_id)
        if cached is not None:
            return cached
        detail = self._details.get(theme_id.removeprefix("thm_"))
        if detail is None:
            self._cases[theme_id] = ()
            return ()
        cases = tuple(
            case
            for case in (
                _past_case(detail, history, theme_id) for history in detail.history
            )
            if case is not None
        )
        self._cases[theme_id] = cases
        return cases

    def case(self, matched_event_id: str) -> PastCase | None:
        number = _theme_number(matched_event_id)
        if number is None or number not in self._details:
            return None
        for case in self.cases(f"thm_{number}"):
            if case.matched_event_id == matched_event_id:
                return case
        return None


def today_context(
    catalyst_text: str | None, leader_codes: Iterable[str] = ()
) -> TodayContext | None:
    """오늘 원인문을 분류한다. 유형이 하나도 안 붙으면 비교할 축이 없어 None이다."""

    if not catalyst_text or not catalyst_text.strip():
        return None
    classification = classify_catalyst(catalyst_text)
    if not classification.type_ids:
        return None
    return TodayContext(
        ordered_type_ids=classification.type_ids,
        direction=classification.direction,
        certainty=classification.certainty,
        leader_codes=frozenset(code for code in leader_codes if code),
    )


def _score(today: TodayContext, case: PastCase) -> ScoredCase | None:
    shared = today.type_ids & case.type_ids
    if not shared:
        return None
    score = OVERLAP_WEIGHT * (len(shared) / len(today.type_ids | case.type_ids))
    reasons = [
        _TYPE_NAMES[type_id] for type_id in today.ordered_type_ids if type_id in shared
    ]
    if today.primary_type_id == case.primary_type_id:
        score += PRIMARY_WEIGHT
        reasons.insert(0, "같은 주 소재")
    if today.direction == case.direction and case.direction != "UNKNOWN":
        score += DIRECTION_WEIGHT
        reasons.append("같은 방향")
    if today.certainty == case.certainty and case.certainty != "UNSPECIFIED":
        score += CERTAINTY_WEIGHT
    overlap = today.leader_codes & case.leader_codes
    if overlap and today.leader_codes:
        score += LEADER_WEIGHT * (len(overlap) / len(today.leader_codes))
        reasons.append("주도주 중첩")
    return ScoredCase(case=case, score=score, reasons=tuple(reasons))


def rank_cases(
    today: TodayContext,
    cases: Iterable[PastCase],
    *,
    before: date,
    limit: int = MAX_ITEMS,
) -> tuple[ScoredCase, ...]:
    """점수 높은 순, 같으면 최근 순으로 고른다. 오늘 이후 사건은 쓰지 않는다."""

    scored = [
        result
        for result in (
            _score(today, case) for case in cases if case.market_date < before
        )
        if result is not None and result.score >= MIN_SCORE
    ]
    scored.sort(
        key=lambda item: (
            -item.score,
            -item.case.market_date.toordinal(),
            item.case.matched_event_id,
        )
    )
    return tuple(scored[:limit])


def _ratio(value: Decimal | None) -> float | None:
    """corpus는 %로 준다. 계약은 비율이라 100으로 나눈다."""

    return None if value is None else round(float(value) / 100.0, 6)


def _unavailable(reason: str) -> list[dict[str, object]]:
    return [
        {
            "horizonTradingDays": horizon,
            "return": None,
            "status": "UNAVAILABLE",
            "unavailableReason": reason,
        }
        for horizon in HORIZONS
    ]


def _priced_basket(
    case: PastCase, source: OutcomeSource | None
) -> list[tuple[str, str, Mapping[int, Decimal | None]]]:
    """가격이 확인된 당시 구성종목만 (코드, 이름, 기간별 수익률)로 돌려준다."""

    if source is None:
        return []
    priced: list[tuple[str, str, Mapping[int, Decimal | None]]] = []
    for stock_code, name in case.basket:
        _, base_close, computed, _ = source.returns(
            stock_code, case.market_date, HORIZONS
        )
        if base_close is None:
            continue
        priced.append((stock_code, name, computed))
    return priced


def _outcomes(
    case: PastCase,
    source: OutcomeSource | None,
    priced: Sequence[tuple[str, str, Mapping[int, Decimal | None]]] | None = None,
) -> list[dict[str, object]]:
    """가격이 확인된 당시 구성종목을 같은 비중으로 담은 바스켓 수익률."""

    if source is None:
        return _unavailable("NO_PRICE_SOURCE")
    if not case.basket:
        return _unavailable("NO_HISTORICAL_MEMBERS")
    basket = _priced_basket(case, source) if priced is None else list(priced)
    if not basket:
        return _unavailable("NO_PRICE_ON_OR_BEFORE_EVENT")
    rows: list[dict[str, object]] = []
    for horizon in HORIZONS:
        values: list[Decimal] = []
        for _, _, computed in basket:
            observed = computed.get(horizon)
            if observed is not None:
                values.append(observed)
        average = (
            None if not values else sum(values, Decimal(0)) / Decimal(len(values))
        )
        value = _ratio(average)
        rows.append(
            {
                "horizonTradingDays": horizon,
                "return": value,
                # 기준가는 있는데 앞으로의 종가가 모자란 것은 아직 관측이 안
                # 끝난 것이다. 없는 값을 0으로 적지 않는다.
                "status": "OBSERVED" if value is not None else "PENDING",
                "unavailableReason": None,
            }
        )
    return rows


def _summary(items: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """기간마다 유효 분모가 다르다. 한 분모로 합치지 않고 줄마다 따로 센다."""

    rows: list[dict[str, object]] = []
    for horizon in HORIZONS:
        observed: list[float] = []
        for item in items:
            outcomes = item["outcomes"]
            assert isinstance(outcomes, list)
            for outcome in outcomes:
                if (
                    outcome["horizonTradingDays"] == horizon
                    and outcome["status"] == "OBSERVED"
                    and isinstance(outcome["return"], float)
                ):
                    observed.append(outcome["return"])
        rows.append(
            {
                "horizonTradingDays": horizon,
                "eligibleCount": len(items),
                "observedCount": len(observed),
                "positiveCount": sum(1 for value in observed if value > 0),
                "medianReturn": (
                    None if not observed else round(median(observed), 6)
                ),
            }
        )
    return rows


def similar_events_data(
    *,
    event_id: str,
    theme_id: str,
    today: TodayContext | None,
    index: HistoricalCaseIndex,
    outcomes: OutcomeSource | None,
    decision_at: datetime,
    market_date: date,
    limit: int = MAX_ITEMS,
) -> dict[str, object]:
    """`/v1/events/{id}/similar-events` 응답 data를 만든다."""

    if today is None:
        return {
            "eventId": event_id,
            "decisionAt": _timestamp(decision_at),
            "availability": "UNAVAILABLE",
            "summary": [],
            "items": [],
            "page": {"nextCursor": None, "hasMore": False, "limit": limit},
        }
    # 한 건 더 뽑아 잘린 게 있는지 본다. 잘린 것을 hasMore로 알리지 않으면
    # 화면이 이 20건이 전부라고 읽는다.
    ranked = rank_cases(
        today, index.cases(theme_id), before=market_date, limit=limit + 1
    )
    has_more = len(ranked) > limit
    ranked = ranked[:limit]
    items = [
        {
            "matchedEventId": scored.case.matched_event_id,
            "marketDate": scored.case.market_date.isoformat(),
            "displayNameAtEvent": scored.case.display_name_at_event,
            "normalizedCatalystSummary": scored.case.catalyst_summary,
            "similarityReasons": list(scored.reasons),
            "outcomes": _outcomes(scored.case, outcomes),
        }
        for scored in ranked
    ]
    return {
        "eventId": event_id,
        "decisionAt": _timestamp(decision_at),
        "availability": "AVAILABLE",
        "summary": _summary(items),
        "items": items,
        "page": {"nextCursor": None, "hasMore": has_more, "limit": limit},
    }


def historical_event_data(
    *,
    case: PastCase,
    outcomes: OutcomeSource | None,
    today: TodayContext | None = None,
) -> dict[str, object]:
    """`/v1/events/{id}` 과거 사건 상세 data를 만든다."""

    scored = None if today is None else _score(today, case)
    priced = _priced_basket(case, outcomes)
    priced_codes = {stock_code for stock_code, _, _ in priced}
    leader_codes = case.leader_codes
    leaders: list[dict[str, object]] = []
    for stock_code, name in case.basket:
        # 가격을 못 찾은 종목도 목록에서 지우지 않고 `기록 없음`으로 남긴다.
        daily = (
            outcomes.daily_return(stock_code, case.market_date)
            if outcomes is not None and stock_code in priced_codes
            else None
        )
        leaders.append(
            {
                "stockId": f"stk_{stock_code}",
                "symbol": stock_code,
                "name": name,
                "return": _ratio(daily),
                # 그날 주도주로 기록된 종목만 LEADER다. 나머지는 당시 구성종목.
                "role": "LEADER" if stock_code in leader_codes else "RELATED",
            }
        )
    return {
        "eventId": case.matched_event_id,
        "marketDate": case.market_date.isoformat(),
        "displayNameAtEvent": case.display_name_at_event,
        "catalystSummary": case.catalyst_summary,
        "similarityReasons": None if scored is None else list(scored.reasons),
        "leaders": leaders,
        "outcomes": _outcomes(case, outcomes, priced),
        "futureOutcomeExcludedFromSelection": True,
    }


def _timestamp(value: datetime) -> str:
    from datetime import UTC

    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


ONTOLOGY_VERSION = f"{TRANSFORM_VERSION}+vocab/{VOCABULARY_VERSION}"

__all__ = [
    "HORIZONS",
    "MATCH_MODEL_VERSION",
    "MAX_ITEMS",
    "MIN_SCORE",
    "ONTOLOGY_VERSION",
    "HistoricalCaseIndex",
    "OutcomeSource",
    "PastCase",
    "ScoredCase",
    "TodayContext",
    "historical_event_data",
    "rank_cases",
    "similar_events_data",
    "today_context",
]
