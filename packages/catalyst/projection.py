"""저장된 근거를 공개 read model로 투영한다. 내부 점수는 내보내지 않는다."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .models import CatalystEvidence, EvidenceRevision

DEFAULT_PAGE_LIMIT = 20


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def evidence_item(item: CatalystEvidence) -> dict[str, object]:
    return {
        "newsId": item.news_id,
        "sourceName": item.publisher,
        "title": item.title,
        "publishedAt": _timestamp(item.published_at),
        "receivedAt": item.received_at.isoformat(),
        "originalUrl": item.original_url,
        "matchBasis": [basis.value for basis in item.match_basis],
        "summary": item.summary,
        "qualityFlags": list(item.quality_flags),
    }


def evidence_list_data(
    revision: EvidenceRevision,
    evidence: Sequence[CatalystEvidence],
    *,
    limit: int = DEFAULT_PAGE_LIMIT,
) -> dict[str, object]:
    selected = [item for item in evidence if item.news_id in set(revision.news_ids)]
    page = selected[:limit]
    return {
        "eventId": revision.event_id,
        "evidenceStatus": revision.evidence_status.value,
        "items": [evidence_item(item) for item in page],
        "page": {
            "nextCursor": page[-1].news_id if len(selected) > limit else None,
            "hasMore": len(selected) > limit,
            "limit": limit,
        },
    }


def evidence_summary(
    revision: EvidenceRevision,
    evidence: Sequence[CatalystEvidence],
) -> dict[str, object]:
    selected = [item for item in evidence if item.news_id in set(revision.news_ids)]
    published = [item.published_at for item in selected if item.published_at is not None]
    publishers = {item.publisher.strip().casefold() for item in selected if item.publisher.strip()}
    return {
        "evidenceStatus": revision.evidence_status.value,
        "summary": revision.summary,
        "sourceCount": len(publishers),
        "latestPublishedAt": _timestamp(max(published)) if published else None,
    }
