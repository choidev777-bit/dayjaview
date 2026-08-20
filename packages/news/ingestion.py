"""수집 규칙·중복 제거·Entity 식별을 거쳐 뉴스 저장소에 적재한다."""

from __future__ import annotations

import re

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

# 보완 검색도 예외로 두지 않는다. 테마명·종목명으로 긁어온 일반 기사가 특징주
# 기사와 같은 자격으로 근거에 올라, 오르지도 않은 종목의 칼럼이 상승 소재로
# 표시됐다(2026-08-18 광통신 테마에 붙은 서울바이오시스 특허 칼럼).
FEATURED_SOURCE_TYPES = frozenset(NewsSourceType)


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
        spans: dict[str, list[tuple[int, int]]] = {}
        for name in self._stock_directory:
            if not name or name not in haystack:
                continue
            spans[name] = [
                (match.start(), match.end())
                for match in re.finditer(re.escape(name), haystack)
            ]
        kept: list[str] = []
        for name, own_spans in spans.items():
            # 더 긴 상장사명 안에서만 나온 이름은 그 회사 언급이 아니다.
            # "SK하이닉스" 속 "SK"가 지주사로 태그되면 무관 테마의 근거
            # 후보가 된다. 독립된 위치에서 한 번이라도 나와야 인정한다.
            longer_spans = [
                span
                for other, other_spans in spans.items()
                if other != name and len(other) > len(name) and name in other
                for span in other_spans
            ]
            if any(
                not any(
                    outer_start <= start and end <= outer_end
                    for outer_start, outer_end in longer_spans
                )
                for start, end in own_spans
            ):
                kept.append(name)
        return tuple(dict.fromkeys(self._stock_directory[name] for name in kept))

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
