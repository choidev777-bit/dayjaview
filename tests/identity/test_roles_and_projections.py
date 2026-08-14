from __future__ import annotations

import asyncio
import json

from httpx import ASGITransport, AsyncClient

from apps.api import ApiSettings, create_fixture_app
from packages.identity import (
    GoogleIdentity,
    Role,
    RuntimeOperatorStatus,
    RuntimeServiceStatus,
)

from .helpers import MutableClock, api_login, service_login


def test_default_empty_bootstrap_keeps_verified_user_out_of_operator_role() -> None:
    environment = create_fixture_app(clock=MutableClock())
    completion = service_login(
        environment,
        code="no-bootstrap",
        identity=GoogleIdentity(
            "google-no-bootstrap",
            "일반 사용자",
            email="operator@example.test",
            email_verified=True,
        ),
    )
    assert completion.roles == frozenset({Role.USER})


def test_bootstrap_requires_both_configured_and_google_verified_email() -> None:
    settings = ApiSettings(
        operator_bootstrap_emails=frozenset({"operator@example.test"})
    )
    environment = create_fixture_app(settings=settings, clock=MutableClock())
    unverified = service_login(
        environment,
        code="operator-unverified",
        identity=GoogleIdentity(
            "google-unverified",
            "미검증 사용자",
            email="operator@example.test",
            email_verified=False,
        ),
    )
    verified = service_login(
        environment,
        code="operator-verified",
        identity=GoogleIdentity(
            "google-verified",
            "운영 사용자",
            email="OPERATOR@example.test",
            email_verified=True,
        ),
    )
    assert Role.OPERATOR not in unverified.roles
    assert verified.roles == frozenset({Role.USER, Role.OPERATOR})


def test_user_gets_403_before_operator_route_existence_is_disclosed() -> None:
    async def scenario() -> None:
        environment = create_fixture_app(clock=MutableClock())
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            await api_login(
                client,
                environment,
                code="user-role-api",
                identity=GoogleIdentity("google-user-role", "일반 사용자"),
            )
            status = await client.get("/v1/operator/status")
            unknown = await client.get("/v1/operator/private-unknown")
            assert status.status_code == 403
            assert unknown.status_code == 403
            assert status.json()["error"]["code"] == "FEATURE_NOT_ENTITLED"

    asyncio.run(scenario())


def test_operator_projection_allowlists_fields_and_never_returns_diagnostics() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        runtime_status = RuntimeOperatorStatus(
            deployment_version="2026.08.14.1",
            commit="abcdef1234567",
            started_at=clock.now(),
            services=(
                RuntimeServiceStatus(
                    name="infostock",
                    status="AUTH_REQUIRED",
                    last_succeeded_at=None,
                    error_code="AUTH_REQUIRED",
                    diagnostic_context={
                        "secret": "fixture-secret-must-not-leak",
                        "token": "fixture-token-must-not-leak",
                        "cookie": "fixture-cookie-must-not-leak",
                        "internalReviewNote": "fixture-review-must-not-leak",
                        "internalPath": "/private/fixture/path",
                    },
                ),
            ),
            internal_context={"credential": "fixture-credential-must-not-leak"},
        )
        settings = ApiSettings(
            operator_bootstrap_emails=frozenset({"operator@example.test"})
        )
        environment = create_fixture_app(
            settings=settings,
            clock=clock,
            operator_status=runtime_status,
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
                code="operator-api",
                identity=GoogleIdentity(
                    "google-operator-api",
                    "운영 사용자",
                    email="operator@example.test",
                    email_verified=True,
                ),
            )
            response = await client.get("/v1/operator/status")
            assert response.status_code == 200
            assert set(response.json()["data"]) == {
                "deploymentVersion",
                "commit",
                "startedAt",
                "services",
            }
            assert set(response.json()["data"]["services"][0]) == {
                "name",
                "status",
                "lastSucceededAt",
                "errorCode",
            }
            serialized = json.dumps(response.json(), ensure_ascii=False)
            for forbidden in (
                "fixture-secret-must-not-leak",
                "fixture-token-must-not-leak",
                "fixture-cookie-must-not-leak",
                "fixture-review-must-not-leak",
                "fixture-credential-must-not-leak",
                "/private/fixture/path",
                "internalReviewNote",
            ):
                assert forbidden not in serialized

            unknown = await client.get("/v1/operator/private-unknown")
            assert unknown.status_code == 404

    asyncio.run(scenario())


def test_operator_projection_fails_closed_when_safe_field_contains_secret_like_value() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        settings = ApiSettings(
            operator_bootstrap_emails=frozenset({"operator@example.test"})
        )
        environment = create_fixture_app(
            settings=settings,
            clock=clock,
            operator_status=RuntimeOperatorStatus(
                deployment_version="2026.08.14.1",
                commit="abcdef1234567",
                started_at=clock.now(),
                services=(
                    RuntimeServiceStatus(
                        name="market",
                        status="FAILED",
                        last_succeeded_at=None,
                        error_code="token=fixture-secret-must-not-leak",
                    ),
                ),
            ),
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
                code="operator-invalid-projection",
                identity=GoogleIdentity(
                    "google-invalid-operator",
                    "운영 사용자",
                    email="operator@example.test",
                    email_verified=True,
                ),
            )
            response = await client.get("/v1/operator/status")
            assert response.status_code == 500
            assert "fixture-secret-must-not-leak" not in response.text
            assert response.json()["error"]["code"] == "INTERNAL_ERROR"

    asyncio.run(scenario())


def test_public_session_projection_contains_only_display_name_and_roles() -> None:
    async def scenario() -> None:
        environment = create_fixture_app(clock=MutableClock())
        transport = ASGITransport(app=environment.app)
        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            await api_login(
                client,
                environment,
                code="projection-api",
                identity=GoogleIdentity(
                    "google-private-subject",
                    "프로필 사용자",
                    email="private-profile@example.test",
                    email_verified=True,
                ),
            )
            response = await client.get("/auth/session")
            assert response.status_code == 200
            data = response.json()["data"]
            assert data == {
                "authenticated": True,
                "user": {"displayName": "프로필 사용자"},
                "roles": ["USER"],
            }
            serialized = json.dumps(response.json(), ensure_ascii=False)
            assert "google-private-subject" not in serialized
            assert "private-profile@example.test" not in serialized
            assert "accessToken" not in serialized
            assert "refreshToken" not in serialized
            assert "reviewStatus" not in serialized

    asyncio.run(scenario())
