from __future__ import annotations

from collections.abc import Mapping, Sequence
from importlib import import_module

from packages.catalyst import EvidenceRevisionStore, EvidenceStatus
from packages.llm import GroundingService
from packages.news import (
    InMemoryNewsStore,
    NewsIngestor,
    NewsSourceType,
    RawNewsItem,
    SourcePoller,
)
from scripts.validate_contracts import validate_instance

from ._factories import (
    ENTITY_VOCABULARY,
    STOCK_DIRECTORY,
    WINDOW_START,
    StubLlmClient,
    StubSource,
    at,
    bio_context,
    grounded_response,
    nuclear_context,
    raw,
)

pipeline_module = import_module("apps." + "worker-news.pipeline")
EvidencePipeline = pipeline_module.EvidencePipeline

SupplementalSearchGate = import_module("packages." + "catalyst").SupplementalSearchGate


class StubSupplementalSource:
    def __init__(self, items: Sequence[RawNewsItem]) -> None:
        self._items = tuple(items)
        self.requests: list[object] = []

    def search(self, request: object) -> Sequence[RawNewsItem]:
        self.requests.append(request)
        return self._items


def build(
    *,
    responses: Sequence[Mapping[str, object]] = (),
    sources: Sequence[StubSource] = (),
    gate: object | None = None,
    supplemental_source: object | None = None,
) -> tuple[object, InMemoryNewsStore]:
    store = InMemoryNewsStore()
    ingestor = NewsIngestor(
        store,
        stock_directory=STOCK_DIRECTORY,
        entity_vocabulary=ENTITY_VOCABULARY,
    )
    return (
        EvidencePipeline(
            store=store,
            ingestor=ingestor,
            grounding=GroundingService(StubLlmClient(responses)),
            revisions=EvidenceRevisionStore(),
            poller=SourcePoller(list(sources)) if sources else None,
            supplemental_gate=gate,
            supplemental_source=supplemental_source,
        ),
        store,
    )


def test_collected_news_becomes_a_contract_shaped_evidence_projection() -> None:
    pipeline, _ = build(
        responses=[grounded_response()],
        sources=[StubSource("rss_ok", [[raw()]])],
    )

    collection = pipeline.collect(now=at(10, 10), window_start=WINDOW_START)
    outcome = pipeline.refresh_event(
        nuclear_context(), now=at(10, 11), window_start=WINDOW_START
    )

    assert len(collection.report.stored) == 1
    assert collection.sources_degraded is False
    assert outcome.revision.evidence_status is EvidenceStatus.SINGLE_SOURCE
    assert outcome.llm_called is True
    assert outcome.llm_record is not None
    assert outcome.llm_record.accepted is True

    list_data = outcome.list_projection()
    summary = outcome.summary_projection()
    validate_instance(list_data, "EvidenceListData", label="evidence-list")
    validate_instance(summary, "EvidenceSummary", label="evidence-summary")
    assert list_data["items"][0]["sourceName"] == "예시 언론사"
    assert list_data["items"][0]["originalUrl"] == "https://example.com/news/123"
    assert list_data["items"][0]["matchBasis"] == ["THEME", "STOCK", "TIME"]
    assert list_data["items"][0]["summary"] == "신규 원전 수주 기대 관련 보도"
    assert summary["sourceCount"] == 1
    assert summary["latestPublishedAt"] == at(10, 8).isoformat()


def test_two_publishers_confirm_the_same_catalyst_in_one_call() -> None:
    pipeline, _ = build(
        responses=[grounded_response()],
        sources=[
            StubSource(
                "rss_ok",
                [
                    [
                        raw(),
                        raw(
                            source_item_id="item-2",
                            publisher="두번째 예시 언론사",
                            title="[특징주] 한국원전, 원전 협상 진전 보도",
                            original_url="https://example.org/news/456",
                            published_at=at(10, 9),
                        ),
                    ]
                ],
            )
        ],
    )

    pipeline.collect(now=at(10, 10), window_start=WINDOW_START)
    outcome = pipeline.refresh_event(
        nuclear_context(), now=at(10, 11), window_start=WINDOW_START
    )

    assert outcome.revision.evidence_status is EvidenceStatus.MULTI_SOURCE_CONFIRMED
    assert outcome.summary_projection()["sourceCount"] == 2
    assert len(outcome.list_projection()["items"]) == 2


def test_without_local_evidence_no_llm_call_and_no_generated_cause() -> None:
    pipeline, _ = build(responses=[grounded_response()])

    outcome = pipeline.refresh_event(
        nuclear_context(), now=at(10, 5), window_start=WINDOW_START
    )

    assert outcome.llm_called is False
    assert outcome.llm_record is None
    assert outcome.revision.evidence_status is EvidenceStatus.SEARCHING
    assert outcome.revision.summary is None
    assert outcome.list_projection()["items"] == []
    assert outcome.summary_projection()["summary"] is None
    validate_instance(outcome.list_projection(), "EvidenceListData", label="searching")


def test_supplemental_search_runs_only_after_a_local_miss_and_reuses_the_store() -> None:
    supplemental = StubSupplementalSource(
        [
            raw(
                source_id="naver_supplemental",
                source_type=NewsSourceType.SUPPLEMENTAL_SEARCH,
                source_item_id="supp-1",
                title="[특징주] 한국원전, 신규 원전 수주 기대",
                original_url="https://example.net/s/1",
            )
        ]
    )
    pipeline, store = build(
        responses=[grounded_response()],
        gate=SupplementalSearchGate(),
        supplemental_source=supplemental,
    )

    outcome = pipeline.refresh_event(
        nuclear_context(), now=at(10, 11), window_start=WINDOW_START
    )
    repeat = pipeline.refresh_event(
        nuclear_context(), now=at(10, 12), window_start=WINDOW_START
    )

    assert outcome.supplemental is not None
    assert outcome.supplemental.query_terms == ("원전", "원자력", "한국원전")
    assert outcome.revision.evidence_status is EvidenceStatus.SINGLE_SOURCE
    assert len(store.search()) == 1
    assert len(supplemental.requests) == 1
    assert repeat.supplemental is None


def test_new_article_refreshes_only_the_events_it_matches() -> None:
    pipeline, store = build(responses=[grounded_response()])
    ingestor = NewsIngestor(
        store,
        stock_directory=STOCK_DIRECTORY,
        entity_vocabulary=ENTITY_VOCABULARY,
    )
    stored = ingestor.ingest(
        [
            raw(),
            raw(
                source_item_id="bio",
                title="[특징주] 바이오헬스, 강세",
                description="수급 영향으로 상승했다.",
                original_url="https://example.com/bio",
            ),
        ],
        now=at(10, 10),
        window_start=WINDOW_START,
    ).stored

    outcomes = pipeline.on_news_created(
        stored[:1],
        [nuclear_context(), bio_context()],
        now=at(10, 11),
        window_start=WINDOW_START,
    )

    assert len(stored) == 2
    assert [outcome.event_id for outcome in outcomes] == ["evt_nuclear"]
    assert outcomes[0].revision.evidence_status is EvidenceStatus.SINGLE_SOURCE
    assert [item.news_id for item in outcomes[0].evidence] == [stored[0].news_id]


def test_bundle_summary_is_not_attached_to_articles_that_do_not_support_it() -> None:
    """묶음 요약을 무관한 기사에 붙이지 않는다.

    LLM 호출 하나가 기사 여러 건을 묶어 요약 하나를 만들지만, 그 요약이 어느
    기사에서 나왔는지는 응답에 없다. 2026-08-18 광통신 테마에서는 "대한광통신,
    연속 오름세"와 서울바이오시스 특허 칼럼이 한 묶음으로 처리돼, 칼럼에도
    광통신 요약이 그대로 붙었다.
    """

    supporting = raw(
        source_id="naver_supplemental",
        source_type=NewsSourceType.SUPPLEMENTAL_SEARCH,
        source_item_id="supp-support",
        title="[특징주] 한국원전, 체코 수주 기대에 강세",
        description="한국원전이 체코 신규 수주 기대에 오르고 있다.",
        original_url="https://example.net/s/support",
    )
    unrelated = raw(
        source_id="naver_supplemental",
        source_type=NewsSourceType.SUPPLEMENTAL_SEARCH,
        source_item_id="supp-unrelated",
        title="[특징주] 한국원전, 특허 소송 승소",
        description="한국원전이 해외 특허 침해 소송에서 최종 승소했다.",
        original_url="https://example.net/s/unrelated",
    )
    pipeline, _ = build(
        responses=[grounded_response(entities=("체코",))],
        gate=SupplementalSearchGate(),
        supplemental_source=StubSupplementalSource([supporting, unrelated]),
    )

    outcome = pipeline.refresh_event(
        nuclear_context(), now=at(10, 11), window_start=WINDOW_START
    )

    kept = [item["newsId"] for item in outcome.list_projection()["items"]]
    assert len(kept) == 1
    assert all(evidence.entities == ("체코",) for evidence in outcome.evidence)
    assert all(
        "특허 침해" not in evidence.title for evidence in outcome.evidence
    )


def test_stored_non_featured_articles_never_become_evidence() -> None:
    """수집 필터가 생기기 전에 저장된 기사도 근거가 되지 않는다.

    수집 단계 필터는 새로 들어오는 기사만 막는다. 보완 검색이 면제로 들여온
    기사가 저장소에 남아 있으면 계속 매칭·근거 후보가 된다. 근거 경로에서 한 번
    더 걸러 저장소 상태와 무관하게 규칙이 지켜지게 한다.
    """

    from packages.news import NewsItem  # noqa: PLC0415

    pipeline, store = build(responses=[grounded_response()])
    ingestor = NewsIngestor(
        store, stock_directory=STOCK_DIRECTORY, entity_vocabulary=ENTITY_VOCABULARY
    )
    legacy = ingestor._normalize(  # noqa: SLF001
        raw(
            source_id="naver_supplemental",
            source_type=NewsSourceType.SUPPLEMENTAL_SEARCH,
            source_item_id="legacy-1",
            title="한국원전, 연속 오름세",
            description="한국원전이 원전 수주 기대에 오르고 있다.",
            original_url="https://example.net/legacy/1",
        )
    )
    assert isinstance(legacy, NewsItem)
    assert store.upsert(legacy)

    outcome = pipeline.refresh_event(
        nuclear_context(), now=at(10, 11), window_start=WINDOW_START
    )

    assert outcome.evidence == ()
    assert outcome.list_projection()["items"] == []
