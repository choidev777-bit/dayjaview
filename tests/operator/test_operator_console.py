from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response

from apps.api import ApiSettings, create_fixture_app
from packages.identity import GoogleIdentity
from packages.operator import (
    InfostockAuthState,
    InfostockAuthStatus,
    InMemoryOperatorRepository,
    JobStatus,
    OperatorCommandReceipt,
    OperatorJob,
    OperatorReview,
    PostgresOperatorRepository,
    ReviewStatus,
)
from scripts.validate_contracts import validate_instance
from tests.identity.helpers import MutableClock, api_login

_BASE = datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
_OPERATOR_EMAIL = "operator@example.test"
_OPERATOR_TEST_DSN = os.environ.get("OPERATOR_TEST_DSN")
_OPERATOR_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "infra/migrations/0006_operator_runtime.sql"
)


def _repository() -> InMemoryOperatorRepository:
    return InMemoryOperatorRepository(
        jobs=(
            OperatorJob(
                run_id="run_infostock_daily",
                job_type="INFOSTOCK_DAILY_INCREMENT",
                status=JobStatus.AUTH_REQUIRED,
                version=1,
                last_changed_at=_BASE,
                error_code="AUTH_REQUIRED",
                internal_context={"cookie": "fixture-cookie-must-not-leak"},
            ),
            OperatorJob(
                run_id="run_reconcile",
                job_type="AFTER_CLOSE_RECONCILE",
                status=JobStatus.PARTIAL,
                version=1,
                last_changed_at=_BASE - timedelta(minutes=5),
                error_code=None,
            ),
            OperatorJob(
                run_id="run_reference_data",
                job_type="REFERENCE_DATA_DAILY",
                status=JobStatus.SUCCEEDED,
                version=3,
                last_changed_at=_BASE - timedelta(minutes=10),
                error_code=None,
            ),
        ),
        reviews=(
            OperatorReview(
                review_id="review_1",
                review_type="EVENT_RECONCILIATION",
                review_status=ReviewStatus.PENDING,
                target_id="evt_unmatched",
                reason_code="RECONCILIATION_UNMATCHED",
                version=1,
                created_at=_BASE,
                resolved_at=None,
                internal_context={"note": "fixture-review-must-not-leak"},
            ),
        ),
        infostock_auth=InfostockAuthStatus(
            status=InfostockAuthState.AUTH_REQUIRED,
            last_authenticated_at=_BASE - timedelta(days=1),
            runbook_key="infostock-session-refresh",
        ),
    )


def _run(
    scenario: Callable[[AsyncClient, Any], Awaitable[None]],
    *,
    operator: bool = True,
    repository: InMemoryOperatorRepository | None = None,
) -> None:
    async def main() -> None:
        clock = MutableClock(_BASE)
        environment = create_fixture_app(
            settings=ApiSettings(operator_bootstrap_emails=frozenset({_OPERATOR_EMAIL})),
            clock=clock,
            operator_repository=repository or _repository(),
        )
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            await api_login(
                client,
                environment,
                code="operator-console",
                identity=GoogleIdentity(
                    "google-operator-console",
                    "운영 사용자",
                    email=_OPERATOR_EMAIL if operator else "user@example.test",
                    email_verified=True,
                ),
            )
            await scenario(client, environment)

    asyncio.run(main())


def _command_headers(client: AsyncClient, key: str) -> dict[str, str]:
    return {
        "Origin": "https://dayjaview.vercel.app",
        "X-CSRF-Token": client.cookies["__Host-dayjaview_csrf"],
        "Idempotency-Key": key,
    }


def _post(
    client: AsyncClient,
    path: str,
    *,
    key: str,
    body: dict[str, object],
) -> Awaitable[Response]:
    return client.post(path, headers=_command_headers(client, key), json=body)


def test_regular_user_is_denied_on_every_operator_surface() -> None:
    async def scenario(client: AsyncClient, _: Any) -> None:
        for path in (
            "/v1/operator/jobs",
            "/v1/operator/jobs/run_infostock_daily",
            "/v1/operator/reviews",
            "/v1/operator/reviews/review_1",
            "/v1/operator/audit",
            "/v1/operator/infostock/auth-status",
        ):
            response = await client.get(path)
            assert response.status_code == 403, path
            assert response.json()["error"]["code"] == "FEATURE_NOT_ENTITLED"
        for path in (
            "/v1/operator/jobs/run_infostock_daily/retry",
            "/v1/operator/jobs/run_reconcile/resume",
            "/v1/operator/reviews/review_1/resolve",
        ):
            denied = await _post(
                client,
                path,
                key="user-attempt",
                body={
                    "reasonCode": "OPERATOR_RETRY",
                    "reason": "권한 없는 시도",
                    "expectedVersion": 1,
                    "resolution": {"decision": "ACCEPT"},
                },
            )
            assert denied.status_code == 403, path
            assert denied.json()["error"]["code"] == "FEATURE_NOT_ENTITLED"

    _run(scenario, operator=False)


def test_operator_lists_pass_the_contract_and_never_carry_internal_context() -> None:
    async def scenario(client: AsyncClient, _: Any) -> None:
        jobs = await client.get("/v1/operator/jobs", params={"limit": 50})
        assert jobs.status_code == 200
        validate_instance(jobs.json(), "OperatorJobListResponse", label="operator-jobs")
        assert [item["runId"] for item in jobs.json()["data"]["items"]] == [
            "run_infostock_daily",
            "run_reconcile",
            "run_reference_data",
        ]
        assert set(jobs.json()["data"]["items"][0]) == {
            "runId",
            "jobType",
            "status",
            "version",
            "lastChangedAt",
            "errorCode",
        }

        filtered = await client.get("/v1/operator/jobs", params={"status": "PARTIAL"})
        assert [item["runId"] for item in filtered.json()["data"]["items"]] == [
            "run_reconcile"
        ]

        job = await client.get("/v1/operator/jobs/run_infostock_daily")
        validate_instance(job.json(), "OperatorJobResponse", label="operator-job")

        reviews = await client.get(
            "/v1/operator/reviews", params={"status": "PENDING", "type": "EVENT_RECONCILIATION"}
        )
        validate_instance(
            reviews.json(), "OperatorReviewListResponse", label="operator-reviews"
        )
        review = await client.get("/v1/operator/reviews/review_1")
        validate_instance(review.json(), "OperatorReviewResponse", label="operator-review")

        auth_status = await client.get("/v1/operator/infostock/auth-status")
        validate_instance(
            auth_status.json(), "InfostockAuthStatusResponse", label="infostock-auth"
        )
        assert auth_status.json()["data"]["status"] == "AUTH_REQUIRED"

        serialized = "".join(
            json.dumps(response.json(), ensure_ascii=False)
            for response in (jobs, job, reviews, review, auth_status)
        )
        for forbidden in ("fixture-cookie-must-not-leak", "fixture-review-must-not-leak"):
            assert forbidden not in serialized

    _run(scenario)


def test_retry_writes_one_revision_and_repeats_return_the_same_receipt() -> None:
    async def scenario(client: AsyncClient, environment: Any) -> None:
        body = {
            "reasonCode": "INFOSTOCK_SESSION_REFRESHED",
            "reason": "운영자 수동 재인증 후 재시도",
            "expectedVersion": 1,
        }
        first = await _post(
            client,
            "/v1/operator/jobs/run_infostock_daily/retry",
            key="retry-key-1",
            body=body,
        )
        assert first.status_code == 200
        validate_instance(
            first.json(), "OperatorCommandResponse", label="operator-retry"
        )
        assert first.json()["data"]["beforeRevision"] == 1
        assert first.json()["data"]["afterRevision"] == 2

        repeated = await _post(
            client,
            "/v1/operator/jobs/run_infostock_daily/retry",
            key="retry-key-1",
            body=body,
        )
        assert repeated.status_code == 200
        assert repeated.json()["data"] == first.json()["data"]

        job = await client.get("/v1/operator/jobs/run_infostock_daily")
        assert job.json()["data"]["version"] == 2
        assert job.json()["data"]["status"] == "RUNNING"
        assert job.json()["data"]["errorCode"] is None

        audit = await client.get("/v1/operator/audit")
        validate_instance(audit.json(), "OperatorAuditListResponse", label="operator-audit")
        items = audit.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["action"] == "RETRY_JOB"
        assert items[0]["targetId"] == "run_infostock_daily"
        assert items[0]["reasonCode"] == "INFOSTOCK_SESSION_REFRESHED"
        assert "운영자 수동 재인증 후 재시도" not in json.dumps(
            audit.json(), ensure_ascii=False
        )

    _run(scenario)


def test_reused_idempotency_key_with_a_different_body_is_rejected() -> None:
    async def scenario(client: AsyncClient, _: Any) -> None:
        accepted = await _post(
            client,
            "/v1/operator/jobs/run_infostock_daily/retry",
            key="shared-key",
            body={
                "reasonCode": "INFOSTOCK_SESSION_REFRESHED",
                "reason": "첫 요청",
                "expectedVersion": 1,
            },
        )
        assert accepted.status_code == 200
        conflict = await _post(
            client,
            "/v1/operator/jobs/run_infostock_daily/retry",
            key="shared-key",
            body={
                "reasonCode": "INFOSTOCK_SESSION_REFRESHED",
                "reason": "다른 내용",
                "expectedVersion": 1,
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "COMMAND_NOT_ALLOWED"

    _run(scenario)


def test_stale_expected_version_and_disallowed_transitions_are_conflicts() -> None:
    async def scenario(client: AsyncClient, _: Any) -> None:
        stale = await _post(
            client,
            "/v1/operator/jobs/run_infostock_daily/retry",
            key="stale-key",
            body={
                "reasonCode": "INFOSTOCK_SESSION_REFRESHED",
                "reason": "오래된 화면에서 보낸 요청",
                "expectedVersion": 9,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "STALE_VERSION"

        succeeded = await _post(
            client,
            "/v1/operator/jobs/run_reference_data/retry",
            key="not-allowed-key",
            body={
                "reasonCode": "OPERATOR_RETRY",
                "reason": "성공한 run 재시도",
                "expectedVersion": 3,
            },
        )
        assert succeeded.status_code == 409
        assert succeeded.json()["error"]["code"] == "COMMAND_NOT_ALLOWED"

        missing = await _post(
            client,
            "/v1/operator/jobs/run_unknown/retry",
            key="missing-key",
            body={
                "reasonCode": "OPERATOR_RETRY",
                "reason": "없는 run",
                "expectedVersion": 1,
            },
        )
        assert missing.status_code == 404

    _run(scenario)


def test_resume_only_applies_to_partial_runs() -> None:
    async def scenario(client: AsyncClient, _: Any) -> None:
        resumed = await _post(
            client,
            "/v1/operator/jobs/run_reconcile/resume",
            key="resume-key",
            body={
                "reasonCode": "OPERATOR_RESUME",
                "reason": "부분 성공 run 이어서 실행",
                "expectedVersion": 1,
            },
        )
        assert resumed.status_code == 200
        assert resumed.json()["data"]["afterRevision"] == 2

        again = await _post(
            client,
            "/v1/operator/jobs/run_reconcile/resume",
            key="resume-key-2",
            body={
                "reasonCode": "OPERATOR_RESUME",
                "reason": "실행 중 run 재개",
                "expectedVersion": 2,
            },
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "COMMAND_NOT_ALLOWED"

    _run(scenario)


def test_review_resolution_creates_a_revision_and_cannot_be_repeated() -> None:
    async def scenario(client: AsyncClient, _: Any) -> None:
        resolved = await _post(
            client,
            "/v1/operator/reviews/review_1/resolve",
            key="resolve-key",
            body={
                "reasonCode": "RECONCILIATION_ACCEPTED",
                "reason": "장후 기사 없이 UNMATCHED 유지 확인",
                "expectedVersion": 1,
                "resolution": {"decision": "KEEP_UNMATCHED"},
            },
        )
        assert resolved.status_code == 200
        validate_instance(
            resolved.json(), "OperatorCommandResponse", label="operator-resolve"
        )

        review = await client.get("/v1/operator/reviews/review_1")
        assert review.json()["data"]["reviewStatus"] == "RESOLVED"
        assert review.json()["data"]["version"] == 2
        assert review.json()["data"]["resolvedAt"] is not None

        pending = await client.get("/v1/operator/reviews", params={"status": "PENDING"})
        assert pending.json()["data"]["items"] == []

        repeated = await _post(
            client,
            "/v1/operator/reviews/review_1/resolve",
            key="resolve-key-2",
            body={
                "reasonCode": "RECONCILIATION_ACCEPTED",
                "reason": "이미 처리한 검수",
                "expectedVersion": 2,
                "resolution": {"decision": "KEEP_UNMATCHED"},
            },
        )
        assert repeated.status_code == 409
        assert repeated.json()["error"]["code"] == "COMMAND_NOT_ALLOWED"

    _run(scenario)


def test_commands_require_origin_csrf_and_an_idempotency_key() -> None:
    async def scenario(client: AsyncClient, _: Any) -> None:
        body = {
            "reasonCode": "OPERATOR_RETRY",
            "reason": "보안 헤더 검증",
            "expectedVersion": 1,
        }
        path = "/v1/operator/jobs/run_infostock_daily/retry"
        csrf = client.cookies["__Host-dayjaview_csrf"]

        without_csrf = await client.post(
            path,
            headers={
                "Origin": "https://dayjaview.vercel.app",
                "Idempotency-Key": "no-csrf",
            },
            json=body,
        )
        assert without_csrf.status_code == 403
        assert without_csrf.json()["error"]["details"]["reasonCode"] == (
            "CSRF_VALIDATION_FAILED"
        )

        foreign_origin = await client.post(
            path,
            headers={
                "Origin": "https://attacker.example",
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "foreign-origin",
            },
            json=body,
        )
        assert foreign_origin.status_code == 403

        without_key = await client.post(
            path,
            headers={
                "Origin": "https://dayjaview.vercel.app",
                "X-CSRF-Token": csrf,
            },
            json=body,
        )
        assert without_key.status_code == 400
        assert without_key.json()["error"]["code"] == "INVALID_REQUEST"

        unchanged = await client.get("/v1/operator/jobs/run_infostock_daily")
        assert unchanged.json()["data"]["version"] == 1

    _run(scenario)


def test_command_bodies_reject_unknown_fields_and_out_of_range_values() -> None:
    async def scenario(client: AsyncClient, _: Any) -> None:
        path = "/v1/operator/jobs/run_infostock_daily/retry"
        for body in (
            {
                "reasonCode": "OPERATOR_RETRY",
                "reason": "추가 field",
                "expectedVersion": 1,
                "note": "허용되지 않는 field",
            },
            {"reasonCode": "operator_retry", "reason": "소문자 코드", "expectedVersion": 1},
            {"reasonCode": "OPERATOR_RETRY", "reason": "", "expectedVersion": 1},
            {"reasonCode": "OPERATOR_RETRY", "reason": "a" * 1001, "expectedVersion": 1},
            {"reasonCode": "OPERATOR_RETRY", "reason": "버전 0", "expectedVersion": 0},
            {"reasonCode": "OPERATOR_RETRY", "reason": "resolution 불필요", "expectedVersion": 1,
             "resolution": {"decision": "ACCEPT"}},
        ):
            response = await _post(client, path, key=f"body-{len(str(body))}", body=body)
            assert response.status_code == 400, body
            assert response.json()["error"]["code"] == "INVALID_REQUEST"

    _run(scenario)


def test_audit_and_job_pages_walk_forward_with_an_opaque_cursor() -> None:
    async def scenario(client: AsyncClient, _: Any) -> None:
        first = await client.get("/v1/operator/jobs", params={"limit": 2})
        page = first.json()["data"]["page"]
        assert page["hasMore"] is True
        assert page["limit"] == 2
        assert page["nextCursor"] == "run_reconcile"

        second = await client.get(
            "/v1/operator/jobs", params={"limit": 2, "cursor": page["nextCursor"]}
        )
        assert [item["runId"] for item in second.json()["data"]["items"]] == [
            "run_reference_data"
        ]
        assert second.json()["data"]["page"]["hasMore"] is False
        assert second.json()["data"]["page"]["nextCursor"] is None

        unknown = await client.get("/v1/operator/jobs", params={"cursor": "run_missing"})
        assert unknown.status_code == 400
        assert unknown.json()["error"]["code"] == "INVALID_REQUEST"

    _run(scenario)


@pytest.mark.skipif(
    _OPERATOR_TEST_DSN is None,
    reason="OPERATOR_TEST_DSN의 disposable PostgreSQL 16이 필요합니다.",
)
def test_postgres_operator_state_survives_repository_reassembly() -> None:
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(_OPERATOR_TEST_DSN, autocommit=True) as admin:
        admin.execute("DROP SCHEMA IF EXISTS operations CASCADE")
        admin.execute(_OPERATOR_MIGRATION.read_text(encoding="utf-8"))

    connection = psycopg.connect(_OPERATOR_TEST_DSN)
    repository = PostgresOperatorRepository(connection)
    job = repository.set_job_status(
        run_id="run_persistent",
        job_type="NEWS_LIVE",
        status=JobStatus.RUNNING,
        now=_BASE,
        internal_context={"stored": 3},
    )
    review = OperatorReview(
        review_id="review_persistent",
        review_type="AFTER_CLOSE_UNMATCHED",
        review_status=ReviewStatus.PENDING,
        target_id="evt_persistent",
        reason_code="NO_INFOSTOCK_CONFIRMATION",
        version=1,
        created_at=_BASE,
        resolved_at=None,
    )
    repository.open_review(review)
    audit = repository.append_audit(
        actor_id="usr_operator",
        occurred_at=_BASE,
        action="RESOLVE_REVIEW",
        target_id=review.review_id,
        reason_code="CHECKED",
        reason="확인 완료",
        before_revision=1,
        after_revision=2,
    )
    repository.store_receipt(
        OperatorCommandReceipt(
            actor_id="usr_operator",
            idempotency_key="persistent-command",
            fingerprint="a" * 64,
            audit=audit,
        )
    )
    connection.close()

    reconnected = psycopg.connect(_OPERATOR_TEST_DSN)
    try:
        persisted = PostgresOperatorRepository(reconnected)
        assert persisted.get_job(job.run_id) == job
        assert persisted.get_review(review.review_id) == review
        receipt = persisted.find_receipt(
            actor_id="usr_operator", idempotency_key="persistent-command"
        )
        assert receipt is not None and receipt.audit == audit
    finally:
        reconnected.close()
