"""수집 규칙·중복 제거·Entity 식별을 거쳐 뉴스 저장소에 적재한다."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .models import (
    IngestionStatus,
    NewsItem,
    NewsSourceType,
    RawNewsItem,
    RejectionReason,
)
from .normalize import (
    canonical_url,
    content_hash,
    is_featured_stock_title,
    news_id,
    title_hash,
)
from .store import NewsStore

FEATURED_SOURCE_TYPES = frozenset({NewsSourceType.RSS, NewsSourceType.NAVER_NEWS_SEARCH})


@dataclass(frozen=True, slots=True)
class RejectedNews:
    source_id: str
    source_item_id: str
    reason: RejectionReason


@dataclass(frozen=True, slots=True)
class IngestionReport:
    stored: tuple[NewsItem, ...]
    duplicates: tuple[NewsItem, ...]
    rejected: tuple[RejectedNews, ...]

    @property
    def created_event_news_ids(self) -> tuple[str, ...]:
        """`feature_news.created`로 발행할 새 기사만."""

        return tuple(item.news_id for item in self.stored)


class NewsIngestor:
    """공급원 payload를 정규화된 저장 단위로 바꾸는 유일한 경로."""

    def __init__(
        self,
        store: NewsStore,
        *,
        stock_directory: Mapping[str, str],
        entity_vocabulary: Iterable[str] = (),
    ) -> None:
        self._store = store
        self._stock_directory = dict(stock_directory)
        self._entities = tuple(sorted({term for term in entity_vocabulary if term.strip()}))

    def ingest(
        self,
        raw_items: Sequence[RawNewsItem],
        *,
        now: datetime,
        window_start: datetime,
    ) -> IngestionReport:
        stored: list[NewsItem] = []
        duplicates: list[NewsItem] = []
        rejected: list[RejectedNews] = []
        for raw in raw_items:
            reason = self._reject_reason(raw, now=now, window_start=window_start)
            if reason is not None:
                rejected.append(RejectedNews(raw.source_id, raw.source_item_id, reason))
                continue
            item = self._normalize(raw)
            existing = self._store.find_duplicate(item)
            if existing is not None:
                duplicates.append(existing)
                continue
            if self._store.upsert(item):
                stored.append(item)
            else:
                duplicates.append(item)
        return IngestionReport(tuple(stored), tuple(duplicates), tuple(rejected))

    def _reject_reason(
        self,
        raw: RawNewsItem,
        *,
        now: datetime,
        window_start: datetime,
    ) -> RejectionReason | None:
        if raw.source_type in FEATURED_SOURCE_TYPES and not is_featured_stock_title(raw.title):
            return RejectionReason.NOT_FEATURED_STOCK
        try:
            canonical_url(raw.original_url)
        except ValueError:
            # 오염된 공급원 한 항목이 배치 전체를 죽이지 않도록 여기서 거부한다.
            return RejectionReason.INVALID_URL
        if not self._resolve_stocks(raw.title, raw.description):
            return RejectionReason.NO_LISTED_STOCK
        if raw.published_at is not None:
            if raw.published_at > now:
                return RejectionReason.PUBLISHED_IN_FUTURE
            if raw.published_at < window_start:
                return RejectionReason.PUBLISHED_BEFORE_WINDOW
        return None

    def _resolve_stocks(self, title: str, description: str) -> tuple[str, ...]:
        haystack = f"{title} {description}"
        return tuple(
            dict.fromkeys(
                stock_id
                for name, stock_id in self._stock_directory.items()
                if name and name in haystack
            )
        )

    def _resolve_entities(self, raw: RawNewsItem) -> tuple[str, ...]:
        haystack = f"{raw.title} {raw.description} {raw.body}"
        return tuple(term for term in self._entities if term in haystack)

    def _normalize(self, raw: RawNewsItem) -> NewsItem:
        canonical = canonical_url(raw.original_url)
        return NewsItem(
            news_id=news_id(canonical),
            source_id=raw.source_id,
            source_type=raw.source_type,
            source_item_id=raw.source_item_id,
            canonical_url=canonical,
            original_url=raw.original_url,
            publisher=raw.publisher,
            title=raw.title,
            description=raw.description,
            published_at=raw.published_at,
            retrieved_at=raw.retrieved_at,
            normalized_title_hash=title_hash(raw.title),
            content_hash=content_hash(raw.title, raw.description),
            rights_scope=raw.rights_scope,
            ingestion_status=IngestionStatus.STORED,
            stock_ids=self._resolve_stocks(raw.title, raw.description),
            entities=self._resolve_entities(raw),
            body=raw.body,
        )
