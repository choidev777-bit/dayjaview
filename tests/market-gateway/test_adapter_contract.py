from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from packages.adapters.kiwoom import (
    FIXTURE_SCHEMA_VERSION,
    FixtureCallKind,
    FixtureContractError,
    FixtureKiwoomAdapter,
    KiwoomConnectionLost,
    LiveValidationStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kiwoom-market-v1.json"


def test_fixture_adapter_is_explicitly_read_only_and_live_pending() -> None:
    adapter = FixtureKiwoomAdapter.from_path(FIXTURE)

    assert adapter.capabilities.read_only is True
    assert adapter.capabilities.orders is False
    assert adapter.capabilities.accounts is False
    assert adapter.capabilities.live_validation is LiveValidationStatus.PENDING_EXTERNAL
    assert not hasattr(adapter, "place_order")
    assert not hasattr(adapter, "fetch_account")


def test_fixture_contract_streams_versioned_sessions_and_injects_disconnect() -> None:
    adapter = FixtureKiwoomAdapter.from_path(FIXTURE)
    now = datetime(2026, 8, 14, tzinfo=UTC)
    connection = adapter.connect(now=now)

    messages = [adapter.read(connection.session_id) for _ in range(3)]

    assert connection.session_id == "fixture-session-old"
    assert [message.source_sequence for message in messages if message] == [1, 2, 3]
    assert all(message.session_id == connection.session_id for message in messages if message)
    with pytest.raises(KiwoomConnectionLost, match="연결 종료"):
        adapter.read(connection.session_id)

    reconnected = adapter.connect(now=now)
    assert reconnected.session_id == "fixture-session-new"
    assert [call.kind for call in adapter.calls[:2]] == [
        FixtureCallKind.CONNECT,
        FixtureCallKind.CONNECT,
    ]


def test_fixture_adapter_enforces_200_subscription_hard_limit() -> None:
    adapter = FixtureKiwoomAdapter.from_path(FIXTURE)
    connection = adapter.connect(now=datetime(2026, 8, 14, tzinfo=UTC))
    stock_ids = [f"KRX:{index:06d}" for index in range(201)]

    with pytest.raises(FixtureContractError, match="200종목"):
        adapter.replace_trade_subscriptions(connection.session_id, stock_ids)


def test_snapshot_fixture_returns_only_requested_codes() -> None:
    adapter = FixtureKiwoomAdapter.from_path(FIXTURE)
    now = datetime(2026, 8, 14, tzinfo=UTC)
    first = adapter.connect(now=now)
    adapter.close_session(first.session_id)
    second = adapter.connect(now=now)

    responses = adapter.fetch_watchlist_snapshots(
        second.session_id,
        ("KRX:005930", "KRX:000660", "KRX:035420"),
        requested_at=now,
    )

    assert len(responses) == 1
    assert responses[0].request_id == "snapshot-request-001"
    assert responses[0].session_id == second.session_id
    assert adapter.calls[-1].kind is FixtureCallKind.FETCH_SNAPSHOTS


def test_fixture_contract_rejects_credentials(tmp_path: Path) -> None:
    unsafe = {
        "fixtureVersion": FIXTURE_SCHEMA_VERSION,
        "adapter": {
            "readOnly": True,
            "liveValidation": "PENDING_EXTERNAL",
            "appSecret": None,
        },
        "sessions": [],
    }
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(unsafe), encoding="utf-8")

    with pytest.raises(FixtureContractError, match="credential"):
        FixtureKiwoomAdapter.from_path(path)
