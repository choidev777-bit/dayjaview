from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from packages.adapters.kiwoom import (
    ConnectionPhase,
    CoverageStatus,
    DemandPriority,
    FixtureKiwoomAdapter,
    GatewayDataStatus,
    IngestDisposition,
    MarketGateway,
    ReconnectController,
    ReconnectNotDue,
    ReconnectPolicy,
    SubscriptionDemand,
    SupplementReason,
)

FIXTURE = Path(__file__).parent / "fixtures" / "kiwoom-market-v1.json"
BASE = datetime(2026, 8, 14, tzinfo=UTC)


def _demands(at: datetime) -> tuple[SubscriptionDemand, ...]:
    return (
        SubscriptionDemand(
            "KRX:005930",
            DemandPriority.ACTIVE_LEADER,
            at,
        ),
        SubscriptionDemand(
            "KRX:000660",
            DemandPriority.ACTIVE_CORE,
            at,
        ),
        SubscriptionDemand(
            "KRX:035420",
            DemandPriority.ACTIVE_RELATED,
            at,
        ),
    )


def _gateway_through_disconnect() -> tuple[MarketGateway, tuple[SubscriptionDemand, ...]]:
    gateway = MarketGateway(FixtureKiwoomAdapter.from_path(FIXTURE))
    demands = _demands(BASE)
    gateway.connect(now=BASE)
    gateway.reconcile_subscriptions(demands, now=BASE, force=True)
    gateway.poll_once(now=BASE + timedelta(seconds=1))
    gateway.poll_once(now=BASE + timedelta(seconds=2))
    gateway.poll_once(now=BASE + timedelta(seconds=3))
    assert gateway.health(
        ("KRX:005930", "KRX:000660"),
        now=BASE + timedelta(seconds=3, milliseconds=100),
    ).data_status is GatewayDataStatus.LIVE
    gateway.poll_once(now=BASE + timedelta(seconds=4))
    return gateway, demands


def test_exponential_backoff_is_deterministic_jittered_and_capped() -> None:
    exact = ReconnectPolicy(jitter_ratio=0)
    delays = [
        exact.delay_for(attempt, jitter_key="session").total_seconds()
        for attempt in range(1, 8)
    ]

    assert delays == [1, 2, 4, 8, 16, 30, 30]
    jittered = ReconnectPolicy(jitter_ratio=0.2)
    first = jittered.delay_for(4, jitter_key="same-session")
    second = jittered.delay_for(4, jitter_key="same-session")
    assert first == second
    assert timedelta(seconds=6.4) <= first <= timedelta(seconds=8)


def test_reconnect_controller_resets_only_after_connection() -> None:
    controller = ReconnectController(ReconnectPolicy(jitter_ratio=0))
    first = controller.schedule_failure(
        now=BASE,
        reason="연결 종료",
        jitter_key="session",
    )
    second = controller.schedule_failure(
        now=first.due_at,
        reason="재연결 실패",
        jitter_key="session",
    )

    assert first.attempt == 1
    assert second.attempt == 2
    assert second.delay == timedelta(seconds=2)
    assert controller.is_due(second.due_at)
    controller.mark_connected()
    assert controller.schedule is None


def test_disconnect_preserves_last_values_but_marks_receipt_stale() -> None:
    gateway, _ = _gateway_through_disconnect()

    health = gateway.health(
        ("KRX:005930", "KRX:000660", "KRX:035420"),
        now=BASE + timedelta(seconds=4),
    )

    assert gateway.phase is ConnectionPhase.RECONNECTING
    assert health.data_status is GatewayDataStatus.STALE
    assert health.coverage.fresh_count == 0
    assert health.coverage.stale_stock_ids == ("KRX:000660", "KRX:005930")
    assert health.coverage.missing_stock_ids == ("KRX:035420",)
    event = gateway.state.event_for("KRX:005930")
    assert event is not None
    assert event.data.current_price == Decimal(73100)  # type: ignore[union-attr]


def test_reconnect_waits_for_backoff_resubscribes_and_supplements_partial_coverage() -> None:
    gateway, demands = _gateway_through_disconnect()
    schedule = gateway.reconnect.schedule
    assert schedule is not None

    with pytest.raises(ReconnectNotDue, match="backoff"):
        gateway.recover(
            demands,
            supplement_stock_ids=("KRX:035420",),
            now=schedule.scheduled_at,
        )

    recovered = gateway.recover(
        demands,
        supplement_stock_ids=("KRX:035420",),
        now=BASE + timedelta(seconds=6, milliseconds=200),
    )

    assert recovered.connected is True
    assert recovered.connection is not None
    assert recovered.connection.session_id == "fixture-session-new"
    assert recovered.subscription_plan is not None
    assert recovered.subscription_plan.subscriptions == (
        "KRX:005930",
        "KRX:000660",
        "KRX:035420",
    )
    assert recovered.supplement is not None
    assert recovered.supplement.coverage.status is CoverageStatus.PARTIAL
    assert recovered.supplement.coverage.fresh_count == 2
    assert Decimal("0.66") < recovered.supplement.coverage.fresh_ratio < Decimal("0.67")
    assert recovered.supplement.coverage.missing_stock_ids == ("KRX:035420",)
    assert gateway.phase is ConnectionPhase.CONNECTED

    complete_slice = gateway.health(
        ("KRX:005930", "KRX:000660"),
        now=BASE + timedelta(seconds=6, milliseconds=200),
    )
    partial_slice = gateway.health(
        ("KRX:005930", "KRX:000660", "KRX:035420"),
        now=BASE + timedelta(seconds=6, milliseconds=200),
    )
    # 관측이 덜 찬 종목이 섞여도 수신이 살아 있으면 LIVE다. 표본 부족은
    # coverage report로만 나가고 화면 상태를 지연으로 바꾸지 않는다.
    assert complete_slice.data_status is GatewayDataStatus.LIVE
    assert partial_slice.data_status is GatewayDataStatus.LIVE
    assert partial_slice.coverage.status is CoverageStatus.PARTIAL


def test_previous_session_payload_is_rejected_after_recovery() -> None:
    gateway, demands = _gateway_through_disconnect()
    gateway.recover(
        demands,
        supplement_stock_ids=(),
        now=BASE + timedelta(seconds=6, milliseconds=200),
    )
    late_adapter = FixtureKiwoomAdapter.from_path(FIXTURE)
    old = late_adapter.connect(now=BASE)
    late_message = late_adapter.read(old.session_id)
    assert late_message is not None

    results = gateway.ingest(late_message)

    assert results
    assert all(result.disposition is IngestDisposition.OLD_SESSION for result in results)


def test_explicit_gap_snapshot_supplement_restores_missing_stock_without_zero_fill() -> None:
    gateway, demands = _gateway_through_disconnect()
    recovered = gateway.recover(
        demands,
        supplement_stock_ids=("KRX:035420",),
        now=BASE + timedelta(seconds=6, milliseconds=200),
    )
    assert recovered.supplement is not None
    assert recovered.supplement.coverage.missing_stock_ids == ("KRX:035420",)

    gap = gateway.supplement(
        ("KRX:035420",),
        reason=SupplementReason.GAP,
        now=BASE + timedelta(seconds=7, milliseconds=200),
    )
    all_stocks = gateway.coverage(
        ("KRX:005930", "KRX:000660", "KRX:035420"),
        now=BASE + timedelta(seconds=7, milliseconds=200),
    )

    assert gap.reason is SupplementReason.GAP
    assert [result.disposition for result in gap.ingest_results] == [
        IngestDisposition.ACCEPTED
    ]
    assert gap.coverage.status is CoverageStatus.COMPLETE
    assert all_stocks.status is CoverageStatus.COMPLETE
    event = gateway.state.event_for("KRX:035420")
    assert event is not None
    assert event.data.current_price == Decimal(207000)  # type: ignore[union-attr]


def test_only_heartbeat_decides_data_status_while_coverage_stays_reported() -> None:
    gateway, demands = _gateway_through_disconnect()
    gateway.recover(
        demands,
        supplement_stock_ids=(),
        now=BASE + timedelta(seconds=6, milliseconds=200),
    )

    heartbeat_stale = gateway.health(
        ("KRX:005930", "KRX:000660"),
        now=BASE + timedelta(seconds=20),
    )
    assert heartbeat_stale.data_status is GatewayDataStatus.STALE
    assert heartbeat_stale.coverage.status is CoverageStatus.COMPLETE

    gateway.mark_heartbeat(at=BASE + timedelta(seconds=50))
    low_confidence = gateway.health(
        ("KRX:005930", "KRX:000660"),
        now=BASE + timedelta(seconds=50),
    )
    assert low_confidence.data_status is GatewayDataStatus.LIVE
    assert low_confidence.coverage.status is CoverageStatus.INSUFFICIENT
    assert low_confidence.coverage.low_confidence_count == 2
    assert low_confidence.coverage.missing_count == 0


def test_empty_coverage_has_null_ratio_instead_of_a_fabricated_zero() -> None:
    gateway = MarketGateway(FixtureKiwoomAdapter.from_path(FIXTURE))
    gateway.connect(now=BASE)

    coverage = gateway.coverage((), now=BASE)

    assert coverage.status is CoverageStatus.INSUFFICIENT
    assert coverage.fresh_ratio is None
