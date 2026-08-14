"""News dedupe audit와 distinct source revision을 보존하는 PIT store."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from .errors import NewsRevisionConflictError, NewsTemporalConflictError
from .hashing import canonical_json, normalize_match_text, sha256_text
from .models import NewsArticle, require_aware


class NewsWriteDisposition(StrEnum):
    CREATED = "CREATED"
    CREATED_REVISION = "CREATED_REVISION"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class NewsArticleRevision:
    news_id: str
    document_key: str
    revision: int
    article: NewsArticle
    known_from: datetime
    known_to: datetime | None

    def contains(self, decision_at: datetime) -> bool:
        require_aware(decision_at, "decision_at")
        return self.known_from <= decision_at and (
            self.known_to is None or decision_at < self.known_to
        )

    def visible_for_evidence(self, decision_at: datetime) -> bool:
        return (
            self.contains(decision_at)
            and self.article.published_at is not None
            and self.article.published_at <= decision_at
            and self.article.as_of <= decision_at
            and self.article.collected_at <= decision_at
        )


@dataclass(frozen=True, slots=True)
class DuplicateObservation:
    news_id: str
    source_id: str
    source_item_id: str
    source_revision: int
    canonical_url: str
    content_hash: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ApplyNewsResult:
    revision: NewsArticleRevision
    disposition: NewsWriteDisposition
    duplicate_observation: DuplicateObservation | None = None

    @property
    def created(self) -> bool:
        return self.disposition in {
            NewsWriteDisposition.CREATED,
            NewsWriteDisposition.CREATED_REVISION,
        }


def _dedupe_key(article: NewsArticle) -> str:
    return sha256_text(
        canonical_json(
            {
                "canonicalUrl": article.canonical_url,
                "publishedAt": (
                    None
                    if article.published_at is None
                    else article.published_at.isoformat()
                ),
                "sourceId": article.source_id,
                "sourceName": normalize_match_text(article.source_name),
                "title": normalize_match_text(article.title),
            }
        )
    )


def _document_key(article: NewsArticle) -> str:
    return sha256_text(
        canonical_json(
            {
                "publicationId": article.publication_id,
                "sourceId": article.source_id,
            }
        )
    )


def _news_id(document_key: str) -> str:
    return f"news_{sha256_text(document_key)[:32]}"


def _source_revision_fingerprint(article: NewsArticle) -> str:
    return sha256_text(
        canonical_json(
            {
                "canonicalUrl": article.canonical_url,
                "contentHash": article.content_hash,
                "lineage": article.publication_lineage,
                "originalUrl": article.original_url,
                "parserVersion": article.parser_version,
                "providerVersion": article.provider_version,
                "publicationId": article.publication_id,
                "publishedAt": (
                    None
                    if article.published_at is None
                    else article.published_at.isoformat()
                ),
                "revisionOf": article.revision_of,
                "rightsVersion": article.rights_version,
                "sourceItemId": article.source_item_id,
                "sourceName": article.source_name,
                "sourceRevision": article.source_revision,
                "title": article.title,
            }
        )
    )


class InMemoryNewsStore:
    """같은 delivery는 no-op이고 publication revision은 절대 합치지 않는다."""

    def __init__(self) -> None:
        self._histories: dict[str, list[NewsArticleRevision]] = {}
        self._primary: dict[tuple[str, str], str] = {}
        self._dedupe: dict[str, str] = {}
        self._news_to_document: dict[str, str] = {}
        self._duplicates: list[DuplicateObservation] = []

    def apply(self, article: NewsArticle) -> ApplyNewsResult:
        primary_key = article.source_id, article.source_item_id
        dedupe_key = _dedupe_key(article)
        document_key = self._primary.get(primary_key) or self._dedupe.get(dedupe_key)
        if document_key is None:
            document_key = _document_key(article)
        history = self._histories.setdefault(document_key, [])
        news_id = _news_id(document_key)

        same_source_revision = next(
            (
                revision
                for revision in history
                if revision.article.source_id == article.source_id
                and revision.article.source_revision == article.source_revision
                and (
                    revision.article.source_item_id == article.source_item_id
                    or revision.article.publication_id == article.publication_id
                )
            ),
            None,
        )
        if same_source_revision is not None:
            if _source_revision_fingerprint(
                same_source_revision.article
            ) != _source_revision_fingerprint(article):
                raise NewsRevisionConflictError(
                    "같은 source publication revision이 서로 다른 metadata/content를 가집니다."
                )
            duplicate = self._record_duplicate(news_id, article)
            self._primary[primary_key] = document_key
            self._dedupe[dedupe_key] = document_key
            return ApplyNewsResult(
                revision=same_source_revision,
                disposition=NewsWriteDisposition.DUPLICATE,
                duplicate_observation=duplicate,
            )

        if history:
            current = history[-1]
            if article.collected_at <= current.known_from:
                raise NewsTemporalConflictError(
                    "변경 publication revision의 collected_at은 현재 known_from보다 늦어야 합니다."
                )
            if article.source_revision <= current.article.source_revision:
                raise NewsTemporalConflictError(
                    "source_revision은 현재 publication revision보다 단조 증가해야 합니다."
                )
            if article.revision_of != current.article.source_revision:
                raise NewsRevisionConflictError(
                    "revision_of가 직전 source revision을 가리키지 않습니다."
                )
            history[-1] = replace(current, known_to=article.collected_at)
            revision = NewsArticleRevision(
                news_id=news_id,
                document_key=document_key,
                revision=current.revision + 1,
                article=article,
                known_from=article.collected_at,
                known_to=None,
            )
            disposition = NewsWriteDisposition.CREATED_REVISION
        else:
            if article.revision_of is not None:
                raise NewsRevisionConflictError(
                    "첫 관측 publication revision에는 revision_of가 없어야 합니다."
                )
            revision = NewsArticleRevision(
                news_id=news_id,
                document_key=document_key,
                revision=1,
                article=article,
                known_from=article.collected_at,
                known_to=None,
            )
            disposition = NewsWriteDisposition.CREATED
        history.append(revision)
        self._primary[primary_key] = document_key
        self._dedupe[dedupe_key] = document_key
        self._news_to_document[news_id] = document_key
        return ApplyNewsResult(revision=revision, disposition=disposition)

    def _record_duplicate(
        self, news_id: str, article: NewsArticle
    ) -> DuplicateObservation:
        observation = DuplicateObservation(
            news_id=news_id,
            source_id=article.source_id,
            source_item_id=article.source_item_id,
            source_revision=article.source_revision,
            canonical_url=article.canonical_url,
            content_hash=article.content_hash,
            observed_at=article.collected_at,
        )
        self._duplicates.append(observation)
        return observation

    def point_in_time(
        self,
        news_id: str,
        *,
        decision_at: datetime,
        require_evidence_visibility: bool = True,
    ) -> NewsArticleRevision | None:
        require_aware(decision_at, "decision_at")
        document_key = self._news_to_document.get(news_id)
        if document_key is None:
            return None
        for revision in reversed(self._histories[document_key]):
            visible = (
                revision.visible_for_evidence(decision_at)
                if require_evidence_visibility
                else revision.contains(decision_at)
            )
            if visible:
                return revision
        return None

    def visible_revisions(
        self, *, decision_at: datetime, limit: int
    ) -> tuple[NewsArticleRevision, ...]:
        require_aware(decision_at, "decision_at")
        if limit < 1:
            raise ValueError("limit은 1 이상이어야 합니다.")
        candidates = [
            revision
            for history in self._histories.values()
            for revision in history
            if revision.visible_for_evidence(decision_at)
        ]
        candidates.sort(
            key=lambda item: (
                item.article.published_at,
                item.article.collected_at,
                item.news_id,
            ),
            reverse=True,
        )
        return tuple(candidates[:limit])

    def history(self, news_id: str) -> tuple[NewsArticleRevision, ...]:
        document_key = self._news_to_document.get(news_id)
        if document_key is None:
            return ()
        return tuple(self._histories[document_key])

    @property
    def duplicate_observations(self) -> tuple[DuplicateObservation, ...]:
        return tuple(self._duplicates)
