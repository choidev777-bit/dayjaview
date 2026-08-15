from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class JobStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FAILED = "FAILED"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"


class InfostockAuthState(StrEnum):
    READY = "READY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNKNOWN = "UNKNOWN"


# 실패한 run은 다시 처음부터, 부분 성공한 run은 이어서. 그 외 상태는 command를 받지 않는다.
RETRYABLE_JOB_STATUSES = frozenset(
    {JobStatus.FAILED, JobStatus.RATE_LIMITED, JobStatus.AUTH_REQUIRED}
)
RESUMABLE_JOB_STATUSES = frozenset({JobStatus.PARTIAL})


@dataclass(frozen=True, slots=True)
class OperatorJob:
    """batch·ingestion run 한 건.

    internal_context는 내부 진단용이며 어떤 응답에도 투영하지 않는다
    (packages/identity의 RuntimeServiceStatus.diagnostic_context와 같은 규칙).
    """

    run_id: str
    job_type: str
    status: JobStatus
    version: int
    last_changed_at: datetime
    error_code: str | None
    internal_context: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class OperatorReview:
    review_id: str
    review_type: str
    review_status: ReviewStatus
    target_id: str
    reason_code: str
    version: int
    created_at: datetime
    resolved_at: datetime | None
    internal_context: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class OperatorAuditEntry:
    """immutable audit 1건. reason 원문은 기록만 하고 응답에는 투영하지 않는다."""

    audit_id: str
    actor_id: str
    occurred_at: datetime
    action: str
    target_id: str
    reason_code: str
    reason: str
    before_revision: int | None
    after_revision: int | None


@dataclass(frozen=True, slots=True)
class InfostockAuthStatus:
    """인포스탁 인증 준비 상태. cookie·session state 원문은 담지 않는다."""

    status: InfostockAuthState
    last_authenticated_at: datetime | None
    runbook_key: str | None


@dataclass(frozen=True, slots=True)
class OperatorCommand:
    actor_id: str
    idempotency_key: str
    action: str
    target_id: str
    reason_code: str
    reason: str
    expected_version: int
    resolution: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class OperatorCommandReceipt:
    """실행된 command 1건의 결과. 같은 key의 재요청은 이 값을 그대로 돌려준다."""

    actor_id: str
    idempotency_key: str
    fingerprint: str
    audit: OperatorAuditEntry


@dataclass(frozen=True, slots=True)
class OperatorCommandResult:
    audit: OperatorAuditEntry
    replayed: bool


@dataclass(frozen=True, slots=True)
class OperatorPage[ItemT]:
    items: tuple[ItemT, ...]
    next_cursor: str | None
    has_more: bool
    limit: int
