from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
