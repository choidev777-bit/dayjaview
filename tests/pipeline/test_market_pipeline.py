from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from packages.catalyst import (
    CatalystEvidence,
    EvidenceRevision,
    EvidenceStatus,
    ExtractionMethod,
    MatchBasis,
)
from packages.domain import (
    DataStatus,
    MembershipRole,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.events import InMemoryEventStore
from packages.pipeline import MarketDataPipeline
from packages.realtime import (
    InMemorySnapshotRepository,
    StockRealtimeUpdate,
    VersionedThemeCatalog,
)
from scripts.validate_contracts import validate_instance

MARKET_DATE = date(2026, 8, 14)
KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=UTC)
BASE = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
MEMBERSHIP_VERSION = "membership-test-1"

_PRICES: dict[str, tuple[str, str]] = {
    # stock_id: (previous_close, current_price)
    "KRX:000001": ("10000", "10300"),
    "KRX:000002": ("20000", "20200"),
    "KRX:000003": ("40000", "40100"),
    "KRX:000009": ("5000", "5100"),
}


def _catalog() -> VersionedThemeCatalog:
    return VersionedThemeCatalog(
        (
            ThemeMembershipSnapshot(
                theme_id="thm_full",
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=(
                    ThemeMember("KRX:000001", MembershipRole.CORE),
                    ThemeMember("KRX:000002", MembershipRole.CORE),
                    ThemeMember("KRX:000003", MembershipRole.CORE),
                ),
            ),
            # 관측이 최소 기준(3)에 못 미쳐 rankings에서 제외되어야 하는 테마
            ThemeMembershipSnapshot(
                theme_id="thm_thin",
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=(
                    ThemeMember("KRX:000009", MembershipRole.CORE),
                ),
            ),
        )
    )


def _references() -> tuple[StockReference, ...]:
    return tuple(
        StockReference(
            stock_id=stock_id,
            effective_for=MARKET_DATE,
            known_at=KNOWN_AT,
            previous_adjusted_close=Decimal(previous_close),
            listed_shares=1_000_000,
            free_float_ratio=Decimal("0.5"),
            free_float_validated=True,
            version="reference-test-1",
        )
        for stock_id, (previous_close, _) in _PRICES.items()
    )


def _pipeline() -> MarketDataPipeline:
    return MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_test",
        schema_version="2026-08-14.1",
        catalog=_catalog(),
        references=_references(),
        membership_version=MEMBERSHIP_VERSION,
        theme_names={"thm_full": "테스트 테마", "thm_thin": "빈약 테마"},
        stock_names={stock_id: stock_id for stock_id in _PRICES},
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )


def _update(stock_id: str, *, seconds: int) -> StockRealtimeUpdate:
    _, current_price = _PRICES[stock_id]
    at = BASE + timedelta(seconds=seconds)
    return StockRealtimeUpdate(
        message_id=f"msg_{stock_id}_{seconds}",
        stock_id=stock_id,
        market_date=MARKET_DATE,
        source="test-session",
        source_sequence=seconds,
        occurred_at=at,
        received_at=at,
        current_price=Decimal(current_price),
        cumulative_trading_value=Decimal("1000000"),
    )


def _expected_weighted_return() -> float:
    # 3종목 유동시총이 모두 상한(1/3)에 걸려 동일가중 평균과 같다.
    returns = [
        Decimal(current) / Decimal(previous) - Decimal(1)
        for stock_id, (previous, current) in _PRICES.items()
        if stock_id != "KRX:000009"
    ]
    return float(sum(returns) / Decimal(3))


def test_market_updates_flow_into_active_event_and_ranked_snapshot() -> None:
    pipeline = _pipeline()
    for index, stock_id in enumerate(
        ("KRX:000001", "KRX:000002", "KRX:000003", "KRX:000009")
    ):
        result = pipeline.apply_update(_update(stock_id, seconds=index + 1))
        assert result.changed

    first = pipeline.publish(
        now=BASE + timedelta(seconds=7),
        data_status=DataStatus.LIVE,
    )
    # 첫 발행: hysteresis activate_after(10초) 이전이라 아직 후보 상태
    assert first.rankings.payload == {"items": []}
    assert {event.lifecycle_status.value for event in first.events} == {"CANDIDATE"}

    second = pipeline.publish(
        now=BASE + timedelta(seconds=20),
        data_status=DataStatus.LIVE,
    )
    items = second.rankings.payload["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    item = items[0]
    assert item["lifecycleStatus"] == "ACTIVE"
    assert item["rank"] == 1
    assert item["classification"]["themeId"] == "thm_full"
    assert item["classification"]["displayName"] == "테스트 테마"
    assert item["weightedReturn"] == _expected_weighted_return()
    assert item["advancingCount"] == 3
    assert item["validCount"] == 3
    assert item["coverage"]["status"] == "SUFFICIENT"
    assert item["evidence"] == {
        "evidenceStatus": "SEARCHING",
        "summary": None,
        "publishedAt": None,
    }
    # 수익률 최고 종목(000001: +3%)이 주도주
    assert item["leader"]["stockId"] == "KRX:000001"
    assert item["leader"]["symbol"] == "000001"

    assert second.rankings.sequence == 2
    assert second.rankings.stream_id == "stream_test"
    assert second.rankings.data_status is DataStatus.LIVE

    treemap_items = second.treemap.payload["items"]
    assert isinstance(treemap_items, list)
    assert treemap_items == [
        {
            "eventId": item["eventId"],
            "themeId": "thm_full",
            "displayName": "테스트 테마",
            "lifecycleStatus": "ACTIVE",
            "weightedReturn": item["weightedReturn"],
            "advancingCount": 3,
            "validCount": 3,
            "coverageStatus": "SUFFICIENT",
            "qualityFlags": [],
        }
    ]

    # 최소 관측 미달 테마는 후보로만 남고 rankings·treemap 어디에도 없다
    statuses = {
        event.canonical_theme_id: event.lifecycle_status.value
        for event in second.events
    }
    assert statuses["thm_thin"] == "CANDIDATE"
    assert statuses["thm_full"] == "ACTIVE"


def test_theme_detail_matches_contract_and_lists_top_three_leaders() -> None:
    pipeline = _pipeline()
    for index, stock_id in enumerate(
        ("KRX:000001", "KRX:000002", "KRX:000003", "KRX:000009")
    ):
        pipeline.apply_update(_update(stock_id, seconds=index + 1))
    pipeline.publish(now=BASE + timedelta(seconds=7), data_status=DataStatus.LIVE)
    view = pipeline.publish(
        now=BASE + timedelta(seconds=20),
        data_status=DataStatus.LIVE,
    )
    items = view.rankings.payload["items"]
    assert isinstance(items, list)
    event_id = items[0]["eventId"]

    detail = pipeline.theme_detail(event_id)
    assert detail is not None
    validate_instance(detail, "ThemeDetailData", label="theme-detail")

    assert detail["eventId"] == event_id
    assert detail["marketDate"] == "2026-08-14"
    assert detail["lifecycleStatus"] == "ACTIVE"
    assert detail["canonicalPath"] == f"/v1/themes/thm_full/events/{event_id}"
    assert detail["coverage"] == items[0]["coverage"]
    assert detail["currentReaction"]["weightedReturn"] == items[0]["weightedReturn"]
    # 장중 이력을 축적하지 않는 조립에서는 기준선이 없어 정직하게 null이다.
    assert detail["currentReaction"]["turnoverMultiple"] is None
    assert detail["currentReaction"]["attentionGapTradingDays"] is None
    assert detail["evidenceSummary"] == {
        "evidenceStatus": "SEARCHING",
        "summary": None,
        "sourceCount": 0,
        "latestPublishedAt": None,
    }
    # 유사사례는 E-19 통과 전까지 잠겨 있다.
    assert detail["historicalAccess"]["status"] == "GATED"

    leaders = detail["leaders"]
    assert isinstance(leaders, list)
    # 관측 CORE 3종목이 수익률 내림차순(+3%, +1%, +0.25%)으로 나온다.
    assert [leader["stockId"] for leader in leaders] == [
        "KRX:000001",
        "KRX:000002",
        "KRX:000003",
    ]
    assert all(leader["role"] == "LEADER" for leader in leaders)
    # rankings의 단일 주도주는 상세 leaders의 첫 종목과 같다.
    assert items[0]["leader"]["stockId"] == leaders[0]["stockId"]
    assert items[0]["leader"]["return"] == leaders[0]["return"]

    assert pipeline.theme_id_for_event(event_id) == "thm_full"
    assert pipeline.theme_id_for_event("evt_unknown") is None


def _rank_by_theme(view: object) -> dict[str, dict[str, object]]:
    items = view.rankings.payload["items"]  # type: ignore[attr-defined]
    assert isinstance(items, list)
    return {item["classification"]["themeId"]: item for item in items}


def test_rank_change_and_rising_badge_come_from_earlier_publications() -> None:
    """60초 전 순위·5분 전 수익률과 비교해 순위 변화와 급부상 배지를 낸다."""

    catalog = VersionedThemeCatalog(
        tuple(
            ThemeMembershipSnapshot(
                theme_id=theme_id,
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=tuple(
                    ThemeMember(f"KRX:{theme_index}0000{offset}", MembershipRole.CORE)
                    for offset in (1, 2, 3)
                ),
            )
            for theme_index, theme_id in enumerate(("thm_a", "thm_b", "thm_c", "thm_d"))
        )
    )
    stock_ids = tuple(
        f"KRX:{theme_index}0000{offset}"
        for theme_index in range(4)
        for offset in (1, 2, 3)
    )
    pipeline = MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_rank_change",
        schema_version="2026-08-14.1",
        catalog=catalog,
        references=tuple(
            StockReference(
                stock_id=stock_id,
                effective_for=MARKET_DATE,
                known_at=KNOWN_AT,
                previous_adjusted_close=Decimal("10000"),
                listed_shares=1_000_000,
                free_float_ratio=Decimal("0.5"),
                free_float_validated=True,
                version="reference-test-1",
            )
            for stock_id in stock_ids
        ),
        membership_version=MEMBERSHIP_VERSION,
        theme_names={
            theme_id: theme_id for theme_id in ("thm_a", "thm_b", "thm_c", "thm_d")
        },
        stock_names={stock_id: stock_id for stock_id in stock_ids},
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )

    def feed(prices: dict[str, str], *, seconds: int) -> None:
        at = BASE + timedelta(seconds=seconds)
        for theme_index, theme_id in enumerate(("thm_a", "thm_b", "thm_c", "thm_d")):
            for offset in (1, 2, 3):
                stock_id = f"KRX:{theme_index}0000{offset}"
                pipeline.apply_update(
                    StockRealtimeUpdate(
                        message_id=f"msg_{stock_id}_{seconds}",
                        stock_id=stock_id,
                        market_date=MARKET_DATE,
                        source="test-session",
                        source_sequence=seconds,
                        occurred_at=at,
                        received_at=at,
                        current_price=Decimal(prices[theme_id]),
                        cumulative_trading_value=Decimal("1000000"),
                    )
                )

    # thm_d가 꼴찌인 상태로 5분 넘게 발행한다.
    feed(
        {"thm_a": "10400", "thm_b": "10300", "thm_c": "10200", "thm_d": "10100"},
        seconds=1,
    )
    pipeline.publish(now=BASE + timedelta(seconds=7), data_status=DataStatus.LIVE)
    first = pipeline.publish(
        now=BASE + timedelta(seconds=20),
        data_status=DataStatus.LIVE,
    )
    assert [item["rank"] for item in first.rankings.payload["items"]] == [1, 2, 3, 4]
    # 첫 발행에는 비교할 60초 전 기록이 없다. 0이 아니라 null이다.
    assert {item["rankChange60s"] for item in first.rankings.payload["items"]} == {None}
    assert {tuple(item["badges"]) for item in first.rankings.payload["items"]} == {()}

    pipeline.publish(now=BASE + timedelta(seconds=310), data_status=DataStatus.LIVE)

    # thm_d가 1위로 올라선다: 순위 3계단 상승 + 5분 전보다 높은 수익률.
    feed(
        {"thm_a": "10400", "thm_b": "10300", "thm_c": "10200", "thm_d": "10900"},
        seconds=320,
    )
    view = pipeline.publish(
        now=BASE + timedelta(seconds=380),
        data_status=DataStatus.LIVE,
    )
    ranked = _rank_by_theme(view)
    assert ranked["thm_d"]["rank"] == 1
    assert ranked["thm_d"]["rankChange60s"] == 3
    assert ranked["thm_d"]["badges"] == ["RISING_FAST"]
    # 밀려난 테마는 음수 변화만 남고 배지는 붙지 않는다.
    assert ranked["thm_a"]["rankChange60s"] == -1
    assert ranked["thm_a"]["badges"] == []

    # 다음 발행에서는 순위가 그대로라 급부상이 아니다.
    steady = _rank_by_theme(
        pipeline.publish(now=BASE + timedelta(seconds=450), data_status=DataStatus.LIVE)
    )
    assert steady["thm_d"]["rankChange60s"] == 0
    assert steady["thm_d"]["badges"] == []


def test_theme_detail_is_hidden_before_the_event_becomes_public() -> None:
    pipeline = _pipeline()
    for index, stock_id in enumerate(("KRX:000001", "KRX:000002", "KRX:000003")):
        pipeline.apply_update(_update(stock_id, seconds=index + 1))
    view = pipeline.publish(
        now=BASE + timedelta(seconds=7),
        data_status=DataStatus.LIVE,
    )
    candidate = next(
        event for event in view.events if event.canonical_theme_id == "thm_full"
    )
    assert candidate.lifecycle_status.value == "CANDIDATE"
    # 테마는 알지만 아직 공개 상태가 아니므로 상세 문서는 없다.
    assert pipeline.theme_id_for_event(candidate.event_id) == "thm_full"
    assert pipeline.theme_detail(candidate.event_id) is None
    assert pipeline.theme_detail("evt_unknown") is None


def test_recorded_evidence_reaches_both_rankings_and_detail() -> None:
    pipeline = _pipeline()
    for index, stock_id in enumerate(("KRX:000001", "KRX:000002", "KRX:000003")):
        pipeline.apply_update(_update(stock_id, seconds=index + 1))
    pipeline.publish(now=BASE + timedelta(seconds=7), data_status=DataStatus.LIVE)
    first = pipeline.publish(
        now=BASE + timedelta(seconds=20),
        data_status=DataStatus.LIVE,
    )
    items = first.rankings.payload["items"]
    assert isinstance(items, list)
    event_id = items[0]["eventId"]
    published_at = datetime(2026, 8, 14, 1, 17, tzinfo=UTC)

    pipeline.record_evidence(
        EvidenceRevision(
            event_id=event_id,
            revision=1,
            evidence_status=EvidenceStatus.SINGLE_SOURCE,
            summary="테스트 소재 보도",
            news_ids=("news_1",),
            catalyst_key="TEST_CATALYST",
            reason="테스트 근거 1건 확인",
            policy_version="catalyst-evidence-2026.08.1",
            decided_at=BASE + timedelta(seconds=25),
            evidence_confirmed_at=BASE + timedelta(seconds=25),
        ),
        (
            CatalystEvidence(
                news_id="news_1",
                event_id=event_id,
                publisher="테스트매체",
                title="테스트 소재 보도",
                summary="테스트 소재 보도 요약",
                match_basis=(MatchBasis.THEME,),
                entities=("테스트 테마",),
                published_at=published_at,
                received_at=published_at + timedelta(minutes=1),
                original_url="https://news.example.com/1",
                quality_flags=(),
                extraction_method=ExtractionMethod.LLM_GROUNDED,
                model_name="stub-grounding-model",
                prompt_version="catalyst-grounding-2026.08.1",
                confidence=0.9,
                generated_at=published_at + timedelta(minutes=2),
            ),
        ),
    )
    view = pipeline.publish(
        now=BASE + timedelta(seconds=30),
        data_status=DataStatus.LIVE,
    )

    ranking_items = view.rankings.payload["items"]
    assert isinstance(ranking_items, list)
    # rankings는 RankingEvidence(publishedAt) 모양을 유지한다.
    assert ranking_items[0]["evidence"] == {
        "evidenceStatus": "SINGLE_SOURCE",
        "summary": "테스트 소재 보도",
        "publishedAt": published_at.isoformat(),
    }
    detail = pipeline.theme_detail(event_id)
    assert detail is not None
    validate_instance(detail, "ThemeDetailData", label="theme-detail-evidence")
    # 상세는 sourceCount·latestPublishedAt까지 싣는다.
    assert detail["evidenceSummary"] == {
        "evidenceStatus": "SINGLE_SOURCE",
        "summary": "테스트 소재 보도",
        "sourceCount": 1,
        "latestPublishedAt": published_at.isoformat(),
    }


def test_close_market_discards_candidates_and_closes_active_events() -> None:
    pipeline = _pipeline()
    for index, stock_id in enumerate(
        ("KRX:000001", "KRX:000002", "KRX:000003", "KRX:000009")
    ):
        pipeline.apply_update(_update(stock_id, seconds=index + 1))
    pipeline.publish(now=BASE + timedelta(seconds=7), data_status=DataStatus.LIVE)
    pipeline.publish(now=BASE + timedelta(seconds=20), data_status=DataStatus.LIVE)

    pipeline.close_market(now=BASE + timedelta(hours=7))
    view = pipeline.publish(
        now=BASE + timedelta(hours=7, seconds=1),
        data_status=DataStatus.CLOSED,
    )

    statuses = {
        event.canonical_theme_id: event.lifecycle_status.value
        for event in view.events
    }
    assert statuses == {"thm_full": "CLOSED", "thm_thin": "DISCARDED"}
    # 마감 뒤에도 그날 최종 순위는 남는다 (screen_spec 4.1·5.7 최종값 고정).
    # DISCARDED로 끝난 후보는 공개된 적이 없으므로 그대로 빠진다.
    items = view.rankings.payload["items"]
    assert [item["classification"]["themeId"] for item in items] == ["thm_full"]
    assert items[0]["lifecycleStatus"] == "CLOSED"

    # 이미 종결된 테마는 다시 닫아도 상태가 변하지 않는다.
    pipeline.close_market(now=BASE + timedelta(hours=8))
    assert {
        event.canonical_theme_id: event.lifecycle_status.value
        for event in pipeline.current_events()
    } == statuses


def test_duplicate_updates_do_not_change_published_values() -> None:
    pipeline = _pipeline()
    update = _update("KRX:000001", seconds=1)
    assert pipeline.apply_update(update).changed
    duplicate = pipeline.apply_update(update)
    assert not duplicate.changed

    view = pipeline.publish(
        now=BASE + timedelta(seconds=7),
        data_status=DataStatus.LIVE,
    )
    assert view.rankings.sequence == 1
