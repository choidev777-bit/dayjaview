from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from apps.api.config import TRUSTED_PROXY_HOPS_ENV
from apps.api.production import (
    CURSOR_SIGNING_SECRET_ENV,
    GOOGLE_CLIENT_ID_ENV,
    GOOGLE_CLIENT_SECRET_ENV,
    IDENTITY_DATABASE_DSN_ENV,
)

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = ROOT / "infra" / "deployment"
ENVIRONMENT_CONTRACT = DEPLOYMENT / "environment.contract.json"
COMPOSE_FILES = (
    DEPLOYMENT / "compose.local.yml",
    DEPLOYMENT / "compose.ci.yml",
)
EXPECTED_SERVICES = {
    "api",
    "health-smoke",
    "migrate",
    "migration-idempotency",
    "postgres",
    "redis",
    "worker-infostock-fixture",
    "worker-market-fixture",
    "worker-reference-fixture",
}


def _load(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda path: path.name)
def test_fixture_compose_has_explicit_health_and_dependency_order(path: Path) -> None:
    compose = _load(path)
    services = compose["services"]

    assert set(services) == EXPECTED_SERVICES
    assert compose.get("volumes") is None
    assert compose["networks"]["fixture"]["internal"] is True
    assert services["postgres"]["image"] == "postgres:16.15-bookworm"
    assert services["redis"]["image"] == "redis:7.4.5-bookworm"
    assert services["postgres"]["healthcheck"]
    assert "127.0.0.1" in services["postgres"]["healthcheck"]["test"][1]
    assert services["redis"]["healthcheck"]
    assert services["api"]["build"]["target"] == "api"
    assert services["api"]["healthcheck"]

    assert services["migrate"]["depends_on"] == {
        "postgres": {"condition": "service_healthy"}
    }
    assert services["migration-idempotency"]["depends_on"] == {
        "postgres": {"condition": "service_healthy"},
        "migrate": {"condition": "service_completed_successfully"},
    }
    for worker in (
        "worker-infostock-fixture",
        "worker-market-fixture",
        "worker-reference-fixture",
    ):
        dependencies = services[worker]["depends_on"]
        assert dependencies["migration-idempotency"]["condition"] == (
            "service_completed_successfully"
        )
        assert dependencies["redis"]["condition"] == "service_healthy"

    api_dependencies = services["api"]["depends_on"]
    assert api_dependencies["postgres"]["condition"] == "service_healthy"
    assert api_dependencies["redis"]["condition"] == "service_healthy"
    assert api_dependencies["migration-idempotency"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["health-smoke"]["depends_on"] == {
        "api": {"condition": "service_healthy"}
    }


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda path: path.name)
def test_fixture_compose_is_arm64_ephemeral_and_live_free(path: Path) -> None:
    compose = _load(path)
    services = compose["services"]
    for service in services.values():
        assert service["platform"] == "linux/arm64"

    postgres = services["postgres"]
    redis = services["redis"]
    assert any(entry.startswith("/var/lib/postgresql/data:") for entry in postgres["tmpfs"])
    assert any(entry.startswith("/data:") for entry in redis["tmpfs"])
    assert "volumes" not in postgres
    assert "volumes" not in redis
    assert "ports" not in postgres
    assert "ports" not in redis
    assert postgres["environment"]["POSTGRES_HOST_AUTH_METHOD"] == "trust"
    assert "--appendonly" in redis["command"]
    assert "no" in redis["command"]
    assert redis["command"][-2:] == ["--ignore-warnings", "ARM64-COW-BUG"]

    commands = "\n".join(
        str(service.get("command", "")) for service in services.values()
    )
    assert "kiwoom-market-v1.json" in commands
    assert "krx-stock-daily.json" in commands
    assert "infostock-280.synthetic.json" in commands
    assert "--audit-only" in commands
    assert "http://api:8000/api/health" in commands
    assert "https://" not in commands
    assert "wss://" not in commands

    if path.name == "compose.local.yml":
        assert services["api"]["ports"] == [
            "127.0.0.1:${DAYJAVIEW_LOCAL_API_PORT:-18000}:8000"
        ]
        assert services["health-smoke"]["profiles"] == ["smoke"]
    else:
        assert "ports" not in services["api"]
        assert "profiles" not in services["health-smoke"]


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda path: path.name)
def test_compose_environment_contains_no_secret_or_credential_values(path: Path) -> None:
    services = _load(path)["services"]
    forbidden_names = (
        "APPLICATION_ENCRYPTION_KEY",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "INFOSTOCK_SESSION_STATE_PATH",
        "KRX_API_KEY",
        "OPENDART_API_KEY",
        "PASSWORD",
        "SESSION_SIGNING_SECRET",
        "TOKEN",
    )
    for service_name, service in services.items():
        environment = service.get("environment", {})
        for name in forbidden_names:
            assert name not in environment, f"{service_name}에 {name} 값이 포함됨"


def test_environment_contract_declares_the_names_the_api_actually_reads() -> None:
    """배포 env 계약과 코드가 같은 변수 이름을 써야 한다.

    이 둘이 어긋나면 배포에서 계약대로 값을 넣어도 코드는 비어 있다고 보고
    fixture provider로 떨어진다 — 그게 곧 인증 우회다. 계약 파일을 읽는 코드가
    따로 없으므로 여기서 고정한다.
    """

    declared = {
        variable["name"]
        for variable in json.loads(ENVIRONMENT_CONTRACT.read_text(encoding="utf-8"))[
            "variables"
        ]
    }
    required_by_code = {
        "APP_BASE_URL",
        "OPERATOR_BOOTSTRAP_GOOGLE_EMAILS",
        CURSOR_SIGNING_SECRET_ENV,
        GOOGLE_CLIENT_ID_ENV,
        GOOGLE_CLIENT_SECRET_ENV,
        IDENTITY_DATABASE_DSN_ENV,
        TRUSTED_PROXY_HOPS_ENV,
    }

    assert required_by_code <= declared, required_by_code - declared
