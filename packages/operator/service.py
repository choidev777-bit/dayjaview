from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime

from .errors import (
    OperatorCommandRejected,
    OperatorTargetNotFound,
)
from .models import (
    RESUMABLE_JOB_STATUSES,
    RETRYABLE_JOB_STATUSES,
    InfostockAuthStatus,
    JobStatus,
    OperatorAuditEntry,
    OperatorCommand,
    OperatorCommandReceipt,
    OperatorCommandResult,
    OperatorJob,
    OperatorPage,
    OperatorReview,
    ReviewStatus,
)
from .repository import OperatorRepository

RETRY_JOB = "RETRY_JOB"
RESUME_JOB = "RESUME_JOB"
RESOLVE_REVIEW = "RESOLVE_REVIEW"


class OperatorConsole:
    """운영자 읽기 조회와 audit 가능한 command를 한 곳에서 처리한다.

    command 3개(retry·resume·resolve)는 모두 같은 순서를 지킨다: 같은
    Idempotency-Key의 재요청은 저장된 결과를 재생하고, 대상이 없으면 not found,
    expectedVersion이 다르면 STALE_VERSION, 현재 상태에서 허용되지 않는 전이면
    COMMAND_NOT_ALLOWED다. 실행한 command만 revision을 올리고 audit을 남긴다.
    """

    def __init__(self, repository: OperatorRepository) -> None:
        self._repository = repository

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorJob]:
        return self._repository.list_jobs(status=status, cursor=cursor, limit=limit)

    def get_job(self, run_id: str) -> OperatorJob:
        job = self._repository.get_job(run_id)
        if job is None:
            raise OperatorTargetNotFound
        return job

    def list_reviews(
        self,
        *,
        review_type: str | None,
        review_status: ReviewStatus | None,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorReview]:
        return self._repository.list_reviews(
            review_type=review_type,
            review_status=review_status,
            cursor=cursor,
            limit=limit,
        )

    def get_review(self, review_id: str) -> OperatorReview:
        review = self._repository.get_review(review_id)
        if review is None:
            raise OperatorTargetNotFound
        return review

    def list_audit(
        self,
        *,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorAuditEntry]:
        return self._repository.list_audit(cursor=cursor, limit=limit)

    def infostock_auth_status(self) -> InfostockAuthStatus:
        return self._repository.infostock_auth_status()

    def retry_job(self, command: OperatorCommand, *, now: datetime) -> OperatorCommandResult:
        return self._run_job_command(command, now=now, allowed=RETRYABLE_JOB_STATUSES)

    def resume_job(self, command: OperatorCommand, *, now: datetime) -> OperatorCommandResult:
        return self._run_job_command(command, now=now, allowed=RESUMABLE_JOB_STATUSES)

    def _run_job_command(
        self,
        command: OperatorCommand,
        *,
        now: datetime,
        allowed: frozenset[JobStatus],
    ) -> OperatorCommandResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        job = self._repository.get_job(command.target_id)
        if job is None:
            raise OperatorTargetNotFound
        if job.version != command.expected_version:
            raise OperatorCommandRejected("STALE_VERSION")
        if job.status not in allowed:
            raise OperatorCommandRejected("COMMAND_NOT_ALLOWED")
        started = self._repository.start_job_attempt(job.run_id, now=now)
        return self._record(
            command,
            now=now,
            before_revision=job.version,
            after_revision=started.version,
        )

    def resolve_review(
        self,
        command: OperatorCommand,
        *,
        now: datetime,
    ) -> OperatorCommandResult:
        replay = self._replay(command)
        if replay is not None:
            return replay
        if command.resolution is None:
            raise OperatorCommandRejected("COMMAND_NOT_ALLOWED")
        review = self._repository.get_review(command.target_id)
        if review is None:
            raise OperatorTargetNotFound
        if review.version != command.expected_version:
            raise OperatorCommandRejected("STALE_VERSION")
        if review.review_status is not ReviewStatus.PENDING:
            raise OperatorCommandRejected("COMMAND_NOT_ALLOWED")
        resolved = self._repository.resolve_review(
            review.review_id,
            resolution=command.resolution,
            now=now,
        )
        return self._record(
            command,
            now=now,
            before_revision=review.version,
            after_revision=resolved.version,
        )

    def _replay(self, command: OperatorCommand) -> OperatorCommandResult | None:
        receipt = self._repository.find_receipt(
            actor_id=command.actor_id,
            idempotency_key=command.idempotency_key,
        )
        if receipt is None:
            return None
        if receipt.fingerprint != _fingerprint(command):
            raise OperatorCommandRejected("COMMAND_NOT_ALLOWED")
        return OperatorCommandResult(receipt.audit, replayed=True)

    def _record(
        self,
        command: OperatorCommand,
        *,
        now: datetime,
        before_revision: int | None,
        after_revision: int | None,
    ) -> OperatorCommandResult:
        audit = self._repository.append_audit(
            actor_id=command.actor_id,
            occurred_at=now,
            action=command.action,
            target_id=command.target_id,
            reason_code=command.reason_code,
            reason=command.reason,
            before_revision=before_revision,
            after_revision=after_revision,
        )
        self._repository.store_receipt(
            OperatorCommandReceipt(
                actor_id=command.actor_id,
                idempotency_key=command.idempotency_key,
                fingerprint=_fingerprint(command),
                audit=audit,
            )
        )
        return OperatorCommandResult(audit, replayed=False)


def _fingerprint(command: OperatorCommand) -> str:
    """같은 key로 다른 내용을 보내는 것과 진짜 재시도를 구분하는 지문."""

    payload: dict[str, object] = {
        "action": command.action,
        "expectedVersion": command.expected_version,
        "reason": command.reason,
        "reasonCode": command.reason_code,
        "resolution": _stable(command.resolution),
        "targetId": command.target_id,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable(value: Mapping[str, object] | None) -> object:
    return None if value is None else dict(sorted(value.items()))
