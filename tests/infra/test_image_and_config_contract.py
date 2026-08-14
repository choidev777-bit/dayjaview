from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "infra" / "images" / "runtime.Dockerfile"
ENV_CONTRACT = ROOT / "infra" / "deployment" / "environment.contract.json"


def test_runtime_image_is_pinned_non_root_and_arm64_only() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "python:3.12.11-slim-bookworm" in dockerfile
    assert "postgres:16.15-bookworm" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.8.14" in dockerfile
    assert ":latest" not in dockerfile
    assert dockerfile.count('test "${TARGETARCH}" = "arm64"') >= 3
    assert 'io.dayjaview.target-platform="linux/arm64"' in dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    assert "FROM app-runtime AS api" in dockerfile
    assert "FROM app-runtime AS worker" in dockerfile
    assert "FROM ${POSTGRES_IMAGE} AS migrations" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "USER 999:999" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "--timeout=10s" in dockerfile
    assert "sed -i 's/\\r$//' /usr/local/bin/local-migrate" in dockerfile
    assert "apt-get" not in dockerfile


def test_environment_contract_only_documents_names_and_required_state() -> None:
    contract = json.loads(ENV_CONTRACT.read_text(encoding="utf-8"))
    assert set(contract) == {"schemaVersion", "variables"}
    assert contract["schemaVersion"] == 1
    variables = contract["variables"]
    names = [variable["name"] for variable in variables]
    assert names == sorted(set(names))

    expected_environments = {
        "localFixture",
        "ciFixture",
        "staging",
        "production",
    }
    for variable in variables:
        assert set(variable) == {"name", "required"}
        assert set(variable["required"]) == expected_environments
        assert variable["required"]["localFixture"] is False
        assert variable["required"]["ciFixture"] is False
        assert all(isinstance(value, bool) for value in variable["required"].values())

    operator_bootstrap = next(
        variable
        for variable in variables
        if variable["name"] == "OPERATOR_BOOTSTRAP_GOOGLE_EMAILS"
    )
    assert operator_bootstrap == {
        "name": "OPERATOR_BOOTSTRAP_GOOGLE_EMAILS",
        "required": {
            "localFixture": False,
            "ciFixture": False,
            "staging": True,
            "production": True,
        },
    }


def test_fixture_api_fails_closed_with_korean_diagnostic_without_fixture_mode() -> None:
    result = subprocess.run(
        [sys.executable, "infra/operations/local_stack.py", "api"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode != 0
    assert "로컬 fixture 상태 확인 실패(ko-KR)" in result.stderr
    assert "live 실행은 허용되지 않습니다" in result.stderr


def test_owned_runtime_config_contains_no_credential_literal() -> None:
    roots = (
        ROOT / "infra" / "deployment",
        ROOT / "infra" / "images",
        ROOT / "infra" / "operations",
    )
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in sorted(root.glob("local*" if root.name == "operations" else "**/*"))
        if path.is_file()
    )
    forbidden_patterns = (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_-]{35}",
        r"postgres(?:ql)?://[^\s:/]+:[^\s@/]+@",
        r"redis://:[^\s@/]+@",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, text) is None
