from __future__ import annotations

from datetime import datetime, timedelta

from packages.catalyst import (
    CatalystEvidence,
    EvidenceRevisionStore,
    EvidenceStatus,
    ExtractionMethod,
    MatchBasis,
    MatchTrigger,
    SupplementalDenial,
    SupplementalSearchGate,
    ThemeContext,
    catalyst_key,
    decide,
    match_news_to_events,
    match_theme_to_news,
)
from packages.news import InMemoryNewsStore, NewsIngestor, NewsItem

from ._factories import (
    ENTITY_VOCABULARY,
    STOCK_DIRECTORY,
    WINDOW_START,
    at,
    bio_context,
    nuclear_context,
    raw,
)


def stored(*raw_items) -> tuple[NewsItem, ...]:
    store = InMemoryNewsStore()
    ingestor = NewsIngestor(
        store,
        stock_directory=STOCK_DIRECTORY,
        entity_vocabulary=ENTITY_VOCABULARY,
    )
    return ingestor.ingest(list(raw_items), now=at(10, 10), window_start=WINDOW_START).stored


def evidence(
    *,
    news_id: str = "news_1",
    publisher: str = "예시 언론사",
    entities: tuple[str, ...] = ("원전",),
    summary: str = "신규 원전 수주 기대 관련 보도",
    published_at: datetime | None = None,
) -> CatalystEvidence:
    return CatalystEvidence(
        news_id=news_id,
        event_id="evt_nuclear",
        publisher=publisher,
        title="[특징주] 한국원전 강세",
        summary=summary,
        match_basis=(MatchBasis.THEME, MatchBasis.STOCK),
        entities=entities,
        published_at=published_at if published_at is not None else at(10, 8),
        received_at=at(10, 9),
        original_url=f"https://example.com/{news_id}",
        quality_flags=(),
        extraction_method=ExtractionMethod.LLM_GROUNDED,
        model_name="stub-grounding-model",
        prompt_version="catalyst-grounding-2026.08.1",
        confidence=0.9,
        generated_at=at(10, 10),
    )


def test_theme_to_news_records_theme_stock_and_time_basis() -> None:
    items = stored(raw())

    matches = match_theme_to_news(nuclear_context(), items, decision_at=at(10, 10))

    assert len(matches) == 1
    assert matches[0].trigger is MatchTrigger.THEME_TO_NEWS
    assert matches[0].match_basis == (MatchBasis.THEME, MatchBasis.STOCK, MatchBasis.TIME)
    assert matches[0].matched_stock_ids == ("stk_nuclear_leader",)
    assert matches[0].relevance_score == 0.9


def test_new_news_matches_only_the_related_active_event() -> None:
    item = stored(raw())[0]

    matches = match_news_to_events(item, [nuclear_context(), bio_context()], decision_at=at(10, 10))

    assert [match.event_id for match in matches] == ["evt_nuclear"]
    assert matches[0].trigger is MatchTrigger.NEWS_TO_EVENT


def test_news_published_after_the_decision_time_is_not_used_as_evidence() -> None:
    items = stored(raw())

    assert match_theme_to_news(nuclear_context(), items, decision_at=at(10, 5)) == ()


def test_featured_marker_alone_does_not_match_an_unrelated_theme() -> None:
    items = stored(
        raw(
            source_item_id="bio",
            title="[특징주] 바이오헬스, 장중 강세",
            description="수급 영향으로 상승했다.",
            original_url="https://example.com/bio",
        )
    )

    assert match_theme_to_news(nuclear_context(), items, decision_at=at(10, 10)) == ()


def test_two_independent_publishers_confirm_multi_source() -> None:
    decision = decide(
        nuclear_context(),
        [evidence(), evidence(news_id="news_2", publisher="두번째 예시 언론사")],
        now=at(10, 12),
    )

    assert decision.evidence_status is EvidenceStatus.MULTI_SOURCE_CONFIRMED
    assert decision.news_ids == ("news_1", "news_2")


def test_single_publisher_stays_an_estimate() -> None:
    decision = decide(nuclear_context(), [evidence()], now=at(10, 12))

    assert decision.evidence_status is EvidenceStatus.SINGLE_SOURCE
    assert decision.summary == "신규 원전 수주 기대 관련 보도"


def test_known_catalyst_key_is_reported_as_reemergence() -> None:
    context = nuclear_context(
        known_catalyst_keys=frozenset({catalyst_key("thm_nuclear", ("원전",))})
    )

    decision = decide(context, [evidence()], now=at(10, 12))

    assert decision.evidence_status is EvidenceStatus.REEMERGENCE


def test_no_evidence_separates_collection_failure_from_absent_catalyst() -> None:
    context = nuclear_context()

    searching = decide(context, [], now=at(10, 5))
    degraded = decide(context, [], now=at(10, 30), sources_degraded=True)
    absent = decide(context, [], now=at(10, 30))

    assert searching.evidence_status is EvidenceStatus.SEARCHING
    assert degraded.evidence_status is EvidenceStatus.SEARCHING
    assert absent.evidence_status is EvidenceStatus.NO_NEW_CATALYST
    assert absent.summary is None


def test_confirmed_evidence_is_not_weakened_by_a_later_empty_lookup() -> None:
    revisions = EvidenceRevisionStore()
    revisions.record(
        "evt_nuclear",
        decide(nuclear_context(), [evidence()], now=at(10, 12)),
        now=at(10, 12),
    )

    decision = decide(
        nuclear_context(),
        [],
        now=at(11, 0),
        previous=revisions.current("evt_nuclear"),
    )

    assert decision.evidence_status is EvidenceStatus.SINGLE_SOURCE
    assert decision.news_ids == ("news_1",)


def test_after_close_confirmation_becomes_a_new_revision_on_the_same_event() -> None:
    revisions = EvidenceRevisionStore()
    context = nuclear_context()
    first = revisions.record("evt_nuclear", decide(context, [evidence()], now=at(10, 12)), now=at(10, 12))
    unchanged = revisions.record(
        "evt_nuclear", decide(context, [evidence()], now=at(10, 20)), now=at(10, 20)
    )
    confirmed = revisions.record(
        "evt_nuclear",
        decide(context, [evidence()], now=at(16, 0), after_close_summary="인포스탁 확정 사유"),
        now=at(16, 0),
    )

    assert unchanged is first
    assert first.evidence_confirmed_at == at(10, 12)
    assert confirmed.revision == 2
    assert confirmed.evidence_status is EvidenceStatus.AFTER_CLOSE_CONFIRMED
    assert [item.evidence_status for item in revisions.history("evt_nuclear")] == [
        EvidenceStatus.SINGLE_SOURCE,
        EvidenceStatus.AFTER_CLOSE_CONFIRMED,
    ]


def test_supplemental_search_runs_only_without_local_evidence_and_within_limits() -> None:
    gate = SupplementalSearchGate(cooldown=timedelta(minutes=10), quota_per_window=1)
    context = nuclear_context()

    with_local = gate.request(context, now=at(10, 10), has_local_evidence=True)
    granted = gate.request(context, now=at(10, 10), has_local_evidence=False)
    cooled_down = gate.request(context, now=at(10, 15), has_local_evidence=False)
    other_theme = gate.request(_other_context(), now=at(10, 30), has_local_evidence=False)

    assert with_local.denial is SupplementalDenial.LOCAL_EVIDENCE_EXISTS
    assert granted.request is not None
    assert granted.request.query_terms == ("원전", "원자력", "한국원전")
    assert cooled_down.denial is SupplementalDenial.COOLDOWN
    assert other_theme.denial is SupplementalDenial.QUOTA_EXHAUSTED


def _other_context() -> ThemeContext:
    return ThemeContext(
        event_id="evt_other",
        theme_id="thm_other",
        display_name="조선",
        market_date=nuclear_context().market_date,
        activated_at=at(10, 20),
    )
