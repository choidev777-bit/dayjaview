from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run_live_api(extra_environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "infra/operations/live_stack.py", "api"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PYTHONIOENCODING": "utf-8", **extra_environment},
    )


def test_live_api_fails_closed_with_korean_diagnostic_in_fixture_mode() -> None:
    result = _run_live_api({"DAYJAVIEW_FIXTURE_MODE": "1"})
    assert result.returncode != 0
    assert "운영 live 상태 확인 실패(ko-KR)" in result.stderr
    assert "fixture 모드에서는 실행할 수 없습니다" in result.stderr


def test_live_api_fails_closed_without_reachable_postgres() -> None:
    # 포트 1은 어디서도 listen하지 않으므로 접속 거부가 결정적이다.
    result = _run_live_api({"POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": "1"})
    assert result.returncode != 0
    assert "운영 live 상태 확인 실패(ko-KR)" in result.stderr
    assert "PostgreSQL 연결 실패" in result.stderr
