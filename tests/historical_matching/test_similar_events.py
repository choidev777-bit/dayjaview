from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Mapping, Sequence

from packages.historical_matching import (
    CatalystGroupIndex,
    HistoricalCaseIndex,
    catalyst_detail_data,
    catalyst_top3_data,
    historical_event_data,
    similar_events_data,
    today_context,
)
from packages.infostock.models import (
    RawSnapshot,
    StockReference,
    ThemeDetail,
    ThemeHistory,
)

KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)
MARKET_DATE = date(2026, 8, 14)
TODAY = "美 필라델피아 반도체 지수 급등 및 인텔 대규모 신규시설 투자 결정 등에 상승"


def _stock(order: int, code: str, name: str) -> StockReference:
    return StockReference(
        source_order=order,
        name=name,
        stock_code=code,
        source_url=None,
        display_value=name,
        quality_status="OK",
    )


def _history(
    order: int,
    event_date: date,
    raw_text: str,
    members: tuple[tuple[str, str], ...] = (("000001", "가나전자"),),
    leaders: tuple[tuple[str, str], ...] = (),
) -> ThemeHistory:
    return ThemeHistory(
        source_order=order,
        source_history_id=str(order),
        source_history_key=f"history-{order}",
        event_date=event_date,
        source_date=event_date.isoformat(),
        source_created_at=None,
        source_updated_at=None,
        raw_text=raw_text,
        direction="UP",
        leaders=tuple(
            _stock(index, code, name) for index, (code, name) in enumerate(leaders)
        ),
        member_stocks=tuple(
            _stock(index, code, name) for index, (code, name) in enumerate(members)
        ),
        author=None,
        chart_flag=None,
        source_fingerprint=f"fingerprint-{order}",
        quality_status="OK",
        content_hash=f"hash-{order}",
    )


def _detail(history: tuple[ThemeHistory, ...]) -> ThemeDetail:
    return ThemeDetail(
        source_theme_id="12",
        theme_name="반도체 장비",
        description="",
        theme_revision_hash="revision-12",
        history=history,
        memberships=(),
        snapshot=RawSnapshot(
            page_type="THEME_DETAIL",
            source_entity_id="12",
            source_url="https://infostock.co.kr/Theme/ThemeDB/12",
            collected_at=KNOWN_AT,
            as_of=KNOWN_AT,
            raw_hash="0" * 64,
            source_content_hash=None,
            raw_payload_text="{}",
            raw_format="JSON",
            is_complete=True,
        ),
    )


class _StubOutcomes:
    """사건일마다 정해진 값을 주는 corpus 대역."""

    def __init__(self, values: Mapping[date, Decimal | None]) -> None:
        self._values = values

    def returns(
        self, stock_code: str, occurred_on: date, horizons: Sequence[int]
    ) -> tuple[
        date | None, Decimal | None, Mapping[int, Decimal | None], str | None
    ]:
        value = self._values.get(occurred_on)
        if value is None:
            return None, None, {horizon: None for horizon in horizons}, "NO_PRICE"
        return (
            occurred_on,
            Decimal("10000"),
            {horizon: value * horizon for horizon in horizons},
            None,
        )

    def daily_return(self, stock_code: str, on: date) -> Decimal | None:
        return self._values.get(on)


_CASES = (
    # 오늘과 같은 유형(해외 지수 동조 + 투자·증설)
    _history(
        1,
        date(2022, 3, 16),
        "필라델피아 반도체지수 급등 및 인텔 대규모 투자 계획 발표 등에 상승",
        members=(("000001", "가나전자"), ("000002", "다라소재")),
        leaders=(("000001", "가나전자"),),
    ),
    # 소재가 겹치지 않는 사건. 같은 테마라도 후보가 되어서는 안 된다.
    _history(
        2,
        date(2023, 4, 10),
        "대표이사 횡령 혐의 검찰 수사 소식에 하락",
    ),
    # 오늘 이후 사건. 미래를 과거 사례로 쓰면 안 된다.
    _history(
        3,
        date(2026, 9, 1),
        "필라델피아 반도체지수 급등 및 대규모 증설 투자 발표 등에 상승",
    ),
)


def _index() -> HistoricalCaseIndex:
    return HistoricalCaseIndex((_detail(_CASES),))


def _document(outcomes: _StubOutcomes | None) -> dict[str, object]:
    return similar_events_data(
        event_id="evt_today",
        theme_id="thm_12",
        today=today_context(TODAY, ["000001"]),
        index=_index(),
        outcomes=outcomes,
        decision_at=datetime(2026, 8, 14, 6, 20, tzinfo=UTC),
        market_date=MARKET_DATE,
    )


def test_only_events_sharing_a_catalyst_type_before_today_become_cases() -> None:
    data = _document(_StubOutcomes({date(2022, 3, 16): Decimal("1.5")}))

    assert data["availability"] == "AVAILABLE"
    dates = [item["marketDate"] for item in data["items"]]
    assert dates == ["2022-03-16"]
    reasons = data["items"][0]["similarityReasons"]
    assert "같은 주 소재" in reasons
    assert "주도주 중첩" in reasons


def test_unclassifiable_today_catalyst_reports_unavailable_instead_of_guessing() -> None:
    data = similar_events_data(
        event_id="evt_today",
        theme_id="thm_12",
        today=today_context("   "),
        index=_index(),
        outcomes=None,
        decision_at=datetime(2026, 8, 14, 6, 20, tzinfo=UTC),
        market_date=MARKET_DATE,
    )

    assert data["availability"] == "UNAVAILABLE"
    assert data["items"] == []


def test_summary_counts_each_horizon_on_its_own_denominator() -> None:
    data = _document(_StubOutcomes({date(2022, 3, 16): Decimal("1.5")}))
    rows = {row["horizonTradingDays"]: row for row in data["summary"]}

    assert rows[1]["eligibleCount"] == 1
    assert rows[1]["observedCount"] == 1
    assert rows[1]["positiveCount"] == 1
    assert rows[1]["medianReturn"] == 0.015
    assert rows[20]["medianReturn"] == 0.3


def test_missing_prices_stay_missing_and_never_become_zero() -> None:
    data = _document(_StubOutcomes({}))
    outcomes = data["items"][0]["outcomes"]

    assert [row["return"] for row in outcomes] == [None, None, None]
    assert {row["status"] for row in outcomes} == {"UNAVAILABLE"}
    rows = {row["horizonTradingDays"]: row for row in data["summary"]}
    assert rows[5]["observedCount"] == 0
    assert rows[5]["medianReturn"] is None


def test_future_outcomes_do_not_change_which_cases_are_selected() -> None:
    winners = _document(_StubOutcomes({date(2022, 3, 16): Decimal("9.9")}))
    losers = _document(_StubOutcomes({date(2022, 3, 16): Decimal("-9.9")}))

    assert [item["matchedEventId"] for item in winners["items"]] == [
        item["matchedEventId"] for item in losers["items"]
    ]


def test_requested_limit_is_honoured_and_the_rest_is_flagged() -> None:
    """서버가 limit보다 많이 실어 보내면 API가 cursor 없는 절단으로 500을 낸다."""

    crowded = HistoricalCaseIndex(
        (
            _detail(
                tuple(
                    _history(
                        order,
                        date(2020 + order, 3, 16),
                        "필라델피아 반도체지수 급등 및 인텔 대규모 투자 계획 발표 등에 상승",
                    )
                    for order in range(4)
                )
            ),
        )
    )
    data = similar_events_data(
        event_id="evt_today",
        theme_id="thm_12",
        today=today_context(TODAY),
        index=crowded,
        outcomes=None,
        decision_at=datetime(2026, 8, 14, 6, 20, tzinfo=UTC),
        market_date=MARKET_DATE,
        limit=2,
    )

    assert len(data["items"]) == 2
    assert data["page"] == {"nextCursor": None, "hasMore": True, "limit": 2}


def test_historical_event_detail_marks_the_basket_and_the_exclusion() -> None:
    index = _index()
    matched = _document(None)["items"][0]["matchedEventId"]
    case = index.case(matched)
    assert case is not None

    detail = historical_event_data(
        case=case,
        outcomes=_StubOutcomes({date(2022, 3, 16): Decimal("1.5")}),
        today=today_context(TODAY, ["000001"]),
    )

    assert detail["futureOutcomeExcludedFromSelection"] is True
    assert detail["catalystSummary"].startswith("필라델피아 반도체지수 급등")
    # 당시 주도주로 기록된 종목만 LEADER다. 나머지 당시 구성종목은 RELATED로 남는다.
    assert [leader["role"] for leader in detail["leaders"]] == ["LEADER", "RELATED"]
    assert detail["similarityReasons"] is not None


class _StubSameDay:
    """사건일마다 정해진 당일 반응(%)을 주는 corpus 대역."""

    def __init__(self, values: Mapping[date, Decimal | None]) -> None:
        self._values = values

    def basket_daily_return(
        self, stock_codes: Sequence[str], on: date
    ) -> Decimal | None:
        return None if not stock_codes else self._values.get(on)


def _group_index(same_day: Mapping[date, Decimal | None]) -> CatalystGroupIndex:
    """같은 유형 6건과 표본 부족 유형 2건을 가진 테마."""

    history = tuple(
        _history(
            order,
            date(2015 + order, 3, 16),
            "필라델피아 반도체지수 급등 및 인텔 대규모 투자 계획 발표 등에 상승",
        )
        for order in range(6)
    ) + tuple(
        _history(
            10 + order,
            date(2015 + order, 7, 1),
            "대표이사 횡령 혐의 검찰 수사 소식에 하락",
        )
        for order in range(2)
    )
    return CatalystGroupIndex(
        HistoricalCaseIndex((_detail(history),)), _StubSameDay(same_day)
    )


_SAME_DAY = {date(2015 + order, 3, 16): Decimal(order + 1) for order in range(6)}


def test_only_types_with_enough_samples_enter_the_ranking() -> None:
    groups = _group_index(_SAME_DAY)
    data = catalyst_top3_data(
        theme_id="thm_12",
        event_id="evt_today",
        today=today_context(TODAY),
        groups=groups,
    )

    # 표본 2건짜리 `소송·수사·논란`은 한 번의 급등이 순위를 지배하므로 빠진다.
    assert [item["catalystName"] for item in data["items"]] == ["시장·주가 동조"]
    item = data["items"][0]
    assert item["observedCount"] == 6
    assert item["medianSameDayReturn"] == 0.035
    assert item["matchesToday"] is True


def test_empty_ranking_says_why_instead_of_padding() -> None:
    data = catalyst_top3_data(
        theme_id="thm_12",
        event_id="evt_today",
        today=today_context(TODAY),
        groups=_group_index({}),
    )

    assert data["items"] == []
    assert data["qualityNote"] is not None


def test_catalyst_detail_keeps_the_whole_sample_as_the_denominator() -> None:
    groups = _group_index(_SAME_DAY)
    group = groups.groups("thm_12")[0]

    detail = catalyst_detail_data(
        group=group,
        theme_display_name="반도체 장비",
        outcomes=None,
        limit=2,
    )

    # 목록만 2건으로 자르고 분모는 6건 그대로여야 TOP3의 중앙값과 어긋나지 않는다.
    assert len(detail["events"]) == 2
    assert detail["sameDay"]["eligibleCount"] == 6
    assert detail["sameDay"]["observedCount"] == 6
    assert detail["sameDay"]["medianReturn"] == 0.035
    assert all(row["eligibleCount"] == 6 for row in detail["horizons"])
