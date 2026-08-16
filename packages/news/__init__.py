"""허용된 공급원에서 특징주 뉴스를 수집·정규화·중복 제거해 보존한다."""

from .ingestion import (
    FEATURED_SOURCE_TYPES,
    IngestionReport,
    NewsIngestor,
    RejectedNews,
)
from .live import (
    NaverApiHubClient,
    NaverNewsSearchSource,
    NaverSupplementalSearchSource,
    RssFeedConfig,
    RssNewsSource,
    create_live_news_sources,
    create_supplemental_search_source,
    parse_rss_feed_configs,
)
from .models import (
    NEWS_SCHEMA_VERSION,
    IngestionStatus,
    NewsItem,
    NewsSourceType,
    RawNewsItem,
    RejectionReason,
    RightsScope,
)
from .normalize import (
    canonical_url,
    content_hash,
    is_featured_stock_title,
    news_id,
    normalized_title,
    title_hash,
)
from .postgres import PostgresNewsStore
from .sources import (
    NewsSource,
    PollResult,
    SourceCursor,
    SourceFailure,
    SourcePoller,
    SourceStatus,
)
from .store import InMemoryNewsStore, NewsStore

__all__ = [
    "FEATURED_SOURCE_TYPES",
    "NEWS_SCHEMA_VERSION",
    "InMemoryNewsStore",
    "IngestionReport",
    "IngestionStatus",
    "NaverApiHubClient",
    "NaverNewsSearchSource",
    "NaverSupplementalSearchSource",
    "NewsIngestor",
    "NewsItem",
    "NewsSource",
    "NewsSourceType",
    "NewsStore",
    "PostgresNewsStore",
    "PollResult",
    "RawNewsItem",
    "RejectedNews",
    "RejectionReason",
    "RightsScope",
    "RssFeedConfig",
    "RssNewsSource",
    "SourceCursor",
    "SourceFailure",
    "SourcePoller",
    "SourceStatus",
    "canonical_url",
    "content_hash",
    "create_live_news_sources",
    "create_supplemental_search_source",
    "is_featured_stock_title",
    "news_id",
    "normalized_title",
    "parse_rss_feed_configs",
    "title_hash",
]
