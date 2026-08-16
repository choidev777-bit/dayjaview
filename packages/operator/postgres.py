from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, cast

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


class DbCursor(Protocol):
    rowcount: int

    def execute(
        self, query: str, params: Sequence[object] | None = None
    ) -> object: ...

    def fetchone(self) -> Sequence[Any] | None: ...

    def fetchall(self) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


class DbConnection(Protocol):
    def cursor(self) -> DbCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


def _mapping(value: object) -> dict[str, object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise TypeError("operator internal_context는 JSON object여야 합니다")
    return cast(dict[str, object], decoded)


def _job(row: Sequence[Any]) -> OperatorJob:
    return OperatorJob(
        run_id=str(row[0]),
        job_type=str(row[1]),
        status=JobStatus(str(row[2])),
        version=int(row[3]),
        last_changed_at=cast(datetime, row[4]),
        error_code=None if row[5] is None else str(row[5]),
        internal_context=_mapping(row[6]),
    )


def _review(row: Sequence[Any]) -> OperatorReview:
    context = _mapping(row[8])
    if row[9] is not None:
        context = {**context, "resolution": _mapping(row[9])}
    return OperatorReview(
        review_id=str(row[0]),
        review_type=str(row[1]),
        review_status=ReviewStatus(str(row[2])),
        target_id=str(row[3]),
        reason_code=str(row[4]),
        version=int(row[5]),
        created_at=cast(datetime, row[6]),
        resolved_at=cast(datetime | None, row[7]),
        internal_context=context,
    )


def _audit(row: Sequence[Any]) -> OperatorAuditEntry:
    return OperatorAuditEntry(
        audit_id=f"aud_{int(row[0]):08d}",
        actor_id=str(row[1]),
        occurred_at=cast(datetime, row[2]),
        action=str(row[3]),
        target_id=str(row[4]),
        reason_code=str(row[5]),
        reason=str(row[6]),
        before_revision=None if row[7] is None else int(row[7]),
        after_revision=None if row[8] is None else int(row[8]),
    )


def _page[ItemT](
    items: Sequence[ItemT],
    *,
    cursor: str | None,
    limit: int,
    identifier: Callable[[ItemT], str],
) -> OperatorPage[ItemT]:
    identifiers = [identifier(item) for item in items]
    start = 0
    if cursor is not None:
        if cursor not in identifiers:
            raise UnknownOperatorCursor
        start = identifiers.index(cursor) + 1
    window = tuple(items[start : start + limit])
    has_more = start + limit < len(items)
    next_cursor = identifier(window[-1]) if has_more and window else None
    return OperatorPage(window, next_cursor, has_more, limit)


_JOB_COLUMNS = (
    "run_id, job_type, status, version, last_changed_at, error_code, "
    "internal_context"
)
_REVIEW_COLUMNS = (
    "review_id, review_type, review_status, target_id, reason_code, version, "
    "created_at, resolved_at, internal_context, resolution"
)
_AUDIT_COLUMNS = (
    "audit_id, actor_id, occurred_at, action, target_id, reason_code, reason, "
    "before_revision, after_revision"
)


class PostgresOperatorRepository:
    def __init__(self, connection: DbConnection) -> None:
        self._connection = connection

    def list_jobs(
        self,
        *,
        status: JobStatus | None,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorJob]:
        db = self._connection.cursor()
        try:
            if status is None:
                db.execute(
                    f"SELECT {_JOB_COLUMNS} FROM operations.jobs "
                    "ORDER BY last_changed_at DESC, run_id"
                )
            else:
                db.execute(
                    f"SELECT {_JOB_COLUMNS} FROM operations.jobs WHERE status = %s "
                    "ORDER BY last_changed_at DESC, run_id",
                    (status.value,),
                )
            items = tuple(_job(row) for row in db.fetchall())
            return _page(
                items, cursor=cursor, limit=limit, identifier=lambda item: item.run_id
            )
        finally:
            db.close()

    def get_job(self, run_id: str) -> OperatorJob | None:
        db = self._connection.cursor()
        try:
            db.execute(
                f"SELECT {_JOB_COLUMNS} FROM operations.jobs WHERE run_id = %s",
                (run_id,),
            )
            row = db.fetchone()
            return None if row is None else _job(row)
        finally:
            db.close()

    def set_job_status(
        self,
        *,
        run_id: str,
        job_type: str,
        status: JobStatus,
        now: datetime,
        error_code: str | None = None,
        internal_context: Mapping[str, object] | None = None,
    ) -> OperatorJob:
        context = dict(internal_context or {})
        db = self._connection.cursor()
        try:
            db.execute(
                """
                INSERT INTO operations.jobs (
                    run_id, job_type, status, version, last_changed_at,
                    error_code, internal_context
                ) VALUES (%s, %s, %s, 1, %s, %s, %s::jsonb)
                ON CONFLICT (run_id) DO UPDATE
                   SET job_type = EXCLUDED.job_type,
                       status = EXCLUDED.status,
                       version = jobs.version + 1,
                       last_changed_at = EXCLUDED.last_changed_at,
                       error_code = EXCLUDED.error_code,
                       internal_context = EXCLUDED.internal_context
                 WHERE jobs.job_type <> EXCLUDED.job_type
                    OR jobs.status <> EXCLUDED.status
                    OR jobs.error_code IS DISTINCT FROM EXCLUDED.error_code
                    OR jobs.internal_context <> EXCLUDED.internal_context
                RETURNING run_id, job_type, status, version, last_changed_at,
                          error_code, internal_context
                """,
                (
                    run_id,
                    job_type,
                    status.value,
                    now,
                    error_code,
                    json.dumps(context, ensure_ascii=False, sort_keys=True),
                ),
            )
            row = db.fetchone()
            if row is None:
                db.execute(
                    f"SELECT {_JOB_COLUMNS} FROM operations.jobs WHERE run_id = %s",
                    (run_id,),
                )
                row = db.fetchone()
            if row is None:
                raise RuntimeError("operator job 저장 결과가 없습니다")
            self._connection.commit()
            return _job(row)
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    def start_job_attempt(self, run_id: str, *, now: datetime) -> OperatorJob:
        db = self._connection.cursor()
        try:
            db.execute(
                f"""
                UPDATE operations.jobs
                   SET status = 'RUNNING', version = version + 1,
                       last_changed_at = %s, error_code = NULL
                 WHERE run_id = %s
                RETURNING {_JOB_COLUMNS}
                """,
                (now, run_id),
            )
            row = db.fetchone()
            if row is None:
                raise KeyError(run_id)
            self._connection.commit()
            return _job(row)
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    def list_reviews(
        self,
        *,
        review_type: str | None,
        review_status: ReviewStatus | None,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorReview]:
        clauses: list[str] = []
        params: list[object] = []
        if review_type is not None:
            clauses.append("review_type = %s")
            params.append(review_type)
        if review_status is not None:
            clauses.append("review_status = %s")
            params.append(review_status.value)
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        db = self._connection.cursor()
        try:
            db.execute(
                f"SELECT {_REVIEW_COLUMNS} FROM operations.reviews{where} "
                "ORDER BY created_at, review_id",
                tuple(params),
            )
            items = tuple(_review(row) for row in db.fetchall())
            return _page(
                items,
                cursor=cursor,
                limit=limit,
                identifier=lambda item: item.review_id,
            )
        finally:
            db.close()

    def get_review(self, review_id: str) -> OperatorReview | None:
        db = self._connection.cursor()
        try:
            db.execute(
                f"SELECT {_REVIEW_COLUMNS} FROM operations.reviews WHERE review_id = %s",
                (review_id,),
            )
            row = db.fetchone()
            return None if row is None else _review(row)
        finally:
            db.close()

    def open_review(self, review: OperatorReview) -> None:
        db = self._connection.cursor()
        try:
            db.execute(
                """
                INSERT INTO operations.reviews (
                    review_id, review_type, review_status, target_id,
                    reason_code, version, created_at, resolved_at,
                    internal_context, resolution
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NULL)
                ON CONFLICT (review_id) DO NOTHING
                """,
                (
                    review.review_id,
                    review.review_type,
                    review.review_status.value,
                    review.target_id,
                    review.reason_code,
                    review.version,
                    review.created_at,
                    review.resolved_at,
                    json.dumps(
                        dict(review.internal_context),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                ),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    def resolve_review(
        self,
        review_id: str,
        *,
        resolution: Mapping[str, object],
        now: datetime,
    ) -> OperatorReview:
        db = self._connection.cursor()
        try:
            db.execute(
                f"""
                UPDATE operations.reviews
                   SET review_status = 'RESOLVED', version = version + 1,
                       resolved_at = %s, resolution = %s::jsonb
                 WHERE review_id = %s
                RETURNING {_REVIEW_COLUMNS}
                """,
                (
                    now,
                    json.dumps(dict(resolution), ensure_ascii=False, sort_keys=True),
                    review_id,
                ),
            )
            row = db.fetchone()
            if row is None:
                raise KeyError(review_id)
            self._connection.commit()
            return _review(row)
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

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
        db = self._connection.cursor()
        try:
            db.execute(
                f"""
                INSERT INTO operations.audit_entries (
                    actor_id, occurred_at, action, target_id, reason_code,
                    reason, before_revision, after_revision
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_AUDIT_COLUMNS}
                """,
                (
                    actor_id,
                    occurred_at,
                    action,
                    target_id,
                    reason_code,
                    reason,
                    before_revision,
                    after_revision,
                ),
            )
            row = db.fetchone()
            if row is None:
                raise RuntimeError("operator audit 저장 결과가 없습니다")
            self._connection.commit()
            return _audit(row)
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    def list_audit(
        self,
        *,
        cursor: str | None,
        limit: int,
    ) -> OperatorPage[OperatorAuditEntry]:
        db = self._connection.cursor()
        try:
            db.execute(
                f"SELECT {_AUDIT_COLUMNS} FROM operations.audit_entries "
                "ORDER BY occurred_at DESC, audit_id DESC"
            )
            items = tuple(_audit(row) for row in db.fetchall())
            return _page(
                items,
                cursor=cursor,
                limit=limit,
                identifier=lambda item: item.audit_id,
            )
        finally:
            db.close()

    def find_receipt(
        self,
        *,
        actor_id: str,
        idempotency_key: str,
    ) -> OperatorCommandReceipt | None:
        db = self._connection.cursor()
        try:
            db.execute(
                f"""
                SELECT receipt.actor_id, receipt.idempotency_key,
                       receipt.fingerprint, audit.audit_id, audit.actor_id,
                       audit.occurred_at, audit.action, audit.target_id,
                       audit.reason_code, audit.reason, audit.before_revision,
                       audit.after_revision
                  FROM operations.command_receipts AS receipt
                  JOIN operations.audit_entries AS audit
                    ON audit.audit_id = receipt.audit_id
                 WHERE receipt.actor_id = %s AND receipt.idempotency_key = %s
                """,
                (actor_id, idempotency_key),
            )
            row = db.fetchone()
            if row is None:
                return None
            return OperatorCommandReceipt(
                actor_id=str(row[0]),
                idempotency_key=str(row[1]),
                fingerprint=str(row[2]),
                audit=_audit(row[3:]),
            )
        finally:
            db.close()

    def store_receipt(self, receipt: OperatorCommandReceipt) -> None:
        audit_id = int(receipt.audit.audit_id.removeprefix("aud_"))
        db = self._connection.cursor()
        try:
            db.execute(
                """
                INSERT INTO operations.command_receipts (
                    actor_id, idempotency_key, fingerprint, audit_id
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (actor_id, idempotency_key) DO NOTHING
                """,
                (
                    receipt.actor_id,
                    receipt.idempotency_key,
                    receipt.fingerprint,
                    audit_id,
                ),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()

    def infostock_auth_status(self) -> InfostockAuthStatus:
        db = self._connection.cursor()
        try:
            db.execute(
                """
                SELECT status, last_authenticated_at, runbook_key
                  FROM operations.infostock_auth_status WHERE singleton
                """
            )
            row = db.fetchone()
            if row is None:
                return InfostockAuthStatus(InfostockAuthState.UNKNOWN, None, None)
            return InfostockAuthStatus(
                status=InfostockAuthState(str(row[0])),
                last_authenticated_at=cast(datetime | None, row[1]),
                runbook_key=None if row[2] is None else str(row[2]),
            )
        finally:
            db.close()

    def set_infostock_auth_status(self, status: InfostockAuthStatus) -> None:
        db = self._connection.cursor()
        try:
            db.execute(
                """
                UPDATE operations.infostock_auth_status
                   SET status = %s, last_authenticated_at = %s, runbook_key = %s
                 WHERE singleton
                """,
                (
                    status.status.value,
                    status.last_authenticated_at,
                    status.runbook_key,
                ),
            )
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        finally:
            db.close()
