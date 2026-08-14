"""권리 확인 후에만 provider를 호출하는 허용 공급원 adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from .errors import NewsSourceContractError
from .hashing import article_content_hash, canonical_json, sha256_text
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
from .normalization import (
    canonicalize_url,
    optional_text,
    string_tuple,
    text,
    timestamp,
)
from .rights import RightsRegistry

NEWS_METADATA_FIELDS = frozenset(
    {
        "asOf",
        "canonicalUrl",
        "collectedAt",
        "contentHash",
        "groundingText",
        "lineage",
        "originalUrl",
        "parserVersion",
        "publicationId",
        "publishedAt",
        "qualityFlags",
        "revisionOf",
        "sourceItemId",
        "sourceName",
        "sourceRevision",
        "stockCodes",
        "stockNames",
        "themeIds",
        "themeTerms",
        "title",
    }
)

_FORBIDDEN_BODY_FIELDS = frozenset(
    {"articleBody", "body", "fullText", "html", "rawBody", "rawContent"}
)


class NewsProvider(Protocol):
    """Transport-neutral provider; production/network implementation is absent here."""

    def fetch(self, request: ProviderFetchRequest) -> ProviderBatch: ...


@dataclass(frozen=True, slots=True)
class AllowedSourceSpec:
    source_id: str
    allowed_hosts: frozenset[str]

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id는 비어 있을 수 없습니다.")
        if not self.allowed_hosts or any(
            not host.strip() or host != host.casefold() for host in self.allowed_hosts
        ):
            raise ValueError("allowed_hosts에는 소문자 host가 하나 이상 필요합니다.")


class AllowedSourceAdapter:
    """Authorization, strict parsing, URL allowlist와 lineage 보존을 결합한다."""

    def __init__(
        self,
        *,
        rights: RightsRegistry,
        environment: CollectionEnvironment,
    ) -> None:
        self._rights = rights
        self._environment = environment

    def collect(
        self,
        *,
        provider: NewsProvider,
        source: AllowedSourceSpec,
        requested_at: datetime,
        cursor: str | None = None,
        limit: int = 50,
    ) -> CollectionResult:
        """Authorize before provider.fetch; denied rights therefore make zero calls."""

        record = self._rights.authorize(
            source_id=source.source_id,
            environment=self._environment,
            operations=frozenset(
                {
                    RightsOperation.COLLECT,
                    RightsOperation.NORMALIZE,
                    RightsOperation.DERIVE,
                }
            ),
            fields=NEWS_METADATA_FIELDS,
            content_classes=frozenset(
                {ContentClass.ARTICLE_METADATA, ContentClass.ARTICLE_EXCERPT}
            ),
            checked_at=requested_at,
        )
        request = ProviderFetchRequest(
            source_id=source.source_id,
            cursor=cursor,
            limit=limit,
            requested_fields=tuple(sorted(NEWS_METADATA_FIELDS)),
        )
        batch = provider.fetch(request)
        if batch.source_id != source.source_id:
            raise NewsSourceContractError(
                "SOURCE_ID_MISMATCH",
                "$.sourceId",
                "요청한 source_id와 provider 응답이 일치하지 않습니다.",
            )
        if batch.fetched_at > requested_at:
            raise NewsSourceContractError(
                "FUTURE_COLLECTION_TIME",
                "$.fetchedAt",
                "provider fetchedAt은 collection decision 시각보다 늦을 수 없습니다.",
            )
        if len(batch.items) > limit:
            raise NewsSourceContractError(
                "BATCH_LIMIT_EXCEEDED",
                "$.items",
                "provider가 요청한 bounded limit을 초과했습니다.",
            )

        articles = tuple(
            self._article(
                item,
                path=f"$.items[{index}]",
                source=source,
                provider_version=batch.provider_version,
                rights_version=record.rights_version,
                batch_as_of=batch.as_of,
                batch_fetched_at=batch.fetched_at,
            )
            for index, item in enumerate(batch.items)
        )
        batch_fingerprint = sha256_text(
            canonical_json(
                {
                    "sourceId": source.source_id,
                    "providerVersion": batch.provider_version,
                    "asOf": batch.as_of.isoformat(),
                    "fetchedAt": batch.fetched_at.isoformat(),
                    "nextCursor": batch.next_cursor,
                    "items": [
                        {
                            "sourceItemId": item.source_item_id,
                            "sourceRevision": item.source_revision,
                            "contentHash": item.content_hash,
                        }
                        for item in articles
                    ],
                }
            )
        )
        return CollectionResult(
            articles=articles,
            cursor=CollectionCursor(
                source_id=source.source_id,
                value=batch.next_cursor,
                as_of=batch.as_of,
                collected_at=batch.fetched_at,
                provider_version=batch.provider_version,
                batch_hash=batch_fingerprint,
            ),
            rights_version=record.rights_version,
            live_request_attempted=False,
        )

    @staticmethod
    def _article(
        value: Mapping[str, object],
        *,
        path: str,
        source: AllowedSourceSpec,
        provider_version: str,
        rights_version: str,
        batch_as_of: datetime,
        batch_fetched_at: datetime,
    ) -> NewsArticle:
        unexpected_body = sorted(_FORBIDDEN_BODY_FIELDS.intersection(value))
        if unexpected_body:
            raise NewsSourceContractError(
                "FULL_TEXT_NOT_REQUESTED",
                path,
                "기사 전문 field는 명시적 raw/process 권리 없이 받을 수 없습니다: "
                + ", ".join(unexpected_body),
            )
        unexpected = sorted(set(value) - NEWS_METADATA_FIELDS)
        if unexpected:
            raise NewsSourceContractError(
                "UNREQUESTED_FIELD",
                path,
                "요청하지 않은 provider field가 있습니다: " + ", ".join(unexpected),
            )

        canonical_url = canonicalize_url(
            text(value.get("canonicalUrl"), path=f"{path}.canonicalUrl"),
            allowed_hosts=source.allowed_hosts,
            path=f"{path}.canonicalUrl",
        )
        original_url = canonicalize_url(
            text(value.get("originalUrl"), path=f"{path}.originalUrl"),
            allowed_hosts=source.allowed_hosts,
            path=f"{path}.originalUrl",
        )
        title = text(value.get("title"), path=f"{path}.title")
        grounding_text = text(
            value.get("groundingText"), path=f"{path}.groundingText"
        )
        theme_ids = string_tuple(value.get("themeIds"), path=f"{path}.themeIds")
        theme_terms = string_tuple(
            value.get("themeTerms"), path=f"{path}.themeTerms"
        )
        stock_codes = string_tuple(
            value.get("stockCodes"), path=f"{path}.stockCodes"
        )
        stock_names = string_tuple(
            value.get("stockNames"), path=f"{path}.stockNames"
        )
        quality_flags = string_tuple(
            value.get("qualityFlags"), path=f"{path}.qualityFlags"
        )
        lineage = string_tuple(value.get("lineage"), path=f"{path}.lineage")
        if not lineage:
            raise NewsSourceContractError(
                "LINEAGE_REQUIRED",
                f"{path}.lineage",
                "publication/revision lineage가 하나 이상 필요합니다.",
            )

        published = timestamp(
            value.get("publishedAt"), path=f"{path}.publishedAt", nullable=True
        )
        as_of = cast(
            datetime,
            timestamp(value.get("asOf"), path=f"{path}.asOf"),
        )
        collected_at = cast(
            datetime,
            timestamp(value.get("collectedAt"), path=f"{path}.collectedAt"),
        )
        if as_of > batch_as_of or collected_at > batch_fetched_at:
            raise NewsSourceContractError(
                "ITEM_TIME_OUTSIDE_BATCH",
                path,
                "article 시각은 enclosing provider batch보다 늦을 수 없습니다.",
            )
        content_hash = text(value.get("contentHash"), path=f"{path}.contentHash")
        expected_hash = article_content_hash(
            title=title,
            grounding_text=grounding_text,
            theme_ids=theme_ids,
            theme_terms=theme_terms,
            stock_codes=stock_codes,
            stock_names=stock_names,
        )
        if content_hash != expected_hash:
            raise NewsSourceContractError(
                "CONTENT_HASH_MISMATCH",
                f"{path}.contentHash",
                "정규화 article content와 contentHash가 일치하지 않습니다.",
            )

        source_revision_raw = value.get("sourceRevision")
        if not isinstance(source_revision_raw, int) or isinstance(
            source_revision_raw, bool
        ):
            raise NewsSourceContractError(
                "SOURCE_REVISION_INVALID",
                f"{path}.sourceRevision",
                "양의 integer source revision이 필요합니다.",
            )
        revision_of_raw = value.get("revisionOf")
        if revision_of_raw is not None and (
            not isinstance(revision_of_raw, int) or isinstance(revision_of_raw, bool)
        ):
            raise NewsSourceContractError(
                "REVISION_LINEAGE_INVALID",
                f"{path}.revisionOf",
                "revisionOf는 null 또는 양의 integer여야 합니다.",
            )
        parser_version = text(
            value.get("parserVersion"), path=f"{path}.parserVersion"
        )
        source_item_id = text(
            value.get("sourceItemId"), path=f"{path}.sourceItemId"
        )
        publication_id = text(
            value.get("publicationId"), path=f"{path}.publicationId"
        )
        expected_lineage = {
            f"publication:{source.source_id}:{publication_id}",
            f"source-item:{source_item_id}:revision:{source_revision_raw}",
        }
        if not expected_lineage.issubset(lineage):
            raise NewsSourceContractError(
                "REVISION_LINEAGE_INCOMPLETE",
                f"{path}.lineage",
                "publication ID와 source revision lineage가 모두 필요합니다.",
            )
        return NewsArticle(
            source_id=source.source_id,
            source_item_id=source_item_id,
            publication_id=publication_id,
            source_revision=source_revision_raw,
            revision_of=cast(int | None, revision_of_raw),
            canonical_url=canonical_url,
            original_url=original_url,
            source_name=text(value.get("sourceName"), path=f"{path}.sourceName"),
            title=title,
            published_at=published,
            as_of=as_of,
            collected_at=collected_at,
            content_hash=content_hash,
            parser_version=parser_version,
            provider_version=provider_version,
            rights_version=rights_version,
            publication_lineage=lineage,
            grounding_text=grounding_text,
            theme_ids=theme_ids,
            theme_terms=theme_terms,
            stock_codes=stock_codes,
            stock_names=stock_names,
            quality_flags=quality_flags,
        )
