from __future__ import annotations

from datetime import datetime
from importlib import import_module
from typing import Any

import pytest


@pytest.fixture(scope="session")
def replay() -> Any:
    return import_module("packages.adapters.market-replay.market_replay")


@pytest.fixture
def record_factory(replay: Any):
    def build(
        *,
        sequence: int = 1,
        run_id: str = "market-2026-08-14-synthetic",
        event_type: str = "market.trade",
        source: str = "synthetic_kiwoom_websocket",
        occurred_at: str = "2026-08-14T00:00:00+00:00",
        received_at: str = "2026-08-14T00:00:00.100000+00:00",
        stock_code: str | None = "005930",
        source_sequence: str | None = None,
        payload: dict[str, object] | None = None,
        schema_version: str = "1.0.0",
    ) -> dict[str, object]:
        body = payload or {
            "type": "0B",
            "item": "005930",
            "values": {
                "10": "+73100",
                "12": "1.25",
                "14": "87500000",
            },
        }
        return {
            "sequence": sequence,
            "runId": run_id,
            "eventType": event_type,
            "source": source,
            "occurredAt": occurred_at,
            "receivedAt": received_at,
            "stockCode": stock_code,
            "sourceSequence": source_sequence,
            "payload": body,
            "payloadSha256": replay.payload_sha256(body),
            "schemaVersion": schema_version,
        }

    return build


def assert_same_instant(actual: datetime, expected: str) -> None:
    assert actual == datetime.fromisoformat(expected)
