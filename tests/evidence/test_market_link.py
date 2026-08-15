from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from importlib import import_module

from packages.catalyst import EvidenceRevisionStore, EvidenceStatus
from packages.domain import (
    DataStatus,
    MembershipRole,
    StockReference,
    ThemeMember,
    ThemeMembershipSnapshot,
)
from packages.events import InMemoryEventStore
from packages.llm import GroundingService
from packages.news import InMemoryNewsStore, NewsIngestor
from packages.pipeline import MarketDataPipeline
from packages.realtime import (
    InMemorySnapshotRepository,
    StockRealtimeUpdate,
    VersionedThemeCatalog,
)
from scripts.validate_contracts import validate_instance

from ._factories import StubLlmClient, grounded_response, raw

pipeline_module = import_module("apps." + "worker-news.pipeline")
market_evidence = import_module("apps." + "worker-news.market_evidence")
EvidencePipeline = pipeline_module.EvidencePipeline
refresh_market_evidence = market_evidence.refresh_market_evidence

KST = timezone(timedelta(hours=9))
MARKET_DATE = date(2026, 8, 14)
KNOWN_AT = datetime(2026, 8, 13, 23, 0, tzinfo=KST)
WINDOW_START = datetime(2026, 8, 13, 15, 30, tzinfo=KST)
OPEN = datetime(2026, 8, 14, 9, 0, tzinfo=KST)
MEMBERSHIP_VERSION = "membership-test-1"
STOCK_DIRECTORY = {"한국원전": "KRX:000001", "원전기자재": "KRX:000002"}
ENTITY_VOCABULARY = ("원전", "수주")

_PRICES: dict[str, tuple[str, str]] = {
    # stock_id: (previous_close, current_price)
    "KRX:000001": ("10000", "10300"),
    "KRX:000002": ("20000", "20200"),
    "KRX:000003": ("40000", "40100"),
    "KRX:000009": ("5000", "5100"),
}


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 8, 14, hour, minute, second, tzinfo=KST)


def _catalog() -> VersionedThemeCatalog:
    return VersionedThemeCatalog(
        (
            ThemeMembershipSnapshot(
                theme_id="thm_nuclear",
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=(
                    ThemeMember("KRX:000001", MembershipRole.CORE),
                    ThemeMember("KRX:000002", MembershipRole.CORE),
                    ThemeMember("KRX:000003", MembershipRole.CORE),
                ),
            ),
            # 관측이 최소 기준에 못 미쳐 ACTIVE로 올라가지 못하는 테마
            ThemeMembershipSnapshot(
                theme_id="thm_thin",
                version=MEMBERSHIP_VERSION,
                effective_from=MARKET_DATE,
                known_at=KNOWN_AT,
                members=(ThemeMember("KRX:000009", MembershipRole.CORE),),
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


def _update(stock_id: str, *, seconds: int) -> StockRealtimeUpdate:
    _, current_price = _PRICES[stock_id]
    occurred = OPEN + timedelta(seconds=seconds)
    return StockRealtimeUpdate(
        message_id=f"msg_{stock_id}_{seconds}",
        stock_id=stock_id,
        market_date=MARKET_DATE,
        source="test-session",
        source_sequence=seconds,
        occurred_at=occurred,
        received_at=occurred,
        current_price=Decimal(current_price),
        cumulative_trading_value=Decimal("1000000"),
    )


def _active_market() -> MarketDataPipeline:
    """thm_nuclear가 ACTIVE로 올라간 파이프라인."""

    market = MarketDataPipeline(
        market_date=MARKET_DATE,
        stream_id="stream_test",
        schema_version="2026-08-14.1",
        catalog=_catalog(),
        references=_references(),
        membership_version=MEMBERSHIP_VERSION,
        theme_names={"thm_nuclear": "원전", "thm_thin": "빈약 테마"},
        stock_names={
            "KRX:000001": "한국원전",
            "KRX:000002": "원전기자재",
            "KRX:000003": "기타종목",
            "KRX:000009": "빈약종목",
        },
        event_store=InMemoryEventStore(),
        snapshot_repository=InMemorySnapshotRepository(),
    )
    for index, stock_id in enumerate(_PRICES):
        market.apply_update(_update(stock_id, seconds=index + 1))
    market.publish(now=OPEN + timedelta(seconds=7), data_status=DataStatus.LIVE)
    market.publish(now=OPEN + timedelta(seconds=20), data_status=DataStatus.LIVE)
    return market


def _evidence_pipeline(
    revisions: EvidenceRevisionStore,
    store: InMemoryNewsStore,
) -> object:
    return EvidencePipeline(
        store=store,
        ingestor=NewsIngestor(
            store,
            stock_directory=STOCK_DIRECTORY,
            entity_vocabulary=ENTITY_VOCABULARY,
        ),
        grounding=GroundingService(
            StubLlmClient(
                [
                    grounded_response(
                        stock_ids=("KRX:000001",),
                        theme_ids=("thm_nuclear",),
                    )
                ]
            )
        ),
        revisions=revisions,
    )


def _ranking_item(market: MarketDataPipeline, *, now: datetime) -> dict[str, object]:
    view = market.publish(now=now, data_status=DataStatus.LIVE)
    items = view.rankings.payload["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, dict)
    validate_instance(item, "RankingItem", label="rankings")
    return item


def test_only_active_events_are_offered_to_evidence_matching() -> None:
    market = _active_market()

    contexts = market.active_theme_contexts()

    assert [context.theme_id for context in contexts] == ["thm_nuclear"]
    context = contexts[0]
    assert context.event_id == _ranking_item(market, now=at(9, 1))["eventId"]
    assert context.display_name == "원전"
    assert context.market_date == MARKET_DATE
    assert context.activated_at == OPEN + timedelta(seconds=20)
    # 수익률 최고 종목(000001: +3%)이 주도주, 나머지 구성종목은 관련주
    assert context.leader_stock_ids == ("KRX:000001",)
    assert context.leader_names == ("한국원전",)
    assert context.related_stock_ids == ("KRX:000002", "KRX:000003")


def test_stored_news_becomes_the_published_evidence_status_of_the_active_event() -> None:
    market = _active_market()
    revisions = EvidenceRevisionStore()
    store = InMemoryNewsStore()
    evidence = _evidence_pipeline(revisions, store)
    NewsIngestor(
        store,
        stock_directory=STOCK_DIRECTORY,
        entity_vocabulary=ENTITY_VOCABULARY,
    ).ingest(
        [raw(published_at=at(9, 3), retrieved_at=at(9, 4))],
        now=at(9, 4),
        window_start=WINDOW_START,
    )

    outcomes = refresh_market_evidence(
        evidence,
        market,
        now=at(9, 5),
        window_start=WINDOW_START,
    )

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.llm_called is True
    assert outcome.revision.evidence_status is EvidenceStatus.SINGLE_SOURCE
    assert [match.matched_stock_ids for match in outcome.matches] == [("KRX:000001",)]
    assert revisions.current(outcome.event_id) == outcome.revision

    assert _ranking_item(market, now=at(9, 5))["evidence"] == {
        "evidenceStatus": "SINGLE_SOURCE",
        "summary": "신규 원전 수주 기대 관련 보도",
        "publishedAt": at(9, 3).isoformat(),
    }


def test_evidence_status_transitions_are_kept_as_revisions() -> None:
    market = _active_market()
    revisions = EvidenceRevisionStore()
    evidence = _evidence_pipeline(revisions, InMemoryNewsStore())

    searching = refresh_market_evidence(
        evidence, market, now=at(9, 5), window_start=WINDOW_START
    )[0]
    # 활성화 20분이 지나도 관련 기사가 없으면 소재 없음으로 넘어간다
    no_catalyst = refresh_market_evidence(
        evidence, market, now=at(9, 25), window_start=WINDOW_START
    )[0]

    assert searching.llm_called is False
    assert searching.revision.evidence_status is EvidenceStatus.SEARCHING
    assert no_catalyst.revision.evidence_status is EvidenceStatus.NO_NEW_CATALYST
    history = revisions.history(searching.event_id)
    assert [(item.revision, item.evidence_status) for item in history] == [
        (1, EvidenceStatus.SEARCHING),
        (2, EvidenceStatus.NO_NEW_CATALYST),
    ]

    assert _ranking_item(market, now=at(9, 25))["evidence"] == {
        "evidenceStatus": "NO_NEW_CATALYST",
        "summary": None,
        "publishedAt": None,
    }
