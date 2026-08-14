"""저장된 특징주 뉴스와 공급원 cursor의 조회 경계."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from threading import RLock
from typing import Protocol

from .models import NewsItem
from .sources import SourceCursor


class NewsStore(Protocol):
    def upsert(self, item: NewsItem) -> bool:
        """새로 저장했으면 True, 이미 있으면 False."""

    def get(self, news_id: str) -> NewsItem | None: ...

    def find_duplicate(self, item: NewsItem) -> NewsItem | None: ...

    def search(
        self,
        *,
        stock_ids: Iterable[str] = (),
        keywords: Iterable[str] = (),
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[NewsItem, ...]: ...

    def get_cursor(self, source_id: str) -> SourceCursor | None: ...

    def put_cursor(self, cursor: SourceCursor) -> None: ...


class InMemoryNewsStore:
    """결정적 fixture 저장소. 외부 공급원을 호출하지 않는다."""

    def __init__(self) -> None:
        self._items: dict[str, NewsItem] = {}
        self._by_canonical_url: dict[str, str] = {}
        self._by_title_key: dict[tuple[str, str, str], str] = {}
        self._cursors: dict[str, SourceCursor] = {}
        self._lock = RLock()

    @staticmethod
    def _title_key(item: NewsItem) -> tuple[str, str, str]:
        published = "" if item.published_at is None else item.published_at.isoformat()
        return (item.normalized_title_hash, item.publisher.strip().casefold(), published)

    def upsert(self, item: NewsItem) -> bool:
        with self._lock:
            if item.news_id in self._items:
                return False
            self._items[item.news_id] = item
            self._by_canonical_url[item.canonical_url] = item.news_id
            self._by_title_key[self._title_key(item)] = item.news_id
            return True

    def get(self, news_id: str) -> NewsItem | None:
        with self._lock:
            return self._items.get(news_id)

    def find_duplicate(self, item: NewsItem) -> NewsItem | None:
        with self._lock:
            existing_id = self._by_canonical_url.get(item.canonical_url)
            if existing_id is None:
                existing_id = self._by_title_key.get(self._title_key(item))
            if existing_id is None or existing_id == item.news_id:
                return self._items.get(existing_id) if existing_id else None
            return self._items[existing_id]

    def search(
        self,
        *,
        stock_ids: Iterable[str] = (),
        keywords: Iterable[str] = (),
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[NewsItem, ...]:
        wanted_stocks = frozenset(stock_ids)
        wanted_keywords = tuple(keyword.casefold() for keyword in keywords if keyword.strip())
        with self._lock:
            items: Sequence[NewsItem] = tuple(self._items.values())
        matched = [
            item
            for item in items
            if _within(item, since=since, until=until)
            and (
                not wanted_stocks
                and not wanted_keywords
                or wanted_stocks & frozenset(item.stock_ids)
                or _matches_keyword(item, wanted_keywords)
            )
        ]
        return tuple(sorted(matched, key=_ordering_key, reverse=True))

    def get_cursor(self, source_id: str) -> SourceCursor | None:
        with self._lock:
            return self._cursors.get(source_id)

    def put_cursor(self, cursor: SourceCursor) -> None:
        with self._lock:
            self._cursors[cursor.source_id] = cursor


def _within(item: NewsItem, *, since: datetime | None, until: datetime | None) -> bool:
    published = item.published_at
    if published is None:
        return until is None or item.retrieved_at <= until
    if since is not None and published < since:
        return False
    return until is None or published <= until


def _matches_keyword(item: NewsItem, keywords: tuple[str, ...]) -> bool:
    haystack = f"{item.title} {item.description} {' '.join(item.entities)}".casefold()
    return any(keyword in haystack for keyword in keywords)


def _ordering_key(item: NewsItem) -> tuple[datetime, str]:
    return (item.published_at or item.retrieved_at, item.news_id)
