"""Normalized 특징주 news records and their source metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

NEWS_SCHEMA_VERSION = "news-2026.08.1"


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name}은 비어 있을 수 없습니다")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다")


class NewsSourceType(StrEnum):
    RSS = "RSS"
    NAVER_NEWS_SEARCH = "NAVER_NEWS_SEARCH"
    SUPPLEMENTAL_SEARCH = "SUPPLEMENTAL_SEARCH"


class RightsScope(StrEnum):
    """이용 조건이 허용하는 저장·표시 범위."""

    METADATA_ONLY = "METADATA_ONLY"
    SUMMARY_ALLOWED = "SUMMARY_ALLOWED"
    FULL_TEXT_ALLOWED = "FULL_TEXT_ALLOWED"


class IngestionStatus(StrEnum):
    STORED = "STORED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"


class RejectionReason(StrEnum):
    NOT_FEATURED_STOCK = "NOT_FEATURED_STOCK"
    NO_LISTED_STOCK = "NO_LISTED_STOCK"
    PUBLISHED_IN_FUTURE = "PUBLISHED_IN_FUTURE"
    PUBLISHED_BEFORE_WINDOW = "PUBLISHED_BEFORE_WINDOW"


@dataclass(frozen=True, slots=True)
class RawNewsItem:
    """공급원이 돌려준 그대로의 한 항목."""

    source_id: str
    source_type: NewsSourceType
    source_item_id: str
    publisher: str
    title: str
    description: str
    original_url: str
    published_at: datetime | None
    retrieved_at: datetime
    rights_scope: RightsScope = RightsScope.SUMMARY_ALLOWED
    body: str = ""

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_id", self.source_id),
            ("source_item_id", self.source_item_id),
            ("publisher", self.publisher),
            ("title", self.title),
            ("original_url", self.original_url),
        ):
            _require_text(value, field_name)
        if self.published_at is not None:
            _require_aware(self.published_at, "published_at")
        _require_aware(self.retrieved_at, "retrieved_at")
        if self.rights_scope is not RightsScope.FULL_TEXT_ALLOWED and self.body:
            raise ValueError("본문 저장이 허용되지 않은 공급원입니다")


@dataclass(frozen=True, slots=True)
class NewsItem:
    """중복 제거와 정규화를 마친 저장 단위."""

    news_id: str
    source_id: str
    source_type: NewsSourceType
    source_item_id: str
    canonical_url: str
    original_url: str
    publisher: str
    title: str
    description: str
    published_at: datetime | None
    retrieved_at: datetime
    normalized_title_hash: str
    content_hash: str
    rights_scope: RightsScope
    ingestion_status: IngestionStatus
    stock_ids: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    body: str = ""

    def __post_init__(self) -> None:
        _require_text(self.news_id, "news_id")
        _require_text(self.canonical_url, "canonical_url")
        if len(set(self.stock_ids)) != len(self.stock_ids):
            raise ValueError("stock_ids에 중복이 있습니다")

    @property
    def groundable_text(self) -> str:
        """LLM에 전달할 수 있는 범위의 원문 텍스트."""

        return " ".join(part for part in (self.title, self.description, self.body) if part)

    def to_source_metadata(self) -> dict[str, object]:
        return {
            "newsId": self.news_id,
            "sourceId": self.source_id,
            "sourceType": self.source_type.value,
            "sourceItemId": self.source_item_id,
            "publisher": self.publisher,
            "canonicalUrl": self.canonical_url,
            "originalUrl": self.original_url,
            "publishedAt": None if self.published_at is None else self.published_at.isoformat(),
            "retrievedAt": self.retrieved_at.isoformat(),
            "normalizedTitleHash": self.normalized_title_hash,
            "contentHash": self.content_hash,
            "rightsScope": self.rights_scope.value,
            "schemaVersion": NEWS_SCHEMA_VERSION,
        }
