"""허용 공급원 collection과 canonical news article 값."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name}에는 timezone 정보가 필요합니다.")


def require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name}은 비어 있을 수 없습니다.")


class CollectionEnvironment(StrEnum):
    FIXTURE = "FIXTURE"
    LOCAL_TEST = "LOCAL_TEST"
    RESEARCH = "RESEARCH"
    STAGING_SHADOW = "STAGING_SHADOW"
    PRODUCTION = "PRODUCTION"


class RightsOperation(StrEnum):
    COLLECT = "COLLECT"
    RAW_STORE = "RAW_STORE"
    NORMALIZE = "NORMALIZE"
    DERIVE = "DERIVE"
    INTERNAL_REVIEW = "INTERNAL_REVIEW"
    USER_DISPLAY = "USER_DISPLAY"
    REDISTRIBUTE = "REDISTRIBUTE"


class ContentClass(StrEnum):
    ARTICLE_METADATA = "ARTICLE_METADATA"
    ARTICLE_EXCERPT = "ARTICLE_EXCERPT"
    ARTICLE_FULL_TEXT = "ARTICLE_FULL_TEXT"


@dataclass(frozen=True, slots=True)
class ProviderFetchRequest:
    source_id: str
    cursor: str | None
    limit: int
    requested_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        require_text(self.source_id, "source_id")
        if self.cursor is not None:
            require_text(self.cursor, "cursor")
        if not 1 <= self.limit <= 100:
            raise ValueError("limit은 1 이상 100 이하여야 합니다.")
        if not self.requested_fields or any(
            not field.strip() for field in self.requested_fields
        ):
            raise ValueError("requested_fields에는 비어 있지 않은 field가 필요합니다.")
        if len(set(self.requested_fields)) != len(self.requested_fields):
            raise ValueError("requested_fields는 중복될 수 없습니다.")


@dataclass(frozen=True, slots=True)
class ProviderBatch:
    source_id: str
    provider_version: str
    as_of: datetime
    fetched_at: datetime
    items: tuple[Mapping[str, object], ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        require_text(self.source_id, "source_id")
        require_text(self.provider_version, "provider_version")
        require_aware(self.as_of, "as_of")
        require_aware(self.fetched_at, "fetched_at")
        if self.as_of > self.fetched_at:
            raise ValueError("batch.as_of는 fetched_at보다 늦을 수 없습니다.")
        if self.next_cursor is not None:
            require_text(self.next_cursor, "next_cursor")


@dataclass(frozen=True, slots=True)
class NewsArticle:
    source_id: str
    source_item_id: str
    publication_id: str
    source_revision: int
    revision_of: int | None
    canonical_url: str
    original_url: str
    source_name: str
    title: str
    published_at: datetime | None
    as_of: datetime
    collected_at: datetime
    content_hash: str
    parser_version: str
    provider_version: str
    rights_version: str
    publication_lineage: tuple[str, ...]
    grounding_text: str
    theme_ids: tuple[str, ...]
    theme_terms: tuple[str, ...]
    stock_codes: tuple[str, ...]
    stock_names: tuple[str, ...]
    quality_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name, value in (
            ("source_id", self.source_id),
            ("source_item_id", self.source_item_id),
            ("publication_id", self.publication_id),
            ("canonical_url", self.canonical_url),
            ("original_url", self.original_url),
            ("source_name", self.source_name),
            ("title", self.title),
            ("parser_version", self.parser_version),
            ("provider_version", self.provider_version),
            ("rights_version", self.rights_version),
            ("grounding_text", self.grounding_text),
        ):
            require_text(value, field_name)
        if self.source_revision < 1:
            raise ValueError("source_revision은 1 이상이어야 합니다.")
        if self.revision_of is not None:
            if self.revision_of < 1 or self.revision_of >= self.source_revision:
                raise ValueError("revision_of는 현재보다 작은 양의 source revision이어야 합니다.")
        if not SHA256_RE.fullmatch(self.content_hash):
            raise ValueError("content_hash는 소문자 SHA-256 hex여야 합니다.")
        require_aware(self.as_of, "as_of")
        require_aware(self.collected_at, "collected_at")
        if self.published_at is not None:
            require_aware(self.published_at, "published_at")
            if self.published_at > self.as_of:
                raise ValueError("published_at은 as_of보다 늦을 수 없습니다.")
        if self.as_of > self.collected_at:
            raise ValueError("as_of는 collected_at보다 늦을 수 없습니다.")
        if not self.publication_lineage or any(
            not item.strip() for item in self.publication_lineage
        ):
            raise ValueError("publication_lineage에는 하나 이상의 항목이 필요합니다.")
        if len(set(self.publication_lineage)) != len(self.publication_lineage):
            raise ValueError("publication_lineage는 중복될 수 없습니다.")
        for field_name, values in (
            ("theme_ids", self.theme_ids),
            ("theme_terms", self.theme_terms),
            ("stock_codes", self.stock_codes),
            ("stock_names", self.stock_names),
            ("quality_flags", self.quality_flags),
        ):
            if any(not item.strip() for item in values):
                raise ValueError(f"{field_name}에는 빈 항목을 둘 수 없습니다.")
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name}는 중복될 수 없습니다.")
        if self.published_at is None and "PUBLISHED_TIME_UNKNOWN" not in self.quality_flags:
            raise ValueError(
                "published_at이 없으면 PUBLISHED_TIME_UNKNOWN 품질 flag가 필요합니다."
            )


@dataclass(frozen=True, slots=True)
class CollectionCursor:
    source_id: str
    value: str | None
    as_of: datetime
    collected_at: datetime
    provider_version: str
    batch_hash: str

    def __post_init__(self) -> None:
        require_text(self.source_id, "cursor.source_id")
        require_aware(self.as_of, "cursor.as_of")
        require_aware(self.collected_at, "cursor.collected_at")
        require_text(self.provider_version, "cursor.provider_version")
        if not SHA256_RE.fullmatch(self.batch_hash):
            raise ValueError("cursor.batch_hash는 소문자 SHA-256 hex여야 합니다.")


@dataclass(frozen=True, slots=True)
class CollectionResult:
    articles: tuple[NewsArticle, ...]
    cursor: CollectionCursor
    rights_version: str
    live_request_attempted: bool = False

    def __post_init__(self) -> None:
        require_text(self.rights_version, "rights_version")
        if self.live_request_attempted:
            raise ValueError("fixture evidence pipeline은 live request를 기록할 수 없습니다.")
