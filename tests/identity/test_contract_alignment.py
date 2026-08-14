from __future__ import annotations

import asyncio
import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from jsonschema import Draft202012Validator, FormatChecker

from apps.api import ApiSettings, create_fixture_app
from apps.api.cookies import CSRF_COOKIE
from packages.identity import (
    GoogleIdentity,
    RuntimeOperatorStatus,
    RuntimeServiceStatus,
    SavedCurrentState,
    SavedType,
    TargetRecord,
)

from .helpers import MutableClock, api_login

_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT_SCHEMA = json.loads(
    (_ROOT / "contracts" / "schemas" / "stage0.schema.json").read_text(encoding="utf-8")
)
_VALIDATOR = Draft202012Validator(
    _CONTRACT_SCHEMA,
    format_checker=FormatChecker(),
)


def _assert_contract(definition: str, payload: object) -> None:
    validator = _VALIDATOR.evolve(schema={"$ref": f"#/$defs/{definition}"})
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    assert not errors, "\n".join(error.message for error in errors)


def test_auth_saved_operator_and_deletion_responses_match_machine_contract() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        theme = TargetRecord(
            SavedType.THEME,
            "thm_584",
            "스페이스X(SpaceX)",
            current_state=SavedCurrentState(
                event_id="evt_current",
                event_state="ACTIVE",
                weighted_return=0.0342,
                data_status="LIVE",
                as_of=clock.now(),
            ),
        )
        operator_status = RuntimeOperatorStatus(
            deployment_version="2026.08.14.1",
            commit="abcdef1234567",
            started_at=clock.now(),
            services=(
                RuntimeServiceStatus(
                    name="infostock",
                    status="AUTH_REQUIRED",
                    last_succeeded_at=None,
                    error_code="AUTH_REQUIRED",
                ),
            ),
        )
        environment = create_fixture_app(
            settings=ApiSettings(
                operator_bootstrap_emails=frozenset({"operator@example.test"})
            ),
            clock=clock,
            targets=(theme,),
            operator_status=operator_status,
        )
        transport = ASGITransport(app=environment.app)

        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as anonymous:
            session = await anonymous.get("/auth/session")
            _assert_contract("SessionResponse", session.json())
            denied = await anonymous.get("/v1/me/saved")
            assert denied.status_code == 401
            _assert_contract("ErrorResponse", denied.json())

        async with AsyncClient(
            transport=transport,
            base_url="https://dayjaview.vercel.app",
            follow_redirects=False,
        ) as client:
            await api_login(
                client,
                environment,
                code="contract-code",
                identity=GoogleIdentity(
                    "google-contract",
                    "계약 사용자",
                    email="operator@example.test",
                    email_verified=True,
                ),
            )
            csrf = client.cookies.get(CSRF_COOKIE)
            assert csrf is not None
            mutation_headers = {
                "Origin": "https://dayjaview.vercel.app",
                "X-CSRF-Token": csrf,
            }

            authenticated = await client.get("/auth/session")
            _assert_contract("SessionResponse", authenticated.json())

            saved = await client.put(
                "/v1/me/saved/themes/thm_584",
                headers=mutation_headers,
            )
            _assert_contract("SavedMutationResponse", saved.json())

            library = await client.get("/v1/me/saved")
            _assert_contract("SavedListResponse", library.json())

            ticket = await client.post(
                "/v1/auth/realtime-ticket",
                headers=mutation_headers,
            )
            _assert_contract("RealtimeTicketResponse", ticket.json())

            status = await client.get("/v1/operator/status")
            _assert_contract("OperatorStatusResponse", status.json())

            deleted = await client.delete("/v1/me", headers=mutation_headers)
            assert deleted.status_code == 202
            _assert_contract("AccountDeletionResponse", deleted.json())

            after_delete = await client.get("/v1/me/saved")
            assert after_delete.status_code == 401
            _assert_contract("ErrorResponse", after_delete.json())

    asyncio.run(scenario())
