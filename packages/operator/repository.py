from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from .errors import UnknownOperatorCursor
from .models import (
    InfostockAuthState,
    InfostockAuthStatus,
    JobStatus,
    OperatorAuditEntry,
    OperatorCommandReceipt,
    OperatorJob,
    OperatorPage,
    OperatorReview,
    ReviewStatus,
)


class OperatorRepository(Protocol):
    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorJob]: ...

    def get_job(self, run_id: str) -> OperatorJob | None: ...

    def start_job_attempt(self, run_id: str, *, now: datetime) -> OperatorJob:
        """run을 다시 실행 대기 상태로 만들고 version을 올린다."""
        ...

    def list_reviews(
        self,
        *,
        review_type: str | None,
        review_status: ReviewStatus | None,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorReview]: ...

    def get_review(self, review_id: str) -> OperatorReview | None: ...

    def resolve_review(
        self,
        review_id: str,
        *,
        resolution: Mapping[str, object],
        now: datetime,
    ) -> OperatorReview: ...

    def append_audit(
        self,
        *,
        actor_id: str,
        occurred_at: datetime,
        action: str,
        target_id: str,
        reason_code: str,
        reason: str,
        before_revision: int | None,
        after_revision: int | None,
    ) -> OperatorAuditEntry: ...

    def list_audit(
        self,
        *,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorAuditEntry]: ...

    def find_receipt(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> OperatorCommandReceipt | None: ...

    def store_receipt(self, receipt: OperatorCommandReceipt) -> None: ...

    def infostock_auth_status(self) -> InfostockAuthStatus: ...


class InMemoryOperatorRepository:
    """프로세스 안에서만 사는 운영자 저장소.

    job·review는 이 프로세스가 관측한 것만 담는다. 수집 worker와 정합 실행이
    record_job·open_review로 넣어 준다. audit은 append-only이고 삭제 경로가 없다.
    """

    def __init__(
        self,
        *,
        jobs: Sequence[OperatorJob] = (),
        reviews: Sequence[OperatorReview] = (),
        infostock_auth: InfostockAuthStatus | None = None,
    ) -> None:
        self._jobs: dict[str, OperatorJob] = {job.run_id: job for job in jobs}
        self._reviews: dict[str, OperatorReview] = {
            review.review_id: review for review in reviews
        }
        self._audit: list[OperatorAuditEntry] = []
        self._receipts: dict[tuple[str, str], OperatorCommandReceipt] = {}
        self._infostock_auth = infostock_auth or InfostockAuthStatus(
            status=InfostockAuthState.UNKNOWN,
            last_authenticated_at=None,
            runbook_key=None,
        )

    def record_job(self, job: OperatorJob) -> None:
        self._jobs[job.run_id] = job

    def open_review(self, review: OperatorReview) -> None:
        self._reviews[review.review_id] = review

    def set_infostock_auth_status(self, status: InfostockAuthStatus) -> None:
        self._infostock_auth = status

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorJob]:
        ordered = sorted(
            (job for job in self._jobs.values() if status is None or job.status is status),
            key=lambda job: (-job.last_changed_at.timestamp(), job.run_id),
        )
        return _paginate(ordered, cursor=cursor, limit=limit, key=lambda job: job.run_id)

    def get_job(self, run_id: str) -> OperatorJob | None:
        return self._jobs.get(run_id)

    def start_job_attempt(self, run_id: str, *, now: datetime) -> OperatorJob:
        job = self._jobs[run_id]
        started = OperatorJob(
            run_id=job.run_id,
            job_type=job.job_type,
            status=JobStatus.RUNNING,
            version=job.version + 1,
            last_changed_at=now,
            error_code=None,
            internal_context=job.internal_context,
        )
        self._jobs[run_id] = started
        return started

    def list_reviews(
        self,
        *,
        review_type: str | None,
        review_status: ReviewStatus | None,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorReview]:
        ordered = sorted(
            (
                review
                for review in self._reviews.values()
                if (review_type is None or review.review_type == review_type)
                and (review_status is None or review.review_status is review_status)
            ),
            key=lambda review: (review.created_at, review.review_id),
        )
        return _paginate(
            ordered,
            cursor=cursor,
            limit=limit,
            key=lambda review: review.review_id,
        )

    def get_review(self, review_id: str) -> OperatorReview | None:
        return self._reviews.get(review_id)

    def resolve_review(
        self,
        review_id: str,
        *,
        resolution: Mapping[str, object],
        now: datetime,
    ) -> OperatorReview:
        review = self._reviews[review_id]
        resolved = OperatorReview(
            review_id=review.review_id,
            review_type=review.review_type,
            review_status=ReviewStatus.RESOLVED,
            target_id=review.target_id,
            reason_code=review.reason_code,
            version=review.version + 1,
            created_at=review.created_at,
            resolved_at=now,
            internal_context={**review.internal_context, "resolution": dict(resolution)},
        )
        self._reviews[review_id] = resolved
        return resolved

    def append_audit(
        self,
        *,
        actor_id: str,
        occurred_at: datetime,
        action: str,
        target_id: str,
        reason_code: str,
        reason: str,
        before_revision: int | None,
        after_revision: int | None,
    ) -> OperatorAuditEntry:
        entry = OperatorAuditEntry(
            audit_id=f"aud_{len(self._audit) + 1:08d}",
            actor_id=actor_id,
            occurred_at=occurred_at,
            action=action,
            target_id=target_id,
            reason_code=reason_code,
            reason=reason,
            before_revision=before_revision,
            after_revision=after_revision,
        )
        self._audit.append(entry)
        return entry

    def list_audit(
        self,
        *,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorAuditEntry]:
        ordered = sorted(
            self._audit,
            key=lambda entry: (-entry.occurred_at.timestamp(), entry.audit_id),
        )
        return _paginate(
            ordered,
            cursor=cursor,
            limit=limit,
            key=lambda entry: entry.audit_id,
        )

    def find_receipt(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> OperatorCommandReceipt | None:
        return self._receipts.get((actor_id, idempotency_key))

    def store_receipt(self, receipt: OperatorCommandReceipt) -> None:
        self._receipts[(receipt.actor_id, receipt.idempotency_key)] = receipt

    def infostock_auth_status(self) -> InfostockAuthStatus:
        return self._infostock_auth


def _paginate[ItemT](
    ordered: Sequence[ItemT],
    *,
    cursor: str | None,
    limit: int,
    key: Callable[[ItemT], str],
) -> OperatorPage[ItemT]:
    """cursor는 직전 페이지 마지막 항목의 식별자다. 정렬은 호출자가 정한다."""

    start = 0
    if cursor is not None:
        identifiers = [key(item) for item in ordered]
        if cursor not in identifiers:
            raise UnknownOperatorCursor
        start = identifiers.index(cursor) + 1
    window = tuple(ordered[start : start + limit])
    has_more = start + limit < len(ordered)
    next_cursor = key(window[-1]) if has_more and window else None
    return OperatorPage(window, next_cursor, has_more, limit)
