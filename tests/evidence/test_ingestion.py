from __future__ import annotations

from datetime import timedelta

from packages.news import (
    InMemoryNewsStore,
    NewsIngestor,
    NewsSourceType,
    RejectionReason,
    RightsScope,
    SourcePoller,
    SourceStatus,
)

from ._factories import (
    ENTITY_VOCABULARY,
    STOCK_DIRECTORY,
    WINDOW_START,
    StubSource,
    at,
    raw,
)


def make_ingestor() -> tuple[InMemoryNewsStore, NewsIngestor]:
    store = InMemoryNewsStore()
    return store, NewsIngestor(
        store,
        stock_directory=STOCK_DIRECTORY,
        entity_vocabulary=ENTITY_VOCABULARY,
    )


def test_stores_featured_news_with_source_metadata_and_entities() -> None:
    store, ingestor = make_ingestor()

    report = ingestor.ingest([raw()], now=at(10, 10), window_start=WINDOW_START)

    assert report.created_event_news_ids == (report.stored[0].news_id,)
    item = store.get(report.stored[0].news_id)
    assert item is not None
    metadata = item.to_source_metadata()
    assert metadata["publisher"] == "예시 언론사"
    assert metadata["originalUrl"] == "https://example.com/news/123"
    assert metadata["publishedAt"] == at(10, 8).isoformat()
    assert metadata["rightsScope"] == RightsScope.SUMMARY_ALLOWED.value
    assert item.stock_ids == ("stk_nuclear_leader",)
    assert item.entities == ("수주", "원전")


def test_rejects_non_featured_titles_and_unlisted_stocks() -> None:
    _, ingestor = make_ingestor()

    report = ingestor.ingest(
        [
            raw(source_item_id="a", title="코스피 마감 시황", original_url="https://example.com/a"),
            raw(
                source_item_id="b",
                title="[특징주] 알수없는회사, 급등",
                description="상장 종목을 식별할 수 없다.",
                original_url="https://example.com/b",
            ),
        ],
        now=at(10, 10),
        window_start=WINDOW_START,
    )

    assert report.stored == ()
    assert [rejected.reason for rejected in report.rejected] == [
        RejectionReason.NOT_FEATURED_STOCK,
        RejectionReason.NO_LISTED_STOCK,
    ]


def test_rejects_future_and_out_of_window_publications() -> None:
    _, ingestor = make_ingestor()

    report = ingestor.ingest(
        [
            raw(source_item_id="future", published_at=at(10, 30), original_url="https://example.com/f"),
            raw(
                source_item_id="old",
                published_at=WINDOW_START - timedelta(minutes=1),
                original_url="https://example.com/o",
            ),
        ],
        now=at(10, 10),
        window_start=WINDOW_START,
    )

    assert report.stored == ()
    assert [rejected.reason for rejected in report.rejected] == [
        RejectionReason.PUBLISHED_IN_FUTURE,
        RejectionReason.PUBLISHED_BEFORE_WINDOW,
    ]


def test_deduplicates_tracking_parameters_and_scheme_variants() -> None:
    store, ingestor = make_ingestor()

    report = ingestor.ingest(
        [
            raw(source_item_id="1", original_url="https://example.com/news/123"),
            raw(
                source_item_id="2",
                original_url="http://www.example.com/news/123/?utm_source=rss&utm_medium=feed",
            ),
        ],
        now=at(10, 10),
        window_start=WINDOW_START,
    )

    assert len(report.stored) == 1
    assert len(report.duplicates) == 1
    assert len(store.search()) == 1


def test_deduplicates_same_publisher_title_and_published_at_across_urls() -> None:
    store, ingestor = make_ingestor()

    report = ingestor.ingest(
        [
            raw(source_item_id="1", original_url="https://example.com/news/1"),
            raw(
                source_item_id="2",
                title="[특징주 강세] 한국원전, 신규 원전 수주 기대에 강세!",
                original_url="https://mirror.example.com/news/2",
            ),
        ],
        now=at(10, 10),
        window_start=WINDOW_START,
    )

    assert len(report.stored) == 1
    assert len(report.duplicates) == 1
    assert len(store.search()) == 1


def test_supplemental_search_results_do_not_require_featured_marker() -> None:
    _, ingestor = make_ingestor()

    report = ingestor.ingest(
        [
            raw(
                source_id="naver_supplemental",
                source_type=NewsSourceType.SUPPLEMENTAL_SEARCH,
                title="한국원전 수주 협상 진전",
                original_url="https://example.org/s/1",
            )
        ],
        now=at(10, 10),
        window_start=WINDOW_START,
    )

    assert len(report.stored) == 1


def test_one_failing_source_does_not_stop_the_others_and_records_retry() -> None:
    healthy = StubSource("rss_ok", [[raw(source_id="rss_ok", original_url="https://example.com/ok")]])
    broken = StubSource("rss_broken", [], error=TimeoutError("공급원 응답 없음"))
    poller = SourcePoller([healthy, broken], retry_backoff=timedelta(seconds=30))

    result = poller.poll({}, now=at(10, 10))

    assert len(result.items) == 1
    assert result.degraded_source_ids == ("rss_broken",)
    broken_cursor = next(cursor for cursor in result.cursors if cursor.source_id == "rss_broken")
    assert broken_cursor.status is SourceStatus.RETRYING
    assert broken_cursor.next_poll_at == at(10, 10) + timedelta(seconds=30)
    assert result.failures[0].message == "공급원 응답 없음"


def test_poller_respects_next_poll_at_and_advances_cursor_on_success() -> None:
    source = StubSource("rss_ok", [[raw(source_id="rss_ok", source_item_id="item-9")]])
    poller = SourcePoller([source], poll_interval=timedelta(seconds=45))

    first = poller.poll({}, now=at(10, 10))
    skipped = poller.poll({cursor.source_id: cursor for cursor in first.cursors}, now=at(10, 10, 30))

    assert source.fetch_count == 1
    assert skipped.skipped == ("rss_ok",)
    assert first.cursors[0].last_source_item_id == "item-9"
    assert first.cursors[0].last_published_at == at(10, 8)
