from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from packages.identity import IdentityService, RuntimeOperatorStatus
from packages.operator import (
    RESOLVE_REVIEW,
    RESUME_JOB,
    RETRY_JOB,
    InMemoryOperatorRepository,
    JobStatus,
    OperatorAuditEntry,
    OperatorCommand,
    OperatorCommandRejected,
    OperatorCommandResult,
    OperatorConsole,
    OperatorJob,
    OperatorPage,
    OperatorRepository,
    OperatorReview,
    OperatorTargetNotFound,
    ReviewStatus,
    UnknownOperatorCursor,
)

from .app_types import JsonObject, JsonValue
from .errors import (
    InvalidApiRequest,
    OperatorCommandNotAllowed,
    ProductResourceNotFound,
    StaleOperatorVersion,
)

_SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9._-]+$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_RUNBOOK_KEY = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_SERVICE_STATUSES = {
    "RUNNING",
    "SUCCEEDED",
    "PARTIAL",
    "RATE_LIMITED",
    "AUTH_REQUIRED",
    "FAILED",
}
_MAXIMUM_REASON_LENGTH = 1000
_MAXIMUM_RESOLUTION_PROPERTIES = 20


class OperatorStatusSource(Protocol):
    def read_status(self) -> RuntimeOperatorStatus: ...


class StaticOperatorStatusSource:
    def __init__(self, status: RuntimeOperatorStatus) -> None:
        self._status = status

    def read_status(self) -> RuntimeOperatorStatus:
        return self._status


class OperatorBoundary:
    """Role gate plus an explicit allowlist projection for operator data.

    모든 진입점이 먼저 OPERATOR role을 확인하고, 상태를 바꾸는 command는
    Origin·CSRF·Idempotency-Key를 추가로 요구한다. 응답은 계약이 정한 field만
    담으며 내부 진단값(diagnostic_context·internal_context·command reason 원문)은
    어떤 경로로도 투영하지 않는다.
    """

    def __init__(
        self,
        *,
        identity_service: IdentityService,
        status_source: OperatorStatusSource,
        repository: OperatorRepository | None = None,
    ) -> None:
        self._identity_service = identity_service
        self._status_source = status_source
        self._console = OperatorConsole(repository or InMemoryOperatorRepository())

    def status(self, session_token: str | None) -> JsonObject:
        self._identity_service.require_operator(session_token)
        runtime = self._status_source.read_status()
        self._validate_runtime_status(runtime)
        return {
            "deploymentVersion": runtime.deployment_version,
            "commit": runtime.commit,
            "startedAt": runtime.started_at,
            "services": [
                {
                    "name": service.name,
                    "status": service.status,
                    "lastSucceededAt": service.last_succeeded_at,
                    "errorCode": service.error_code,
                }
                for service in runtime.services
            ],
        }

    def list_jobs(
        self,
        session_token: str | None,
        *,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> JsonObject:
        self._identity_service.require_operator(session_token)
        if status is not None and status not in _SERVICE_STATUSES:
            raise InvalidApiRequest("작업 상태 값을 확인해 주세요.")
        page = _guard(
            lambda: self._console.list_jobs(
                status=None if status is None else JobStatus(status),
                cursor=cursor,
                limit=limit,
            )
        )
        return _page(page, _job)

    def job(self, session_token: str | None, run_id: str) -> JsonObject:
        self._identity_service.require_operator(session_token)
        return _job(_guard(lambda: self._console.get_job(run_id)))

    def retry_job(
        self,
        session_token: str | None,
        run_id: str,
        *,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
        idempotency_key: str | None,
        body: bytes,
        now: datetime,
    ) -> JsonObject:
        command = self._command(
            session_token,
            action=RETRY_JOB,
            target_id=run_id,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
            idempotency_key=idempotency_key,
            body=body,
            with_resolution=False,
        )
        return _receipt(_guard(lambda: self._console.retry_job(command, now=now)))

    def resume_job(
        self,
        session_token: str | None,
        run_id: str,
        *,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
        idempotency_key: str | None,
        body: bytes,
        now: datetime,
    ) -> JsonObject:
        command = self._command(
            session_token,
            action=RESUME_JOB,
            target_id=run_id,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
            idempotency_key=idempotency_key,
            body=body,
            with_resolution=False,
        )
        return _receipt(_guard(lambda: self._console.resume_job(command, now=now)))

    def list_reviews(
        self,
        session_token: str | None,
        *,
        review_type: str | None,
        review_status: str | None,
        cursor: str | None,
        limit: int,
    ) -> JsonObject:
        self._identity_service.require_operator(session_token)
        if review_type is not None and _ERROR_CODE.fullmatch(review_type) is None:
            raise InvalidApiRequest("검수 유형 값을 확인해 주세요.")
        if review_status is not None and review_status != ReviewStatus.PENDING.value:
            raise InvalidApiRequest("검수 상태 값을 확인해 주세요.")
        page = _guard(
            lambda: self._console.list_reviews(
                review_type=review_type,
                review_status=(
                    None if review_status is None else ReviewStatus(review_status)
                ),
                cursor=cursor,
                limit=limit,
            )
        )
        return _page(page, _review)

    def review(self, session_token: str | None, review_id: str) -> JsonObject:
        self._identity_service.require_operator(session_token)
        return _review(_guard(lambda: self._console.get_review(review_id)))

    def resolve_review(
        self,
        session_token: str | None,
        review_id: str,
        *,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
        idempotency_key: str | None,
        body: bytes,
        now: datetime,
    ) -> JsonObject:
        command = self._command(
            session_token,
            action=RESOLVE_REVIEW,
            target_id=review_id,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
            idempotency_key=idempotency_key,
            body=body,
            with_resolution=True,
        )
        return _receipt(_guard(lambda: self._console.resolve_review(command, now=now)))

    def audit(
        self,
        session_token: str | None,
        *,
        cursor: str | None,
        limit: int,
    ) -> JsonObject:
        self._identity_service.require_operator(session_token)
        page = _guard(lambda: self._console.list_audit(cursor=cursor, limit=limit))
        return _page(page, _audit_entry)

    def infostock_auth_status(self, session_token: str | None) -> JsonObject:
        self._identity_service.require_operator(session_token)
        status = self._console.infostock_auth_status()
        if status.runbook_key is not None and (
            _RUNBOOK_KEY.fullmatch(status.runbook_key) is None
        ):
            raise ValueError("unsafe infostock runbook key")
        return {
            "status": status.status.value,
            "lastAuthenticatedAt": status.last_authenticated_at,
            "runbookKey": status.runbook_key,
        }

    def _command(
        self,
        session_token: str | None,
        *,
        action: str,
        target_id: str,
        origin: str | None,
        csrf_token: str | None,
        csrf_cookie: str | None,
        idempotency_key: str | None,
        body: bytes,
        with_resolution: bool,
    ) -> OperatorCommand:
        principal = self._identity_service.authorize_operator_command(
            session_token=session_token,
            origin=origin,
            csrf_token=csrf_token,
            csrf_cookie=csrf_cookie,
        )
        if idempotency_key is None or not 1 <= len(idempotency_key) <= 255:
            raise InvalidApiRequest("Idempotency-Key header가 필요합니다.")
        parsed = _command_body(body, with_resolution=with_resolution)
        return OperatorCommand(
            actor_id=principal.user.user_id,
            idempotency_key=idempotency_key,
            action=action,
            target_id=target_id,
            reason_code=parsed.reason_code,
            reason=parsed.reason,
            expected_version=parsed.expected_version,
            resolution=parsed.resolution,
        )

    @staticmethod
    def _validate_runtime_status(runtime: RuntimeOperatorStatus) -> None:
        if not 1 <= len(runtime.deployment_version) <= 128:
            raise ValueError("unsafe deployment version")
        if _SAFE_VERSION.fullmatch(runtime.deployment_version) is None:
            raise ValueError("unsafe deployment version")
        if not 7 <= len(runtime.commit) <= 128 or _SAFE_VERSION.fullmatch(runtime.commit) is None:
            raise ValueError("unsafe commit identifier")
        if len(runtime.services) > 100:
            raise ValueError("too many operator service records")
        for service in runtime.services:
            if _SERVICE_NAME.fullmatch(service.name) is None:
                raise ValueError("unsafe operator service name")
            if service.status not in _SERVICE_STATUSES:
                raise ValueError("unsafe operator service status")
            if service.error_code is not None and _ERROR_CODE.fullmatch(service.error_code) is None:
                raise ValueError("unsafe operator error code")


@dataclass(frozen=True, slots=True)
class _CommandBody:
    reason_code: str
    reason: str
    expected_version: int
    resolution: dict[str, object] | None


def _command_body(body: bytes, *, with_resolution: bool) -> _CommandBody:
    allowed = {"reasonCode", "reason", "expectedVersion"}
    if with_resolution:
        allowed.add("resolution")
    try:
        payload = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidApiRequest("요청 본문 형식을 확인해 주세요.") from error
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise InvalidApiRequest("요청 본문 형식을 확인해 주세요.")

    reason_code = payload["reasonCode"]
    reason = payload["reason"]
    expected_version = payload["expectedVersion"]
    if not isinstance(reason_code, str) or _ERROR_CODE.fullmatch(reason_code) is None:
        raise InvalidApiRequest("사유 코드를 확인해 주세요.")
    if not isinstance(reason, str) or not 1 <= len(reason) <= _MAXIMUM_REASON_LENGTH:
        raise InvalidApiRequest("사유를 1자 이상 1000자 이하로 적어 주세요.")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in reason):
        raise InvalidApiRequest("사유에 사용할 수 없는 문자가 있습니다.")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int):
        raise InvalidApiRequest("기대 revision을 확인해 주세요.")
    if expected_version < 1:
        raise InvalidApiRequest("기대 revision을 확인해 주세요.")

    resolution: dict[str, object] | None = None
    if with_resolution:
        raw = payload["resolution"]
        if (
            not isinstance(raw, dict)
            or not 1 <= len(raw) <= _MAXIMUM_RESOLUTION_PROPERTIES
            or any(not isinstance(key, str) for key in raw)
        ):
            raise InvalidApiRequest("검수 결과 형식을 확인해 주세요.")
        resolution = dict(raw)
    return _CommandBody(reason_code, reason, expected_version, resolution)


def _guard[ResultT](operation: Callable[[], ResultT]) -> ResultT:
    """도메인 오류를 계약 오류 코드로 옮긴다. 그 외 예외는 그대로 500이 된다."""

    try:
        return operation()
    except OperatorTargetNotFound as error:
        raise ProductResourceNotFound from error
    except UnknownOperatorCursor as error:
        raise InvalidApiRequest("다음 페이지 정보를 확인해 주세요.") from error
    except OperatorCommandRejected as error:
        if error.code == "STALE_VERSION":
            raise StaleOperatorVersion from error
        raise OperatorCommandNotAllowed from error


def _page[ItemT](
    page: OperatorPage[ItemT],
    project: Callable[[ItemT], JsonObject],
) -> JsonObject:
    items: list[JsonValue] = [project(item) for item in page.items]
    return {
        "items": items,
        "page": {
            "nextCursor": page.next_cursor,
            "hasMore": page.has_more,
            "limit": page.limit,
        },
    }


def _job(job: OperatorJob) -> JsonObject:
    if _OPAQUE_ID.fullmatch(job.run_id) is None:
        raise ValueError("unsafe operator run identifier")
    if _ERROR_CODE.fullmatch(job.job_type) is None:
        raise ValueError("unsafe operator job type")
    if job.error_code is not None and _ERROR_CODE.fullmatch(job.error_code) is None:
        raise ValueError("unsafe operator error code")
    if job.version < 1:
        raise ValueError("operator job revision must start at 1")
    return {
        "runId": job.run_id,
        "jobType": job.job_type,
        "status": job.status.value,
        "version": job.version,
        "lastChangedAt": job.last_changed_at,
        "errorCode": job.error_code,
    }


def _review(review: OperatorReview) -> JsonObject:
    if _OPAQUE_ID.fullmatch(review.review_id) is None:
        raise ValueError("unsafe operator review identifier")
    if _OPAQUE_ID.fullmatch(review.target_id) is None:
        raise ValueError("unsafe operator review target identifier")
    if _ERROR_CODE.fullmatch(review.review_type) is None:
        raise ValueError("unsafe operator review type")
    if _ERROR_CODE.fullmatch(review.reason_code) is None:
        raise ValueError("unsafe operator review reason code")
    if review.version < 1:
        raise ValueError("operator review revision must start at 1")
    return {
        "reviewId": review.review_id,
        "reviewType": review.review_type,
        "reviewStatus": review.review_status.value,
        "targetId": review.target_id,
        "reasonCode": review.reason_code,
        "version": review.version,
        "createdAt": review.created_at,
        "resolvedAt": review.resolved_at,
    }


def _audit_entry(entry: OperatorAuditEntry) -> JsonObject:
    _validate_audit(entry)
    return {
        "auditId": entry.audit_id,
        "actorId": entry.actor_id,
        "occurredAt": entry.occurred_at,
        "action": entry.action,
        "targetId": entry.target_id,
        "reasonCode": entry.reason_code,
        "beforeRevision": entry.before_revision,
        "afterRevision": entry.after_revision,
    }


def _receipt(result: OperatorCommandResult) -> JsonObject:
    entry = result.audit
    _validate_audit(entry)
    return {
        "auditId": entry.audit_id,
        "actorId": entry.actor_id,
        "occurredAt": entry.occurred_at,
        "targetId": entry.target_id,
        "beforeRevision": entry.before_revision,
        "afterRevision": entry.after_revision,
    }


def _validate_audit(entry: OperatorAuditEntry) -> None:
    if _OPAQUE_ID.fullmatch(entry.audit_id) is None:
        raise ValueError("unsafe operator audit identifier")
    if _OPAQUE_ID.fullmatch(entry.actor_id) is None:
        raise ValueError("unsafe operator actor identifier")
    if _OPAQUE_ID.fullmatch(entry.target_id) is None:
        raise ValueError("unsafe operator audit target identifier")
    if _ERROR_CODE.fullmatch(entry.action) is None:
        raise ValueError("unsafe operator audit action")
    if _ERROR_CODE.fullmatch(entry.reason_code) is None:
        raise ValueError("unsafe operator audit reason code")
