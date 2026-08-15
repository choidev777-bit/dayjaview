from __future__ import annotations

from importlib import import_module

import httpx
import pytest

from packages.catalyst import EvidenceRevisionStore
from packages.llm import GroundingService
from packages.news import (
    InMemoryNewsStore,
    NaverApiHubClient,
    NaverNewsSearchSource,
    NaverSupplementalSearchSource,
    NewsIngestor,
    NewsSourceType,
    RssFeedConfig,
    RssNewsSource,
    SourceCursor,
    SourcePoller,
    create_live_news_sources,
    parse_rss_feed_configs,
)

from ._factories import (
    ENTITY_VOCABULARY,
    STOCK_DIRECTORY,
    WINDOW_START,
    StubLlmClient,
    at,
)

EvidencePipeline = import_module("apps." + "worker-news.pipeline").EvidencePipeline

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>예시 증권</title>
    <item>
      <title>[특징주] 한국원전, 신규 &lt;b&gt;원전&lt;/b&gt; 수주 기대에 강세</title>
      <link>https://example.com/news/2</link>
      <guid>rss-item-2</guid>
      <description>&lt;p&gt;체코 신규 원전 관련 보도.&lt;/p&gt;</description>
      <pubDate>Fri, 14 Aug 2026 10:08:00 +0900</pubDate>
    </item>
    <item>
      <title>[특징주] 원전기자재 동반 강세</title>
      <link>https://example.com/news/1</link>
      <pubDate>Fri, 14 Aug 2026 09:00:00 +0900</pubDate>
    </item>
    <item>
      <title>링크 없는 항목</title>
      <pubDate>Fri, 14 Aug 2026 10:00:00 +0900</pubDate>
    </item>
  </channel>
</rss>
"""

NAVER_PAYLOAD = {
    "lastBuildDate": "Fri, 14 Aug 2026 10:10:00 +0900",
    "total": 2,
    "start": 1,
    "display": 2,
    "items": [
        {
            "title": "[특징주] <b>한국원전</b>, 원전 수주 기대 강세",
            "originallink": "https://www.press.example.co.kr/articles/77?utm_source=x",
            "link": "https://n.news.example.com/mnews/77",
            "description": "체코 신규 <b>원전</b> 관련 보도.",
            "pubDate": "Fri, 14 Aug 2026 10:08:00 +0900",
        },
        {
            "title": "[특징주] 원전기자재 동반 강세",
            "originallink": "",
            "link": "https://n.news.example.com/mnews/76",
            "description": "",
            "pubDate": "Fri, 14 Aug 2026 09:00:00 +0900",
        },
    ],
}


def rss_source(
    xml: str = RSS_XML, *, status_code: int = 200
) -> tuple[RssNewsSource, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status_code, text=xml)

    source = RssNewsSource(
        RssFeedConfig("rss_example", "예시 언론사", "https://example.com/rss.xml"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: at(10, 9),
    )
    return source, requests


def naver_client(
    payload: object = NAVER_PAYLOAD,
) -> tuple[NaverApiHubClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload)

    client = NaverApiHubClient(
        "client-id",
        "client-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: at(10, 9),
    )
    return client, requests


def empty_cursor(source_id: str, source_type: NewsSourceType) -> SourceCursor:
    return SourceCursor(source_id=source_id, source_type=source_type)


def test_rss_source_maps_items_and_skips_ones_without_link() -> None:
    source, _ = rss_source()

    items = source.fetch(empty_cursor("rss_example", NewsSourceType.RSS))

    assert [item.source_item_id for item in items] == [
        "https://example.com/news/1",
        "rss-item-2",
    ]
    newest = items[-1]
    assert newest.publisher == "예시 언론사"
    assert newest.title == "[특징주] 한국원전, 신규 원전 수주 기대에 강세"
    assert newest.description == "체코 신규 원전 관련 보도."
    assert newest.original_url == "https://example.com/news/2"
    assert newest.published_at == at(10, 8)
    assert newest.retrieved_at == at(10, 9)


def test_rss_source_returns_only_items_newer_than_cursor() -> None:
    source, _ = rss_source()
    cursor = SourceCursor(
        source_id="rss_example",
        source_type=NewsSourceType.RSS,
        last_published_at=at(9, 0),
    )

    items = source.fetch(cursor)

    assert [item.source_item_id for item in items] == ["rss-item-2"]


def test_rss_source_raises_on_http_error_for_poller_isolation() -> None:
    source, _ = rss_source(status_code=503)

    with pytest.raises(httpx.HTTPStatusError):
        source.fetch(empty_cursor("rss_example", NewsSourceType.RSS))


def test_naver_search_sends_credentials_and_latest_sort() -> None:
    client, requests = naver_client()
    source = NaverNewsSearchSource(client)

    items = source.fetch(
        empty_cursor("naver_api_hub_featured", NewsSourceType.NAVER_NEWS_SEARCH)
    )

    request = requests[0]
    assert request.headers["X-NCP-APIGW-API-KEY-ID"] == "client-id"
    assert request.headers["X-NCP-APIGW-API-KEY"] == "client-secret"
    assert request.url.params["query"] == "특징주"
    assert request.url.params["sort"] == "date"
    assert [item.source_item_id for item in items] == [
        "https://n.news.example.com/mnews/76",
        "https://www.press.example.co.kr/articles/77?utm_source=x",
    ]
    newest = items[-1]
    assert newest.title == "[특징주] 한국원전, 원전 수주 기대 강세"
    assert newest.description == "체코 신규 원전 관련 보도."
    assert newest.publisher == "press.example.co.kr"
    assert newest.published_at == at(10, 8)
    assert newest.source_type is NewsSourceType.NAVER_NEWS_SEARCH


def test_naver_search_uses_link_when_originallink_is_empty() -> None:
    client, _ = naver_client()
    source = NaverNewsSearchSource(client)

    items = source.fetch(
        empty_cursor("naver_api_hub_featured", NewsSourceType.NAVER_NEWS_SEARCH)
    )

    fallback = items[0]
    assert fallback.original_url == "https://n.news.example.com/mnews/76"
    assert fallback.publisher == "n.news.example.com"


def test_naver_search_rejects_payload_without_items() -> None:
    client, _ = naver_client(payload={"total": 0})
    source = NaverNewsSearchSource(client)

    with pytest.raises(ValueError):
        source.fetch(
            empty_cursor("naver_api_hub_featured", NewsSourceType.NAVER_NEWS_SEARCH)
        )


def test_supplemental_search_queries_each_term_once_and_deduplicates() -> None:
    client, requests = naver_client()
    source = NaverSupplementalSearchSource(client)

    class Request:
        query_terms = ("원전", "원자력", "한국원전", "네번째-무시")

    items = source.search(Request())

    assert [request.url.params["query"] for request in requests] == [
        "원전",
        "원자력",
        "한국원전",
    ]
    assert len(items) == 2
    assert all(
        item.source_type is NewsSourceType.SUPPLEMENTAL_SEARCH for item in items
    )


def test_parse_rss_feed_configs_reads_semicolon_entries() -> None:
    configs = parse_rss_feed_configs(
        "a|매체A|https://a.example/rss; b|매체B|https://b.example/rss;"
    )

    assert [config.source_id for config in configs] == ["a", "b"]
    assert configs[0].publisher == "매체A"

    with pytest.raises(ValueError):
        parse_rss_feed_configs("형식이|잘못됨")


def test_create_live_news_sources_assembles_only_configured_sources() -> None:
    assert create_live_news_sources({}) == ()

    sources = create_live_news_sources(
        {
            "NEWS_RSS_SOURCES": "a|매체A|https://a.example/rss",
            "NAVER_API_HUB_CLIENT_ID": "id",
            "NAVER_API_HUB_CLIENT_SECRET": "secret",
        }
    )

    assert [source.source_id for source in sources] == [
        "a",
        "naver_api_hub_featured",
    ]


def test_live_source_feeds_evidence_pipeline_collection() -> None:
    client, _ = naver_client()
    store = InMemoryNewsStore()
    pipeline = EvidencePipeline(
        store=store,
        ingestor=NewsIngestor(
            store,
            stock_directory=STOCK_DIRECTORY,
            entity_vocabulary=ENTITY_VOCABULARY,
        ),
        grounding=GroundingService(StubLlmClient()),
        revisions=EvidenceRevisionStore(),
        poller=SourcePoller([NaverNewsSearchSource(client)]),
    )

    result = pipeline.collect(now=at(10, 10), window_start=WINDOW_START)

    assert len(result.report.stored) == 2
    assert not result.sources_degraded
    stored = store.search(stock_ids=("stk_nuclear_leader",))
    assert stored and stored[0].publisher == "press.example.co.kr"
