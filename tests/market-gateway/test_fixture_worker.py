from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
WORKER = ROOT / "apps" / "worker-market" / "fixture_worker.py"
FIXTURE = Path(__file__).parent / "fixtures" / "kiwoom-market-v1.json"


def test_worker_runs_fixture_only_and_reports_live_validation_pending() -> None:
    completed = subprocess.run(
        [sys.executable, str(WORKER), "--fixture", str(FIXTURE)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    result = json.loads(completed.stdout)

    assert result["sessionId"] == "fixture-session-old"
    assert result["liveValidation"] == "PENDING_EXTERNAL"
    assert len(result["events"]) == 5
    assert {event["type"] for event in result["events"]} == {
        "market.candidate.entered",
        "market.trade",
    }
