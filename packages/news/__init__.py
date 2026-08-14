"""허용 공급원 뉴스 수집, 정규화, revision과 PIT 저장 경계."""

from .adapter import AllowedSourceAdapter, AllowedSourceSpec, NewsProvider
from .errors import (
    NewsSourceContractError,
    NewsTemporalConflictError,
    SourceRightsDeniedError,
)
from .fixture import FixtureNewsProvider, load_fixture_provider
from .models import (
    CollectionCursor,
    CollectionEnvironment,
    CollectionResult,
    ContentClass,
    NewsArticle,
    ProviderBatch,
    ProviderFetchRequest,
    RightsOperation,
)
from .rights import RightsRegistry, SourceRightsRecord
from .store import (
    ApplyNewsResult,
    InMemoryNewsStore,
    NewsArticleRevision,
    NewsWriteDisposition,
)

__all__ = [
    "AllowedSourceAdapter",
    "AllowedSourceSpec",
    "ApplyNewsResult",
    "CollectionCursor",
    "CollectionEnvironment",
    "CollectionResult",
    "ContentClass",
    "FixtureNewsProvider",
    "InMemoryNewsStore",
    "NewsArticle",
    "NewsArticleRevision",
    "NewsProvider",
    "NewsSourceContractError",
    "NewsTemporalConflictError",
    "NewsWriteDisposition",
    "ProviderBatch",
    "ProviderFetchRequest",
    "RightsOperation",
    "RightsRegistry",
    "SourceRightsDeniedError",
    "SourceRightsRecord",
    "load_fixture_provider",
]
