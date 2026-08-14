"""허용된 공급원에서 특징주 뉴스를 수집·정규화·중복 제거해 보존한다."""

from .ingestion import (
    FEATURED_SOURCE_TYPES,
    IngestionReport,
    NewsIngestor,
    RejectedNews,
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
    "NewsIngestor",
    "NewsItem",
    "NewsSource",
    "NewsSourceType",
    "NewsStore",
    "PollResult",
    "RawNewsItem",
    "RejectedNews",
    "RejectionReason",
    "RightsScope",
    "SourceCursor",
    "SourceFailure",
    "SourcePoller",
    "SourceStatus",
    "canonical_url",
    "content_hash",
    "is_featured_stock_title",
    "news_id",
    "normalized_title",
    "title_hash",
]
