"""Offline-only entry point for the S2 Market Gateway fixture contract."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_PACKAGE_ROOT = WORKSPACE_ROOT / "packages" / "adapters"
if str(ADAPTER_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ADAPTER_PACKAGE_ROOT))

from kiwoom import (
    CanonicalMarketEvent,
    FixtureKiwoomAdapter,
    KiwoomConnectionLost,
    MarketGateway,
)


@dataclass(frozen=True, slots=True)
class FixtureRunResult:
    session_id: str
    events: tuple[CanonicalMarketEvent, ...]
    live_validation: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sessionId": self.session_id,
            "events": [event.to_dict() for event in self.events],
            "liveValidation": self.live_validation,
        }


def run_fixture(path: str | Path) -> FixtureRunResult:
    adapter = FixtureKiwoomAdapter.from_path(path)
    gateway = MarketGateway(adapter)
    connection = gateway.connect(now=datetime.now(UTC))
    while True:
        try:
            envelope = adapter.read(connection.session_id)
        except KiwoomConnectionLost:
            break
        if envelope is None:
            break
        gateway.ingest(envelope)
    return FixtureRunResult(
        session_id=connection.session_id,
        events=gateway.accepted_events,
        live_validation=adapter.capabilities.live_validation.value,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="키움 라이브 접속 없이 synthetic fixture 계약만 실행합니다."
    )
    parser.add_argument("--fixture", required=True, help="fixture JSON 경로")
    return parser


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"Market Gateway fixture 실행 실패: {message}")


def main() -> int:
    args = _parser().parse_args()
    try:
        result = run_fixture(args.fixture)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail(str(exc))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
