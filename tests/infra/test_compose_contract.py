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
PRODUCTION_COMPOSE = DEPLOYMENT / "compose.production.yml"
ALL_COMPOSE_FILES = COMPOSE_FILES + (PRODUCTION_COMPOSE,)
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


@pytest.mark.parametrize("path", ALL_COMPOSE_FILES, ids=lambda path: path.name)
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


def test_production_compose_persists_data_and_exposes_only_caddy() -> None:
    """production은 fixture와 반대다: 데이터는 남고, 공개는 Caddy 80/443뿐이다."""

    compose = _load(PRODUCTION_COMPOSE)
    services = compose["services"]

    assert set(services) == {
        "api",
        "caddy",
        "infostock-bootstrap",
        "migrate",
        "migration-idempotency",
        "postgres",
        "worker-after-close-reconcile",
        "worker-infostock-increment",
        "worker-news",
    }
    for service in services.values():
        assert service["platform"] == "linux/arm64"

    # 재부팅에도 남아야 하는 것: DB 데이터와 TLS 인증서.
    assert set(compose["volumes"]) == {"postgres_data", "caddy_data", "caddy_config"}
    assert services["postgres"]["volumes"] == ["postgres_data:/var/lib/postgresql/data"]
    assert "tmpfs" not in services["postgres"]
    assert services["postgres"]["restart"] == "unless-stopped"
    assert "POSTGRES_HOST_AUTH_METHOD" not in services["postgres"]["environment"]

    # 공개 ingress는 Caddy 80/443뿐, DB·API는 직접 공개하지 않는다(ADR-009 5항).
    assert services["caddy"]["ports"] == ["80:80", "443:443"]
    for name in services.keys() - {"caddy"}:
        assert "ports" not in services[name], f"{name}는 호스트 포트를 공개할 수 없다"
    assert compose["networks"]["backend"]["internal"] is True
    assert services["postgres"]["networks"] == ["backend"]

    # API는 live 진입·live probe로 돌고, 마이그레이션이 먼저 끝나야 한다.
    api = services["api"]
    assert api["command"] == ["python", "infra/operations/live_stack.py", "api"]
    assert "infra/operations/live_stack.py" in api["healthcheck"]["test"]
    assert api["restart"] == "unless-stopped"
    assert api["environment"]["TRUSTED_PROXY_HOPS"] == "1"
    assert api["environment"]["THEME_UNIVERSE_MODE"] == "infostock"
    assert api["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert api["depends_on"]["migration-idempotency"]["condition"] == (
        "service_completed_successfully"
    )
    assert api["depends_on"]["infostock-bootstrap"]["condition"] == (
        "service_completed_successfully"
    )
    assert api["read_only"] is True

    # 인포스탁 증분 수집은 profile 뒤라 `up`으로는 시작되지 않는다.
    assert services["worker-infostock-increment"]["profiles"] == ["collect"]
    assert "--approved" in services["worker-infostock-increment"]["command"]
    assert services["worker-after-close-reconcile"]["profiles"] == ["collect"]
    assert services["worker-news"]["restart"] == "unless-stopped"
    assert services["worker-news"]["depends_on"]["migration-idempotency"] == {
        "condition": "service_completed_successfully"
    }
    assert services["infostock-bootstrap"]["command"][-2:] == [
        "--collection-dir",
        "/workspace/data/infostock-import",
    ]


def test_production_compose_takes_secrets_only_from_root_env_files() -> None:
    """secret은 값이 아니라 /etc/dayjaview/*.env 참조로만 들어온다(ADR-009 7항)."""

    services = _load(PRODUCTION_COMPOSE)["services"]
    for name in (
        "postgres",
        "migrate",
        "migration-idempotency",
        "api",
        "infostock-bootstrap",
        "worker-infostock-increment",
        "worker-after-close-reconcile",
        "worker-news",
    ):
        env_files = services[name]["env_file"]
        assert env_files, f"{name}에 env_file이 없다"
        for entry in env_files:
            assert str(entry).startswith("/etc/dayjaview/"), (
                f"{name}의 env_file이 VM secret 경로 밖이다: {entry}"
            )
    for name, service in services.items():
        environment = service.get("environment", {})
        assert "DAYJAVIEW_FIXTURE_MODE" not in environment, (
            f"{name}가 production에서 fixture 모드를 켠다"
        )
    for name in ("migrate", "migration-idempotency"):
        assert services[name]["environment"]["DAYJAVIEW_PRODUCTION_MIGRATION"] == "1"


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
